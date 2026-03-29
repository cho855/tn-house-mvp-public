from fastapi import APIRouter, Query
from typing import List, Dict, Any
from db import get_conn

router = APIRouter()


@router.get("/nearby_schools")
def nearby_schools(
    lat: float = Query(...),
    lon: float = Query(...),
    radius_m: float = Query(1000),
    limit: int = Query(200),
):
    sql = """
    SELECT
      edu_code,
      school_name,
      stage,
      school_type,
      district,
      addr_raw,
      address_norm,
      center_source,
      match_level,
      ST_X(geom) AS lon,
      ST_Y(geom) AS lat,
      ST_Distance(
        geom::geography,
        ST_SetSRID(ST_Point(%s,%s),4326)::geography
      ) AS dist_m
    FROM schools
    WHERE geom IS NOT NULL
      AND ST_DWithin(
        geom::geography,
        ST_SetSRID(ST_Point(%s,%s),4326)::geography,
        %s
      )
    ORDER BY dist_m
    LIMIT %s;
    """

    items: List[Dict[str, Any]] = []

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (lon, lat, lon, lat, radius_m, limit))
            rows = cur.fetchall()

            for r in rows:
                items.append(
                    {                        
                        "edu_code": r["edu_code"],
                        "school_name": r["school_name"],
                        "stage": r["stage"],
                        "school_type": r["school_type"],
                        "district": r["district"],
                        "addr_raw": r["addr_raw"],
                        "address_norm": r["address_norm"],
                        "center_source": r["center_source"],
                        "match_level": r["match_level"],
                        "lon": float(r["lon"]),
                        "lat": float(r["lat"]),
                        "dist_m": float(r["dist_m"]),
                    }
                )

    return {
        "center": {"lat": lat, "lon": lon},
        "radius_m": radius_m,
        "limit": limit,
        "items": items,
    }