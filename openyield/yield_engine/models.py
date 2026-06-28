"""
yield_engine/models.py
-----------------------
Author: Yeonkuk Woo

Semiconductor yield models for OpenYield.

Three industry-standard models are implemented, each suited to different
process nodes and defect clustering characteristics.

All models take:
    A   : critical area of the die (mm²) — full die area in OpenYield v0.1
    D0  : defect density (defects / mm²) — from system_a inspection data
    alpha : clustering parameter (negative binomial only)

Model selection guidance
------------------------
Poisson        : Simple, assumes random defect distribution. Use for:
                 - Mature nodes (180nm+) with well-controlled processes
                 - Quick estimates when clustering data is unavailable
                 - Conservative (over-pessimistic) yield floor

Murphy         : Assumes a triangular distribution of defect densities
                 across the wafer. More realistic than Poisson for most
                 production processes. Use for:
                 - Mid-range nodes (28nm–180nm)
                 - When spatial uniformity data is unavailable

Negative Binomial (Seeds model) : Most accurate for clustered defects.
                 Parameterized by α (clustering factor):
                 - α → ∞  : degenerates to Poisson (random)
                 - α = 1  : moderately clustered (glass panel typical)
                 - α = 0.5: highly clustered (advanced wafer typical)
                 Use for:
                 - Advanced nodes (<28nm) with process-driven clustering
                 - When per-die defect variance is measurable

References
----------
C.H. Stapper, "Modeling of Integrated Circuit Defect Sensitivities",
IBM Journal of Research and Development, 27(6), 1983.

W. Maly, "Modeling of Lithography Related Yield Losses for CAD of VLSI
Circuits", IEEE Trans. CAD, 4(3), 1985.
"""

from __future__ import annotations

import math
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class YieldEstimate:
    """
    Yield calculation results for a single panel.

    All yield values are in [0.0, 1.0] — multiply by 100 for percentage.
    """
    panel_id:          str
    substrate_type:    str

    # Inputs
    die_area_mm2:      float    # full die area (pitch²)
    inspected_dies:    int      # active dies only (edge-excluded dies excluded)
    defect_count:      int      # system_a defects on active dies
    defect_density:    float    # defects / mm²  (defect_count / total_active_area)

    # Outputs
    yield_poisson:     float    # e^(-A × D0)
    yield_murphy:      float    # Murphy triangular model
    yield_negbinom:    float    # Negative binomial (Seeds model)

    # Clustering
    clustering_alpha:  float    # α used in negative binomial
    alpha_method:      str      # 'empirical' or 'profile'

    # Advisory
    recommended_model: str      # which model best fits this substrate/process
    model_notes:       str      # human-readable explanation

    # Critical area (None when computed without CA correction)
    critical_area_fraction: float | None = None  # A_eff / A_die


# ---------------------------------------------------------------------------
# Core model functions
# ---------------------------------------------------------------------------

def poisson_yield(A: float, D0: float) -> float:
    """
    Poisson yield model.

    Y = exp(-A × D0)

    Assumes defects are randomly and independently distributed (Poisson process).
    Underestimates yield for clustered defects (pessimistic).

    Parameters
    ----------
    A  : Die critical area (mm²)
    D0 : Defect density (defects/mm²)

    Returns
    -------
    float : Yield in [0.0, 1.0]
    """
    if A <= 0 or D0 < 0:
        raise ValueError(f"A must be > 0 and D0 >= 0, got A={A}, D0={D0}")
    return math.exp(-A * D0)


def murphy_yield(A: float, D0: float) -> float:
    """
    Murphy yield model (triangular defect density distribution).

    Y = ((1 - exp(-A × D0)) / (A × D0))²

    Assumes defect density varies across the wafer following a triangular
    distribution. More realistic than Poisson for most production processes.
    Degenerates to Poisson when A × D0 → 0.

    Parameters
    ----------
    A  : Die critical area (mm²)
    D0 : Defect density (defects/mm²)

    Returns
    -------
    float : Yield in [0.0, 1.0]
    """
    if A <= 0 or D0 < 0:
        raise ValueError(f"A must be > 0 and D0 >= 0, got A={A}, D0={D0}")

    AD = A * D0
    if AD < 1e-10:
        # Taylor expansion for numerical stability near zero: limit → 1.0
        return 1.0

    return ((1.0 - math.exp(-AD)) / AD) ** 2


