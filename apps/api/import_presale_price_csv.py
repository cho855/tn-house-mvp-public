# -*- coding: utf-8 -*-
import csv
import hashlib
import os
import sys
from pathlib import Path

import psycopg
from psycopg.types.json import Json

SOURCE_TABLE = 'presale_price_csv'
CSV_DIR_DEFAULT = '/data/預售屋111~11501'

COL_DISTRICT = '鄉鎮市區'
COL_ADDR = '土地位置建物門牌'
COL_TRADE_DATE = '交易年月日'
COL_BUILD_CASE = '建案名稱'
COL_TOTAL_PRICE = '總價元'
COL_UNIT_PRICE_SQM = '單價元平方公尺'
COL_TARGET = '交易標的'
COL_BUILDING_TYPE = '建物型態'
COL_MAIN_USE = '主要用途'
COL_MAIN_MATERIAL = '主要建材'
COL_TOTAL_FLOORS = '總樓層數'
COL_PARKING_TOTAL = '車位總價元'
COL_REMARK = '備註'
COL_LAND_AREA = '土地移轉總面積平方公尺'
COL_BUILDING_AREA = '建物移轉總面積平方公尺'


def get_conn():
    dsn = os.getenv('DATABASE_URL') or os.getenv('DB_DSN')
    return psycopg.connect(dsn)


def normalize_project_key(name: str) -> str:
    if not name:
        return ''
    return (
        name.strip()
        .replace(' ', '')
        .replace('　', '')
        .replace('．', '.')
        .replace('‧', '.')
        .replace('・', '.')
        .lower()
    )


def ensure_tables(cur):
    cur.execute(
        '''
        CREATE TABLE IF NOT EXISTS presale_price_txn (
          id bigserial PRIMARY KEY,
          source_table text NOT NULL DEFAULT 'presale_price_csv',
          source_file text NOT NULL,
          source_row_hash text NOT NULL,
          district text,
          build_case text,
          project_key text,
          addr_raw text,
          trade_date text,
          total_price bigint,
          unit_price_sqm numeric,
          transaction_target text,
          building_type text,
          main_use text,
          main_material text,
          total_floors text,
          parking_total_price bigint,
          land_area_sqm numeric,
          building_area_sqm numeric,
          remark text,
          raw_payload jsonb,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (source_table, source_row_hash)
        )
        '''
    )
    cur.execute('CREATE INDEX IF NOT EXISTS idx_presale_price_txn_project_key ON presale_price_txn (project_key)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_presale_price_txn_district_project_key ON presale_price_txn (district, project_key)')

    cur.execute(
        '''
        CREATE TABLE IF NOT EXISTS presale_price_summary (
          id bigserial PRIMARY KEY,
          district text,
          build_case text,
          project_key text NOT NULL,
          txn_count integer NOT NULL,
          latest_trade_date text,
          latest_total_price bigint,
          latest_unit_price_sqm numeric,
          avg_total_price numeric,
          avg_unit_price_sqm numeric,
          min_unit_price_sqm numeric,
          max_unit_price_sqm numeric,
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (district, project_key)
        )
        '''
    )
    cur.execute('CREATE INDEX IF NOT EXISTS idx_presale_price_summary_project_key ON presale_price_summary (project_key)')

    cur.execute('ALTER TABLE presale_projects ADD COLUMN IF NOT EXISTS project_key text')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_presale_projects_district_project_key ON presale_projects (district, project_key)')


def is_csv_candidate(path: Path) -> bool:
    name = path.name
    return path.is_file() and name.lower().endswith('.csv') and 'Zone.Identifier' not in name and not name.startswith('.~lock.')


def to_int(value):
    s = (value or '').strip().replace(',', '')
    if not s:
        return None
    try:
        return int(float(s))
    except Exception:
        return None


def to_numeric(value):
    s = (value or '').strip().replace(',', '')
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None


def make_row_hash(source_file: str, row_no: int, row: dict) -> str:
    payload = '|'.join([
        source_file,
        str(row_no),
        row.get(COL_DISTRICT, '') or '',
        row.get(COL_BUILD_CASE, '') or '',
        row.get(COL_ADDR, '') or '',
        row.get(COL_TRADE_DATE, '') or '',
        row.get(COL_TOTAL_PRICE, '') or '',
        row.get(COL_UNIT_PRICE_SQM, '') or '',
    ])
    return hashlib.sha1(payload.encode('utf-8')).hexdigest()


