import csv
import os
import psycopg
from pathlib import Path

out_path = Path('/app/use_permits_full.csv')
dsn = os.environ.get('DATABASE_URL') or os.environ.get('DB_DSN')
query = '''
SELECT
  id,
  permit_no,
  address_raw,
  address_norm,
  issue_date,
  start_date,
  floors_above,
  floors_below,
  height_m,
  usage,
  units,
  source_dataset,
  source_row_hash,
  created_at,
  geocode_status,
  geocode_attempts,
  geocode_updated_at,
  match_key,
  ST_AsText(geom) AS geom_wkt,
  ST_AsText(geom_section) AS geom_section_wkt,
  ST_AsText(geom_point) AS geom_point_wkt
FROM use_permits
ORDER BY id
'''

with psycopg.connect(dsn) as conn:
    with conn.cursor() as cur:
        cur.execute(query)
        columns = [desc.name for desc in cur.description]
        with out_path.open('w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow(columns)
            for row in cur:
                writer.writerow(row)

print(out_path)
