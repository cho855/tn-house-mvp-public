from fastapi import APIRouter, Query
from typing import List, Dict, Any

from db import get_conn

router = APIRouter()

PROJECT_KEY_SQL = "lower(replace(replace(replace(replace(replace(trim(p.project_name), ' ', ''), '　', ''), '．', '.'), '‧', '.'), '・', '.'))"
PRICE_SUMMARY_LATERAL = f"""
LEFT JOIN LATERAL (
    SELECT
        s.txn_count,
        s.latest_trade_date,
        s.latest_total_price,
        s.latest_unit_price_sqm,
        s.avg_total_price,
        s.avg_unit_price_sqm,
        s.min_unit_price_sqm,
        s.max_unit_price_sqm,
        s.district AS price_district,
        s.build_case AS price_build_case
    FROM presale_price_summary s
    WHERE s.project_key = COALESCE(p.project_key, {PROJECT_KEY_SQL})
    ORDER BY CASE WHEN s.district = p.district THEN 0 ELSE 1 END, s.txn_count DESC, s.updated_at DESC
    LIMIT 1
) price ON true
"""


def row_to_item(r):
    price_summary = None
    if r['price_txn_count'] is not None:
        price_summary = {
            'txn_count': int(r['price_txn_count']),
            'latest_trade_date': r['price_latest_trade_date'],
            'latest_total_price': int(r['price_latest_total_price']) if r['price_latest_total_price'] is not None else None,
            'latest_unit_price_sqm': float(r['price_latest_unit_price_sqm']) if r['price_latest_unit_price_sqm'] is not None else None,
            'avg_total_price': float(r['price_avg_total_price']) if r['price_avg_total_price'] is not None else None,
            'avg_unit_price_sqm': float(r['price_avg_unit_price_sqm']) if r['price_avg_unit_price_sqm'] is not None else None,
            'min_unit_price_sqm': float(r['price_min_unit_price_sqm']) if r['price_min_unit_price_sqm'] is not None else None,
            'max_unit_price_sqm': float(r['price_max_unit_price_sqm']) if r['price_max_unit_price_sqm'] is not None else None,
            'matched_district': r['price_district'],
            'matched_build_case': r['price_build_case'],
        }

    return {
        'id': r['id'],
        'source_table': r['source_table'],
        'source_id': r['source_id'],
        'district': r['district'],
        'project_name': r['project_name'],
        'road': r['road'],
        'builder': r['builder'],
        'household': r['household'],
        'use_zoning': r['use_zoning'],
        'main_use': r['main_use'],
        'main_material': r['main_material'],
        'declare_date': r['declare_date'],
        'selling_period': r['selling_period'],
        'building_lands': r['building_lands'],
        'building_permit_date': r['building_permit_date'],
        'building_permit_no': r['building_permit_no'],
        'first_registration_date': r['first_registration_date'],
        'address_seed': r['address_seed'],
        'address_norm': r['address_norm'],
        'center_source': r['center_source'],
        'match_level': r['match_level'],
        'lon': float(r['lon']) if r['lon'] is not None else None,
        'lat': float(r['lat']) if r['lat'] is not None else None,
        'dist_m': float(r['dist_m']) if r['dist_m'] is not None else None,
        'distance_m': float(r['dist_m']) if r['dist_m'] is not None else None,
        'extra': r['extra'],
        'price_summary': price_summary,
    }


PRICE_SELECT = """
    price.txn_count AS price_txn_count,
    price.latest_trade_date AS price_latest_trade_date,
    price.latest_total_price AS price_latest_total_price,
    price.latest_unit_price_sqm AS price_latest_unit_price_sqm,
    price.avg_total_price AS price_avg_total_price,
    price.avg_unit_price_sqm AS price_avg_unit_price_sqm,
    price.min_unit_price_sqm AS price_min_unit_price_sqm,
    price.max_unit_price_sqm AS price_max_unit_price_sqm,
    price.price_district AS price_district,
    price.price_build_case AS price_build_case,
"""


