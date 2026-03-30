import json
import os
import urllib.request
from typing import Any, Dict, List

import psycopg
from psycopg.types.json import Json

PARKS_URL = "https://soa.tainan.gov.tw/Api/Service/Get/3423c289-e4cc-4f4f-89df-c38d4618b350"


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


def get_field(rec: Dict[str, Any], *names: str):
    for name in names:
        if name in rec:
            return rec[name]
    return None


def upsert_parks_raw(cur, records: List[Dict[str, Any]]):
    sql = """
    INSERT INTO parks_raw (source_key, payload, created_at, updated_at)
    VALUES (%s, %s, now(), now())
    ON CONFLICT (source_key) DO UPDATE
    SET payload = EXCLUDED.payload,
        updated_at = now()
    """
    count = 0
    for rec in records:
        park_name = str(get_field(rec, "公園名稱") or "").strip()
        addr_raw = str(get_field(rec, "座落位置", "座落 位置") or "").strip()
        source_key = f"{park_name}|{addr_raw}"
        cur.execute(sql, (source_key, Json(rec)))
        count += 1
    return count


def upsert_parks_clean(cur, records: List[Dict[str, Any]]):
    sql = """
    INSERT INTO parks (
      source_key, park_name, village, area_ha, park_type, addr_raw, zoning,
      manager_unit, district_code, x_97, y_97,
      lon, lat, geom,
      center_source, match_level, extra,
      created_at, updated_at
    )
    VALUES (
      %s, %s, %s, %s, %s, %s, %s,
      %s, %s, %s, %s,
      ST_X(ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 3826), 4326)),
      ST_Y(ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 3826), 4326)),
      ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 3826), 4326),
      %s, %s, %s,
      now(), now()
    )
    ON CONFLICT (source_key) DO UPDATE
    SET
      park_name = EXCLUDED.park_name,
      village = EXCLUDED.village,
      area_ha = EXCLUDED.area_ha,
      park_type = EXCLUDED.park_type,
      addr_raw = EXCLUDED.addr_raw,
      zoning = EXCLUDED.zoning,
      manager_unit = EXCLUDED.manager_unit,
      district_code = EXCLUDED.district_code,
      x_97 = EXCLUDED.x_97,
      y_97 = EXCLUDED.y_97,
      lon = EXCLUDED.lon,
      lat = EXCLUDED.lat,
      geom = EXCLUDED.geom,
      center_source = EXCLUDED.center_source,
      match_level = EXCLUDED.match_level,
      extra = EXCLUDED.extra,
      updated_at = now()
    """
    count = 0
    for rec in records:
        park_name = str(get_field(rec, "公園名稱") or "").strip()
        village = str(get_field(rec, "里別") or "").strip() or None
        area_ha = to_float(get_field(rec, "面積"))
        park_type = str(get_field(rec, "類別") or "").strip() or None
        addr_raw = str(get_field(rec, "座落位置", "座落 位置") or "").strip() or None
        zoning = str(get_field(rec, "使用分區") or "").strip() or None
        manager_unit = str(get_field(rec, "維護管理單位") or "").strip() or None
        district_code = str(get_field(rec, "行政區域代碼") or "").strip() or None
        x_97 = to_float(get_field(rec, "X座標"))
        y_97 = to_float(get_field(rec, "Y座標"))

        if not park_name or x_97 is None or y_97 is None:
            continue

        source_key = f"{park_name}|{addr_raw or ''}"

        extra = {
            "raw_name": park_name,
            "village": village,
            "area_ha": area_ha,
            "park_type": park_type,
            "zoning": zoning,
            "manager_unit": manager_unit,
            "district_code": district_code,
        }

        cur.execute(
            sql,
            (
                source_key, park_name, village, area_ha, park_type, addr_raw, zoning,
                manager_unit, district_code, x_97, y_97,
                x_97, y_97,
                x_97, y_97,
                x_97, y_97,
                "SOURCE_XY_TWD97", "TWD97_TM2_TO_WGS84", Json(extra)
            )
        )
        count += 1
    return count


def main():
    data = fetch_json(PARKS_URL)
    records = data.get("data", [])
    print(f"[FETCH] records={len(records)}")

    with get_conn() as conn:
        with conn.cursor() as cur:
            raw_n = upsert_parks_raw(cur, records)
            print(f"[RAW] upserted={raw_n}")

            clean_n = upsert_parks_clean(cur, records)
            print(f"[PARKS] upserted={clean_n}")

        conn.commit()

    print("[DONE]")


if __name__ == "__main__":
    main()