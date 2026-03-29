from fastapi import APIRouter, Query
from typing import Optional, List, Dict, Any
from db import get_conn

router = APIRouter()


@router.get("/nearby_poi")
def nearby_poi(
    lat: float = Query(...),
    lon: float = Query(...),
    radius_m: float = Query(1000),
    category: Optional[str] = Query(None),
    limit: int = Query(100),
):
    conn = get_conn()

    with conn.cursor() as cur:
        if category:
            sql = """
            SELECT
                id,
                source_table,
                source_id,
                category,
                subtype,
                name,
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
                ) AS dist_m,
                extra
            FROM poi
            WHERE geom IS NOT NULL
              AND category = %s
              AND ST_DWithin(
                    geom::geography,
                    ST_SetSRID(ST_Point(%s,%s),4326)::geography,
                    %s
              )
            ORDER BY dist_m
            LIMIT %s
            """
            cur.execute(sql, (lon, lat, category, lon, lat, radius_m, limit))
        else:
            sql = """
            SELECT
                id,
                source_table,
                source_id,
                category,
                subtype,
                name,
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
                ) AS dist_m,
                extra
            FROM poi
            WHERE geom IS NOT NULL
              AND ST_DWithin(
                    geom::geography,
                    ST_SetSRID(ST_Point(%s,%s),4326)::geography,
                    %s
              )
            ORDER BY dist_m
            LIMIT %s
            """
            cur.execute(sql, (lon, lat, lon, lat, radius_m, limit))

        rows = cur.fetchall()

    items: List[Dict[str, Any]] = []

    for r in rows:
        items.append({
            "id": r["id"],
            "source_table": r["source_table"],
            "source_id": r["source_id"],
            "category": r["category"],
            "subtype": r["subtype"],
            "name": r["name"],
            "district": r["district"],
            "addr_raw": r["addr_raw"],
            "address_norm": r["address_norm"],
            "center_source": r["center_source"],
            "match_level": r["match_level"],
            "lon": float(r["lon"]) if r["lon"] is not None else None,
            "lat": float(r["lat"]) if r["lat"] is not None else None,
            "dist_m": float(r["dist_m"]) if r["dist_m"] is not None else None,
            "extra": r["extra"],
        })

    return {
        "center": {
            "lat": lat,
            "lon": lon
        },
        "radius_m": radius_m,
        "limit": limit,
        "count": len(items),
        "items": items
    }