@router.get('/nearby_presale')
def nearby_presale(
    lat: float = Query(...),
    lon: float = Query(...),
    radius_m: float = Query(1000),
    limit: int = Query(100),
):
    conn = get_conn()

    with conn.cursor() as cur:
        cur.execute(
            f'''
            SELECT
                p.id,
                p.source_table,
                p.source_id,
                p.district,
                p.project_name,
                p.road,
                p.builder,
                p.household,
                p.use_zoning,
                p.main_use,
                p.main_material,
                p.declare_date,
                p.selling_period,
                p.building_lands,
                p.building_permit_date,
                p.building_permit_no,
                p.first_registration_date,
                p.address_seed,
                p.address_norm,
                p.center_source,
                p.match_level,
                ST_X(p.geom_center) AS lon,
                ST_Y(p.geom_center) AS lat,
                ST_Distance(
                    p.geom_center::geography,
                    ST_SetSRID(ST_Point(%s,%s),4326)::geography
                ) AS dist_m,
                p.extra,
                {PRICE_SELECT}
                p.project_key
            FROM presale_projects p
            {PRICE_SUMMARY_LATERAL}
            WHERE p.geom_center IS NOT NULL
              AND ST_DWithin(
                    p.geom_center::geography,
                    ST_SetSRID(ST_Point(%s,%s),4326)::geography,
                    %s
              )
            ORDER BY dist_m, p.id
            LIMIT %s
            ''',
            (lon, lat, lon, lat, radius_m, limit),
        )
        rows = cur.fetchall()

    items: List[Dict[str, Any]] = [row_to_item(r) for r in rows]
    return {
        'center': {'lat': lat, 'lon': lon},
        'radius_m': radius_m,
        'limit': limit,
        'count': len(items),
        'items': items,
    }


@router.get('/presale_options')
def presale_options(
    district: str = Query('', description='Optional district filter for project list'),
):
    district = (district or '').strip()
    conn = get_conn()

    with conn.cursor() as cur:
        cur.execute(
            '''
            SELECT DISTINCT district
            FROM presale_projects
            WHERE geom_center IS NOT NULL
              AND district IS NOT NULL
              AND district <> ''
            ORDER BY district
            '''
        )
        districts = [r['district'] for r in cur.fetchall()]

        if district:
            cur.execute(
                '''
                SELECT DISTINCT project_name
                FROM presale_projects
                WHERE geom_center IS NOT NULL
                  AND district = %s
                  AND project_name IS NOT NULL
                  AND project_name <> ''
                ORDER BY project_name
                ''',
                (district,),
            )
        else:
            cur.execute(
                '''
                SELECT DISTINCT project_name
                FROM presale_projects
                WHERE geom_center IS NOT NULL
                  AND project_name IS NOT NULL
                  AND project_name <> ''
                ORDER BY project_name
                LIMIT 500
                '''
            )
        projects = [r['project_name'] for r in cur.fetchall()]

    return {
        'districts': districts,
        'projects': projects,
    }


@router.get('/presale_search')
def presale_search(
    district: str = Query(''),
    project_name: str = Query(''),
    limit: int = Query(100),
):
    district = (district or '').strip()
    project_name = (project_name or '').strip()
    conn = get_conn()

    sql = f'''
        SELECT
            p.id,
            p.source_table,
            p.source_id,
            p.district,
            p.project_name,
            p.road,
            p.builder,
            p.household,
            p.use_zoning,
            p.main_use,
            p.main_material,
            p.declare_date,
            p.selling_period,
            p.building_lands,
            p.building_permit_date,
            p.building_permit_no,
            p.first_registration_date,
            p.address_seed,
            p.address_norm,
            p.center_source,
            p.match_level,
            ST_X(p.geom_center) AS lon,
            ST_Y(p.geom_center) AS lat,
            NULL::double precision AS dist_m,
            p.extra,
            {PRICE_SELECT}
            p.project_key
        FROM presale_projects p
        {PRICE_SUMMARY_LATERAL}
        WHERE p.geom_center IS NOT NULL
    '''
    params = []
    if district:
        sql += ' AND p.district = %s'
        params.append(district)
    if project_name:
        sql += ' AND p.project_name = %s'
        params.append(project_name)
    sql += ' ORDER BY p.district, p.project_name, p.id LIMIT %s'
    params.append(limit)

    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    items: List[Dict[str, Any]] = [row_to_item(r) for r in rows]
    return {
        'district': district,
        'project_name': project_name,
        'limit': limit,
        'count': len(items),
        'items': items,
    }
