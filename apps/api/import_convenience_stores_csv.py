import csv
import os
import re
import sys
from pathlib import Path

import psycopg
from psycopg.types.json import Json

from address import normalize_address, parse

SOURCE_TABLE = 'convenience_store_csv'
CATEGORY = 'convenience'
ACTIVE_STATUS = '01'

COL_COMPANY_ID = '\u516c\u53f8\u7d71\u4e00\u7de8\u865f'
COL_COMPANY_NAME = '\u516c\u53f8\u540d\u7a31'
COL_BRANCH_ID = '\u5206\u516c\u53f8\u7d71\u4e00\u7de8\u865f'
COL_BRANCH_NAME = '\u5206\u516c\u53f8\u540d\u7a31'
COL_BRANCH_ADDR = '\u5206\u516c\u53f8\u5730\u5740'
COL_BRANCH_STATUS = '\u5206\u516c\u53f8\u72c0\u614b'
COL_ESTABLISHED = '\u5206\u516c\u53f8\u6838\u51c6\u8a2d\u7acb\u65e5\u671f'
COL_UPDATED = '\u5206\u516c\u53f8\u6700\u5f8c\u6838\u51c6\u8b8a\u66f4\u65e5\u671f'

RE_DIGITS = re.compile(r'\d+')
RE_MULTI_HOUSE_NO = re.compile(
    r'(?P<prefix>.*?)(?P<num_seq>[0-9\uFF10-\uFF19\u4E00\u4E8C\u4E09\u56DB\u4E94\u516D\u4E03\u516B\u4E5D\u5341\u767E\u5343\u3007\u96F6\u25CB\uFF2F]+(?:[-\u2013\uFF0D\u4E4B][0-9\uFF10-\uFF19\u4E00\u4E8C\u4E09\u56DB\u4E94\u516D\u4E03\u516B\u4E5D\u5341\u767E\u5343\u3007\u96F6\u25CB\uFF2F]+)?(?:[\u3001,\uFF0C][0-9\uFF10-\uFF19\u4E00\u4E8C\u4E09\u56DB\u4E94\u516D\u4E03\u516B\u4E5D\u5341\u767E\u5343\u3007\u96F6\u25CB\uFF2F]+(?:[-\u2013\uFF0D\u4E4B][0-9\uFF10-\uFF19\u4E00\u4E8C\u4E09\u56DB\u4E94\u516D\u4E03\u516B\u4E5D\u5341\u767E\u5343\u3007\u96F6\u25CB\uFF2F]+)?)+)\u865F'
)

RE_NUMERIC_MARKER = re.compile(r'([0-9\uFF10-\uFF19\u4E00\u4E8C\u4E09\u56DB\u4E94\u516D\u4E03\u516B\u4E5D\u5341\u767E\u5343\u3007\u96F6\u25CB\uFF2F]+(?:[-\u2013\uFF0D\u4E4B][0-9\uFF10-\uFF19\u4E00\u4E8C\u4E09\u56DB\u4E94\u516D\u4E03\u516B\u4E5D\u5341\u767E\u5343\u3007\u96F6\u25CB\uFF2F]+)?)(?=(\u6BB5|\u5DF7|\u5F04|\u865F))')
FW_MAP = str.maketrans('0123456789', '\uFF10\uFF11\uFF12\uFF13\uFF14\uFF15\uFF16\uFF17\uFF18\uFF19')
FW_TO_ASCII = str.maketrans('\uFF10\uFF11\uFF12\uFF13\uFF14\uFF15\uFF16\uFF17\uFF18\uFF19\uFF2F\u25CB', '012345678900')
CN_DIGITS = {'\u96F6': 0, '\u3007': 0, '\u4E00': 1, '\u4E8C': 2, '\u4E09': 3, '\u56DB': 4, '\u4E94': 5, '\u516D': 6, '\u4E03': 7, '\u516B': 8, '\u4E5D': 9}


def to_fullwidth_digits(s: str) -> str:
    return s.translate(FW_MAP)


