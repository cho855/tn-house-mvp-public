CREATE EXTENSION IF NOT EXISTS postgis;

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE TABLE IF NOT EXISTS use_permits (
  id BIGSERIAL PRIMARY KEY,
  permit_no TEXT,
  building_permit_no TEXT,
  address_raw TEXT NOT NULL,
  address_norm TEXT NOT NULL,
  issue_date DATE,
  start_date DATE,
  floors_above INT,
  floors_below INT,
  height_m NUMERIC,
  usage TEXT,
  units INT,
  geom GEOMETRY(Point, 4326),
  source_dataset TEXT DEFAULT 'tainan_use_permit',
  source_row_hash TEXT UNIQUE,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_use_permits_address_trgm
  ON use_permits USING GIN (address_norm gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_use_permits_geom
  ON use_permits USING GIST (geom);

CREATE TABLE IF NOT EXISTS pois (
  id BIGSERIAL PRIMARY KEY,
  category TEXT NOT NULL,
  name TEXT,
  geom GEOMETRY(Point, 4326) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pois_geom
  ON pois USING GIST (geom);