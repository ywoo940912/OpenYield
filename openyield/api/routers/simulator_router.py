"""api/routers/simulator_router.py — Yield simulation endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from openyield.api.dependencies import get_db
from openyield.simulation.monte_carlo import run_monte_carlo
from openyield.simulation.learning_curve import run_learning_curve

router = APIRouter(prefix="/simulate", tags=["simulate"])
Connection = Any


# ---------------------------------------------------------------------------
# Monte Carlo
# ---------------------------------------------------------------------------

class MonteCarloRequest(BaseModel):
    d0:                     float = Field(gt=0, description="Defect density (defects/cm²)")
    die_area_mm2:           float = Field(gt=0, description="Full die area in mm²")
    wafer_diameter_mm:      float = 300.0
    n_runs:                 int   = Field(default=2000, ge=100, le=20000)
    n_wafers:               int   = Field(default=25, ge=1)
    critical_area_fraction: float = Field(default=1.0, ge=0.01, le=1.0)
    alpha:                  float = Field(default=2.0, gt=0)
    seed:                   int   = 42


class MonteCarloResponse(BaseModel):
    d0:                     float
    die_area_mm2:           float
    critical_area_fraction: float
    n_runs:                 int
    n_dies_per_wafer:       int
    mean_yield:             float
    std_yield:              float
    p10_yield:              float
    p50_yield:              float
    p90_yield:              float
    min_yield:              float
    max_yield:              float
    poisson_yield:          float
    murphy_yield:           float
    negbinom_yield:         float
    yield_d0_minus20:       float
    yield_d0_plus20:        float
    histogram:              list[dict]


@router.post("/monte-carlo", response_model=MonteCarloResponse)
def monte_carlo(body: MonteCarloRequest):
    """
    Run a Monte Carlo yield simulation.

    Randomly places Poisson-distributed defects on n_runs virtual wafers
    and returns the full yield distribution (mean, std, percentiles,
    histogram) alongside closed-form Poisson / Murphy / NegBinom references.

    Also returns a sensitivity sweep: yield at D₀ ×0.8 and D₀ ×1.2 to
    quantify the ROI of a 20 % process improvement.
    """
    result = run_monte_carlo(
        d0=body.d0,
        die_area_mm2=body.die_area_mm2,
        wafer_diameter_mm=body.wafer_diameter_mm,
        n_wafers=body.n_wafers,
        n_runs=body.n_runs,
        critical_area_fraction=body.critical_area_fraction,
        alpha=body.alpha,
        seed=body.seed,
    )
    return MonteCarloResponse(
        d0=result.d0,
        die_area_mm2=result.die_area_mm2,
        critical_area_fraction=result.critical_area_fraction,
        n_runs=result.n_runs,
        n_dies_per_wafer=result.n_dies_per_wafer,
        mean_yield=round(result.mean_yield, 6),
        std_yield=round(result.std_yield, 6),
        p10_yield=round(result.p10_yield, 6),
        p50_yield=round(result.p50_yield, 6),
        p90_yield=round(result.p90_yield, 6),
        min_yield=round(result.min_yield, 6),
        max_yield=round(result.max_yield, 6),
        poisson_yield=round(result.poisson_yield, 6),
        murphy_yield=round(result.murphy_yield, 6),
        negbinom_yield=round(result.negbinom_yield, 6),
        yield_d0_minus20=round(result.yield_d0_minus20, 6),
        yield_d0_plus20=round(result.yield_d0_plus20, 6),
        histogram=result.histogram,
    )


@router.post("/monte-carlo/spec/{spec_id}", response_model=MonteCarloResponse)
def monte_carlo_from_spec(
    spec_id: str,
    n_runs: int = 2000,
    conn: Connection = Depends(get_db),
):
    """
    Run Monte Carlo simulation using parameters from a saved product spec.
    Requires d0_target to be set on the spec.
    """
    row = conn.execute(
        "SELECT * FROM product_specs WHERE spec_id = ?", (spec_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"spec {spec_id!r} not found")
    if row["d0_target"] is None:
        raise HTTPException(
            status_code=422,
            detail="spec has no d0_target set — patch it with PATCH /products/specs/{spec_id}",
        )

    result = run_monte_carlo(
        d0=row["d0_target"],
        die_area_mm2=row["die_width_mm"] * row["die_height_mm"],
        wafer_diameter_mm=row["wafer_diameter_mm"],
        n_runs=n_runs,
        critical_area_fraction=row["critical_area_fraction"],
        alpha=row["alpha"],
    )
    return MonteCarloResponse(
        d0=result.d0,
        die_area_mm2=result.die_area_mm2,
        critical_area_fraction=result.critical_area_fraction,
        n_runs=result.n_runs,
        n_dies_per_wafer=result.n_dies_per_wafer,
        mean_yield=round(result.mean_yield, 6),
        std_yield=round(result.std_yield, 6),
        p10_yield=round(result.p10_yield, 6),
        p50_yield=round(result.p50_yield, 6),
        p90_yield=round(result.p90_yield, 6),
        min_yield=round(result.min_yield, 6),
        max_yield=round(result.max_yield, 6),
        poisson_yield=round(result.poisson_yield, 6),
        murphy_yield=round(result.murphy_yield, 6),
        negbinom_yield=round(result.negbinom_yield, 6),
        yield_d0_minus20=round(result.yield_d0_minus20, 6),
        yield_d0_plus20=round(result.yield_d0_plus20, 6),
        histogram=result.histogram,
    )


# ---------------------------------------------------------------------------
# Learning curve
# ---------------------------------------------------------------------------

class LearningCurveRequest(BaseModel):
    current_yield:    float = Field(ge=0.0, le=1.0)
    target_yield:     float = Field(gt=0.0, le=1.0)
    model:            str   = Field(default="exponential",
                                    description="linear | exponential | d0_learning")
    improvement_rate: float = Field(default=0.05, gt=0.0)
    y_max:            float = Field(default=0.98, gt=0.0, le=1.0)
    n_months:         int   = Field(default=24, ge=1, le=120)
    die_area_mm2:     float | None = None
    initial_d0:       float | None = None


class LearningCurvePoint(BaseModel):
    month:          int
    yield_fraction: float
    d0:             float | None


class LearningCurveResponse(BaseModel):
    model:             str
    current_yield:     float
    target_yield:      float
    y_max:             float
    months_to_target:  float | None
    improvement_rate:  float
    die_area_mm2:      float | None
    initial_d0:        float | None
    final_d0:          float | None
    projected:         list[LearningCurvePoint]


@router.post("/learning-curve", response_model=LearningCurveResponse)
def learning_curve(body: LearningCurveRequest):
    """
    Project yield improvement over time using a learning curve model.

    Three models:
    - linear      : fixed pp/month gain
    - exponential : diminishing returns as yield approaches ceiling
    - d0_learning : defect density decays exponentially; yield follows
                    (requires die_area_mm2 and initial_d0)

    Returns monthly yield projections and months_to_target.
    """
    try:
        result = run_learning_curve(
            current_yield=body.current_yield,
            target_yield=body.target_yield,
            model=body.model,
            improvement_rate=body.improvement_rate,
            y_max=body.y_max,
            n_months=body.n_months,
            die_area_mm2=body.die_area_mm2,
            initial_d0=body.initial_d0,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return LearningCurveResponse(
        model=result.model,
        current_yield=result.current_yield,
        target_yield=result.target_yield,
        y_max=result.y_max,
        months_to_target=result.months_to_target,
        improvement_rate=result.improvement_rate,
        die_area_mm2=result.die_area_mm2,
        initial_d0=result.initial_d0,
        final_d0=result.final_d0,
        projected=[
            LearningCurvePoint(month=p.month,
                               yield_fraction=round(p.yield_fraction, 6),
                               d0=p.d0)
            for p in result.projected
        ],
    )


@router.post("/learning-curve/spec/{spec_id}", response_model=LearningCurveResponse)
def learning_curve_from_spec(
    spec_id: str,
    current_yield: float,
    model: str   = "exponential",
    improvement_rate: float = 0.05,
    n_months: int = 24,
    conn: Connection = Depends(get_db),
):
    """Run learning curve simulation using a saved product spec's target_yield."""
    row = conn.execute(
        "SELECT * FROM product_specs WHERE spec_id = ?", (spec_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"spec {spec_id!r} not found")

    try:
        result = run_learning_curve(
            current_yield=current_yield,
            target_yield=row["target_yield"],
            model=model,
            improvement_rate=improvement_rate,
            n_months=n_months,
            die_area_mm2=row["die_width_mm"] * row["die_height_mm"] if model == "d0_learning" else None,
            initial_d0=row["d0_target"] if model == "d0_learning" else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return LearningCurveResponse(
        model=result.model,
        current_yield=result.current_yield,
        target_yield=result.target_yield,
        y_max=result.y_max,
        months_to_target=result.months_to_target,
        improvement_rate=result.improvement_rate,
        die_area_mm2=result.die_area_mm2,
        initial_d0=result.initial_d0,
        final_d0=result.final_d0,
        projected=[
            LearningCurvePoint(month=p.month,
                               yield_fraction=round(p.yield_fraction, 6),
                               d0=p.d0)
            for p in result.projected
        ],
    )