def digits(v):
    if v is None:
        return None
    m = RE_DIGITS.search(str(v).translate(FW_TO_ASCII))
    return m.group(0) if m else None


def cn_to_int(token: str) -> int | None:
    token = (token or '').strip()
    if not token:
        return None
    token = token.translate(FW_TO_ASCII)
    if token.isdigit():
        return int(token)
    if all(ch in CN_DIGITS for ch in token):
        return int(''.join(str(CN_DIGITS[ch]) for ch in token))
    total = 0
    current = 0
    unit_seen = False
    for ch in token:
        if ch in CN_DIGITS:
            current = CN_DIGITS[ch]
        elif ch == '\u5341':
            unit_seen = True
            total += (current or 1) * 10
            current = 0
        elif ch == '\u767E':
            unit_seen = True
            total += (current or 1) * 100
            current = 0
        elif ch == '\u5343':
            unit_seen = True
            total += (current or 1) * 1000
            current = 0
        else:
            return None
    total += current
    if total == 0 and not unit_seen:
        return None
    return total


def normalize_number_token(token: str) -> str:
    token = (token or '').strip().translate(FW_TO_ASCII)
    if not token:
        return token

    pieces = re.split(r'([-\u2013\uFF0D\u4E4B])', token)
    normalized = []
    for piece in pieces:
        if not piece:
            continue
        if piece in {'-', '\u2013', '\uFF0D'}:
            normalized.append('-')
            continue
        if piece == '\u4E4B':
            normalized.append('\u4E4B')
            continue
        value = cn_to_int(piece)
        normalized.append(str(value) if value is not None else piece)
    return ''.join(normalized)


def normalize_numeric_markers(addr: str) -> str:
    if not addr:
        return addr
    addr = addr.translate(FW_TO_ASCII)

    def repl(m):
        token = m.group(1)
        return normalize_number_token(token)

    return RE_NUMERIC_MARKER.sub(repl, addr)



RE_DISTRICT_PREFIX = re.compile(r'^(?:\u53f0\u5357\u5e02|\u81fa\u5357\u5e02)?(?P<district>[^\u8def\u8857\u5927\u9053\u5df7\u5f04\u6bb5]{1,8}?(?:\u5340|\u5e02|\u93ae|\u9109))(?P<rest>.*)$')
RE_FLOOR_INFO = re.compile(r'[0-9\uFF10-\uFF19\u4E00\u4E8C\u4E09\u56DB\u4E94\u516D\u4E03\u516B\u4E5D\u5341\u767E\u5343\u3007\u96F6\u25CB\uFF2F]+\u6A13')
RE_DUP_CITY_AFTER_DISTRICT = re.compile(r'^(?P<city>\u53f0\u5357\u5e02|\u81fa\u5357\u5e02)(?P<district>[^\u8def\u8857\u5927\u9053\u5df7\u5f04\u6bb5]{1,8}?(?:\u5340|\u5e02|\u93ae|\u9109))(?P<dup>\u53f0\u5357\u5e02|\u81fa\u5357\u5e02)(?P<rest>.*)$')


def strip_noise_tokens(addr: str) -> str:
    addr = (addr or '').strip()
    if not addr:
        return addr
    addr = RE_FLOOR_INFO.sub('', addr)
    addr = addr.replace('\u53ca', '\u3001')
    addr = re.sub(r'\u3001+', '\u3001', addr)
    m = RE_DUP_CITY_AFTER_DISTRICT.match(addr)
    if m:
        addr = f"{m.group('city')}{m.group('district')}{m.group('rest')}"
    return addr


RE_VILLAGE_PREFIX = re.compile(r'^(?:(?:[^\u8def\u8857\u5927\u9053\u5df7\u5f04\u6bb5]{1,8}(?:\u91cc|\u6751))(?:[0-9\uFF10-\uFF19\u4E00\u4E8C\u4E09\u56DB\u4E94\u516D\u4E03\u516B\u4E5D\u5341\u767E\u5343\u3007\u96F6\u25CB\uFF2F]+\u9130)?)')


