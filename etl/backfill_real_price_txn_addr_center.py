import os
import re
from dataclasses import dataclass
from typing import Optional, List, Tuple

from psycopg import connect
from psycopg.rows import dict_row


# =========================================================
# 內建 address parse（避免 ETL container 找不到 apps/api）
# 你可以之後再把這段抽成 etl/address_parse.py
# =========================================================

@dataclass
class ParsedAddress:
    raw: str
    canon: str
    city: Optional[str]
    district: Optional[str]
    road: Optional[str]
    lane: Optional[str]
    alley: Optional[str]
    no: Optional[str]
    subno: Optional[str]
    key_core: Optional[str]


# 這幾個 regex 你原本 address.py 有更完整版本
# 這裡先用「足夠支援你門牌 join」的最小版本
RE_CITY = re.compile(r"^(臺南市|台南市)")
RE_DIST = re.compile(r"^(.+?區)")
RE_LANE = re.compile(r"(?P<lane>\d+)巷")
RE_ALLEY = re.compile(r"(?P<alley>\d+)弄")

RE_ZH_NUM = str.maketrans("０１２３４５６７８９", "0123456789")


def normalize(s: str) -> str:
    s = (s or "").strip()
    s = s.translate(RE_ZH_NUM)
    s = re.sub(r"\s+", "", s)
    s = s.replace("臺南市", "台南市")
    s = s.replace("一段", "1段").replace("二段", "2段").replace("三段", "3段").replace("四段", "4段").replace("五段", "5段")
    s = s.replace("-", "之")  # 97-2 -> 97之2（你的策略）
    # 去樓層/室等（簡化版，若你原本更完整可再加）
    s = re.sub(r"(?:[一二三四五六七八九十0-9]+樓.*)$", "", s)
        # ---- 保留到門牌為止：把「號」後面的樓層/室/括號雜訊砍掉 ----
    m = re.search(r"^(.+?\d+號(?:之\d+)?)", s)
    if m:
        s = m.group(1)
    return s


def build_key_core(road: Optional[str], no: Optional[str], subno: Optional[str]) -> Optional[str]:
    # 你的 address.py 的 build_keys 可能更複雜；這裡先做 join 需要的核心 key
    if not road or not no:
        return None
    if subno:
        return f"{road}{no}號之{subno}"
    return f"{road}{no}號"


def parse(s: str) -> ParsedAddress:
    raw = s or ""
    canon = normalize(raw)

    city = None
    district = None

    m_city = RE_CITY.search(canon)
    if m_city:
        city = m_city.group(1)
        rest = canon[len(city):]
    else:
        rest = canon

    m_dist = RE_DIST.search(rest)
    if m_dist:
        district = m_dist.group(1)
        rest2 = rest[len(district):]
    else:
        rest2 = rest

    m_lane = RE_LANE.search(rest2)
    lane = m_lane.group("lane") if m_lane else None

    m_alley = RE_ALLEY.search(rest2)
    alley = m_alley.group("alley") if m_alley else None

    road = None
    no = None
    subno = None

    m_main = re.search(r"^(?P<road>.+?)(?P<no>\d+)號(?:之(?P<subno>\d+))?", rest2)
    if m_main:
        road = m_main.group("road")
        no = m_main.group("no")
        subno = m_main.group("subno")

    key_core = build_key_core(road, no, subno)

    return ParsedAddress(
        raw=raw,
        canon=canon,
        city=city,
        district=district,
        road=road,
        lane=lane,
        alley=alley,
        no=no,
        subno=subno,
        key_core=key_core,
    )


# =========================================================
# DB Backfill
# =========================================================

DB_DSN = os.environ.get("DB_DSN") or os.environ.get("DATABASE_URL")
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "2000"))


def infer_match_level(road: Optional[str], lane: Optional[str], alley: Optional[str], no: Optional[str]) -> Optional[str]:
    if not road or not no:
        return None
    if lane and alley:
        return "BASE_ROAD_LANE_ALLEY_NO"
    if lane:
        return "BASE_ROAD_LANE_NO"
    return "BASE_ROAD_NO"


