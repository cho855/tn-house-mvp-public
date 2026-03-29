import os
import re
import psycopg
from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from psycopg.rows import dict_row
from typing import Optional
from datetime import date as dt_date
from fastapi.middleware.cors import CORSMiddleware

from db import get_conn
from address import normalize_address
from routers.nearby import router
from routers.nearby_by_address import router as nearby_by_address_router
from routers.nearby_txn_by_address import router as nearby_txn_router
from routers.nearby_schools import router as nearby_schools_router
from routers.nearby_poi import router as nearby_poi_router
from routers.nearby_presale import router as nearby_presale_router
from permit_address_summary import router as permit_address_router
from backfill_presale_intersections import run_backfill, clean_landlot_section, load_landlot_section_centroids


DB_DSN = os.getenv(
    "POSTGRES_DSN",
    "postgresql://postgres:postgres@db:5432/tn_house"
)

app = FastAPI(title="Tainan Used House MVP", version="0.1.0-dev (apps/api)")
app.include_router(router)
app.include_router(nearby_by_address_router)
app.include_router(nearby_txn_router)
app.include_router(nearby_schools_router)
app.include_router(nearby_poi_router)
app.include_router(nearby_presale_router)
app.include_router(permit_address_router)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/__admin/presale/backfill")
def admin_presale_backfill():
    return run_backfill()


@app.get("/__admin/presale/summary")
def admin_presale_summary():
    section_only_remaining = 0
    section_only_matchable = 0
    centroids = load_landlot_section_centroids()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS cnt FROM presale_projects WHERE geom_center IS NOT NULL")
            located = cur.fetchone()["cnt"]
            cur.execute("SELECT count(*) AS cnt FROM presale_projects WHERE geom_center IS NULL")
            unlocated = cur.fetchone()["cnt"]
            cur.execute("SELECT district, road FROM presale_projects WHERE geom_center IS NULL AND road IS NOT NULL")
            for district, road in cur.fetchall():
                road = (road or '').strip()
                if road.endswith('段') and '地號' not in road and not any(tok in road for tok in ('路', '街', '大道', '縣道', '巷', '弄', '號')):
                    section_only_remaining += 1
                    section = clean_landlot_section(road, district)
                    if (district, section) in centroids:
                        section_only_matchable += 1
    return {
        'located': located,
        'unlocated': unlocated,
        'section_only_remaining': section_only_remaining,
        'section_only_matchable': section_only_matchable,
    }


_NEARBY_HINT_RE = re.compile(r'附近|旁|對面|斜對面|隔壁|路面')
_INTERSECTION_HINT_RE = re.compile(r'與|及|和|、|/|VS|vs|路口|街口|巷口|交叉路口|交叉口|交接口|交會處|交叉')
_LANDLOT_EXACT_RE = re.compile(r'[^\d，、,\s]+(?:小段)?段.*地號')
_LANDLOT_SECTION_ONLY_RE = re.compile(r'^[^\d，、,\s]+(?:小段)?段$')
_ROADISH_RE = re.compile(r'路|街|大道|縣道')
_PLACEISH_RE = re.compile(r'里|庄|寮|腳|社內|寮|崙子頂|牛庄|胡厝寮|三甲子|海寮|東勢寮|後壁')


def classify_presale_unlocated_road(road: str) -> str:
    road = (road or '').strip()
    if not road:
        return 'empty'
    if _LANDLOT_EXACT_RE.search(road):
        return 'landlot_exact'
    if _LANDLOT_SECTION_ONLY_RE.fullmatch(road):
        return 'landlot_section_only'
    if _INTERSECTION_HINT_RE.search(road):
        return 'intersection_like'
    if _NEARBY_HINT_RE.search(road):
        return 'nearby_desc'
    if _ROADISH_RE.search(road):
        return 'roadish_other'
    if _PLACEISH_RE.search(road):
        return 'other_place'
    return 'other'


@app.get("/__admin/presale/classify")
def admin_presale_classify(limit_per_group: int = 5):
    counts = {}
    samples = {}
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, district, project_name, road FROM presale_projects WHERE geom_center IS NULL ORDER BY id")
            for row in cur.fetchall():
                category = classify_presale_unlocated_road(row['road'])
                counts[category] = counts.get(category, 0) + 1
                bucket = samples.setdefault(category, [])
                if len(bucket) < limit_per_group:
                    bucket.append({
                        'id': row['id'],
                        'district': row['district'],
                        'project_name': row['project_name'],
                        'road': row['road'],
                    })
    ordered_counts = dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
    ordered_samples = {k: samples[k] for k in ordered_counts.keys()}
    return {
        'total_unlocated': sum(ordered_counts.values()),
        'counts': ordered_counts,
        'samples': ordered_samples,
    }


def roc_to_ad(s):
    if s is None:
        return None

    if isinstance(s, dt_date):
        y, m, d = s.year, s.month, s.day
        if y < 1911:
            y += 1911
        return f"{y:04d}-{m:02d}-{d:02d}"

    try:
        s = str(s)
        y, m, d = s.split("-")
        y = int(y)
        if y < 1911:
            y += 1911
        return f"{y:04d}-{int(m):02d}-{int(d):02d}"
    except Exception:
        return str(s)


def normalize_query_address(s: str) -> str:
    s = normalize_address(s)
    return s.replace("臺", "台")


class SearchItem(BaseModel):
    id: int
    address_raw: str
    address_norm: str
    issue_date: str | None


class PropertyResp(BaseModel):
    id: int
    permit_no: str | None
    address_raw: str
    address_norm: str
    issue_date: str | None
    start_date: str | None
    floors_above: int | None
    floors_below: int | None
    height_m: float | None
    usage: str | None
    units: int | None
    lat: float | None
    lon: float | None


class NearbyResp(BaseModel):
    radius_m: int
    counts: dict[str, int]


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/use-permits")
def query_use_permits(
    address: str = Query(..., min_length=2),
    limit: int = Query(20, ge=1, le=200),
):
    q = normalize_query_address(address)

    sql = """
    SELECT
      permit_no,
      issue_date,
      floors_above,
      usage,
      units
    FROM use_permits
    WHERE address_norm ILIKE %(q)s
    ORDER BY issue_date DESC NULLS LAST
    LIMIT %(limit)s
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, {"q": f"%{q}%", "limit": limit})
            rows = cur.fetchall()

    for r in rows:
        r["issue_date"] = roc_to_ad(r.get("issue_date"))

    return {
        "ok": True,
        "query": {"address": address, "address_norm": q, "limit": limit},
        "count": len(rows),
        "data": rows,
    }


@app.get("/search", response_model=list[SearchItem])
def search(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=50),
):
    qn = normalize_query_address(q)
    if not qn:
        return []

    sql = """
      SELECT id, address_raw, address_norm, issue_date
      FROM use_permits
      WHERE address_norm ILIKE %s
      ORDER BY similarity(address_norm, %s) DESC, issue_date DESC NULLS LAST
      LIMIT %s
    """

    pattern = f"%{qn}%"

    with get_conn() as conn:
        conn.row_factory = dict_row
        with conn.cursor() as cur:
            cur.execute(sql, (pattern, qn, limit))
            rows = cur.fetchall()

    for r in rows:
        r["issue_date"] = roc_to_ad(r.get("issue_date"))

    return rows