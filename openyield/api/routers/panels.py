"""
api/routers/panels.py
----------------------
Author: Yeonkuk Woo

Panel and component endpoints.
"""

from __future__ import annotations
from typing import Annotated, Any
from fastapi import APIRouter, Depends, HTTPException, Query
from openyield.api.dependencies import get_db
from openyield.api.schemas import (
    PanelResponse, PanelListResponse, ComponentResponse
)
from openyield.db.connection import get_placeholder

router = APIRouter(prefix="/panels", tags=["panels"])
Connection = Any


@router.get("", response_model=PanelListResponse)
def list_panels(
    substrate_type: str | None = Query(None, description="Filter by substrate type"),
    page:  int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    conn:  Connection = Depends(get_db),
):
    ph = get_placeholder(conn)
    offset = (page - 1) * limit

    if substrate_type:
        total = conn.execute(
            f"SELECT COUNT(*) FROM panels WHERE substrate_type={ph}",
            (substrate_type,)
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM panels WHERE substrate_type={ph} "
            f"ORDER BY created_at DESC LIMIT {ph} OFFSET {ph}",
            (substrate_type, limit, offset)
        ).fetchall()
    else:
        total = conn.execute("SELECT COUNT(*) FROM panels").fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM panels ORDER BY created_at DESC "
            f"LIMIT {ph} OFFSET {ph}",
            (limit, offset)
        ).fetchall()

    return PanelListResponse(
        total=total, page=page, limit=limit,
        results=[PanelResponse(**dict(r)) for r in rows]
    )


@router.get("/{panel_id}", response_model=PanelResponse)
def get_panel(panel_id: str, conn: Connection = Depends(get_db)):
    ph = get_placeholder(conn)
    row = conn.execute(
        f"SELECT * FROM panels WHERE panel_id={ph}", (panel_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Panel '{panel_id}' not found")
    return PanelResponse(**dict(row))


@router.get("/{panel_id}/components", response_model=list[ComponentResponse])
def get_components(panel_id: str, conn: Connection = Depends(get_db)):
    ph = get_placeholder(conn)
    # verify panel exists
    if not conn.execute(
        f"SELECT 1 FROM panels WHERE panel_id={ph}", (panel_id,)
    ).fetchone():
        raise HTTPException(status_code=404, detail=f"Panel '{panel_id}' not found")

    rows = conn.execute(
        f"SELECT * FROM components WHERE panel_id={ph} "
        f"ORDER BY component_row, component_col",
        (panel_id,)
    ).fetchall()
    return [ComponentResponse(
        **{**dict(r), "active": bool(r["active"])}
    ) for r in rows]
