from __future__ import annotations

import os
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel
from psycopg import connect
from psycopg.rows import dict_row

from address import parse, road_candidates

router = APIRouter(tags=["real_price"])

DATABASE_URL = os.getenv("DATABASE_URL")


class Center(BaseModel):
    source: str
    lon: Optional[float] = None
    lat: Optional[float] = None
    match_level: Optional[str] = None


class TxnItem(BaseModel):
    id: int
    trade_date: Optional[str] = None
    district: Optional[str] = None
    address_norm: Optional[str] = None
    total_price: Optional[int] = None
    unit_price_sqm: Optional[float] = None
    building_area_sqm: Optional[float] = None
    land_area_sqm: Optional[float] = None
    building_type: Optional[str] = None
    main_usage: Optional[str] = None
    floor_text: Optional[str] = None
    total_floors: Optional[int] = None
    year_built_ad: Optional[int] = None
    has_elevator: Optional[str] = None
    has_mgmt_org: Optional[str] = None
    lon: Optional[float]
    lat: Optional[float]
    distance_m: float
    txn_center_source: Optional[str] = None


class Summary(BaseModel):
    n: int
    p25_unit_price_sqm: Optional[float] = None
    p50_unit_price_sqm: Optional[float] = None
    p75_unit_price_sqm: Optional[float] = None


class Coverage(BaseModel):
    txns_total: int
    txns_locatable: int
    excluded_landlot_no_geom: int


class NearbyTxnResponse(BaseModel):
    query_address: str
    address_norm: str
    radius_m: float
    limit: int
    center: Center
    items: List[TxnItem]
    summary: Summary
    coverage: Coverage


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")
    return connect(DATABASE_URL, row_factory=dict_row)


def _center_from_address_points_base(
    conn,
    road: Optional[str],
    lane: Optional[str],
    alley: Optional[str],
    number_clean: Optional[str],
) -> Optional[Dict[str, Any]]:
    road_options = road_candidates(road)
    if not road_options or not number_clean:
        return None

    with conn.cursor() as cur:
        for road_used in road_options:
            cur.execute(
                """
                SELECT
                  ST_X(geom) AS lon,
                  ST_Y(geom) AS lat,
                  'BASE_ROAD_LANE_ALLEY_NO' AS match_level
                FROM address_points_base
                WHERE road = %s
                  AND number_clean = %s
                  AND lane = %s
                  AND alley = %s
                LIMIT 1
                """,
                (road_used, number_clean, lane, alley),
            )
            r = cur.fetchone()
            if r and r["lon"] is not None and r["lat"] is not None:
                out = dict(r)
                if road_used != road:
                    out["match_level"] = f"{out['match_level']}_ALIAS"
                return out

        if lane:
            for road_used in road_options:
                cur.execute(
                    """
                    SELECT
                      ST_X(geom) AS lon,
                      ST_Y(geom) AS lat,
                      'BASE_ROAD_LANE_NO' AS match_level
                    FROM address_points_base
                    WHERE road = %s
                      AND number_clean = %s
                      AND lane = %s
                    LIMIT 1
                    """,
                    (road_used, number_clean, lane),
                )
                r = cur.fetchone()
                if r and r["lon"] is not None and r["lat"] is not None:
                    out = dict(r)
                    if road_used != road:
                        out["match_level"] = f"{out['match_level']}_ALIAS"
                    return out

        for road_used in road_options:
            cur.execute(
                """
                SELECT
                  ST_X(geom) AS lon,
                  ST_Y(geom) AS lat,
                  'BASE_ROAD_NO' AS match_level
                FROM address_points_base
                WHERE road = %s
                  AND number_clean = %s
                LIMIT 1
                """,
                (road_used, number_clean),
            )
            r = cur.fetchone()
            if r and r["lon"] is not None and r["lat"] is not None:
                out = dict(r)
                if road_used != road:
                    out["match_level"] = f"{out['match_level']}_ALIAS"
                return out

    return None


def _fetch_txns(
    conn,
    lon: float,
    lat: float,
    radius_m: float,
    limit: int,
    date_from: Optional[str],
    date_to: Optional[str],
) -> List[Dict[str, Any]]:
    where_date = ""
    date_params: List[Any] = []

    if date_from:
        where_date += " AND trade_date >= %s"
        date_params.append(date_from)
    if date_to:
        where_date += " AND trade_date <= %s"
        date_params.append(date_to)

    sql = f"""
    SELECT
      id,
      trade_date::text AS trade_date,
      district,
      address_norm,
      total_price,
      unit_price_sqm::float AS unit_price_sqm,
      building_area_sqm::float AS building_area_sqm,
      land_area_sqm::float AS land_area_sqm,
      building_type,
      main_usage,
      floor_text,
      total_floors,
      year_built_ad,
      has_elevator,
      has_mgmt_org,
      center_source AS txn_center_source,
      ST_X(geom_center) AS lon,
      ST_Y(geom_center) AS lat,
      ST_Distance(
        geom_center::geography,
        ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
      ) AS distance_m
    FROM real_price_txn
    WHERE geom_center IS NOT NULL
      AND ST_DWithin(
        geom_center::geography,
        ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
        %s
      )
      {where_date}
    ORDER BY distance_m ASC
    LIMIT %s
    """

    params_final: List[Any] = [lon, lat, lon, lat, radius_m] + date_params + [limit]

    with conn.cursor() as cur:
        cur.execute(sql, params_final)
        return cur.fetchall()


