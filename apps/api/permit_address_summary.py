from __future__ import annotations

import os
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from psycopg import connect
from psycopg.rows import dict_row

router = APIRouter(tags=["permit-address"])

DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("DB_DSN")


class TopAddressCandidate(BaseModel):
    rank: int
    addr_display: str
    hit_count: int
    max_score: Optional[float] = None


class PermitAddressSummaryResponse(BaseModel):
    permit_no: str
    best_address: Optional[str] = None
    best_hit_count: Optional[int] = None
    best_max_score: Optional[float] = None
    candidate_count: int
    top_addresses: List[TopAddressCandidate]


@router.get("/permit_address_summary", response_model=PermitAddressSummaryResponse)
def get_permit_address_summary(
    permit_no: str = Query(..., description="使用執照號碼"),
) -> PermitAddressSummaryResponse:
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="DATABASE_URL / DB_DSN not set")

    summary_sql = """
        SELECT
            permit_no,
            addr_display,
            hit_count,
            max_score,
            rnk
        FROM permit_address_summary_top3
        WHERE permit_no = %s
        ORDER BY rnk ASC
    """

    with connect(DATABASE_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(summary_sql, (permit_no,))
            rows = cur.fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail=f"permit_no not found: {permit_no}")

    top_addresses = [
        TopAddressCandidate(
            rank=int(row["rnk"]),
            addr_display=row["addr_display"],
            hit_count=int(row["hit_count"]),
            max_score=float(row["max_score"]) if row["max_score"] is not None else None,
        )
        for row in rows
    ]

    best = top_addresses[0]

    return PermitAddressSummaryResponse(
        permit_no=permit_no,
        best_address=best.addr_display,
        best_hit_count=best.hit_count,
        best_max_score=best.max_score,
        candidate_count=len(top_addresses),
        top_addresses=top_addresses,
    )

class PermitMapCandidate(BaseModel):
    addr_display: str
    distance_m: Optional[float] = None
    total_score: Optional[float] = None
    confidence_level: Optional[str] = None
    source_rule: Optional[str] = None
    lat: float
    lon: float


class PermitAddressCandidatesMapResponse(BaseModel):
    permit_no: str
    items: List[PermitMapCandidate]


@router.get("/permit_address_candidates_map", response_model=PermitAddressCandidatesMapResponse)
def get_permit_address_candidates_map(
    permit_no: str = Query(..., description="使用執照號碼"),
    limit: int = Query(50, ge=1, le=200),
) -> PermitAddressCandidatesMapResponse:
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="DATABASE_URL / DB_DSN not set")

    sql = """
        SELECT
            permit_no,
            addr_display,
            distance_m,
            total_score,
            confidence_level,
            source_rule,
            lat,
            lon
        FROM v_permit_address_candidates_map
        WHERE permit_no = %s
        ORDER BY total_score DESC NULLS LAST, distance_m ASC NULLS LAST
        LIMIT %s
    """

    with connect(DATABASE_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (permit_no, limit))
            rows = cur.fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail=f"permit_no not found: {permit_no}")

    items = [
        PermitMapCandidate(
            addr_display=row["addr_display"],
            distance_m=float(row["distance_m"]) if row["distance_m"] is not None else None,
            total_score=float(row["total_score"]) if row["total_score"] is not None else None,
            confidence_level=row.get("confidence_level"),
            source_rule=row.get("source_rule"),
            lat=float(row["lat"]),
            lon=float(row["lon"]),
        )
        for row in rows
    ]

    return PermitAddressCandidatesMapResponse(
        permit_no=permit_no,
        items=items,
    )

class PermitMapSummaryCandidate(BaseModel):
    addr_display: str
    hit_count: int
    min_distance_m: Optional[float] = None
    avg_distance_m: Optional[float] = None
    max_score: Optional[float] = None
    confidence_level: Optional[str] = None
    source_rule: Optional[str] = None
    lat: float
    lon: float


class PermitAddressCandidatesMapSummaryResponse(BaseModel):
    permit_no: str
    items: List[PermitMapSummaryCandidate]


@router.get(
    "/permit_address_candidates_map_summary",
    response_model=PermitAddressCandidatesMapSummaryResponse
)
def get_permit_address_candidates_map_summary(
    permit_no: str = Query(..., description="使用執照號碼"),
    limit: int = Query(10, ge=1, le=50),
) -> PermitAddressCandidatesMapSummaryResponse:
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="DATABASE_URL / DB_DSN not set")

    sql = """
        SELECT
            permit_no,
            addr_display,
            hit_count,
            min_distance_m,
            avg_distance_m,
            max_score,
            confidence_level,
            source_rule,
            lat,
            lon
        FROM v_permit_address_candidates_map_summary
        WHERE permit_no = %s
        ORDER BY hit_count DESC, max_score DESC, min_distance_m ASC
        LIMIT %s
    """

    with connect(DATABASE_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (permit_no, limit))
            rows = cur.fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail=f"permit_no not found: {permit_no}")

    items = [
        PermitMapSummaryCandidate(
            addr_display=row["addr_display"],
            hit_count=int(row["hit_count"]),
            min_distance_m=float(row["min_distance_m"]) if row["min_distance_m"] is not None else None,
            avg_distance_m=float(row["avg_distance_m"]) if row["avg_distance_m"] is not None else None,
            max_score=float(row["max_score"]) if row["max_score"] is not None else None,
            confidence_level=row.get("confidence_level"),
            source_rule=row.get("source_rule"),
            lat=float(row["lat"]),
            lon=float(row["lon"]),
        )
        for row in rows
    ]

    return PermitAddressCandidatesMapSummaryResponse(
        permit_no=permit_no,
        items=items,
    )