"""
api/routers/spatial_router.py
-------------------------------
Author: Yeonkuk Woo

Spatial yield prediction endpoint.

Registered in main.py as:
    from openyield.api.routers import spatial_router
    app.include_router(spatial_router.router)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from openyield.api.dependencies import get_db
from openyield.yield_engine.spatial_predictor import compute_spatial_yield

router = APIRouter(prefix="/yield", tags=["yield"])
Connection = Any


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class DieYieldResponse(BaseModel):
    """Per-die spatial yield contribution."""
    row:            int
    col:            int
    defect_count:   int
    d0:             float  = Field(description="Local defect density (defects/mm²)")
    yield_poisson:  float
    yield_murphy:   float
    yield_negbinom: float
    active:         bool


class SpatialYieldResponse(BaseModel):
    """
    Spatial yield prediction for a single panel.

    Attributes
    ----------
    spatial_yield_*   : Die-averaged yield — accounts for within-substrate
                        density non-uniformity.
    global_yield_*    : Yield from global D0 (same as calculator.py output).
    cv_d0             : Coefficient of variation of per-die D0.
                        0.0 = perfectly uniform; spatial yield = global yield.
                        > 0 = non-uniform; spatial > global (Jensen's inequality).
    yield_gain_*      : Y_spatial − Y_global. Positive = global model underestimated.
    """
    panel_id:       str
    substrate_type: str

    spatial_yield_poisson:  float
    spatial_yield_murphy:   float
    spatial_yield_negbinom: float

    global_yield_poisson:   float
    global_yield_murphy:    float
    global_yield_negbinom:  float

    mean_d0: float = Field(description="Mean per-die defect density (defects/mm²)")
    std_d0:  float = Field(description="Std dev of per-die defect density")
    cv_d0:   float = Field(description="Coefficient of variation of D0 (0 = uniform)")

    yield_gain_poisson:  float = Field(description="Y_spatial − Y_global (Poisson)")
    yield_gain_negbinom: float = Field(description="Y_spatial − Y_global (NegBinom)")

    n_active_dies:          int
    die_area_mm2:           float
    critical_area_fraction: float | None

    die_yields: list[DieYieldResponse]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.get("/{panel_id}/spatial", response_model=SpatialYieldResponse)
def get_spatial_yield(panel_id: str, conn: Connection = Depends(get_db)):
    """
    Compute spatial yield prediction for a panel.

    Disaggregates defect density to the per-die level and averages yield
    estimates over all active dies. More accurate than the global yield model
    when defect density is non-uniform across the substrate.

    **cv_d0** (coefficient of variation of per-die D0) quantifies non-uniformity:
    - `cv_d0 = 0.0` → perfectly uniform substrate; spatial yield equals global yield.
    - `cv_d0 > 0.5` → significant spatial variation; yield gain can exceed 5–15%.

    **yield_gain** shows how much yield is underestimated by the global model.
    By Jensen's inequality for convex yield functions, yield_gain ≥ 0 always.

    Returns per-die breakdown in `die_yields` for heatmap rendering.
    """
    try:
        result = compute_spatial_yield(conn, panel_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return SpatialYieldResponse(
        panel_id=result.panel_id,
        substrate_type=result.substrate_type,
        spatial_yield_poisson=result.spatial_yield_poisson,
        spatial_yield_murphy=result.spatial_yield_murphy,
        spatial_yield_negbinom=result.spatial_yield_negbinom,
        global_yield_poisson=result.global_yield_poisson,
        global_yield_murphy=result.global_yield_murphy,
        global_yield_negbinom=result.global_yield_negbinom,
        mean_d0=result.mean_d0,
        std_d0=result.std_d0,
        cv_d0=result.cv_d0,
        yield_gain_poisson=result.yield_gain_poisson,
        yield_gain_negbinom=result.yield_gain_negbinom,
        n_active_dies=result.n_active_dies,
        die_area_mm2=result.die_area_mm2,
        critical_area_fraction=result.critical_area_fraction,
        die_yields=[
            DieYieldResponse(
                row=d.row, col=d.col,
                defect_count=d.defect_count,
                d0=d.d0,
                yield_poisson=d.yield_poisson,
                yield_murphy=d.yield_murphy,
                yield_negbinom=d.yield_negbinom,
                active=d.active,
            )
            for d in result.die_yields
        ],
    )
