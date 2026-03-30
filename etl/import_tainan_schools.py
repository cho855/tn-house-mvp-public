#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ETL: Import Tainan schools (JSON) -> schools_raw -> enrich -> schools (with geom)

Usage (inside your repo root):
  docker compose run --rm etl python etl/import_tainan_schools.py

Env (optional):
  DATABASE_URL=postgresql://postgres:postgres@db:5432/tn_house
  or PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD
"""

import os
import re
import json
import time
from typing import Any, Dict, List, Optional, Tuple

import requests
import psycopg


# ---- import your existing address normalizer/parser ----
# Put this script under etl/ and ensure your project path allows importing `address.py`.
# If your address.py is under apps/api/address.py, adjust import accordingly.
from address import normalize_address, parse  # type: ignore

URL = "https://soa.tainan.gov.tw/Api/Service/Get/b32ffde3-b030-4445-80a3-e83f06477c78"

RE_ZIP_PREFIX = re.compile(r"^\[\d+\]\s*")  # [704]
RE_SPACES = re.compile(r"\s+")
RE_NUMERIC_MARKER = re.compile(r"([0-9０-９一二三四五六七八九十百千〇零○Ｏ]+(?:[-–－之][0-9０-９一二三四五六七八九十百千〇零○Ｏ]+)?)(?=(段|巷|弄|號))")
RE_MULTI_HOUSE_NO = re.compile(r"(?P<prefix>.*?)(?P<num_seq>[0-9０-９一二三四五六七八九十百千〇零○Ｏ]+(?:[-–－之][0-9０-９一二三四五六七八九十百千〇零○Ｏ]+)?(?:[、,，][0-9０-９一二三四五六七八九十百千〇零○Ｏ]+(?:[-–－之][0-9０-９一二三四五六七八九十百千〇零○Ｏ]+)?)+)號")
FW_TO_ASCII = str.maketrans("０１２３４５６７８９Ｏ○", "012345678900")
CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
RE_DISTRICT_PREFIX = re.compile(r"^(?:台南市|臺南市)?(?P<district>[^路街大道巷弄段]{1,8}?(?:區|市|鎮|鄉))(?P<rest>.*)$")
RE_VILLAGE_PREFIX = re.compile(r"^(?:(?:[^路街大道巷弄段]{1,8}(?:里|村))(?:[0-9０-９一二三四五六七八九十百千〇零○Ｏ]+鄰)?)")
RE_FLOOR_INFO = re.compile(r"[0-9０-９一二三四五六七八九十百千〇零○Ｏ]+樓")
RE_DUP_CITY_AFTER_DISTRICT = re.compile(r"^(?P<city>台南市|臺南市)(?P<district>[^路街大道巷弄段]{1,8}?(?:區|市|鎮|鄉))(?P<dup>台南市|臺南市)(?P<rest>.*)$")


def get_dsn() -> str:
    dsn = os.getenv("DATABASE_URL")
    if dsn:
        return dsn

    host = os.getenv("PGHOST", "db")
    port = os.getenv("PGPORT", "5432")
    db = os.getenv("PGDATABASE", "tn_house")
    user = os.getenv("PGUSER", "postgres")
    pw = os.getenv("PGPASSWORD", "postgres")
    return f"host={host} port={port} dbname={db} user={user} password={pw}"


def http_get_json(url: str, timeout: int = 60) -> Dict[str, Any]:
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()


def cn_to_int(token: str) -> Optional[int]:
    token = (token or "").strip().translate(FW_TO_ASCII)
    if not token:
        return None
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
        elif ch == "十":
            unit_seen = True
            total += (current or 1) * 10
            current = 0
        elif ch == "百":
            unit_seen = True
            total += (current or 1) * 100
            current = 0
        elif ch == "千":
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
    token = (token or "").strip().translate(FW_TO_ASCII)
    if not token:
        return token
    pieces = re.split(r"([-–－之])", token)
    normalized = []
    for piece in pieces:
        if not piece:
            continue
        if piece in {"-", "–", "－"}:
            normalized.append("-")
            continue
        if piece == "之":
            normalized.append("之")
            continue
        value = cn_to_int(piece)
        normalized.append(str(value) if value is not None else piece)
    return ''.join(normalized)


def normalize_numeric_markers(addr: str) -> str:
    if not addr:
        return addr
    addr = addr.translate(FW_TO_ASCII)
    def repl(m):
        return normalize_number_token(m.group(1))
    return RE_NUMERIC_MARKER.sub(repl, addr)


def strip_noise_tokens(addr: str) -> str:
    addr = (addr or "").strip()
    if not addr:
        return addr
    addr = RE_FLOOR_INFO.sub("", addr)
    addr = addr.replace("及", "、")
    addr = re.sub(r"、+", "、", addr)
    m = RE_DUP_CITY_AFTER_DISTRICT.match(addr)
    if m:
        addr = f"{m.group('city')}{m.group('district')}{m.group('rest')}"
    return addr


def strip_village_prefix(addr: str) -> str:
    addr = (addr or "").strip()
    if not addr:
        return addr
    m = RE_DISTRICT_PREFIX.match(addr)
    if not m:
        return addr
    district = m.group('district')
    rest = (m.group('rest') or '').strip()
    rest2 = RE_VILLAGE_PREFIX.sub('', rest).strip()
    if rest2 and rest2 != rest:
        prefix = '台南市' if addr.startswith('台南市') else ('臺南市' if addr.startswith('臺南市') else '')
        return f"{prefix}{district}{rest2}"
    return addr


def address_variants(addr_raw: str) -> List[str]:
    base = strip_noise_tokens(clean_addr_for_geocode(addr_raw))
    if not base:
        return []
    variants: List[str] = []
    def add(v: Optional[str]) -> None:
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
    for variant in list(variants):
        m = RE_MULTI_HOUSE_NO.search(variant)
        if not m:
            continue
        prefix = m.group('prefix')
        suffix = variant[m.end():]
        parts = re.split(r'[、,，]', m.group('num_seq'))
        for part in parts:
            add(f"{prefix}{normalize_number_token(part)}號{suffix}")
    return variants


def clean_addr_for_geocode(addr_raw: Optional[str]) -> str:
    """
    School Addr example:
      "[704]臺南市北區成德里22鄰文成五街66號"

    Goal:
      remove zip prefix
      remove spaces
      remove village/neighborhood segments like "...里22鄰" if present
      keep something parseable by your address.py:
        台南市北區文成五街66號
    """
    if not addr_raw:
        return ""

    s = addr_raw.strip()
    s = RE_ZIP_PREFIX.sub("", s)
    s = s.replace("　", " ")
    s = RE_SPACES.sub("", s)

    # Normalize 臺->台 early (your normalize_address will do, but here helps substring ops)
    s = s.replace("臺", "台")

    # If contains "...里...鄰", keep substring AFTER "鄰"
    if "鄰" in s:
        idx = s.rfind("鄰")
        if idx != -1 and idx + 1 < len(s):
            # Keep prefix city+district if present, plus tail after 鄰
            # Example: 台南市北區成德里22鄰文成五街66號 -> 台南市北區 + 文成五街66號
            prefix = ""
            # Try preserve city+district (simple heuristic)
            m_city = re.match(r"^(台南市)(.+?[區鎮鄉市])", s)
            if m_city:
                prefix = m_city.group(1) + m_city.group(2)
            s = prefix + s[idx + 1 :]

    # If still contains "...里" (without 鄰), keep substring AFTER "里"
    # Example: 台南市北區成德里文成五街66號 -> 台南市北區 + 文成五街66號
    if "里" in s:
        idx = s.rfind("里")
        if idx != -1 and idx + 1 < len(s):
            prefix = ""
            m_city = re.match(r"^(台南市)(.+?[區鎮鄉市])", s)
            if m_city:
                prefix = m_city.group(1) + m_city.group(2)
            s = prefix + s[idx + 1 :]

    return s


def ensure_schema(cur) -> None:
    # extensions
    cur.execute("CREATE EXTENSION IF NOT EXISTS postgis;")
    cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")

    # raw table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schools_raw (
          edu_code text PRIMARY KEY,
          school_name text,
          school_type text,
          school_type2 text,
          stage text,
          region text,
          district text,
          addr_raw text,
          tel text,
          fax text,
          url text,
          payload jsonb,
          updated_at timestamptz default now()
        );
        """
    )

    # enriched table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schools (
          edu_code text PRIMARY KEY,
          school_name text,
          stage text,
          school_type text,
          district text,
          addr_raw text,
          address_norm text,
          lon double precision,
          lat double precision,
          geom geometry(Point,4326),
          center_source text,
          match_level text,
          updated_at timestamptz default now()
        );
        """
    )

    # indexes
    cur.execute("CREATE INDEX IF NOT EXISTS idx_schools_geom ON schools USING GIST (geom);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_schools_addr_trgm ON schools USING GIN (address_norm gin_trgm_ops);")


def table_has_column(cur, table: str, column: str) -> bool:
    cur.execute(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema='public' AND table_name=%s AND column_name=%s
        LIMIT 1
        """,
        (table, column),
    )
    return cur.fetchone() is not None


