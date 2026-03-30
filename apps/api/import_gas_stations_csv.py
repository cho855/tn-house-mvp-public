import csv
import os
import re
import sys
from pathlib import Path

import psycopg
from psycopg.types.json import Json

from address import normalize_address, parse

SOURCE_TABLE = 'gas_station_csv'
CATEGORY = 'gas_station'

COL_NAME = '\u52a0\u6cb9\u7ad9\u540d\u7a31'
COL_BRAND = '\u54c1\u724c'
COL_ADDR = '\u5730\u5740'
COL_CITY = '\u7e23\u5e02'
COL_DISTRICT = '\u9109\u93ae\u5e02\u5340'
COL_PHONE = '\u96fb\u8a71'
COL_SEQ = '\u9805'

RE_DIGITS = re.compile(r'\d+')
RE_HOUSE_SUBNO = re.compile(r'(?P<main>\d+)-(?P<sub>\d+)\u865f')


def to_fullwidth_digits(s: str) -> str:
    return ''.join(chr(ord(ch) + 65248) if '0' <= ch <= '9' else ch for ch in s)


def digits(v):
    if v is None:
        return None
    m = RE_DIGITS.search(str(v))
    return m.group(0) if m else None


def is_tainan(city: str, addr_raw: str) -> bool:
    city = (city or '').strip()
    addr_raw = (addr_raw or '').strip()
    return city in ('\u53f0\u5357\u5e02', '\u81fa\u5357\u5e02') or addr_raw.startswith('\u53f0\u5357\u5e02') or addr_raw.startswith('\u81fa\u5357\u5e02')


def address_variants(addr_raw: str) -> list[str]:
    base = (addr_raw or '').strip()
    if not base:
        return []
    variants = [base]
    if RE_HOUSE_SUBNO.search(base):
        variants.append(RE_HOUSE_SUBNO.sub(lambda m: f"{m.group('main')}\u865f\u4e4b{m.group('sub')}", base))
        variants.append(RE_HOUSE_SUBNO.sub(lambda m: f"{m.group('main')}-{m.group('sub')}\u865f", base))
    seen = set()
    out = []
    for v in variants:
        norm = normalize_address(v)
        if norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out


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
        params.append(f"{to_fullwidth_digits(lane_d)}\u5df7")
    if alley_d:
        clauses.append('alley = %s')
        params.append(f"{to_fullwidth_digits(alley_d)}\u5f04")

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


def find_point_by_ap_exact(cur, address_norm: str):
    sql = """
    SELECT ST_X(geom) AS lon, ST_Y(geom) AS lat
    FROM address_points
    WHERE geom IS NOT NULL
      AND address_norm = %s
    LIMIT 1
    """
    cur.execute(sql, (address_norm,))
    row = cur.fetchone()
    if not row:
        return None
    return float(row[0]), float(row[1]), 'ADDRESS_POINTS', 'ADDRESS_NORM_EXACT'


def find_point_by_ap_trgm(cur, address_norm: str):
    sql = """
    SELECT ST_X(geom) AS lon, ST_Y(geom) AS lat, similarity(address_norm, %s) AS sim
    FROM address_points
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
    if sim < 0.65:
        return None
    return lon, lat, 'ADDRESS_POINTS', f'TRGM_{sim:.3f}'


def find_best_point(cur, addr_raw: str):
    for norm in address_variants(addr_raw):
        parsed = parse(norm)
        point = find_point_by_apb_key(cur, parsed.road, parsed.lane, parsed.alley, parsed.no)
        if point is not None:
            return point, norm, parsed
    for norm in address_variants(addr_raw):
        point = find_point_by_ap_exact(cur, norm)
        if point is not None:
            return point, norm, parse(norm)
    for norm in address_variants(addr_raw):
        point = find_point_by_apb_trgm(cur, norm)
        if point is not None:
            return point, norm, parse(norm)
    for norm in address_variants(addr_raw):
        point = find_point_by_ap_trgm(cur, norm)
        if point is not None:
            return point, norm, parse(norm)
    return None, normalize_address(addr_raw), parse(addr_raw)


def upsert_poi(cur, rec, normalized_addr, district, lon, lat, center_source, match_level):
    ewkt = None
    if lon is not None and lat is not None:
        ewkt = f'SRID=4326;POINT({lon} {lat})'

    seq = (rec.get(COL_SEQ) or '').strip()
    brand = (rec.get(COL_BRAND) or '').strip() or None
    name = (rec.get(COL_NAME) or '').strip() or None
    addr_raw = (rec.get(COL_ADDR) or '').strip() or None
    phone = (rec.get(COL_PHONE) or '').strip() or None

    source_id = seq or f"{brand or ''}|{name or ''}|{addr_raw or ''}"
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
        'brand': brand,
        'phone': phone,
        'seq': seq,
    }
    cur.execute(
        sql,
        (
            SOURCE_TABLE,
            source_id,
            CATEGORY,
            brand,
            name,
            district,
            addr_raw,
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
                addr_raw = (rec.get(COL_ADDR) or '').strip()
                city = (rec.get(COL_CITY) or '').strip()
                if not addr_raw or not is_tainan(city, addr_raw):
                    continue

                kept += 1
                point, normalized_addr, parsed = find_best_point(cur, addr_raw)
                district = parsed.district or (rec.get(COL_DISTRICT) or '').strip() or None

                if point is not None:
                    lon, lat, center_source, match_level = point
                    located += 1
                else:
                    lon = lat = None
                    center_source = 'NONE'
                    match_level = 'NONE'

                upsert_poi(cur, rec, normalized_addr, district, lon, lat, center_source, match_level)

        conn.commit()

    print(f'[CSV] total={total}')
    print(f'[FILTER] kept_tainan={kept}')
    print(f'[GEO] located={located}')
    print('[DONE]')


if __name__ == '__main__':
    csv_arg = sys.argv[1] if len(sys.argv) > 1 else '/data/\u52a0\u6cb9\u7ad9/\u81fa\u5357\u5e02\u52a0\u6cb9\u7ad9\u4f4d\u7f6e.csv'
    main(csv_arg)
