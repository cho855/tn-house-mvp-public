import csv
import os
import re
from functools import lru_cache
from pathlib import Path

import psycopg

from address import normalize_address, parse

INTERSECTION_SEPARATORS = ('\u8207', '\u53ca', '\u3001', '/', 'VS', 'vs')
INTERSECTION_HINTS = ('\u8207', '\u53ca', '\u3001', '\u548c', '/', 'VS', 'vs', '\u8def\u53e3', '\u8857\u53e3', '\u5df7\u53e3', '\u4ea4\u53c9\u8def\u53e3', '\u4ea4\u53c9\u53e3', '\u4ea4\u63a5\u53e3', '\u4ea4\u6703\u8655', '\u4ea4\u53c9')
ROAD_TOKEN_RE = re.compile(r'^(?P<left>.+?(?:\u8def|\u8857|\u5927\u9053|\u7e23\u9053)(?:[\u4e00-\u9fff\d]+\u6bb5)?)(?P<right>.+)$')
SINGLE_LANE_MOUTH_RE = re.compile(r'^\s*(.+?(?:\u8def|\u8857|\u5927\u9053))\s*(\d+)\s*\u5df7\u53e3\s*$')
NEARBY_SUFFIX_RE = re.compile(r'(\u9644\u8fd1|\u8def\u9762|\u659c\u5c0d\u9762|\u5c0d\u9762|\u9694\u58c1|\u65c1)$')
NEARBY_TRIM_RE = re.compile(r'(?:\u9644\u8fd1.*|\u65c1(?:\u5de5\u5730|\u57fa\u5730)?|\u6b63\u5c0d\u9762|\u659c\u5c0d\u9762|\u5c0d\u9762|\u9694\u58c1|\u8def\u9762)$')
NEARBY_ROADISH_RE = re.compile(r'^(?P<road>.+?(?:\u8def|\u8857|\u5927\u9053))(?:\u4e0a)?(?P<extra>[^\d\u5df7\u5f04\u865f-]*)?(?:(?P<lane>\d+)\u5df7)?(?:(?P<alley>\d+)\u5f04)?(?:(?P<no>\d+)(?:[-\u4e4b](?P<subno>\d+))?\u865f?)?$')
LEADING_JOINERS_RE = re.compile(r'^[\s\u8207\u53ca\u3001/]+')
LANDLOT_RE = re.compile(r'(?P<section>[^\d\uff0c\u3001,\s]+\u6bb5)(?:\u5730\u865f)?(?P<lot>\d+(?:-\d+){0,3})')
LOT_TOKEN_RE = re.compile(r'(?:^|[\s\uff0c\u3001,])(?P<lot>\d+(?:[-\u4e4b]\d+)*)')
LANDLOT_TAIL_CUTOFF_RE = re.compile(r'(?:\u7b49|\u5171)\s*\d+\s*\u7b46')
FW_DIGITS = str.maketrans('0123456789', '\uff10\uff11\uff12\uff13\uff14\uff15\uff16\uff17\uff18\uff19')
SEGMENT_DIGITS = str.maketrans({
    '\u4e00': '1',
    '\u4e8c': '2',
    '\u4e09': '3',
    '\u56db': '4',
    '\u4e94': '5',
    '\u516d': '6',
    '\u4e03': '7',
    '\u516b': '8',
    '\u4e5d': '9',
})
LANDLOT_DATA_DIR_CANDIDATES = (
    Path('/data/114\u5e74\u5ea6\u81fa\u5357\u5e02\u5b97\u5730\u5730\u865f\u5c6c\u6027\u8cc7\u6599'),
    Path('/home/cindy/whproject/tn-house-mvp/data/114\u5e74\u5ea6\u81fa\u5357\u5e02\u5b97\u5730\u5730\u865f\u5c6c\u6027\u8cc7\u6599'),
)


def get_conn():
    dsn = os.getenv('DATABASE_URL') or os.getenv('DB_DSN')
    return psycopg.connect(dsn)


def to_fullwidth_digits(s: str) -> str:
    return s.translate(FW_DIGITS)


def extract_nearby_roadish_parts(raw: str):
    cleaned = NEARBY_TRIM_RE.sub('', (raw or '').strip()).strip()
    if not cleaned:
        return None

    m = NEARBY_ROADISH_RE.match(cleaned)
    if not m:
        return None

    road = (m.group('road') or '').strip() or None
    lane = normalize_lane_alley(m.group('lane'), '\u5df7')
    alley = normalize_lane_alley(m.group('alley'), '\u5f04')
    no = (m.group('no') or '').strip() or None
    return cleaned, road, lane, alley, no


