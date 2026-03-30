import json
import os
import urllib.request
from typing import Any, Dict, List

import psycopg
from psycopg.types.json import Json

HOSPITALS_URL = "https://soa.tainan.gov.tw/Api/Service/Get/a31f9af7-a9ef-4004-a448-a3f2e9415e92"


def get_conn():
    host = os.getenv("PGHOST") or os.getenv("POSTGRES_HOST") or "db"
    port = os.getenv("PGPORT") or os.getenv("POSTGRES_PORT") or "5432"
    dbname = os.getenv("PGDATABASE") or os.getenv("POSTGRES_DB") or "tn_house"
    user = os.getenv("PGUSER") or os.getenv("POSTGRES_USER") or "postgres"
    password = os.getenv("PGPASSWORD") or os.getenv("POSTGRES_PASSWORD") or "postgres"

    print(f"[DB] host={host} port={port} dbname={dbname} user={user}")

    return psycopg.connect(
        host=host,
        port=port,
        dbname=dbname,
        user=user,
        password=password,
    )


def fetch_json(url: str) -> Dict[str, Any]:
    with urllib.request.urlopen(url) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def to_float(v):
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def upsert_hospitals_raw(cur, records: List[Dict[str, Any]]):
    sql = """
    INSERT INTO hospitals_raw (source_key, payload, created_at, updated_at)
    VALUES (%s, %s, now(), now())
    ON CONFLICT (source_key) DO UPDATE
    SET payload = EXCLUDED.payload,
        updated_at = now()
    """
    count = 0
    for rec in records:
        seq = str(rec.get("序號") or "").strip()
        name = str(rec.get("機構名稱") or "").strip()
        source_key = seq or name
        if not source_key:
            continue
        cur.execute(sql, (source_key, Json(rec)))
        count += 1
    return count


def upsert_hospitals_clean(cur, records: List[Dict[str, Any]]):
    sql = """
    INSERT INTO hospitals (
      source_key, hospital_name, addr_raw, lon, lat, geom, phone,
      center_source, match_level, extra, created_at, updated_at
    )
    VALUES (
      %s, %s, %s, %s, %s,
      ST_SetSRID(ST_MakePoint(%s, %s), 4326),
      %s,
      %s, %s, %s, now(), now()
    )
    ON CONFLICT (source_key) DO UPDATE
    SET
      hospital_name = EXCLUDED.hospital_name,
      addr_raw = EXCLUDED.addr_raw,
      lon = EXCLUDED.lon,
      lat = EXCLUDED.lat,
      geom = EXCLUDED.geom,
      phone = EXCLUDED.phone,
      center_source = EXCLUDED.center_source,
      match_level = EXCLUDED.match_level,
      extra = EXCLUDED.extra,
      updated_at = now()
    """
    count = 0
    for rec in records:
        seq = str(rec.get("序號") or "").strip()
        name = str(rec.get("機構名稱") or "").strip()
        addr_raw = str(rec.get("地址") or "").strip() or None
        lon = to_float(rec.get("經度"))
        lat = to_float(rec.get("緯度"))
        phone = str(rec.get("電話") or "").strip() or None

        source_key = seq or name
        if not source_key or not name or lon is None or lat is None:
            continue

        extra = {
            "seq": seq,
            "raw_name": name,
            "address": addr_raw,
            "phone": phone,
        }

        cur.execute(
            sql,
            (
                source_key,
                name,
                addr_raw,
                lon,
                lat,
                lon,
                lat,
                phone,
                "SOURCE_LONLAT",
                "DIRECT_LONLAT",
                Json(extra),
            ),
        )
        count += 1
    return count


def main():
    data = fetch_json(HOSPITALS_URL)
    records = data.get("data", [])
    print(f"[FETCH] records={len(records)}")

    with get_conn() as conn:
        with conn.cursor() as cur:
            raw_n = upsert_hospitals_raw(cur, records)
            print(f"[RAW] upserted={raw_n}")

            clean_n = upsert_hospitals_clean(cur, records)
            print(f"[HOSPITALS] upserted={clean_n}")

        conn.commit()

    print("[DONE]")


if __name__ == "__main__":
    main()