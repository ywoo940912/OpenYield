"""
yield_engine/spatial_predictor.py
-----------------------------------
Author: Yeonkuk Woo

Spatial yield prediction for semiconductor wafer and glass panel inspection data.

Standard yield models (Poisson, Murphy, Negative Binomial) treat defect density
as globally uniform. In practice, defect density varies die-to-die — process
gradients, edge effects, equipment contamination rings, and reticle-linked patterns
all create within-substrate non-uniformity.

Spatial yield prediction disaggregates the global estimate into per-die local
estimates and averages them:

    Y_spatial = (1 / N_active) × Σᵢ Y_model(A_eff, D0ᵢ)

where D0ᵢ = n_defects_i / A_die is the per-die defect density.

This is strictly more accurate than the global model when density is non-uniform.
By Jensen's inequality, for convex yield functions (Poisson, Murphy):

    Y_spatial ≥ Y_model(A_eff, D0_mean)    when Var(D0) > 0

The gap  Δ = Y_spatial − Y_global  quantifies how much yield is underestimated
by the global model. It is zero only when all active dies have identical density.

This approach is the foundational technique of KLA Klarity's yield prediction
module and Onto Semiconductor's Discover Yield platform. OpenYield implements
it with no proprietary dependencies.

Reference: C.H. Stapper, "Modeling of Integrated Circuit Defect Sensitivities",
IBM J. Res. Dev., 27(6), pp. 549–557, 1983.

Usage
-----
    from openyield.yield_engine.spatial_predictor import compute_spatial_yield

    result = compute_spatial_yield(conn, "WF_ABC123")
    print(f"Spatial NegBinom yield: {result.spatial_yield_negbinom:.1%}")
    print(f"Global NegBinom yield:  {result.global_yield_negbinom:.1%}")
    print(f"Yield gain from CA:     {result.yield_gain_negbinom:+.3%}")
    print(f"Within-substrate CV:    {result.cv_d0:.3f}")
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from openyield.db.connection import get_placeholder
from openyield.synthetic.substrate_profiles import get_profile
from openyield.yield_engine.critical_area import compute_panel_critical_area
from openyield.yield_engine.models import (
    poisson_yield,
    murphy_yield,
    negbinom_yield,
    estimate_alpha_empirical,
)

logger = logging.getLogger(__name__)

Connection = Any


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class DieYield:
    """
    Per-die spatial yield contribution.

    Attributes
    ----------
    row / col       : Die grid position.
    defect_count    : system_a defects on this die.
    d0              : Local defect density (defects/mm²) for this die.
    yield_poisson   : Poisson yield for this die's local D0.
    yield_murphy    : Murphy yield for this die's local D0.
    yield_negbinom  : Neg. binomial yield for this die's local D0.
    active          : False if the die is edge-excluded (inactive).
    """
    row:            int
    col:            int
    defect_count:   int
    d0:             float
    yield_poisson:  float
    yield_murphy:   float
    yield_negbinom: float
    active:         bool


@dataclass
class SpatialYieldResult:
    """
    Spatial yield prediction for a single panel.

    Spatial yields are computed per-die and averaged; global yields use a
    single D0 value across all active dies. The difference is the spatial
    advantage of accounting for within-substrate density non-uniformity.

    Attributes
    ----------
    panel_id / substrate_type : Panel identifiers.

    Spatial yields (die-averaged):
        spatial_yield_poisson/murphy/negbinom

    Global yields (from mean D0 — same as calculator.py output):
        global_yield_poisson/murphy/negbinom

    Density statistics (over active dies):
        mean_d0   : Mean per-die D0 — equals the global D0.
        std_d0    : Standard deviation of per-die D0.
        cv_d0     : Coefficient of variation (std/mean). Zero = uniform.

    Spatial advantage (Δ = Y_spatial − Y_global):
        yield_gain_poisson  : > 0 when density is non-uniform.
        yield_gain_negbinom : > 0 when density is non-uniform.

    Inputs:
        n_active_dies          : Active die count.
        die_area_mm2           : Full die area (pitch²).
        critical_area_fraction : CA fraction applied to A_eff, or None.

    Per-die breakdown:
        die_yields : One DieYield per die (active and inactive).
    """
    panel_id:       str
    substrate_type: str

    # Spatial (die-averaged) yield estimates
    spatial_yield_poisson:  float
    spatial_yield_murphy:   float
    spatial_yield_negbinom: float

    # Global yield (from global D0, for comparison)
    global_yield_poisson:  float
    global_yield_murphy:   float
    global_yield_negbinom: float

    # Defect density statistics across active dies
    mean_d0: float
    std_d0:  float
    cv_d0:   float

    # Spatial advantage over global model
    yield_gain_poisson:  float
    yield_gain_negbinom: float

    # Inputs
    n_active_dies:          int
    die_area_mm2:           float
    critical_area_fraction: float | None

    # Per-die breakdown (active + inactive, sorted by row then col)
    die_yields: list[DieYield] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _population_std(values: list[float]) -> float:
    """Population standard deviation (not sample — D0 stats are exhaustive)."""
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    return math.sqrt(sum((x - mean) ** 2 for x in values) / n)


def _fetch_active_dies(conn: Connection, panel_id: str) -> list[dict]:
    """Return all components for the panel, sorted by (row, col)."""
    ph = get_placeholder(conn)
    rows = conn.execute(
        f"SELECT component_row, component_col, x_mm, y_mm, active "
        f"FROM components WHERE panel_id = {ph} "
        f"ORDER BY component_row, component_col",
        (panel_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _fetch_defects_by_die(conn: Connection, panel_id: str) -> dict[tuple[int, int], int]:
    """
    Return system_a defect counts keyed by (row, col).
    Dies with zero defects are absent from the dict (handle with .get(..., 0)).
    """
    ph = get_placeholder(conn)
    rows = conn.execute(
        f"SELECT component_row, component_col, COUNT(*) AS n "
        f"FROM defects "
        f"WHERE panel_id = {ph} AND source_system = 'system_a' "
        f"GROUP BY component_row, component_col",
        (panel_id,),
    ).fetchall()
    return {(r["component_row"], r["component_col"]): r["n"] for r in rows}


# ---------------------------------------------------------------------------
# Core algorithm
# ---------------------------------------------------------------------------

def compute_spatial_yield(
    conn: Connection,
    panel_id: str,
    *,
    use_critical_area: bool = True,
) -> SpatialYieldResult:
    """
    Compute spatial yield prediction for a panel.

    Fetches per-die defect counts from system_a, applies yield models locally,
    and averages over active dies. Returns both spatial and global yield
    estimates for comparison, plus per-die breakdown.

    Parameters
    ----------
    conn               : Database connection (SQLite or PostgreSQL).
    panel_id           : Panel to analyze.
    use_critical_area  : Apply Maly CA correction to A_eff (default True).

    Returns
    -------
    SpatialYieldResult

    Raises
    ------
    ValueError : If panel not found or has no active dies.
    """
    ph = get_placeholder(conn)

    # Panel metadata
    panel_row = conn.execute(
        f"SELECT * FROM panels WHERE panel_id = {ph}", (panel_id,)
    ).fetchone()
    if panel_row is None:
        raise ValueError(f"Panel not found: {panel_id!r}")

    substrate_type = panel_row["substrate_type"]
    profile = get_profile(substrate_type)
    die_area_mm2 = profile.component_pitch_mm ** 2

    # Fetch all components and per-die defect counts
    components = _fetch_active_dies(conn, panel_id)
    defect_map = _fetch_defects_by_die(conn, panel_id)

    active_components = [c for c in components if c["active"]]
    if not active_components:
        raise ValueError(f"Panel {panel_id!r} has no active dies.")

    # Critical area
    ca_fraction: float | None = None
    if use_critical_area:
        ca_result = compute_panel_critical_area(
            conn, panel_id,
            layout_density=profile.layout_density,
            min_feature_mm=profile.min_feature_mm,
        )
        ca_fraction = ca_result.ca_fraction
        effective_area = ca_fraction * die_area_mm2
    else:
        effective_area = die_area_mm2

    # Clustering alpha — same logic as calculator.py
    active_counts = [
        defect_map.get((c["component_row"], c["component_col"]), 0)
        for c in active_components
    ]
    total_defects = sum(active_counts)
    global_d0 = total_defects / (len(active_components) * die_area_mm2)

    if profile.use_empirical_alpha:
        alpha = estimate_alpha_empirical(active_counts, die_area_mm2, global_d0)
    else:
        alpha = profile.clustering_alpha_default

    # Per-die yields
    die_yields: list[DieYield] = []
    active_yields_poisson:  list[float] = []
    active_yields_murphy:   list[float] = []
    active_yields_negbinom: list[float] = []
    active_d0_values:       list[float] = []

    for comp in components:
        r, c = comp["component_row"], comp["component_col"]
        active = bool(comp["active"])
        count = defect_map.get((r, c), 0)
        d0_local = count / die_area_mm2

        if active:
            y_poisson  = poisson_yield(effective_area, d0_local)
            y_murphy   = murphy_yield(effective_area, d0_local)
            y_negbinom = negbinom_yield(effective_area, d0_local, alpha)

            active_yields_poisson.append(y_poisson)
            active_yields_murphy.append(y_murphy)
            active_yields_negbinom.append(y_negbinom)
            active_d0_values.append(d0_local)
        else:
            y_poisson = y_murphy = y_negbinom = 0.0

        die_yields.append(DieYield(
            row=r, col=c,
            defect_count=count,
            d0=round(d0_local, 8),
            yield_poisson=round(y_poisson, 6),
            yield_murphy=round(y_murphy, 6),
            yield_negbinom=round(y_negbinom, 6),
            active=active,
        ))

    n_active = len(active_components)

    # Spatial yields — mean over active dies
    spatial_y_poisson  = sum(active_yields_poisson)  / n_active
    spatial_y_murphy   = sum(active_yields_murphy)   / n_active
    spatial_y_negbinom = sum(active_yields_negbinom) / n_active

    # Global yields — from mean D0 (same as calculator.py)
    global_y_poisson  = poisson_yield(effective_area, global_d0)
    global_y_murphy   = murphy_yield(effective_area, global_d0)
    global_y_negbinom = negbinom_yield(effective_area, global_d0, alpha)

    # Density statistics
    mean_d0 = global_d0
    std_d0  = _population_std(active_d0_values)
    cv_d0   = std_d0 / mean_d0 if mean_d0 > 0 else 0.0

    logger.info(
        "[%s] Spatial yield — spatial_NB=%.1f%% global_NB=%.1f%% "
        "gain=%+.2f%% cv_D0=%.3f n_active=%d",
        panel_id,
        spatial_y_negbinom * 100, global_y_negbinom * 100,
        (spatial_y_negbinom - global_y_negbinom) * 100,
        cv_d0, n_active,
    )

    return SpatialYieldResult(
        panel_id=panel_id,
        substrate_type=substrate_type,
        spatial_yield_poisson=round(spatial_y_poisson, 6),
        spatial_yield_murphy=round(spatial_y_murphy, 6),
        spatial_yield_negbinom=round(spatial_y_negbinom, 6),
        global_yield_poisson=round(global_y_poisson, 6),
        global_yield_murphy=round(global_y_murphy, 6),
        global_yield_negbinom=round(global_y_negbinom, 6),
        mean_d0=round(mean_d0, 8),
        std_d0=round(std_d0, 8),
        cv_d0=round(cv_d0, 6),
        yield_gain_poisson=round(spatial_y_poisson - global_y_poisson, 6),
        yield_gain_negbinom=round(spatial_y_negbinom - global_y_negbinom, 6),
        n_active_dies=n_active,
        die_area_mm2=die_area_mm2,
        critical_area_fraction=ca_fraction,
        die_yields=die_yields,
    )