def _fetch_summary(
    conn,
    lon: float,
    lat: float,
    radius_m: float,
    date_from: Optional[str],
    date_to: Optional[str],
) -> Dict[str, Any]:
    where_date = ""
    params: List[Any] = [lon, lat, radius_m]

    if date_from:
        where_date += " AND trade_date >= %s"
        params.append(date_from)
    if date_to:
        where_date += " AND trade_date <= %s"
        params.append(date_to)

    sql = f"""
    SELECT
      COUNT(*)::int AS n,
      percentile_cont(0.25) WITHIN GROUP (ORDER BY unit_price_sqm)::float AS p25,
      percentile_cont(0.50) WITHIN GROUP (ORDER BY unit_price_sqm)::float AS p50,
      percentile_cont(0.75) WITHIN GROUP (ORDER BY unit_price_sqm)::float AS p75
    FROM real_price_txn
    WHERE geom_center IS NOT NULL
      AND unit_price_sqm IS NOT NULL
      AND ST_DWithin(
        geom_center::geography,
        ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
        %s
      )
      {where_date}
    """

    params2: List[Any] = [lon, lat, radius_m]
    if date_from:
        params2.append(date_from)
    if date_to:
        params2.append(date_to)

    with conn.cursor() as cur:
        cur.execute(sql, params2)
        row = cur.fetchone()
        return row or {"n": 0, "p25": None, "p50": None, "p75": None}


def _fetch_coverage(conn) -> Dict[str, int]:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*)::int AS n FROM real_price_txn")
        total = cur.fetchone()["n"]

        cur.execute("SELECT COUNT(*)::int AS n FROM real_price_txn WHERE geom_center IS NOT NULL")
        loc = cur.fetchone()["n"]

        cur.execute(
            """
            SELECT COUNT(*)::int AS n
            FROM real_price_txn
            WHERE geom_center IS NULL
              AND address_norm LIKE '%地號%'
            """
        )
        landlot = cur.fetchone()["n"]

    return {
        "txns_total": total,
        "txns_locatable": loc,
        "excluded_landlot_no_geom": landlot,
    }


@router.get("/nearby_txn_by_address", response_model=NearbyTxnResponse)
def nearby_txn_by_address(
    address: str = Query(..., description="輸入地址（門牌）"),
    radius_m: float = Query(800.0, gt=0, le=20000, description="半徑（公尺）"),
    limit: int = Query(50, gt=0, le=200, description="最多回傳筆數"),
    date_from: Optional[str] = Query(None, description="起日（YYYY-MM-DD，可選）"),
    date_to: Optional[str] = Query(None, description="迄日（YYYY-MM-DD，可選）"),
):
    if not address.strip():
        raise HTTPException(status_code=400, detail="address is required")

    p = parse(address)

    road = p.road
    lane = p.lane
    alley = p.alley
    number_clean = p.no

    with get_conn() as conn:
        center_row = _center_from_address_points_base(conn, road, lane, alley, number_clean)
        if not center_row:
            cov = _fetch_coverage(conn)
            return NearbyTxnResponse(
                query_address=address,
                address_norm=p.canon,
                radius_m=radius_m,
                limit=limit,
                center=Center(source="NONE", lon=None, lat=None, match_level=None),
                items=[],
                summary=Summary(n=0, p25_unit_price_sqm=None, p50_unit_price_sqm=None, p75_unit_price_sqm=None),
                coverage=Coverage(**cov),
            )

        lon = float(center_row["lon"])
        lat = float(center_row["lat"])
        match_level = center_row["match_level"]

        items = _fetch_txns(conn, lon, lat, radius_m, limit, date_from, date_to)
        summ = _fetch_summary(conn, lon, lat, radius_m, date_from, date_to)
        cov = _fetch_coverage(conn)

        return NearbyTxnResponse(
            query_address=address,
            address_norm=p.canon,
            radius_m=radius_m,
            limit=limit,
            center=Center(source="ADDRESS_POINT", lon=lon, lat=lat, match_level=match_level),
            items=[TxnItem(**r) for r in items],
            summary=Summary(
                n=summ.get("n", 0),
                p25_unit_price_sqm=summ.get("p25"),
                p50_unit_price_sqm=summ.get("p50"),
                p75_unit_price_sqm=summ.get("p75"),
            ),
            coverage=Coverage(**cov),
        )