def strip_village_prefix(addr: str) -> str:
    addr = (addr or '').strip()
    if not addr:
        return addr
    m = RE_DISTRICT_PREFIX.match(addr)
    if not m:
        return addr
    district = m.group('district')
    rest = (m.group('rest') or '').strip()
    rest2 = RE_VILLAGE_PREFIX.sub('', rest).strip()
    if rest2 and rest2 != rest:
        prefix = '\u53f0\u5357\u5e02' if addr.startswith('\u53f0\u5357\u5e02') else ('\u81fa\u5357\u5e02' if addr.startswith('\u81fa\u5357\u5e02') else '')
        return f"{prefix}{district}{rest2}"
    return addr


def address_variants(addr_raw: str) -> list[str]:
    base = strip_noise_tokens((addr_raw or '').strip())
    if not base:
        return []
    variants = []

    def add(v: str | None):
        v = (v or '').strip()
        if v and v not in variants:
            variants.append(v)

    stripped = strip_village_prefix(base)
    normalized = normalize_numeric_markers(base)
    stripped_normalized = normalize_numeric_markers(stripped)
    add(base)
    add(stripped)
    add(normalized)
    add(stripped_normalized)

    base_variants = list(variants)
    for variant in base_variants:
        m = RE_MULTI_HOUSE_NO.search(variant)
        if not m:
            continue
        prefix = m.group('prefix')
        suffix = variant[m.end():]
        parts = re.split(r'[\u3001,\uFF0C]', m.group('num_seq'))
        for part in parts:
            num = normalize_number_token(part)
            add(f"{prefix}{num}\u865F{suffix}")

    return variants


def is_tainan(addr_raw: str) -> bool:
    return addr_raw.startswith('\u53f0\u5357\u5e02') or addr_raw.startswith('\u81fa\u5357\u5e02')


def get_conn():
    dsn = os.getenv('DATABASE_URL') or os.getenv('DB_DSN')
    return psycopg.connect(dsn)


def find_point_by_apb_key(cur, road, lane, alley, no):
    if not road or not no:
        return None

    clauses = ['road = %s', 'number_clean = %s', 'geom IS NOT NULL']
    params = [road, no]

    lane_d = digits(lane)
    alley_d = digits(alley)
    if lane_d:
        clauses.append('lane = %s')
        params.append(f"{to_fullwidth_digits(lane_d)}\u5DF7")
    if alley_d:
        clauses.append('alley = %s')
        params.append(f"{to_fullwidth_digits(alley_d)}\u5F04")

    sql = f"""
    SELECT ST_X(geom) AS lon, ST_Y(geom) AS lat
    FROM address_points_base
    WHERE {' AND '.join(clauses)}
    LIMIT 1
    """
    cur.execute(sql, params)
    row = cur.fetchone()
    if not row:
        return None
    return float(row[0]), float(row[1]), 'ADDRESS_POINT_BASE', 'ROAD_KEY'


def find_point_by_apb_trgm(cur, address_norm: str):
    sql = """
    SELECT ST_X(geom) AS lon, ST_Y(geom) AS lat, similarity(address_norm, %s) AS sim
    FROM address_points_base
    WHERE geom IS NOT NULL
      AND address_norm %% %s
    ORDER BY sim DESC
    LIMIT 1
    """
    cur.execute(sql, (address_norm, address_norm))
    row = cur.fetchone()
    if not row:
        return None
    lon, lat, sim = float(row[0]), float(row[1]), float(row[2])
    if sim < 0.60:
        return None
    return lon, lat, 'ADDRESS_POINT_BASE', f'TRGM_{sim:.3f}'


