import csv
import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

out_path = Path('/app/permits_search_items_full.csv')
dsn = os.environ.get('DATABASE_URL') or os.environ.get('DB_DSN')

TAINAN_DISTRICTS = [
    '中西區','東區','南區','北區','安平區','安南區','永康區','歸仁區','新化區','左鎮區',
    '玉井區','楠西區','南化區','仁德區','關廟區','龍崎區','官田區','麻豆區','佳里區','西港區',
    '七股區','將軍區','學甲區','北門區','新營區','後壁區','白河區','東山區','六甲區','下營區',
    '柳營區','鹽水區','善化區','大內區','山上區','新市區','安定區'
]

sql = '''
SELECT
    id,
    permit_no,
    address_raw,
    issue_date::text AS issue_date,
    start_date::text AS start_date,
    usage AS use_kind,
    floors_above,
    floors_below,
    height_m,
    units AS household_count,
    COALESCE(ST_Y(geom_point), ST_Y(geom), ST_Y(geom_section)) AS lat,
    COALESCE(ST_X(geom_point), ST_X(geom), ST_X(geom_section)) AS lon,
    CASE
        WHEN geom_point IS NOT NULL THEN 'GEOM_POINT'
        WHEN geom IS NOT NULL THEN 'GEOM'
        WHEN geom_section IS NOT NULL THEN 'GEOM_SECTION'
        ELSE 'NO_GEO'
    END AS geo_source,
    geocode_status,
    source_dataset
FROM use_permits
ORDER BY issue_date DESC NULLS LAST, id DESC
'''

with psycopg.connect(dsn, row_factory=dict_row) as conn:
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()

for item in rows:
    address_raw = item.get('address_raw') or ''
    item['district'] = next((d for d in TAINAN_DISTRICTS if d in address_raw), None)
    item['city'] = '台南市'
    item['completion_date'] = None
    item['builder'] = None
    item['designer'] = None
    item['contractor'] = None

    floors_above = item.get('floors_above')
    floors_below = item.get('floors_below')
    if floors_above is not None and floors_below is not None:
        item['floor_count'] = f'地上{floors_above} / 地下{floors_below}'
    elif floors_above is not None:
        item['floor_count'] = f'地上{floors_above}'
    elif floors_below is not None:
        item['floor_count'] = f'地下{floors_below}'
    else:
        item['floor_count'] = None

fieldnames = [
    'id', 'permit_no', 'address_raw', 'district', 'issue_date', 'start_date',
    'use_kind', 'floors_above', 'floors_below', 'height_m', 'household_count',
    'lat', 'lon', 'geo_source', 'geocode_status', 'source_dataset', 'city',
    'completion_date', 'builder', 'designer', 'contractor', 'floor_count'
]

with out_path.open('w', newline='', encoding='utf-8-sig') as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

print(out_path)
print(len(rows))
