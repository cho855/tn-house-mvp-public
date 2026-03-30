import os
import re
import json
import time
from typing import Optional, Tuple

import requests
from psycopg import connect


DB_DSN = os.environ.get("DB_DSN")  # e.g. postgresql://tn:tn@localhost:5432/tn_house
TGOS_APP_ID = os.environ.get("TGOS_APP_ID")
TGOS_API_KEY = os.environ.get("TGOS_API_KEY")

TGOS_QUERY_URL = os.environ.get(
    "TGOS_QUERY_URL",
    "https://addr.tgos.tw/addrws/v30/QueryAddr.asmx/QueryAddr",
)

BATCH_SIZE = int(os.environ.get("GEOCODE_BATCH_SIZE", "50"))
SLEEP_SEC = float(os.environ.get("GEOCODE_SLEEP_SEC", "0.2"))  # 自己保守一點
TIMEOUT_SEC = float(os.environ.get("GEOCODE_TIMEOUT_SEC", "20"))


XML_STRING_RE = re.compile(r"<string[^>]*>(.*?)</string>", re.DOTALL)


def ensure_columns(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            ALTER TABLE real_price_txn
              ADD COLUMN IF NOT EXISTS geom geometry(Point, 4326),
              ADD COLUMN IF NOT EXISTS geocode_status text,
              ADD COLUMN IF NOT EXISTS geocode_msg text,
              ADD COLUMN IF NOT EXISTS geocode_src text;
            """
        )
    conn.commit()


def pick_pending(conn, limit: int):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, address_norm
            FROM real_price_txn
            WHERE address_norm IS NOT NULL
              AND (geom IS NULL)
              AND (geocode_status IS NULL OR geocode_status NOT IN ('OK'))
            ORDER BY id
            LIMIT %s;
            """,
            (limit,),
        )
        return cur.fetchall()


def parse_tgos_payload(text: str) -> dict:
    """
    TGOS 有時會回 text/xml，內容是一個 <string>JSON字串</string>
    也可能直接就是 JSON。這裡做容錯。
    """
    t = text.strip()
    if t.startswith("<"):
        m = XML_STRING_RE.search(t)
        if not m:
            raise ValueError("TGOS response looks like XML but <string>...</string> not found")
        inner = m.group(1).strip()
        # 有些會把特殊字元 encode，通常 JSON 本體還是可以直接 loads
        return json.loads(inner)
    else:
        return json.loads(t)


def query_addr(address: str) -> Tuple[Optional[float], Optional[float], str]:
    """
    return: (lat, lon, message)
    """
    params = {
        "oAPPId": TGOS_APP_ID,
        "oAPIKey": TGOS_API_KEY,
        "oAddress": address,
        "oSRS": "EPSG:4326",
        "oFuzzyType": "2",            # 單雙號 + 最近門牌（比較好找）
        "oResultDataType": "JSON",
        "oFuzzyBuffer": "0",
        "oIsOnlyFullMatch": "false",
        "oIsLockCounty": "false",
        "oIsLockTown": "false",
        "oIsLockVillage": "false",
        "oIsLockRoadSection": "false",
        "oIsLockLane": "false",
        "oIsLockAlley": "false",
        "oIsLockArea": "false",
        "oIsSameNumber_SubNumber": "true",
        "oCanIgnoreVillage": "true",
        "oCanIgnoreNeighborhood": "true",
        "oReturnMaxCount": "1",
    }

    r = requests.get(TGOS_QUERY_URL, params=params, timeout=TIMEOUT_SEC)
    print("[TGOS] status:", r.status_code)
    print("[TGOS] text head:", r.text[:300]) 
    r.raise_for_status()

    payload = parse_tgos_payload(r.text)

    info = (payload.get("Info") or [{}])[0]
    addr_list = payload.get("AddressList") or []

    out_total = str(info.get("OutTotal", "0"))
    out_match_type = str(info.get("OutMatchType", ""))
    out_trace = str(info.get("OutTraceInfo", ""))

    if not addr_list or out_total in ("0", "", "None"):
        return None, None, f"NO_MATCH {out_match_type} {out_trace}".strip()

    best = addr_list[0]
    # EPSG:4326: X=lon, Y=lat（習慣上）
    lon = best.get("X")
    lat = best.get("Y")
    full_addr = best.get("FULL_ADDR", "")

    if lon is None or lat is None:
        return None, None, f"NO_COORD {out_match_type} {full_addr}".strip()

    return float(lat), float(lon), f"OK {out_match_type} {full_addr}".strip()


def update_row(conn, row_id: int, lat: Optional[float], lon: Optional[float], status: str, msg: str):
    with conn.cursor() as cur:
        if lat is not None and lon is not None and status == "OK":
            cur.execute(
                """
                UPDATE real_price_txn
                SET geom = ST_SetSRID(ST_MakePoint(%s, %s), 4326),
                    geocode_status = %s,
                    geocode_msg = %s,
                    geocode_src = 'TGOS'
                WHERE id = %s
                """,
                (lon, lat, status, msg, row_id),
            )
        else:
            cur.execute(
                """
                UPDATE real_price_txn
                SET geocode_status = %s,
                    geocode_msg = %s,
                    geocode_src = 'TGOS'
                WHERE id = %s
                """,
                (status, msg, row_id),
            )


def main():
    if not DB_DSN:
        raise SystemExit("Missing DB_DSN env var")
    if not TGOS_APP_ID or not TGOS_API_KEY:
        raise SystemExit("Missing TGOS_APP_ID / TGOS_API_KEY env vars")

    with connect(DB_DSN) as conn:
        ensure_columns(conn)

        while True:
            rows = pick_pending(conn, BATCH_SIZE)
            if not rows:
                print("[TGOS] No pending rows. Done.")
                break

            ok = 0
            fail = 0

            for row_id, address in rows:
                address = (address or "").strip()
                if not address:
                    update_row(conn, row_id, None, None, "SKIP", "empty address")
                    fail += 1
                    continue

                try:
                    lat, lon, msg = query_addr(address)
                    if lat is not None and lon is not None:
                        update_row(conn, row_id, lat, lon, "OK", msg)
                        ok += 1
                    else:
                        update_row(conn, row_id, None, None, "NO_MATCH", msg)
                        fail += 1
                except Exception as e:
                    update_row(conn, row_id, None, None, "ERROR", repr(e))
                    fail += 1

                conn.commit()
                time.sleep(SLEEP_SEC)

            print(f"[TGOS] batch done ok={ok} fail={fail} (last_id={rows[-1][0]})")


if __name__ == "__main__":
    main()