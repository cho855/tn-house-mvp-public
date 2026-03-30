# -*- coding: utf-8 -*-
import csv
import os
import psycopg
from pathlib import Path

dsn = os.getenv('DATABASE_URL') or os.getenv('DB_DSN')
out_path = Path('/app/presale_projects_without_price_summary.csv')
conn = psycopg.connect(dsn)
with conn, conn.cursor() as cur:
    cur.execute("""
        SELECT
            p.id,
            p.district,
            p.project_name,
            p.road,
            p.builder,
            p.building_permit_no,
            p.building_permit_date,
            p.address_seed,
            p.address_norm,
            p.center_source,
            p.match_level,
            ST_X(p.geom_center) AS lon,
            ST_Y(p.geom_center) AS lat,
            p.project_key
        FROM presale_projects p
        LEFT JOIN LATERAL (
            SELECT s.project_key
            FROM presale_price_summary s
            WHERE s.project_key = COALESCE(
                p.project_key,
                lower(replace(replace(replace(replace(replace(trim(p.project_name), ' ', ''), '　', ''), '．', '.'), '‧', '.'), '・', '.'))
            )
            ORDER BY CASE WHEN s.district = p.district THEN 0 ELSE 1 END, s.txn_count DESC, s.updated_at DESC
            LIMIT 1
        ) price ON true
        WHERE price.project_key IS NULL
        ORDER BY p.district, p.project_name, p.id
    """)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
with out_path.open('w', encoding='utf-8-sig', newline='') as f:
    w = csv.writer(f)
    w.writerow(cols)
    for row in rows:
        w.writerow(row)
print(f'rows={len(rows)} path={out_path}')
