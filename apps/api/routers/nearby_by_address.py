from __future__ import annotations
import re
import os
import logging
from typing import Optional, Literal, Tuple, Dict, Any, List

from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel
from psycopg import connect
from psycopg.rows import dict_row

# 你專案已經有 address.py：用既有的 normalize / parse 能力
# - normalize_address: 產出 address_norm（你已經有）
# - parse_address: 解析出 road / lane / alley / no（或 number_clean 的來源）
#
# 如果你的函數名稱不同，請在這裡改掉 import / 呼叫點即可
from address import normalize_address, parse, road_candidates



logger = logging.getLogger("tn_house.nearby_by_address")

router = APIRouter(tags=["spatial"])

DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("DB_DSN")


# =========================
# Response Model
# =========================
CenterSource = Literal["ADDRESS_POINT", "PERMIT_GEOM_SECTION"]

class CenterInfo(BaseModel):
    source: CenterSource
    # WGS84 lon/lat
    lon: float
    lat: float
    # 可選：補充 debug 用
    match_level: Optional[str] = None
    # 若來源是 use_permits，可回傳是哪一筆 permit 被拿來當中心
    permit_id: Optional[int] = None
    permit_no: Optional[str] = None


class NearbyItem(BaseModel):
    id: int
    permit_no: Optional[str] = None
    address_raw: str
    address_norm: Optional[str] = None
    city: Optional[str] = None
    district: Optional[str] = None
    issue_date: Optional[str] = None
    start_date: Optional[str] = None
    use_kind: Optional[str] = None
    floors_above: Optional[int] = None
    floors_below: Optional[int] = None
    height_m: Optional[float] = None
    household_count: Optional[int] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    geo_source: Optional[str] = None
    geocode_status: Optional[str] = None
    source_dataset: Optional[str] = None
    completion_date: Optional[str] = None
    builder: Optional[str] = None
    designer: Optional[str] = None
    contractor: Optional[str] = None
    floor_count: Optional[str] = None
    distance_m: float


class NearbyByAddressResponse(BaseModel):
    query_address: str
    address_norm: str
    radius_m: float
    limit: int
    center: CenterInfo
    items: List[NearbyItem]


# =========================
# DB helper
# =========================
def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL (or DB_DSN) env is not set")
    return connect(DATABASE_URL, row_factory=dict_row)


# =========================
# Center strategy
# 1) address_points_base 優先
# 2) use_permits.geom_section fallback
# =========================


FW_MAP = str.maketrans("0123456789", "０１２３４５６７８９")


def _to_fullwidth_digits(s: str) -> str:
    return s.translate(FW_MAP)


