"""
api/routers/defects.py
-----------------------
Author: Yeonkuk Woo

Defect query endpoints.
"""

from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query
from openyield.api.dependencies import get_db
from openyield.api.schemas import DefectResponse, DefectListResponse
from openyield.db.connection import get_placeholder

router = APIRouter(prefix="/defects", tags=["defects"])
Connection = Any


@router.get("", response_model=DefectListResponse)
def list_defects(
    panel_id:      str | None = Query(None),
    source_system: str | None = Query(None, description="system_a or system_b"),
    defect_type:   str | None = Query(None),
    matched_only:  bool       = Query(False, description="Only return matched defects"),
    page:          int        = Query(1, ge=1),
    limit:         int        = Query(50, ge=1, le=500),
    conn:          Connection = Depends(get_db),
):
    ph = get_placeholder(conn)
    filters, params = [], []

    if panel_id:
        filters.append(f"panel_id={ph}"); params.append(panel_id)
    if source_system:
        filters.append(f"source_system={ph}"); params.append(source_system)
    if defect_type:
        filters.append(f"defect_type={ph}"); params.append(defect_type)
    if matched_only:
        filters.append("match_id IS NOT NULL AND match_id != ''")

    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    offset = (page - 1) * limit

    total = conn.execute(
        f"SELECT COUNT(*) FROM defects {where}", params
    ).fetchone()[0]

    rows = conn.execute(
        f"SELECT * FROM defects {where} "
        f"ORDER BY defect_id DESC LIMIT {ph} OFFSET {ph}",
        params + [limit, offset]
    ).fetchall()

    return DefectListResponse(
        total=total, page=page, limit=limit,
        results=[DefectResponse(**dict(r)) for r in rows]
    )
