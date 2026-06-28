"""
simulation/monte_carlo.py
--------------------------
Author: Yeonkuk Woo

Monte Carlo yield simulator for semiconductor manufacturing.

Runs N independent trials, each placing Poisson-distributed defects on a
virtual wafer, and returns the full yield distribution.  This captures
statistical uncertainty that closed-form models (Poisson, Murphy) cannot —
particularly important for small lots or early-stage processes with high D₀.

Algorithm
---------
For each trial t = 1 … n_runs:
  For each die i = 1 … n_dies:
    k_i ~ Poisson(D₀ × A_crit)          # defects landing on critical area
    die_i passes iff k_i == 0
  Y_t = (# passing dies) / n_dies

Collect {Y_t} and compute statistics: mean, std, p10/p50/p90, histogram.

The simulation also runs a sensitivity sweep over ±20 % D₀ variants to
quantify how much yield improves from process improvements.

References
----------
[1] C. H. Stapper, "Yield model for fault clusters within integrated
    circuits," IBM J. Res. Dev., 28(5):636–640, 1984.
[2] A. Berglund, "A unified yield model incorporating both defect and
    parametric effects," IEEE Trans. Semicond. Manuf., 9(3):447–454, 1996.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class MonteCarloResult:
    """Full output of a Monte Carlo yield simulation run."""
    # Inputs (echoed back)
    d0:                     float
    die_area_mm2:           float
    critical_area_fraction: float
    n_wafers:               int
    n_runs:                 int
    n_dies_per_wafer:       int

    # Yield statistics across all runs
    mean_yield:   float
    std_yield:    float
    p10_yield:    float
    p50_yield:    float
    p90_yield:    float
    min_yield:    float
    max_yield:    float

    # Closed-form reference values (for comparison)
    poisson_yield:  float
    murphy_yield:   float
    negbinom_yield: float

    # Sensitivity: yield at ±20 % D₀
    yield_d0_minus20: float
    yield_d0_plus20:  float

    # Raw yield distribution (n_runs values)
    yield_samples: list[float] = field(default_factory=list)

    # Histogram for plotting: list of {bin_low, bin_high, count}
    histogram: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Die-per-wafer estimate
# ---------------------------------------------------------------------------

def dies_per_wafer(wafer_diameter_mm: float, die_area_mm2: float) -> int:
    """
    Estimate the number of good dies on a circular wafer.

    Uses the standard formula:
        N ≈ (π × (D/2)²) / A  −  (π × D) / √(2A)

    The second term approximates edge dies that are partially off the wafer.
    """
    if die_area_mm2 <= 0 or wafer_diameter_mm <= 0:
        return 0
    r = wafer_diameter_mm / 2.0
    wafer_area = math.pi * r * r
    edge_loss  = math.pi * wafer_diameter_mm / math.sqrt(2.0 * die_area_mm2)
    return max(1, int(wafer_area / die_area_mm2 - edge_loss))


# ---------------------------------------------------------------------------
# Closed-form yield helpers
# ---------------------------------------------------------------------------

def _poisson_yield(d0: float, area_cm2: float) -> float:
    return math.exp(-d0 * area_cm2)


def _murphy_yield(d0: float, area_cm2: float) -> float:
    x = d0 * area_cm2
    if x < 1e-12:
        return 1.0
    return ((1.0 - math.exp(-x)) / x) ** 2


def _negbinom_yield(d0: float, area_cm2: float, alpha: float = 2.0) -> float:
    return (1.0 + d0 * area_cm2 / alpha) ** (-alpha)


# ---------------------------------------------------------------------------
# Core simulator
# ---------------------------------------------------------------------------

def run_monte_carlo(
    d0: float,
    die_area_mm2: float,
    *,
    wafer_diameter_mm: float     = 300.0,
    n_wafers: int                = 25,
    n_runs: int                  = 2_000,
    critical_area_fraction: float = 1.0,
    alpha: float                 = 2.0,
    n_histogram_bins: int        = 20,
    seed: int                    = 42,
) -> MonteCarloResult:
    """
    Run a Monte Carlo yield simulation.

    Parameters
    ----------
    d0                     : Defect density (defects / cm²).
    die_area_mm2           : Full die area in mm².
    wafer_diameter_mm      : Wafer diameter in mm (default 300).
    n_wafers               : Dies per run = n_dies_per_wafer × n_wafers.
                             Unused in per-die Poisson model but returned
                             for reference.
    n_runs                 : Number of independent Monte Carlo trials.
    critical_area_fraction : Fraction of die area that is yield-sensitive
                             (0–1).  Default 1.0 (entire die is critical).
    alpha                  : Negative binomial clustering parameter.
    n_histogram_bins       : Number of bins in the yield histogram.
    seed                   : NumPy RNG seed for reproducibility.

    Returns
    -------
    MonteCarloResult
    """
    rng = np.random.default_rng(seed)

    n_dies       = dies_per_wafer(wafer_diameter_mm, die_area_mm2)
    crit_area_cm2 = die_area_mm2 * critical_area_fraction / 100.0
    lam          = d0 * crit_area_cm2   # expected defects per die

    # Simulate: shape (n_runs, n_dies), each cell = defect count for that die
    defect_counts = rng.poisson(lam, size=(n_runs, n_dies))
    # A die passes if it has zero defects on critical area
    yields = (defect_counts == 0).mean(axis=1).astype(np.float64)

    # Statistics
    mean_y = float(yields.mean())
    std_y  = float(yields.std())
    p10    = float(np.percentile(yields, 10))
    p50    = float(np.percentile(yields, 50))
    p90    = float(np.percentile(yields, 90))

    # Histogram
    counts, edges = np.histogram(yields, bins=n_histogram_bins, range=(0.0, 1.0))
    histogram = [
        {"bin_low": float(edges[i]), "bin_high": float(edges[i + 1]),
         "count": int(counts[i])}
        for i in range(len(counts))
    ]

    # Closed-form references using full die area (no CA fraction — matches
    # the convention used everywhere else in OpenYield)
    full_area_cm2 = die_area_mm2 / 100.0
    py = _poisson_yield(d0, full_area_cm2)
    my = _murphy_yield(d0, full_area_cm2)
    ny = _negbinom_yield(d0, full_area_cm2, alpha)

    # Sensitivity sweep: ±20 % D₀, using same Monte Carlo approach
    def _mc_mean(d0_: float) -> float:
        lam_ = d0_ * crit_area_cm2
        dc   = rng.poisson(lam_, size=(max(n_runs // 4, 200), n_dies))
        return float((dc == 0).mean())

    y_minus20 = _mc_mean(d0 * 0.80)
    y_plus20  = _mc_mean(d0 * 1.20)

    return MonteCarloResult(
        d0=d0,
        die_area_mm2=die_area_mm2,
        critical_area_fraction=critical_area_fraction,
        n_wafers=n_wafers,
        n_runs=n_runs,
        n_dies_per_wafer=n_dies,
        mean_yield=mean_y,
        std_yield=std_y,
        p10_yield=p10,
        p50_yield=p50,
        p90_yield=p90,
        min_yield=float(yields.min()),
        max_yield=float(yields.max()),
        poisson_yield=py,
        murphy_yield=my,
        negbinom_yield=ny,
        yield_d0_minus20=y_minus20,
        yield_d0_plus20=y_plus20,
        yield_samples=yields.tolist(),
        histogram=histogram,
    )