def upsert_raw(cur, rows: List[Dict[str, Any]]) -> int:
    sql = """
    INSERT INTO schools_raw(
      edu_code, school_name, school_type, school_type2, stage,
      region, district, addr_raw, tel, fax, url, payload, updated_at
    )
    VALUES(
      %(Edu_code)s, %(SchoolName)s, %(SchoolType)s, %(SchoolType2)s, %(Stage)s,
      %(Region)s, %(District)s, %(Addr)s, %(Tel)s, %(Fax)s, %(URL)s,
      %(payload)s, now()
    )
    ON CONFLICT (edu_code) DO UPDATE SET
      school_name=EXCLUDED.school_name,
      school_type=EXCLUDED.school_type,
      school_type2=EXCLUDED.school_type2,
      stage=EXCLUDED.stage,
      region=EXCLUDED.region,
      district=EXCLUDED.district,
      addr_raw=EXCLUDED.addr_raw,
      tel=EXCLUDED.tel,
      fax=EXCLUDED.fax,
      url=EXCLUDED.url,
      payload=EXCLUDED.payload,
      updated_at=now()
    ;
    """
    n = 0
    for r in rows:
        rr = dict(r)
        rr["payload"] = json.dumps(r, ensure_ascii=False)
        cur.execute(sql, rr)
        n += 1
    return n