def negbinom_yield(A: float, D0: float, alpha: float) -> float:
    """
    Negative binomial yield model (Seeds / Stapper model).

    Y = (1 + A × D0 / α)^(-α)

    Parameterized by α (clustering factor):
        α → ∞  : degenerates to Poisson (Y → exp(-A × D0))
        α = 1  : moderately clustered
        α = 0.5: highly clustered (advanced node typical)
        α → 0  : all defects in one cluster (Y → 1 - A × D0 for small AD)

    Parameters
    ----------
    A     : Die critical area (mm²)
    D0    : Defect density (defects/mm²)
    alpha : Clustering parameter (must be > 0)

    Returns
    -------
    float : Yield in [0.0, 1.0]
    """
    if A <= 0 or D0 < 0:
        raise ValueError(f"A must be > 0 and D0 >= 0, got A={A}, D0={D0}")
    if alpha <= 0:
        raise ValueError(f"alpha must be > 0, got {alpha}")

    base = 1.0 + (A * D0) / alpha
    result = base ** (-alpha)
    return max(0.0, min(1.0, result))


# ---------------------------------------------------------------------------
# Clustering alpha estimation
# ---------------------------------------------------------------------------

def estimate_alpha_empirical(
    defects_per_die: list[int],
    die_area_mm2: float,
    D0: float,
) -> float:
    """
    Estimate the negative binomial clustering parameter α from
    the observed per-die defect count distribution.

    Method: Method of Moments.
    For a negative binomial distribution with mean μ and variance σ²:
        α = μ² / (σ² - μ)

    If σ² <= μ (sub-Poisson or Poisson — variance ≤ mean), the distribution
    is not over-dispersed and α cannot be estimated this way. In that case
    we return a large α (≈ Poisson behavior).

    Parameters
    ----------
    defects_per_die : List of defect counts, one per active die
    die_area_mm2    : Area of each die (mm²)
    D0              : Defect density (defects/mm²)

    Returns
    -------
    float : Estimated α (> 0). Large values indicate near-random distribution.
    """
    n = len(defects_per_die)
    if n < 4:
        logger.warning(
            "Too few dies (%d) for reliable empirical alpha — using fallback α=1.0", n
        )
        return 1.0

    mean = sum(defects_per_die) / n
    if mean == 0:
        logger.warning("All dies have zero defects — using fallback α=1.0")
        return 1.0

    variance = sum((x - mean) ** 2 for x in defects_per_die) / (n - 1)

    if variance <= mean:
        # Distribution is Poisson or sub-Poisson — return large alpha
        logger.info(
            "Defect distribution is Poisson-like (var=%.3f, mean=%.3f) — "
            "returning α=50 (near-random)", variance, mean
        )
        return 50.0

    alpha = (mean ** 2) / (variance - mean)
    alpha = max(0.05, min(alpha, 100.0))  # clip to sensible range
    logger.info(
        "Empirical α estimated: %.4f (mean=%.3f, var=%.3f, n=%d dies)",
        alpha, mean, variance, n
    )
    return round(alpha, 4)


# ---------------------------------------------------------------------------
# Recommended model selector
# ---------------------------------------------------------------------------

def select_recommended_model(
    substrate_type: str,
    alpha: float,
    AD: float,
) -> tuple[str, str]:
    """
    Return (recommended_model_name, notes) based on substrate type,
    clustering, and yield loss severity.

    Parameters
    ----------
    substrate_type : 'glass_panel' or 'wafer'
    alpha          : Clustering factor used
    AD             : A × D0 product (dimensionless yield loss factor)

    Returns
    -------
    tuple[str, str] : (model_name, explanation)
    """
    if substrate_type == "wafer":
        if alpha < 1.0:
            return (
                "negbinom",
                f"Wafer shows clustered defects (α={alpha:.3f} < 1.0). "
                "Negative binomial model recommended — Poisson would over-penalize yield."
            )
        elif alpha < 10.0:
            return (
                "murphy",
                f"Wafer shows moderate clustering (α={alpha:.3f}). "
                "Murphy model balances accuracy and simplicity."
            )
        else:
            return (
                "poisson",
                f"Wafer defects appear near-random (α={alpha:.3f} ≫ 1). "
                "Poisson model is appropriate."
            )
    else:  # glass_panel
        if AD < 0.5:
            return (
                "poisson",
                f"Glass panel with low yield loss (A×D0={AD:.3f}). "
                "Poisson model is accurate in this regime."
            )
        else:
            return (
                "murphy",
                f"Glass panel with significant yield loss (A×D0={AD:.3f}). "
                "Murphy model recommended for non-uniform AOI defect distribution."
            )
