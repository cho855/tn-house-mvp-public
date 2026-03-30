import os
import time
import random
import requests
from psycopg import connect

DB_DSN = os.environ["DB_DSN"]  # 例如: postgresql://tn:tn@tn_db:5432/tn_house

# 先用 Nominatim 跑小量驗證；大量建議改 TGOS
NOMINATIM_URL = os.environ.get("NOMINATIM_URL", "https://nominatim.openstreetmap.org/search")
USER_AGENT = os.environ.get(
    "NOMINATIM_USER_AGENT",
    "tn-house-mvp/0.1 (contact: your-email@example.com)"
)

BATCH_SIZE = int(os.environ.get("GEOCODE_BATCH_SIZE", "20"))
SLEEP_SEC = float(os.environ.get("GEOCODE_SLEEP_SEC", "1.05"))  # Nominatim 建議 <= 1 req/sec
MAX_ATTEMPTS = int(os.environ.get("GEOCODE_MAX_ATTEMPTS", "3"))


def fetch_pending(conn, limit: int):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, address_norm, geocode_attempts
            FROM public.use_permits
            WHERE geom IS NULL
              AND geocode_status = 'pending'
              AND geocode_attempts < %s
            ORDER BY id
            LIMIT %s
            """,
            (MAX_ATTEMPTS, limit),
        )
        return cur.fetchall()


def nominatim_geocode(address: str):
    params = {
        "q": address,
        "format": "jsonv2",
        "limit": 1,
        "countrycodes": "tw",
    }
    headers = {"User-Agent": USER_AGENT}
    r = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data:
        return None
    lat = float(data[0]["lat"])
    lon = float(data[0]["lon"])
    return lat, lon


def mark_ok(conn, _id: int, lat: float, lon: float):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE public.use_permits
            SET geom = ST_SetSRID(ST_MakePoint(%s, %s), 4326),
                geocode_status = 'ok',
                geocode_updated_at = NOW()
            WHERE id = %s
            """,
            (lon, lat, _id),
        )


def mark_failed_attempt(conn, _id: int, final: bool):
    with conn.cursor() as cur:
        if final:
            cur.execute(
                """
                UPDATE public.use_permits
                SET geocode_attempts = geocode_attempts + 1,
                    geocode_status = 'failed',
                    geocode_updated_at = NOW()
                WHERE id = %s
                """,
                (_id,),
            )
        else:
            cur.execute(
                """
                UPDATE public.use_permits
                SET geocode_attempts = geocode_attempts + 1,
                    geocode_updated_at = NOW()
                WHERE id = %s
                """,
                (_id,),
            )


def main():
    random.seed(42)
    total_ok = 0
    total_fail = 0

    with connect(DB_DSN) as conn:
        conn.autocommit = False

        while True:
            jobs = fetch_pending(conn, BATCH_SIZE)
            if not jobs:
                print("[geocode] no more pending jobs (or attempts exceeded). done.")
                break

            for _id, addr, attempts in jobs:
                try:
                    # 避免空字串
                    addr = (addr or "").strip()
                    if not addr:
                        mark_failed_attempt(conn, _id, final=True)
                        conn.commit()
                        total_fail += 1
                        continue

                    # Nominatim 有速率限制：每筆 sleep
                    time.sleep(SLEEP_SEC)

                    res = nominatim_geocode(addr)
                    if res is None:
                        # 找不到 -> 算一次嘗試；如果到上限就標 failed
                        final = (attempts + 1) >= MAX_ATTEMPTS
                        mark_failed_attempt(conn, _id, final=final)
                        conn.commit()
                        total_fail += 1
                        continue

                    lat, lon = res
                    mark_ok(conn, _id, lat, lon)
                    conn.commit()
                    total_ok += 1
                    print(f"[geocode] ok id={_id} addr={addr} lat={lat} lon={lon}")

                except Exception as e:
                    # API / network error -> 也算一次嘗試
                    final = (attempts + 1) >= MAX_ATTEMPTS
                    try:
                        mark_failed_attempt(conn, _id, final=final)
                        conn.commit()
                    except Exception:
                        conn.rollback()
                    total_fail += 1
                    print(f"[geocode] error id={_id} addr={addr}: {e}")

            print(f"[geocode] batch done ok={total_ok} fail={total_fail}")

    print(f"[geocode] DONE ok={total_ok} fail={total_fail}")


if __name__ == "__main__":
    main()