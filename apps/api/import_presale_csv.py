import csv
import os
import sys
from pathlib import Path

import psycopg
from psycopg.types.json import Json

from address import normalize_address, parse

SOURCE_TABLE = 'presale_csv'
CSV_DEFAULT = '/home/cindy/whproject/tn-house-mvp/data/預售屋/預售屋.csv'

COL_DISTRICT = '鄉鎮市區'
COL_PROJECT = '建案名稱'
COL_ROAD = '坐落街道'
COL_BUILDER = '起造人'
COL_HOUSEHOLD = '層棟戶數'
COL_USE_ZONING = '使用分區'
COL_MAIN_USE = '主要用途'
COL_MAIN_MATERIAL = '主要建材'
COL_DECLARE_DATE = '申報備查日期'
COL_SELLING_PERIOD = '銷售期間'
COL_BUILDING_LANDS = '坐落基地'
COL_BUILDING_LANDS_ALT = '地號'
COL_PERMIT_DATE = '建照核發日期'
COL_PERMIT_NO = '建造執照'
COL_FIRST_REG_DATE = '第1次登記日期'
COL_SOURCE_NO = '編號'


def get_conn():
    dsn = os.getenv('DATABASE_URL') or os.getenv('DB_DSN')
    return psycopg.connect(dsn)


def ensure_table(cur):
    cur.execute(
        '''
        CREATE TABLE IF NOT EXISTS presale_projects (
          id bigserial PRIMARY KEY,
          source_table text NOT NULL DEFAULT 'presale_csv',
          source_id text NOT NULL,
          district text,
          project_name text,
          road text,
          builder text,
          household text,
          use_zoning text,
          main_use text,
          main_material text,
          declare_date text,
          selling_period text,
          building_lands text,
          building_permit_date text,
          building_permit_no text,
          first_registration_date text,
          address_seed text,
          address_norm text,
          lon double precision,
          lat double precision,
          geom_center geometry(Point,4326),
          center_source text,
          match_level text,
          extra jsonb,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (source_table, source_id)
        )
        '''
    )
    cur.execute('CREATE INDEX IF NOT EXISTS idx_presale_projects_geom_center ON presale_projects USING gist (geom_center)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_presale_projects_district ON presale_projects (district)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_presale_projects_project_name ON presale_projects (project_name)')
    cur.execute(
        '''
        CREATE TABLE IF NOT EXISTS presale_manual_overrides (
          id bigserial PRIMARY KEY,
          presale_project_id bigint UNIQUE REFERENCES presale_projects(id) ON DELETE CASCADE,
          source_id text,
          district text,
          project_name text,
          road text,
          address_note text,
          lon double precision NOT NULL,
          lat double precision NOT NULL,
          geom_center geometry(Point,4326) GENERATED ALWAYS AS (
            ST_SetSRID(ST_MakePoint(lon, lat), 4326)
          ) STORED,
          center_source text NOT NULL DEFAULT 'MANUAL_OVERRIDE',
          match_level text NOT NULL DEFAULT 'MANUAL_OVERRIDE',
          note text,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now()
        )
        '''
    )
    cur.execute('CREATE INDEX IF NOT EXISTS idx_presale_manual_overrides_geom_center ON presale_manual_overrides USING gist (geom_center)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_presale_manual_overrides_source_id ON presale_manual_overrides (source_id)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_presale_manual_overrides_project_name ON presale_manual_overrides (project_name)')


def is_header_like(rec):
    district = (rec.get(COL_DISTRICT) or '').strip().upper()
    project = (rec.get(COL_PROJECT) or '').strip().upper()
    road = (rec.get(COL_ROAD) or '').strip().upper()
    return district == 'TOWN' and project == 'BUILDCASE' and road == 'LOCATION'


def find_road_point(cur, district: str, road_raw: str):
    if not district or not road_raw:
        return None, None, None, None

    address_seed = f'台南市{district}{road_raw}'
    address_norm = normalize_address(address_seed)
    parsed = parse(address_norm)
    road_key = parsed.road or road_raw

    cur.execute(
        '''
        SELECT
          ST_X(ST_Centroid(ST_Collect(geom))) AS lon,
          ST_Y(ST_Centroid(ST_Collect(geom))) AS lat,
          count(*)
        FROM address_points_base
        WHERE geom IS NOT NULL
          AND road = %s
        ''',
        (road_key,),
    )
    row = cur.fetchone()
    if not row or row[0] is None or row[1] is None:
        return address_seed, address_norm, None, None

    lon, lat, n = float(row[0]), float(row[1]), int(row[2])
    return address_seed, address_norm, (lon, lat), f'ROAD_CENTROID_{n}'


def build_source_id(rec, district, project_name, road, permit_no):
    source_no = (rec.get(COL_SOURCE_NO) or '').strip()
    if source_no:
        return source_no
    return '|'.join([
        district or '',
        project_name or '',
        road or '',
        permit_no or '',
    ])