def find_point_by_apb_key(cur, road: str, lane: Optional[str], alley: Optional[str], no: str) -> Optional[Tuple[float, float]]:
    """
    Primary match using address_points_base columns:
      road, lane, alley, number_clean, geom(Point,4326)
    Return (lon, lat)

    IMPORTANT:
      Avoid "(%s IS NULL OR lane=%s)" because NULL placeholder has unknown type in Postgres.
      Build SQL conditions dynamically instead.
    """
    clauses = ["road = %s", "number_clean = %s", "geom IS NOT NULL"]
    params: List[object] = [road, no]

    if lane is not None and str(lane).strip() != "":
        clauses.append("lane = %s")
        params.append(lane)

    if alley is not None and str(alley).strip() != "":
        clauses.append("alley = %s")
        params.append(alley)

    sql = f"""
    SELECT ST_X(geom) AS lon, ST_Y(geom) AS lat
    FROM address_points_base
    WHERE {" AND ".join(clauses)}
    LIMIT 1;
    """
    cur.execute(sql, params)
    row = cur.fetchone()
    if not row:
        return None
    return (float(row[0]), float(row[1]))

def find_point_by_apb_trgm(cur, address_norm: str) -> Optional[Tuple[float, float, float]]:
    """
    Fallback match using pg_trgm on address_points_base.address_norm
    Returns: (lon, lat, similarity)
    """
    sql = """
    SELECT ST_X(geom) AS lon, ST_Y(geom) AS lat, similarity(address_norm, %s) AS sim
    FROM address_points_base
    WHERE geom IS NOT NULL
      AND address_norm %% %s
    ORDER BY sim DESC
    LIMIT 1;
    """
    cur.execute(sql, (address_norm, address_norm))
    row = cur.fetchone()
    if not row:
        return None
    return (float(row[0]), float(row[1]), float(row[2]))


