from __future__ import annotations
import re
import os
import logging
from typing import Optional, Literal, Tuple, Dict, Any, List

from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel
from psycopg import connect
from psycopg.rows import dict_row

from address import normalize_address, parse, road_candidates

logger = logging.getLogger("tn_house.nearby_by_address")

router = APIRouter(tags=["spatial"])

DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("DB_DSN")

CenterSource = Literal["ADDRESS_POINT", "PERMIT_GEOM_SECTION"]

class CenterInfo(BaseModel):
    source: CenterSource
    lon: float
    lat: float
    match_level: Optional[str] = None
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


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL (or DB_DSN) env is not set")
    return connect(DATABASE_URL, row_factory=dict_row)


FW_MAP = str.maketrans("0123456789", "０１２３４５６７８９")


def _to_fullwidth_digits(s: str) -> str:
    return s.translate(FW_MAP)


def _digits(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    m = re.search(r"\d+", str(s))
    return m.group(0) if m else None


_RE_STREET_LANE_ALLEY_NO = re.compile(
    r"(?P<road>[^0-9０-９]+?(?:路|街|大道))"
    r"(?:(?P<lane>[0-9０-９]+)巷)?"
    r"(?:(?P<alley>[0-9０-９]+)弄)?"
    r"(?P<no>[0-9０-９]+)號"
)


def _strip_city_district_prefix(s: str) -> str:
    s = re.sub(r"^(臺?南市)", "", s)
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

    lane_db = f"{_to_fullwidth_digits(_digits(lane))}?" if lane else None
    alley_db = f"{_to_fullwidth_digits(_digits(alley))}?" if alley else None
    no_clean = _digits(no)

    return {"road": road, "lane": lane_db, "alley": alley_db, "no": no_clean}


def find_center_by_address_points_base(
    conn,
    parsed: Dict[str, Any],
    address_norm: str,
) -> Optional[Tuple["CenterInfo", str]]:

    road = parsed.get("road")
    lane = parsed.get("lane")
    alley = parsed.get("alley")
    no = parsed.get("no") or parsed.get("number_clean") or parsed.get("no_clean")

    lane_d = _digits(lane)
    alley_d = _digits(alley)
    no_d = _digits(no)

    lane_db = f"{_to_fullwidth_digits(lane_d)}?" if lane_d else None
    alley_db = f"{_to_fullwidth_digits(alley_d)}?" if alley_d else None
    no_str = str(no_d) if no_d else (str(no).strip() if no is not None else None)

    fallback = _coerce_from_address_norm(address_norm)

    if fallback.get("road") and fallback.get("no"):
        road = fallback["road"]
        lane_db = fallback.get("lane") or lane_db
        alley_db = fallback.get("alley") or alley_db
        no_str = fallback.get("no") or no_str
    else:
        if not road or not no_str:
            road = road or fallback["road"]
            lane_db = lane_db or fallback["lane"]
            alley_db = alley_db or fallback["alley"]
            no_str = no_str or fallback["no"]

    if not road or not no_str:
        return None

    road_options = road_candidates(road)
    if not road_options:
        return None

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