def clean_landlot_section(raw: str, district: str | None = None):
    s = (raw or '').strip()
    for prefix in ('\u53f0\u5357\u5e02', district or ''):
        if prefix and s.startswith(prefix):
            s = s[len(prefix):]

    for marker in ('\u865f\u5c0d\u9762', '\u865f\u65c1', '\u865f\u9694\u58c1', '\u865f\u9644\u8fd1', '\u5c0d\u9762', '\u65c1', '\u9694\u58c1', '\u9644\u8fd1'):
        if marker in s:
            s = s.split(marker)[-1]

    candidates = re.findall(r'[^\d\uff0c\u3001,\s]+(?:\u5c0f\u6bb5)?\u6bb5', s)
    if not candidates:
        return s

    for cand in reversed(candidates):
        if not any(tok in cand for tok in ('\u8def', '\u8857', '\u5df7', '\u5f04', '\u5927\u9053', '\u7e23\u9053')):
            return cand
        for tok in ('\u8def', '\u8857', '\u5df7', '\u5f04'):
            if tok in cand:
                tail = cand.split(tok)[-1]
                if tail.endswith('\u6bb5'):
                    return tail

    return candidates[-1]


def is_suspicious_landlot_section(section: str | None):
    if not section:
        return True
    return section in {'\u4e00\u6bb5', '\u4e8c\u6bb5', '\u4e09\u6bb5', '\u56db\u6bb5', '\u4e94\u6bb5', '\u516d\u6bb5', '\u4e03\u6bb5', '\u516b\u6bb5', '\u4e5d\u6bb5', '1\u6bb5', '2\u6bb5', '3\u6bb5', '4\u6bb5', '5\u6bb5', '6\u6bb5', '7\u6bb5', '8\u6bb5', '9\u6bb5'}


def normalize_lot_number(raw: str) -> str:
    raw = (raw or '').strip()
    if not raw:
        return raw

    normalized = []
    for i, part in enumerate(raw.split('-')):
        part = part.strip()
        if not part:
            continue
        if part.isdigit():
            value = str(int(part))
            if i == 0:
                normalized.append(value)
            elif value != '0':
                normalized.append(value)
        else:
            normalized.append(part)
    return '-'.join(normalized)



def extract_landlot_lots(raw: str):
    s = (raw or '').strip()
    if not s or '段' not in s or '地號' not in s:
        return []

    tail = s.split('段', 1)[-1]
    cutoff = LANDLOT_TAIL_CUTOFF_RE.search(tail)
    if cutoff:
        tail = tail[:cutoff.start()]
    if '地號' in tail:
        tail = tail.split('地號', 1)[0]
    tail = tail.replace('之', '-')

    lots = []
    for m in LOT_TOKEN_RE.finditer(tail):
        token = normalize_lot_number(m.group('lot'))
        if token and token not in lots:
            lots.append(token)
    return lots


def find_landlot_data_dir() -> Path | None:
    for path in LANDLOT_DATA_DIR_CANDIDATES:
        if path.exists():
            return path
    return None