def fetch_pending(conn, limit: int):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, address_norm
            FROM real_price_txn
            WHERE address_norm IS NOT NULL
              AND geocode_status IS DISTINCT FROM 'SKIP_PARSE'  
              AND (road IS NULL OR number_clean IS NULL OR key_core IS NULL)
            ORDER BY id
            LIMIT %s
            """,
            (limit,),
        )
        return cur.fetchall()


def update_addr_fields(conn, rows: List[Tuple]):
    sql = """
    UPDATE real_price_txn
    SET
      road = %s,
      lane = %s,
      alley = %s,
      number_clean = %s,
      key_core = %s,
      match_level = COALESCE(match_level, %s)
    WHERE id = %s
    """
    with conn.cursor() as cur:
        for rid, road, lane, alley, number_clean, key_core, match_level in rows:
            cur.execute(sql, (road, lane, alley, number_clean, key_core, match_level, rid))

def mark_skip(conn, ids, reason: str):
    if not ids:
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE real_price_txn
            SET
              geocode_status = 'SKIP_PARSE',     -- ✅ 直接覆蓋
              geocode_msg = %s,                 -- ✅ 直接寫原因（別 coalesce）
              geom_center = COALESCE(geom_center, geom),
              center_source = COALESCE(
                  center_source,
                  CASE
                      WHEN COALESCE(geom_center, geom) IS NOT NULL THEN 'TXN_GEOCODE'
                      ELSE 'NONE'
                  END
              )
            WHERE id = ANY(%s)
            """,
            (reason, ids),
        )


def fill_geom_center_from_address_points_base(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE real_price_txn t
            SET
              geom_center = b.geom,
              center_source = 'ADDRESS_POINT'
            FROM address_points_base b
            WHERE
              t.road IS NOT NULL
              AND t.number_clean IS NOT NULL
              AND t.road = b.road
              AND COALESCE(t.lane,'')  = COALESCE(b.lane,'')
              AND COALESCE(t.alley,'') = COALESCE(b.alley,'')
              AND t.number_clean = b.number_clean
              AND (t.center_source IS DISTINCT FROM 'ADDRESS_POINT'
                   OR t.geom_center IS DISTINCT FROM b.geom)
            """
        )
        return cur.rowcount


def set_fallback_center_source(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE real_price_txn
            SET center_source = 'TXN_GEOCODE'
            WHERE center_source IS NULL
              AND geom_center IS NOT NULL
            """
        )
        a = cur.rowcount

        cur.execute(
            """
            UPDATE real_price_txn
            SET center_source = 'NONE'
            WHERE center_source IS NULL
              AND geom_center IS NULL
            """
        )
        b = cur.rowcount
        return a + b


def main():
    if not DB_DSN:
        raise SystemExit("Missing DB_DSN or DATABASE_URL env var")

    conn = connect(DB_DSN)
    conn.autocommit = False

    total = 0
    try:
        while True:
            pending = fetch_pending(conn, BATCH_SIZE)
            if not pending:
                break

            updates = []
            skip_ids = []
            for r in pending:
                rid = r["id"]
                s = r["address_norm"]
                if not s:
                    continue

                p = parse(s)

                if not p.road or not p.no:
                    skip_ids.append(rid)
                    continue

                road = p.road
                lane = p.lane
                alley = p.alley
                number_clean = p.no  # 97號之2 -> 97

                key_core = p.key_core
                mlevel = infer_match_level(road, lane, alley, p.no)

                updates.append((rid, road, lane, alley, number_clean, key_core, mlevel))

            if updates:
                update_addr_fields(conn, updates)

            if skip_ids:
                mark_skip(conn, skip_ids, "parse failed: not doorplate format")

            conn.commit()
            total += len(pending)
            print(f"[backfill] parsed+updated rows: {total}")

        n1 = fill_geom_center_from_address_points_base(conn)
        conn.commit()
        print(f"[backfill] geom_center from ADDRESS_POINT updated: {n1}")

        n2 = set_fallback_center_source(conn)
        conn.commit()
        print(f"[backfill] center_source fallback updated: {n2}")

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()