def upsert_txn(cur, source_file: str, row_no: int, row: dict):
    district = (row.get(COL_DISTRICT) or '').strip() or None
    build_case = (row.get(COL_BUILD_CASE) or '').strip() or None
    project_key = normalize_project_key(build_case)
    addr_raw = (row.get(COL_ADDR) or '').strip() or None
    trade_date = (row.get(COL_TRADE_DATE) or '').strip() or None
    total_price = to_int(row.get(COL_TOTAL_PRICE))
    unit_price_sqm = to_numeric(row.get(COL_UNIT_PRICE_SQM))
    transaction_target = (row.get(COL_TARGET) or '').strip() or None
    building_type = (row.get(COL_BUILDING_TYPE) or '').strip() or None
    main_use = (row.get(COL_MAIN_USE) or '').strip() or None
    main_material = (row.get(COL_MAIN_MATERIAL) or '').strip() or None
    total_floors = (row.get(COL_TOTAL_FLOORS) or '').strip() or None
    parking_total_price = to_int(row.get(COL_PARKING_TOTAL))
    land_area_sqm = to_numeric(row.get(COL_LAND_AREA))
    building_area_sqm = to_numeric(row.get(COL_BUILDING_AREA))
    remark = (row.get(COL_REMARK) or '').strip() or None
    source_row_hash = make_row_hash(source_file, row_no, row)

    cur.execute(
        '''
        INSERT INTO presale_price_txn (
          source_table, source_file, source_row_hash, district, build_case, project_key,
          addr_raw, trade_date, total_price, unit_price_sqm, transaction_target,
          building_type, main_use, main_material, total_floors, parking_total_price,
          land_area_sqm, building_area_sqm, remark, raw_payload, updated_at
        )
        VALUES (
          %s, %s, %s, %s, %s, %s,
          %s, %s, %s, %s, %s,
          %s, %s, %s, %s, %s,
          %s, %s, %s, %s, now()
        )
        ON CONFLICT (source_table, source_row_hash) DO UPDATE SET
          district = EXCLUDED.district,
          build_case = EXCLUDED.build_case,
          project_key = EXCLUDED.project_key,
          addr_raw = EXCLUDED.addr_raw,
          trade_date = EXCLUDED.trade_date,
          total_price = EXCLUDED.total_price,
          unit_price_sqm = EXCLUDED.unit_price_sqm,
          transaction_target = EXCLUDED.transaction_target,
          building_type = EXCLUDED.building_type,
          main_use = EXCLUDED.main_use,
          main_material = EXCLUDED.main_material,
          total_floors = EXCLUDED.total_floors,
          parking_total_price = EXCLUDED.parking_total_price,
          land_area_sqm = EXCLUDED.land_area_sqm,
          building_area_sqm = EXCLUDED.building_area_sqm,
          remark = EXCLUDED.remark,
          raw_payload = EXCLUDED.raw_payload,
          updated_at = now()
        ''',
        (
            SOURCE_TABLE,
            source_file,
            source_row_hash,
            district,
            build_case,
            project_key,
            addr_raw,
            trade_date,
            total_price,
            unit_price_sqm,
            transaction_target,
            building_type,
            main_use,
            main_material,
            total_floors,
            parking_total_price,
            land_area_sqm,
            building_area_sqm,
            remark,
            Json(row),
        ),
    )


def refresh_summary(cur):
    cur.execute("""
        UPDATE presale_projects
        SET project_key = lower(replace(replace(replace(replace(replace(trim(project_name), ' ', ''), '　', ''), '．', '.'), '‧', '.'), '・', '.'))
        WHERE project_name IS NOT NULL
    """)
    cur.execute('TRUNCATE presale_price_summary')
    cur.execute(
        '''
        WITH ranked AS (
          SELECT
            district,
            build_case,
            project_key,
            trade_date,
            total_price,
            unit_price_sqm,
            row_number() OVER (
              PARTITION BY district, project_key
              ORDER BY trade_date DESC NULLS LAST, id DESC
            ) AS rn
          FROM presale_price_txn
          WHERE project_key IS NOT NULL AND project_key <> ''
        ),
        agg AS (
          SELECT
            district,
            max(build_case) AS build_case,
            project_key,
            count(*)::int AS txn_count,
            avg(total_price)::numeric AS avg_total_price,
            avg(unit_price_sqm)::numeric AS avg_unit_price_sqm,
            min(unit_price_sqm)::numeric AS min_unit_price_sqm,
            max(unit_price_sqm)::numeric AS max_unit_price_sqm
          FROM presale_price_txn
          WHERE project_key IS NOT NULL AND project_key <> ''
          GROUP BY district, project_key
        )
        INSERT INTO presale_price_summary (
          district, build_case, project_key, txn_count,
          latest_trade_date, latest_total_price, latest_unit_price_sqm,
          avg_total_price, avg_unit_price_sqm, min_unit_price_sqm, max_unit_price_sqm,
          updated_at
        )
        SELECT
          a.district,
          a.build_case,
          a.project_key,
          a.txn_count,
          r.trade_date,
          r.total_price,
          r.unit_price_sqm,
          a.avg_total_price,
          a.avg_unit_price_sqm,
          a.min_unit_price_sqm,
          a.max_unit_price_sqm,
          now()
        FROM agg a
        LEFT JOIN ranked r
          ON r.district = a.district
         AND r.project_key = a.project_key
         AND r.rn = 1
        '''
    )


def main():
    csv_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(CSV_DIR_DEFAULT)
    files = sorted([p for p in csv_dir.iterdir() if is_csv_candidate(p)])
    conn = get_conn()
    processed = 0
    with conn:
        with conn.cursor() as cur:
            ensure_tables(cur)
            for path in files:
                with path.open('r', encoding='utf-8-sig', newline='') as f:
                    reader = csv.DictReader(f)
                    for row_no, row in enumerate(reader, start=1):
                        if (row.get(COL_DISTRICT) or '').strip().upper() == 'THE VILLAGES AND TOWNS URBAN DISTRICT':
                            continue
                        upsert_txn(cur, path.name, row_no, row)
                        processed += 1
            refresh_summary(cur)
    print(f'[DONE] files={len(files)} processed={processed}')


if __name__ == '__main__':
    main()