def upsert_enriched(
    cur,
    edu_code: str,
    school_name: str,
    stage: str,
    school_type: str,
    district: str,
    addr_raw: str,
    address_norm: str,
    lon: Optional[float],
    lat: Optional[float],
    center_source: str,
    match_level: str,
) -> None:
    # Build EWKT in Python to avoid NULL-typed placeholders in CASE WHEN
    ewkt = None
    if lon is not None and lat is not None:
        ewkt = f"SRID=4326;POINT({lon} {lat})"

    sql = """
    INSERT INTO schools(
      edu_code, school_name, stage, school_type, district,
      addr_raw, address_norm, lon, lat, geom, center_source, match_level, updated_at
    )
    VALUES(
      %s, %s, %s, %s, %s,
      %s, %s, %s, %s,
      ST_GeomFromEWKT(%s::text),
      %s, %s, now()
    )
    ON CONFLICT (edu_code) DO UPDATE SET
      school_name=EXCLUDED.school_name,
      stage=EXCLUDED.stage,
      school_type=EXCLUDED.school_type,
      district=EXCLUDED.district,
      addr_raw=EXCLUDED.addr_raw,
      address_norm=EXCLUDED.address_norm,
      lon=EXCLUDED.lon,
      lat=EXCLUDED.lat,
      geom=EXCLUDED.geom,
      center_source=EXCLUDED.center_source,
      match_level=EXCLUDED.match_level,
      updated_at=now()
    ;
    """
    cur.execute(
        sql,
        (
            edu_code,
            school_name,
            stage,
            school_type,
            district,
            addr_raw,
            address_norm,
            lon,
            lat,
            ewkt,          # <-- text or None (casted in SQL)
            center_source,
            match_level,
        ),
    )


def enrich_one(cur, r: Dict[str, Any], apb_has_address_norm: bool) -> Tuple[str, str]:
    edu_code = r.get("Edu_code") or ""
    school_name = r.get("SchoolName") or ""
    stage = r.get("Stage") or ""
    school_type = r.get("SchoolType") or ""
    district = r.get("District") or ""
    addr_raw = r.get("Addr") or ""

    variants = address_variants(addr_raw)
    addr_clean = variants[0] if variants else clean_addr_for_geocode(addr_raw)
    address_norm = normalize_address(addr_clean)

    lon = None
    lat = None
    center_source = "NONE"
    match_level = "NONE"

    for idx, variant in enumerate(variants or [addr_clean]):
        pa = parse(variant)
        if pa.road and pa.no:
            hit = find_point_by_apb_key(cur, pa.road, pa.lane, pa.alley, pa.no)
            if hit:
                lon, lat = hit
                center_source = "ADDRESS_POINT_BASE"
                match_level = "ROAD_LANE_ALLEY_NO_VARIANT" if idx > 0 else "ROAD_LANE_ALLEY_NO"
                address_norm = normalize_address(variant)
                break

    if (lon is None or lat is None) and apb_has_address_norm:
        for idx, variant in enumerate(variants or [addr_clean]):
            norm_variant = normalize_address(variant)
            if not norm_variant:
                continue
            hit2 = find_point_by_apb_trgm(cur, norm_variant)
            if hit2:
                lon, lat, sim = hit2
                center_source = "ADDRESS_POINT_BASE"
                match_level = f"TRGM(sim={sim:.3f})_VARIANT" if idx > 0 else f"TRGM(sim={sim:.3f})"
                address_norm = norm_variant
                break

    upsert_enriched(
        cur,
        edu_code=edu_code,
        school_name=school_name,
        stage=stage,
        school_type=school_type,
        district=district,
        addr_raw=addr_raw,
        address_norm=address_norm,
        lon=lon,
        lat=lat,
        center_source=center_source,
        match_level=match_level,
    )

    return center_source, match_level


def main() -> None:
    dsn = get_dsn()
    print(f"[DB] DSN: {dsn}")

    payload = http_get_json(URL)
    # common patterns: {"data":[...]} or directly list
    rows = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise RuntimeError(f"Unexpected JSON shape. keys={list(payload.keys()) if isinstance(payload, dict) else type(payload)}")

    print(f"[HTTP] rows={len(rows)}")

    conn = psycopg.connect(dsn)
    conn.autocommit = False

    try:
        with conn.cursor() as cur:
            ensure_schema(cur)

            apb_has_address_norm = table_has_column(cur, "address_points_base", "address_norm")
            print(f"[DB] address_points_base.address_norm exists? {apb_has_address_norm}")

            # 1) upsert raw
            n_raw = upsert_raw(cur, rows)
            print(f"[RAW] upserted {n_raw}")

            # 2) enrich
            ok = 0
            none = 0
            for i, r in enumerate(rows, 1):
                cs, ml = enrich_one(cur, r, apb_has_address_norm)
                if cs != "NONE":
                    ok += 1
                else:
                    none += 1

                if i % 500 == 0:
                    conn.commit()
                    print(f"[ENRICH] processed={i} ok={ok} none={none} (committed)")

            conn.commit()
            print(f"[DONE] processed={len(rows)} ok={ok} none={none}")

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