def upsert_row(cur, rec, address_seed, address_norm, point, match_level):
    district = (rec.get(COL_DISTRICT) or '').strip() or None
    project_name = (rec.get(COL_PROJECT) or '').strip() or None
    road = (rec.get(COL_ROAD) or '').strip() or None
    builder = (rec.get(COL_BUILDER) or '').strip() or None
    household = (rec.get(COL_HOUSEHOLD) or '').strip() or None
    use_zoning = (rec.get(COL_USE_ZONING) or '').strip() or None
    main_use = (rec.get(COL_MAIN_USE) or '').strip() or None
    main_material = (rec.get(COL_MAIN_MATERIAL) or '').strip() or None
    declare_date = (rec.get(COL_DECLARE_DATE) or '').strip() or None
    selling_period = (rec.get(COL_SELLING_PERIOD) or '').strip() or None
    building_lands = ((rec.get(COL_BUILDING_LANDS_ALT) or '').strip() or (rec.get(COL_BUILDING_LANDS) or '').strip() or None)
    permit_date = (rec.get(COL_PERMIT_DATE) or '').strip() or None
    permit_no = (rec.get(COL_PERMIT_NO) or '').strip() or None
    first_reg_date = (rec.get(COL_FIRST_REG_DATE) or '').strip() or None
    source_id = build_source_id(rec, district, project_name, road, permit_no)

    if point is None:
        lon = lat = None
        center_source = 'NONE'
        ewkt = None
    else:
        lon, lat = point
        center_source = 'ADDRESS_POINT_BASE'
        ewkt = f'SRID=4326;POINT({lon} {lat})'

    extra = {
        'builder': builder,
        'household': household,
        'use_zoning': use_zoning,
        'main_use': main_use,
        'main_material': main_material,
        'declare_date': declare_date,
        'selling_period': selling_period,
        'building_lands': building_lands,
        'building_permit_date': permit_date,
        'building_permit_no': permit_no,
        'first_registration_date': first_reg_date,
    }

    cur.execute(
        '''
        INSERT INTO presale_projects (
          source_table, source_id, district, project_name, road, builder, household,
          use_zoning, main_use, main_material, declare_date, selling_period,
          building_lands, building_permit_date, building_permit_no, first_registration_date,
          address_seed, address_norm, lon, lat, geom_center, center_source, match_level, extra,
          created_at, updated_at
        )
        VALUES (
          %s, %s, %s, %s, %s, %s, %s,
          %s, %s, %s, %s, %s,
          %s, %s, %s, %s,
          %s, %s, %s, %s, ST_GeomFromEWKT(%s::text), %s, %s, %s,
          now(), now()
        )
        ON CONFLICT (source_table, source_id) DO UPDATE SET
          district = EXCLUDED.district,
          project_name = EXCLUDED.project_name,
          road = EXCLUDED.road,
          builder = EXCLUDED.builder,
          household = EXCLUDED.household,
          use_zoning = EXCLUDED.use_zoning,
          main_use = EXCLUDED.main_use,
          main_material = EXCLUDED.main_material,
          declare_date = EXCLUDED.declare_date,
          selling_period = EXCLUDED.selling_period,
          building_lands = EXCLUDED.building_lands,
          building_permit_date = EXCLUDED.building_permit_date,
          building_permit_no = EXCLUDED.building_permit_no,
          first_registration_date = EXCLUDED.first_registration_date,
          address_seed = EXCLUDED.address_seed,
          address_norm = EXCLUDED.address_norm,
          lon = EXCLUDED.lon,
          lat = EXCLUDED.lat,
          geom_center = EXCLUDED.geom_center,
          center_source = EXCLUDED.center_source,
          match_level = EXCLUDED.match_level,
          extra = EXCLUDED.extra,
          updated_at = now()
        ''',
        (
            SOURCE_TABLE,
            source_id,
            district,
            project_name,
            road,
            builder,
            household,
            use_zoning,
            main_use,
            main_material,
            declare_date,
            selling_period,
            building_lands,
            permit_date,
            permit_no,
            first_reg_date,
            address_seed,
            address_norm,
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
            ensure_table(cur)
            cur.execute('DELETE FROM presale_projects WHERE source_table = %s', (SOURCE_TABLE,))
            for rec in rows:
                if is_header_like(rec):
                    continue
                kept += 1
                district = (rec.get(COL_DISTRICT) or '').strip()
                road = (rec.get(COL_ROAD) or '').strip()
                address_seed, address_norm, point, match_level = find_road_point(cur, district, road)
                if point is not None:
                    located += 1
                upsert_row(cur, rec, address_seed, address_norm, point, match_level or 'NONE')
        conn.commit()

    print(f'[CSV] total={total}')
    print(f'[KEEP] imported={kept}')
    print(f'[GEO] located={located}')
    print('[DONE]')


if __name__ == '__main__':
    csv_arg = sys.argv[1] if len(sys.argv) > 1 else CSV_DEFAULT
    main(csv_arg)


