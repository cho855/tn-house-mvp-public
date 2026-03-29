from typing import Optional, List
import os

from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel
from psycopg import connect
from psycopg.rows import dict_row

router = APIRouter(tags=["spatial"])

DATABASE_URL = os.getenv("DATABASE_URL")


class NearbyItem(BaseModel):
    id: int
    permit_no: Optional[str] = None
    address_raw: str
    address_norm: Optional[str] = None
    floors_above: Optional[int] = None
    issue_date: Optional[str] = None
    lat: float
    lon: float
    distance_m: float
    location_source: str


class Coverage(BaseModel):
    locatable: int
    total: int


class NearbyResp(BaseModel):
    coverage: Coverage
    items: List[NearbyItem]


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")
    return connect(DATABASE_URL, row_factory=dict_row)


@router.get("/nearby", response_model=NearbyResp)
def nearby(
    lat: float = Query(..., ge=-90, le=90, description="緯度"),
    lon: float = Query(..., ge=-180, le=180, description="經度"),
    radius_m: int = Query(500, ge=50, le=5000, description="半徑(公尺)"),
    limit: int = Query(50, ge=1, le=200, description="回傳筆數上限"),
):
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  COUNT(*)::int AS total,
                  COUNT(*) FILTER (WHERE geom_section IS NOT NULL)::int AS locatable
                FROM public.use_permits;
                """
            )
            cov = cur.fetchone()
            if not cov:
                raise HTTPException(status_code=500, detail="coverage query failed")

            cur.execute(
                """
                WITH q AS (
                  SELECT ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography AS g
                )
                SELECT
                  u.id,
                  u.permit_no,
                  u.address_raw,
                  u.address_norm,
                  u.floors_above,
                  u.issue_date,
                  ST_Y(u.geom_section) AS lat,
                  ST_X(u.geom_section) AS lon,
                  ST_Distance(u.geom_section::geography, q.g) AS distance_m
                FROM public.use_permits u, q
                WHERE u.geom_section IS NOT NULL
                  AND ST_DWithin(u.geom_section::geography, q.g, %(radius_m)s)
                ORDER BY distance_m
                LIMIT %(limit)s;
                """,
                {"lat": lat, "lon": lon, "radius_m": radius_m, "limit": limit},
            )
            rows = cur.fetchall()

            items: List[NearbyItem] = []
            for r in rows:
                items.append(
                    NearbyItem(
                        id=r["id"],
                        permit_no=r.get("permit_no"),
                        address_raw=r["address_raw"],
                        address_norm=r.get("address_norm"),
                        floors_above=r.get("floors_above"),
                        issue_date=(str(r["issue_date"]) if r.get("issue_date") is not None else None),
                        lat=float(r["lat"]),
                        lon=float(r["lon"]),
                        distance_m=float(r["distance_m"]),
                        location_source="section_center",
                    )
                )

            return NearbyResp(
                coverage=Coverage(locatable=cov["locatable"], total=cov["total"]),
                items=items,
            )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))