def _digits(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    m = re.search(r"\d+", str(s))
    return m.group(0) if m else None


# 保底：從 address_norm 抓 road/lane/alley/no
# 例：頂美一街48巷13弄2號 -> road=頂美一街 lane=48 alley=13 no=2
_RE_STREET_LANE_ALLEY_NO = re.compile(
    r"(?P<road>[^0-9０-９]+?(?:路|街|大道))"
    r"(?:(?P<lane>[0-9０-９]+)巷)?"
    r"(?:(?P<alley>[0-9０-９]+)弄)?"
    r"(?P<no>[0-9０-９]+)號"
)


def _strip_city_district_prefix(s: str) -> str:
    # 去掉 台南市/臺南市
    s = re.sub(r"^(臺?南市)", "", s)
    # 去掉 開頭的「XX區」（通常 1~6 個中文字 + 區）
    s = re.sub(r"^[\u4e00-\u9fff]{1,6}區", "", s)
    return s.strip()


def _coerce_from_address_norm(address_norm: str) -> Dict[str, Optional[str]]:
    m = _RE_STREET_LANE_ALLEY_NO.search(address_norm)
    if not m:
        return {"road": None, "lane": None, "alley": None, "no": None}

    road = _strip_city_district_prefix(m.group("road"))
    lane = m.group("lane")
    alley = m.group("alley")
    no = m.group("no")

    # 轉成 address_points_base 常用格式：全形巷/弄 + 純數字 number_clean
    lane_db = f"{_to_fullwidth_digits(_digits(lane))}?" if lane else None
    alley_db = f"{_to_fullwidth_digits(_digits(alley))}?" if alley else None
    no_clean = _digits(no)

    return {"road": road, "lane": lane_db, "alley": alley_db, "no": no_clean}


def find_center_by_address_points_base(
    conn,
    parsed: Dict[str, Any],
    address_norm: str,
) -> Optional[Tuple["CenterInfo", str]]:
    """
    回傳 `(CenterInfo, match_level)` 或 `None`
    `match_level`:
      - BASE_ROAD_LANE_ALLEY_NO
      - BASE_ROAD_LANE_NO
      - BASE_ROAD_NO

    若 `parse()` 沒有穩定拆出 lane/alley/no，會再用 `address_norm` regex 保底，
    並轉成 DB 使用的全形巷/弄格式；若 `parse()` 漏掉 road/no，也會用 fallback 補齊。
    """

    road = parsed.get("road")
    lane = parsed.get("lane")
    alley = parsed.get("alley")
    no = parsed.get("no") or parsed.get("number_clean") or parsed.get("no_clean")

    # lane/alley/no 轉成 DB 使用的格式（全形巷/弄 + 純數字）
    lane_d = _digits(lane)
    alley_d = _digits(alley)
    no_d = _digits(no)

    lane_db = f"{_to_fullwidth_digits(lane_d)}?" if lane_d else None
    alley_db = f"{_to_fullwidth_digits(alley_d)}?" if alley_d else None
    no_str = str(no_d) if no_d else (str(no).strip() if no is not None else None)

    # fallback：用 address_norm regex 再抓一次 road/lane/alley/no
    fallback = _coerce_from_address_norm(address_norm)
    if fallback.get("road") and fallback.get("no"):
        # fallback 命中時，以 fallback 的 road 為主
        road = fallback["road"]
        lane_db = fallback.get("lane") or lane_db
        alley_db = fallback.get("alley") or alley_db
        no_str = fallback.get("no") or no_str
    else:
        # 若 fallback 只抓到部分欄位，拿來補足 parse 缺漏
        if not road or not no_str:
            road = road or fallback["road"]
            lane_db = lane_db or fallback["lane"]
            alley_db = alley_db or fallback["alley"]
            no_str = no_str or (fallback["no"] if fallback["no"] else None)

    if not road or not no_str:
        return None

    road_options = road_candidates(road)
    if not road_options:
        return None

    # ---- level 1: road + lane + alley + number_clean
    if lane_db and alley_db:
        sql = """
        SELECT ST_X(geom) AS lon, ST_Y(geom) AS lat
        FROM address_points_base
        WHERE road = %s
          AND lane = %s
          AND alley = %s
          AND number_clean = %s
        LIMIT 1
        """
        for road_used in road_options:
            row = conn.execute(sql, (road_used, lane_db, alley_db, no_str)).fetchone()
            if row:
                level = "BASE_ROAD_LANE_ALLEY_NO"
                if road_used != road:
                    level += "_ALIAS"
                center = CenterInfo(
                    source="ADDRESS_POINT",
                    lon=float(row["lon"]),
                    lat=float(row["lat"]),
                    match_level=level,
                )
                return center, level

    # ---- level 2: road + lane + number_clean
    if lane_db:
        sql = """
        SELECT ST_X(geom) AS lon, ST_Y(geom) AS lat
        FROM address_points_base
        WHERE road = %s
          AND lane = %s
          AND number_clean = %s
        LIMIT 1
        """
        for road_used in road_options:
            row = conn.execute(sql, (road_used, lane_db, no_str)).fetchone()
            if row:
                level = "BASE_ROAD_LANE_NO"
                if road_used != road:
                    level += "_ALIAS"
                center = CenterInfo(
                    source="ADDRESS_POINT",
                    lon=float(row["lon"]),
                    lat=float(row["lat"]),
                    match_level=level,
                )
                return center, level

    # ---- level 3: road + number_clean
    sql = """
    SELECT ST_X(geom) AS lon, ST_Y(geom) AS lat
    FROM address_points_base
    WHERE road = %s
      AND number_clean = %s
    LIMIT 1
    """
    for road_used in road_options:
        row = conn.execute(sql, (road_used, no_str)).fetchone()
        if row:
            level = "BASE_ROAD_NO"
            if road_used != road:
                level += "_ALIAS"
            center = CenterInfo(
                source="ADDRESS_POINT",
                lon=float(row["lon"]),
                lat=float(row["lat"]),
                match_level=level,
            )
            return center, level

    return None


def find_center_by_use_permits_geom_section(conn, address_norm: str) -> Optional[CenterInfo]:
    """
    fallback：用 use_permits.geom_section 當中心點
    只用 address_norm exact（符合你目前穩定版策略）
    """
    sql = """
    SELECT
      id,
      permit_no,
      ST_X(geom) AS lon,
      ST_Y(geom) AS lat
    FROM use_permits
    WHERE address_norm = %s
      AND geom IS NOT NULL
    ORDER BY id ASC
    LIMIT 1
    """
    row = conn.execute(sql, (address_norm,)).fetchone()
    if not row:
        return None

    return CenterInfo(
        source="PERMIT_GEOM_SECTION",
        lon=float(row["lon"]),
        lat=float(row["lat"]),
        permit_id=int(row["id"]),
        permit_no=row.get("permit_no"),
    )


# =========================
# Nearby query
# =========================
def query_nearby_use_permits(conn, center_lon: float, center_lat: float, radius_m: float, limit: int):
    radius_deg = radius_m / 111_320.0

    sql = """
    WITH center AS (
      SELECT ST_SetSRID(ST_MakePoint(%s, %s), 4326) AS g
    )
    SELECT
      u.id,
      u.permit_no,
      u.address_raw,
      u.address_norm,
      u.issue_date::text AS issue_date,
      u.start_date::text AS start_date,
      u.usage AS use_kind,
      u.floors_above,
      u.floors_below,
      u.height_m,
      u.units AS household_count,
      ST_Y(u.geom) AS lat,
      ST_X(u.geom) AS lon,
      CASE
        WHEN u.geom IS NOT NULL THEN 'GEOM'
        ELSE 'NO_GEO'
      END AS geo_source,
      NULL::text AS geocode_status,
      u.source_dataset,
      ST_Distance(u.geom::geography, c.g::geography) AS distance_m
    FROM use_permits u
    CROSS JOIN center c
    WHERE u.geom IS NOT NULL
      AND u.geom && ST_Expand(c.g, %s)
      AND ST_DWithin(u.geom, c.g, %s)
    ORDER BY distance_m ASC
    LIMIT %s
    """
    rows = conn.execute(sql, (center_lon, center_lat, radius_deg, radius_deg, limit)).fetchall()

    items = []
    for r in rows:
        floors_above = r.get("floors_above")
        floors_below = r.get("floors_below")
        if floors_above is not None and floors_below is not None:
            floor_count = f"地上{floors_above} / 地下{floors_below}"
        elif floors_above is not None:
            floor_count = f"地上{floors_above}"
        elif floors_below is not None:
            floor_count = f"地下{floors_below}"
        else:
            floor_count = None

        items.append(NearbyItem(
            id=int(r["id"]),
            permit_no=r.get("permit_no"),
            address_raw=r["address_raw"],
            address_norm=r.get("address_norm"),
            city="台南市",
            district=parse(r.get("address_raw") or "").district,
            issue_date=r.get("issue_date"),
            start_date=r.get("start_date"),
            use_kind=r.get("use_kind"),
            floors_above=r.get("floors_above"),
            floors_below=r.get("floors_below"),
            height_m=float(r["height_m"]) if r.get("height_m") is not None else None,
            household_count=r.get("household_count"),
            lat=float(r["lat"]) if r.get("lat") is not None else None,
            lon=float(r["lon"]) if r.get("lon") is not None else None,
            geo_source=r.get("geo_source"),
            geocode_status=r.get("geocode_status"),
            source_dataset=r.get("source_dataset"),
            completion_date=None,
            builder=None,
            designer=None,
            contractor=None,
            floor_count=floor_count,
            distance_m=float(r["distance_m"]),
        ))
    return items

# =========================
# API
# =========================
@router.get("/nearby_by_address", response_model=NearbyByAddressResponse)
def nearby_by_address(
    address: str = Query(..., description="使用者輸入地址"),
    radius_m: float = Query(500.0, gt=0, le=5000, description="半徑（公尺）"),
    limit: int = Query(30, gt=0, le=200, description="回傳筆數上限"),
):
    address_norm = normalize_address(address)
    parsed_obj = parse(address_norm)

# ParsedAddress 不是 iterable，避免 dict(parsed_obj) 爆掉
# 用 getattr 取值最穩（不管是 dataclass / pydantic / 自訂 class 都可）
    parsed = {
        "road": getattr(parsed_obj, "road", None),
        "lane": getattr(parsed_obj, "lane", None),
        "alley": getattr(parsed_obj, "alley", None),
    # no 的欄位名稱你專案可能是 no / number_clean / no_clean，這裡做容錯
        "no": getattr(parsed_obj, "no", None)
            or getattr(parsed_obj, "number_clean", None)
            or getattr(parsed_obj, "no_clean", None),
}

    # 你想要的 log：center_source + match_level
    logger.info(
        "nearby_by_address input address=%s address_norm=%s parsed=%s radius_m=%.1f limit=%d",
        address, address_norm, parsed, radius_m, limit
    )

    with get_conn() as conn:
        # 1) 門牌點優先
        apb = find_center_by_address_points_base(conn, parsed, address_norm)
        if apb:
            center, match_level = apb
            logger.info(
                "center resolved: source=%s match_level=%s road=%s lane=%s alley=%s no=%s center=(%.6f,%.6f)",
                center.source,
                match_level,
                parsed.get("road"),
                parsed.get("lane"),
                parsed.get("alley"),
                parsed.get("no") or parsed.get("number_clean") or parsed.get("no_clean"),
                center.lon,
                center.lat,
            )
        else:
            # 2) fallback：use_permits.geom_section
            center = find_center_by_use_permits_geom_section(conn, address_norm)
            if not center:
                logger.warning(
                    "center not found: address_points_base miss + use_permits.geom_section miss. address_norm=%s",
                    address_norm,
                )
                raise HTTPException(
                    status_code=404,
                    detail={
                        "message": "center point not found",
                        "address_norm": address_norm,
                        "hint": "門牌點找不到，且 use_permits 找不到可用 geom_section（address_norm exact）。",
                    },
                )

            logger.info(
                "center resolved: source=%s permit_id=%s permit_no=%s center=(%.6f,%.6f)",
                center.source,
                center.permit_id,
                center.permit_no,
                center.lon,
                center.lat,
            )

        # 3) 用中心點去做 nearby（仍以 use_permits.geom_section 為被查對象）
        items = query_nearby_use_permits(conn, center.lon, center.lat, radius_m, limit)

        return NearbyByAddressResponse(
            query_address=address,
            address_norm=address_norm,
            radius_m=radius_m,
            limit=limit,
            center=center,
            items=items,
        )