def upsert_poi(cur, rec, normalized_addr, district, lon, lat, center_source, match_level):
    ewkt = None
    if lon is not None and lat is not None:
        ewkt = f'SRID=4326;POINT({lon} {lat})'

    company_id = (rec.get(COL_COMPANY_ID) or '').strip() or None
    company_name = (rec.get(COL_COMPANY_NAME) or '').strip() or None
    branch_id = (rec.get(COL_BRANCH_ID) or '').strip()
    branch_name = (rec.get(COL_BRANCH_NAME) or '').strip() or None
    branch_addr = (rec.get(COL_BRANCH_ADDR) or '').strip() or None
    branch_status = (rec.get(COL_BRANCH_STATUS) or '').strip() or None
    established = (rec.get(COL_ESTABLISHED) or '').strip() or None
    updated = (rec.get(COL_UPDATED) or '').strip() or None

    source_id = branch_id or f"{company_id or ''}|{branch_name or ''}|{branch_addr or ''}"
    sql = """
    INSERT INTO poi (
      source_table, source_id, category, subtype, name, district,
      addr_raw, address_norm, lon, lat, geom, center_source, match_level, extra,
      created_at, updated_at
    )
    VALUES (
      %s, %s, %s, %s, %s, %s,
      %s, %s, %s, %s, ST_GeomFromEWKT(%s::text), %s, %s, %s,
      now(), now()
    )
    ON CONFLICT (source_table, source_id) DO UPDATE SET
      category = EXCLUDED.category,
      subtype = EXCLUDED.subtype,
      name = EXCLUDED.name,
      district = EXCLUDED.district,
      addr_raw = EXCLUDED.addr_raw,
      address_norm = EXCLUDED.address_norm,
      lon = EXCLUDED.lon,
      lat = EXCLUDED.lat,
      geom = EXCLUDED.geom,
      center_source = EXCLUDED.center_source,
      match_level = EXCLUDED.match_level,
      extra = EXCLUDED.extra,
      updated_at = now()
    """
    extra = {
        'company_id': company_id,
        'company_name': company_name,
        'branch_id': branch_id,
        'branch_status': branch_status,
        'established_date': established,
        'updated_date': updated,
    }
    cur.execute(
        sql,
        (
            SOURCE_TABLE,
            source_id,
            CATEGORY,
            company_name,
            branch_name,
            district,
            branch_addr,
            normalized_addr,
            lon,
            lat,
            ewkt,
            center_source,
            match_level,
            Json(extra),
        ),
    )


def main(csv_path: str):
    p = Path(csv_path)
    with p.open('r', encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))

    total = len(rows)
    kept = 0
    located = 0

    with get_conn() as conn:
        with conn.cursor() as cur:
            for rec in rows:
                addr_raw = (rec.get(COL_BRANCH_ADDR) or '').strip()
                status = (rec.get(COL_BRANCH_STATUS) or '').strip()
                if not addr_raw or status != ACTIVE_STATUS or not is_tainan(addr_raw):
                    continue

                kept += 1
                variants = address_variants(addr_raw)
                address_norm = normalize_address(variants[0]) if variants else normalize_address(addr_raw)
                district = None
                point = None

                for idx, variant in enumerate(variants or [addr_raw]):
                    parsed = parse(variant)
                    district = district or parsed.district
                    point = find_point_by_apb_key(cur, parsed.road, parsed.lane, parsed.alley, parsed.no)
                    if point is not None:
                        lon, lat, center_source, match_level = point
                        if idx > 0:
                            match_level = f"{match_level}_VARIANT"
                        break

                if point is None:
                    for idx, variant in enumerate(variants or [addr_raw]):
                        norm_variant = normalize_address(variant)
                        point = find_point_by_apb_trgm(cur, norm_variant)
                        if point is not None:
                            lon, lat, center_source, match_level = point
                            address_norm = norm_variant
                            if idx > 0:
                                match_level = f"{match_level}_VARIANT"
                            break

                if point is not None:
                    located += 1
                else:
                    lon = lat = None
                    center_source = 'NONE'
                    match_level = 'NONE'

                upsert_poi(cur, rec, address_norm, district, lon, lat, center_source, match_level)

        conn.commit()

    print(f'[CSV] total={total}')
    print(f'[FILTER] kept_tainan_active={kept}')
    print(f'[GEO] located={located}')
    print('[DONE]')


if __name__ == '__main__':
    csv_arg = sys.argv[1] if len(sys.argv) > 1 else '/tmp/convenience.csv'
    main(csv_arg)
