"""
api/routers/yield_router.py
----------------------------
Author: Yeonkuk Woo

Yield calculation endpoints.
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from openyield.api.dependencies import get_db
from openyield.api.schemas import YieldResponse, CriticalAreaResponse
from openyield.db.connection import get_placeholder
from openyield.yield_engine.calculator import (
    calculate_panel_yield, calculate_all_yields
)
from openyield.yield_engine.critical_area import compute_panel_critical_area
from openyield.synthetic.substrate_profiles import get_profile

router = APIRouter(prefix="/yield", tags=["yield"])
Connection = Any


def _row_to_yield_response(row: dict) -> YieldResponse:
    return YieldResponse(
        panel_id=row["panel_id"],
        substrate_type=row["substrate_type"],
        calculated_at=str(row["calculated_at"]),
        die_area_mm2=row["die_area_mm2"],
        inspected_dies=row["inspected_dies"],
        defect_count=row["defect_count"],
        defect_density=row["defect_density"],
        yield_poisson=row["yield_poisson"],
        yield_murphy=row["yield_murphy"],
        yield_negbinom=row["yield_negbinom"],
        clustering_alpha=row["clustering_alpha"],
        alpha_method=row["alpha_method"],
        recommended_model=row.get("recommended_model", ""),
        model_notes=row.get("model_notes", ""),
    )


@router.get("/{panel_id}", response_model=YieldResponse)
def get_yield(panel_id: str, conn: Connection = Depends(get_db)):
    """Return the most recent yield estimate for a panel."""
    ph = get_placeholder(conn)
    row = conn.execute(
        f"SELECT * FROM yield_estimates WHERE panel_id={ph} "
        f"ORDER BY calculated_at DESC LIMIT 1",
        (panel_id,)
    ).fetchone()

    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"No yield estimate found for '{panel_id}'. "
                   f"POST /yield/{panel_id}/calculate to compute it."
        )
    return _row_to_yield_response(dict(row))


@router.post("/{panel_id}/calculate", response_model=YieldResponse)
def calculate_yield(panel_id: str, conn: Connection = Depends(get_db)):
    """Trigger yield calculation for a panel and persist the result."""
    ph = get_placeholder(conn)
    if not conn.execute(
        f"SELECT 1 FROM panels WHERE panel_id={ph}", (panel_id,)
    ).fetchone():
        raise HTTPException(status_code=404, detail=f"Panel '{panel_id}' not found")

    try:
        est = calculate_panel_yield(conn, panel_id, persist=True)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return YieldResponse(
        panel_id=est.panel_id,
        substrate_type=est.substrate_type,
        calculated_at=datetime.now(timezone.utc).isoformat(),
        die_area_mm2=est.die_area_mm2,
        inspected_dies=est.inspected_dies,
        defect_count=est.defect_count,
        defect_density=est.defect_density,
        yield_poisson=est.yield_poisson,
        yield_murphy=est.yield_murphy,
        yield_negbinom=est.yield_negbinom,
        clustering_alpha=est.clustering_alpha,
        alpha_method=est.alpha_method,
        recommended_model=est.recommended_model,
        model_notes=est.model_notes,
    )


@router.get("/{panel_id}/critical-area", response_model=CriticalAreaResponse)
def get_critical_area(panel_id: str, conn: Connection = Depends(get_db)):
    """
    Compute critical area fraction for a panel using the Maly linear expansion model.

    The critical area fraction quantifies what fraction of die area is sensitive
    to defects. Yield models should use A_eff = ca_fraction × full_die_area
    instead of the full die area, particularly at advanced process nodes where
    significant die area is non-critical (power straps, filler cells, decaps).
    """
    ph = get_placeholder(conn)
    panel_row = conn.execute(
        f"SELECT * FROM panels WHERE panel_id = {ph}", (panel_id,)
    ).fetchone()
    if panel_row is None:
        raise HTTPException(status_code=404, detail=f"Panel '{panel_id}' not found")

    panel = dict(panel_row)
    profile = get_profile(panel["substrate_type"])
    full_die_area = profile.component_pitch_mm ** 2

    try:
        result = compute_panel_critical_area(
            conn, panel_id,
            layout_density=profile.layout_density,
            min_feature_mm=profile.min_feature_mm,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return CriticalAreaResponse(
        panel_id=panel_id,
        ca_fraction=result.ca_fraction,
        layout_density=result.layout_density,
        min_feature_mm=result.min_feature_mm,
        effective_area_mm2=round(result.ca_fraction * full_die_area, 4),
        full_die_area_mm2=full_die_area,
        n_defects=result.n_defects,
        mean_defect_size_mm=result.mean_defect_size_mm,
        method=result.method,
    )


@router.post("/calculate/all", response_model=list[YieldResponse])
def calculate_all(
    substrate_type: str | None = None,
    conn: Connection = Depends(get_db),
):
    """Trigger yield calculation for all panels."""
    estimates = calculate_all_yields(conn, substrate_type=substrate_type, persist=True)
    return [
        YieldResponse(
            panel_id=e.panel_id,
            substrate_type=e.substrate_type,
            calculated_at=datetime.now(timezone.utc).isoformat(),
            die_area_mm2=e.die_area_mm2,
            inspected_dies=e.inspected_dies,
            defect_count=e.defect_count,
            defect_density=e.defect_density,
            yield_poisson=e.yield_poisson,
            yield_murphy=e.yield_murphy,
            yield_negbinom=e.yield_negbinom,
            clustering_alpha=e.clustering_alpha,
            alpha_method=e.alpha_method,
            recommended_model=e.recommended_model,
            model_notes=e.model_notes,
        )
        for e in estimates
    ]
