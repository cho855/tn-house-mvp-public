import os
import time
import json
import random
import requests
from psycopg import connect

DB_DSN = os.environ.get("DB_DSN")  
NOMINATIM_URL = os.environ.get("NOMINATIM_URL", "https://nominatim.openstreetmap.org/search")
USER_AGENT = os.environ.get(
    "NOMINATIM_USER_AGENT",
    "tn-house-mvp/0.1 (contact: your-email@example.com)"
)

BATCH_SIZE = int(os.environ.get("GEOCODE_BATCH_SIZE", "20"))
SLEEP_SEC = float(os.environ.get("GEOCODE_SLEEP_SEC", "1.05"))  
MAX_ATTEMPTS = int(os.environ.get("GEOCODE_MAX_ATTEMPTS", "3"))

#從資料庫撈待處理任務
def fetch_pending_jobs(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, address_norm
            FROM geocode_jobs
            WHERE status = 'pending' AND attempts < %s
            ORDER BY id
            LIMIT %s
            """,
            (MAX_ATTEMPTS, BATCH_SIZE),
        )
        return cur.fetchall()

#先查快取，避免一直打外部 API
def cache_lookup(conn, address_norm: str):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT lat, lon FROM geocode_cache WHERE address_norm = %s",
            (address_norm,),
        )
        return cur.fetchone()

#查到的結果寫進快取（有就更新，沒有就新增）
def cache_upsert(conn, address_norm: str, lat: float, lon: float, raw_json: dict, importance=None):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO geocode_cache(address_norm, provider, lat, lon, geom, importance, raw_json, updated_at)
            VALUES (%s, 'nominatim', %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s::jsonb, now())
            ON CONFLICT (address_norm) DO UPDATE SET
              provider = EXCLUDED.provider,
              lat = EXCLUDED.lat,
              lon = EXCLUDED.lon,
              geom = EXCLUDED.geom,
              importance = EXCLUDED.importance,
              raw_json = EXCLUDED.raw_json,
              updated_at = now()
            """,
            (address_norm, lat, lon, lon, lat, importance, json.dumps(raw_json)),
        )

def mark_job_success(conn, job_id: int):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE geocode_jobs
            SET status='success', updated_at=now()
            WHERE id=%s
            """,
            (job_id,),
        )

def mark_job_failed(conn, job_id: int, err: str):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE geocode_jobs
            SET attempts = attempts + 1,
                last_error = %s,
                updated_at = now(),
                status = CASE WHEN attempts + 1 >= %s THEN 'failed' ELSE 'pending' END
            WHERE id=%s
            """,
            (err[:500], MAX_ATTEMPTS, job_id),
        )


def nominatim_geocode(address_norm: str):
    params = {
        "format": "jsonv2",
        "q": address_norm,
        "addressdetails": 0,
        "limit": 1,
        "email": os.environ.get("NOMINATIM_EMAIL", ""),
    }
    headers = {"User-Agent": USER_AGENT}
    r = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=20)
    r.raise_for_status()
    data = r.json()
    if not data:
        return None
    top = data[0]
    lat = float(top["lat"])
    lon = float(top["lon"])
    importance = float(top.get("importance", 0.0)) if top.get("importance") is not None else None
    return lat, lon, importance, top

def main():
    if not DB_DSN:
        raise SystemExit("DB_DSN is not set. Example: postgresql://tn:tn@tn_db:5432/tn_house")

    with connect(DB_DSN) as conn:
        conn.autocommit = False

        while True:
            jobs = fetch_pending_jobs(conn)
            if not jobs:
                print("No pending jobs. Done.")
                break

            for job_id, addr in jobs:
                try:
                    cached = cache_lookup(conn, addr)
                    if cached:
                        mark_job_success(conn, job_id)
                        conn.commit()
                        continue

                    result = nominatim_geocode(addr)
                    if result is None:
                        mark_job_failed(conn, job_id, "no_result")
                        conn.commit()
                    else:
                        lat, lon, importance, raw = result
                        cache_upsert(conn, addr, lat, lon, raw_json=raw, importance=importance)
                        mark_job_success(conn, job_id)
                        conn.commit()

                    
                    time.sleep(SLEEP_SEC + random.uniform(0, 0.2))

                except Exception as e:
                    conn.rollback()
                    mark_job_failed(conn, job_id, f"exception: {e}")
                    conn.commit()
                    time.sleep(SLEEP_SEC)

if __name__ == "__main__":
    main()