@lru_cache(maxsize=1)
def load_landlot_index():
    data_dir = find_landlot_data_dir()
    if data_dir is None:
        return {}

    index = {}
    for csv_path in sorted(data_dir.glob('*.csv')):
        with csv_path.open('r', encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                district = (row.get('\u5340\u540d') or '').strip()
                section = (row.get('\u5730\u6bb5\u540d') or '').strip()
                lot = normalize_lot_number(row.get('\u5730\u865f') or '')
                lon = (row.get('\u7d93\u5ea6') or '').strip()
                lat = (row.get('\u7def\u5ea6') or '').strip()
                if not district or not section or not lot or not lon or not lat:
                    continue
                index[(district, section, lot)] = (float(lon), float(lat))
    return index



@lru_cache(maxsize=1)
def load_landlot_section_centroids():
    section_points = {}
    for (district, section, _lot), (lon, lat) in load_landlot_index().items():
        section_points.setdefault((district, section), []).append((lon, lat))

    centroids = {}
    for key, points in section_points.items():
        if not points:
            continue
        lon = sum(p[0] for p in points) / len(points)
        lat = sum(p[1] for p in points) / len(points)
        centroids[key] = (lon, lat)
    return centroids


def road_variants(road: str):
    variants = []
    if road:
        variants.append(road)

    m = re.match(r'^(.*(?:\u8def|\u8857|\u5927\u9053))([\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d])\u6bb5$', road or '')
    if m:
        alt = f"{m.group(1)}{m.group(2).translate(SEGMENT_DIGITS)}\u6bb5"
        if alt not in variants:
            variants.append(alt)

    return variants


def find_landlot_point(district: str, road_raw: str):
    if not district or not road_raw:
        return None

    raw = road_raw.strip()
    m = LANDLOT_RE.search(raw)
    if not m:
        return None

    section = clean_landlot_section(m.group('section'), district)
    if is_suspicious_landlot_section(section):
        return None

    candidate_lots = []
    primary_lot = normalize_lot_number(m.group('lot'))
    if primary_lot:
        candidate_lots.append(primary_lot)
    for lot in extract_landlot_lots(raw):
        if lot not in candidate_lots:
            candidate_lots.append(lot)

    index = load_landlot_index()
    for lot in candidate_lots:
        point = index.get((district, section, lot))
        if not point:
            continue
        lon, lat = point
        return lon, lat, f'LANDLOT_CENTROID_{section}_{lot}'
    return None


def find_landlot_section_point(district: str, road_raw: str):
    if not district or not road_raw:
        return None

    road = (road_raw or '').strip()
    if not road:
        return None
    if '地號' in road:
        return None

    section = clean_landlot_section(road, district)
    if is_suspicious_landlot_section(section):
        return None
    if not section.endswith('段'):
        return None

    point = load_landlot_section_centroids().get((district, section))
    if not point:
        return None

    lon, lat = point
    return lon, lat, f'SECTION_CENTROID_{section}'


def road_centroid(cur, road: str):
    for candidate in road_variants(road):
        cur.execute(
            """
            SELECT
              ST_X(ST_Centroid(ST_Collect(geom))) AS lon,
              ST_Y(ST_Centroid(ST_Collect(geom))) AS lat
            FROM address_points_base
            WHERE geom IS NOT NULL AND road = %s
            """,
            (candidate,),
        )
        row = cur.fetchone()
        if row and row[0] is not None and row[1] is not None:
            return float(row[0]), float(row[1]), candidate
    return None


def lane_alley_centroid(cur, road: str, lane: str | None = None, alley: str | None = None):
    clauses = ['geom IS NOT NULL', 'road = %s']
    params = [road]
    label_parts = [road]

    if lane:
        clauses.append('lane = %s')
        params.append(lane)
        label_parts.append(lane)
    if alley:
        clauses.append('alley = %s')
        params.append(alley)
        label_parts.append(alley)

    cur.execute(
        f"""
        SELECT
          ST_X(ST_Centroid(ST_Collect(geom))) AS lon,
          ST_Y(ST_Centroid(ST_Collect(geom))) AS lat
        FROM address_points_base
        WHERE {' AND '.join(clauses)}
        """,
        params,
    )
    row = cur.fetchone()
    if not row or row[0] is None or row[1] is None:
        return None
    return float(row[0]), float(row[1]), '_'.join(label_parts)


def clean_intersection_piece(piece: str):
    s = LEADING_JOINERS_RE.sub('', piece.strip())
    for suffix in ('\u4ea4\u53c9\u8def\u53e3', '\u4ea4\u53c9\u53e3', '\u4ea4\u63a5\u53e3', '\u4ea4\u6703\u8655', '\u4ea4\u53c9'):
        if s.endswith(suffix):
            s = s[:-len(suffix)].strip()
            break

    for suffix, token in (('\u8def\u53e3', '\u8def'), ('\u8857\u53e3', '\u8857'), ('\u5df7\u53e3', '\u5df7')):
        if s.endswith(suffix):
            stem = s[:-len(suffix)].strip()
            if any(mark in stem for mark in ('\u8def', '\u8857', '\u5df7', '\u5927\u9053', '\u7e23\u9053')):
                s = stem
            else:
                s = f'{stem}{token}'
            break

    return LEADING_JOINERS_RE.sub('', s.strip())


def normalize_lane_alley(raw: str | None, suffix: str):
    if not raw:
        return None
    digits = ''.join(ch for ch in raw if ch.isdigit())
    if not digits:
        return None
    return f'{to_fullwidth_digits(digits)}{suffix}'


def resolve_intersection_side_point(cur, district: str, side_raw: str, fallback_road: str | None = None):
    side = clean_intersection_piece(side_raw)
    parsed = parse(normalize_address(f'\u53f0\u5357\u5e02{district}{side}'))

    road = parsed.road
    lane = normalize_lane_alley(parsed.lane, '\u5df7')
    alley = normalize_lane_alley(parsed.alley, '\u5f04')

    if road is None and fallback_road and re.fullmatch(r'\d+\u5df7(?:\d+\u5f04)?', side):
        road = fallback_road
        lane_m = re.search(r'(\d+)\u5df7', side)
        alley_m = re.search(r'(\d+)\u5f04', side)
        lane = f'{to_fullwidth_digits(lane_m.group(1))}\u5df7' if lane_m else lane
        alley = f'{to_fullwidth_digits(alley_m.group(1))}\u5f04' if alley_m else alley

    if road is None:
        m = re.match(r'^(?P<road>.+?(?:\u8def|\u8857|\u5927\u9053|\u7e23\u9053)(?:[\u4e00-\u9fff\d]+\u6bb5)?)(?P<rest>.*)$', side)
        if m:
            road = m.group('road').strip()
            rest = m.group('rest').strip()
            lane_m = re.search(r'(\d+)\u5df7', rest)
            alley_m = re.search(r'(\d+)\u5f04', rest)
            lane = f'{to_fullwidth_digits(lane_m.group(1))}\u5df7' if lane_m else lane
            alley = f'{to_fullwidth_digits(alley_m.group(1))}\u5f04' if alley_m else alley

    if road is None:
        road = side

    if lane and alley:
        point = lane_alley_centroid(cur, road, lane, alley)
        if point:
            return point

    if lane:
        point = lane_alley_centroid(cur, road, lane, None)
        if point:
            return point

    return road_centroid(cur, road)


def split_intersection_roads(road_raw: str):
    if not road_raw:
        return None

    road = road_raw.strip()
    if road.endswith('\u65c1'):
        road = road[:-1]

    for sep in INTERSECTION_SEPARATORS:
        if sep in road:
            left, right = road.split(sep, 1)
            left = clean_intersection_piece(left)
            right = clean_intersection_piece(right)
            if left and right:
                return left, right

    m = ROAD_TOKEN_RE.match(road)
    if not m:
        return None
    left = clean_intersection_piece(m.group('left'))
    right = clean_intersection_piece(m.group('right'))
    if left and right:
        return left, right
    return None


def find_intersection_point(cur, district: str, road_raw: str):
    if not district or not road_raw or not any(h in road_raw for h in INTERSECTION_HINTS):
        return None

    roads = split_intersection_roads(road_raw)
    if not roads:
        return None

    road_a_raw, road_b_raw = roads
    parsed_left = parse(normalize_address(f'\u53f0\u5357\u5e02{district}{road_a_raw}'))
    fallback_left_road = parsed_left.road or clean_intersection_piece(road_a_raw)

    pt_a = resolve_intersection_side_point(cur, district, road_a_raw)
    pt_b = resolve_intersection_side_point(cur, district, road_b_raw, fallback_left_road)
    if not pt_a or not pt_b:
        return None

    lon = (pt_a[0] + pt_b[0]) / 2.0
    lat = (pt_a[1] + pt_b[1]) / 2.0
    return lon, lat, f'INTERSECTION_MIDPOINT_{pt_a[2]}_{pt_b[2]}'


def find_single_road_mouth_point(cur, district: str, road_raw: str):
    if not district or not road_raw:
        return None

    m = SINGLE_LANE_MOUTH_RE.match(road_raw.strip())
    if not m:
        return None

    road_raw_part, lane_digits = m.group(1).strip(), m.group(2).strip()
    road = parse(normalize_address(f'\u53f0\u5357\u5e02{district}{road_raw_part}')).road or road_raw_part
    lane = f'{to_fullwidth_digits(lane_digits)}\u5df7'

    point = lane_alley_centroid(cur, road, lane, None)
    if not point:
        return None
    lon, lat, _ = point
    return float(lon), float(lat), f'SINGLE_LANE_MOUTH_{road}_{lane_digits}'


def find_cleaned_nearby_point(cur, district: str, road_raw: str):
    if not district or not road_raw:
        return None

    parts = extract_nearby_roadish_parts(road_raw)
    if not parts:
        return None

    cleaned, road, lane, alley, no = parts
    if not road:
        parsed = parse(normalize_address(f'\u53f0\u5357\u5e02{district}{cleaned}'))
        road = parsed.road
        if not road:
            return None

    if no:
        clauses = ['road = %s', 'number_clean = %s', 'geom IS NOT NULL']
        params = [road, no]
        if lane:
            clauses.append('lane = %s')
            params.append(lane)
        if alley:
            clauses.append('alley = %s')
            params.append(alley)
        cur.execute(
            f"""
            SELECT ST_X(geom) AS lon, ST_Y(geom) AS lat
            FROM address_points_base
            WHERE {' AND '.join(clauses)}
            LIMIT 1
            """,
            params,
        )
        row = cur.fetchone()
        if row and row[0] is not None and row[1] is not None:
            return float(row[0]), float(row[1]), f'CLEANED_NEARBY_ADDR_{road}'

    if lane and alley:
        point = lane_alley_centroid(cur, road, lane, alley)
        if point:
            lon, lat, label = point
            return lon, lat, f'CLEANED_NEARBY_LANE_{label}'

    if lane:
        point = lane_alley_centroid(cur, road, lane, None)
        if point:
            lon, lat, label = point
            return lon, lat, f'CLEANED_NEARBY_LANE_{label}'

    pt = road_centroid(cur, road)
    if pt:
        return pt[0], pt[1], f'CLEANED_NEARBY_ROAD_{pt[2]}'
    return None


def find_road_lane_point(cur, district: str, road_raw: str):
    if not district or not road_raw:
        return None

    raw = road_raw.strip()
    if not raw:
        return None
    if any(h in raw for h in INTERSECTION_HINTS):
        return None
    if any(h in raw for h in ('\u5730\u865f', '\u9644\u8fd1', '\u8def\u9762', '\u659c\u5c0d\u9762', '\u5c0d\u9762', '\u9694\u58c1', '\u65c1', '\u865f')):
        return None

    canon = normalize_address(f'\u53f0\u5357\u5e02{district}{raw}')
    if canon.startswith('\u53f0\u5357\u5e02'):
        canon = canon[3:]
    if canon.startswith(district):
        canon = canon[len(district):]

    m_road = re.match(r'^(?P<road>.+?(?:\u8def|\u8857|\u5927\u9053)(?:\d+\u6bb5)?)(?P<rest>.*)$', canon)
    if not m_road:
        return None

    road = m_road.group('road').strip()
    rest = m_road.group('rest').strip()
    if not road:
        return None

    m_lane = re.search(r'(\d+)\u5df7', rest)
    m_alley = re.search(r'(\d+)\u5f04', rest)
    lane = f'{to_fullwidth_digits(m_lane.group(1))}\u5df7' if m_lane else None
    alley = f'{to_fullwidth_digits(m_alley.group(1))}\u5f04' if m_alley else None

    if lane and alley:
        point = lane_alley_centroid(cur, road, lane, alley)
        if point:
            lon, lat, label = point
            return lon, lat, f'ROAD_LANE_ALLEY_CENTROID_{label}'

    if lane:
        point = lane_alley_centroid(cur, road, lane, None)
        if point:
            lon, lat, label = point
            return lon, lat, f'ROAD_LANE_CENTROID_{label}'

    pt = road_centroid(cur, road)
    if pt:
        return pt[0], pt[1], f'ROAD_ONLY_CENTROID_{pt[2]}'
    return None


def run_backfill():
    scanned = 0
    updated = 0

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, district, road
                FROM presale_projects
                WHERE geom_center IS NULL
                  AND road IS NOT NULL
                ORDER BY id
                """
            )
            rows = cur.fetchall()

            for row_id, district, road in rows:
                scanned += 1
                point = find_landlot_point(district, road)
                if point is None:
                    point = find_landlot_section_point(district, road)
                if point is None:
                    point = find_intersection_point(cur, district, road)
                if point is None:
                    point = find_single_road_mouth_point(cur, district, road)
                if point is None:
                    point = find_cleaned_nearby_point(cur, district, road)
                if point is None:
                    point = find_road_lane_point(cur, district, road)
                if point is None:
                    continue
                lon, lat, match_level = point
                ewkt = f'SRID=4326;POINT({lon} {lat})'
                cur.execute(
                    """
                    UPDATE presale_projects
                    SET lon = %s,
                        lat = %s,
                        geom_center = ST_GeomFromEWKT(%s::text),
                        center_source = 'ADDRESS_POINT_BASE',
                        match_level = %s,
                        updated_at = now()
                    WHERE id = %s
                    """,
                    (lon, lat, ewkt, match_level, row_id),
                )
                updated += 1
        conn.commit()

    return {'candidates': scanned, 'updated': updated}


def main():
    result = run_backfill()
    print(f"[SCAN] candidates={result['candidates']}")
    print(f"[DONE] updated={result['updated']}")


if __name__ == '__main__':
    main()
