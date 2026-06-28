"""
simulation/learning_curve.py
-----------------------------
Author: Yeonkuk Woo

Yield learning curve simulator for semiconductor manufacturing.

As a fab gains experience with a new process, defect density decreases
and yield improves.  This module implements three learning models:

1. Linear
   Y(t) = min(Y₀ + rate × t, Y_max)
   Simple; works well for early-ramp data.

2. Exponential gap closure
   Y(t) = Y_max − (Y_max − Y₀) × exp(−k × t)
   Models diminishing returns as yield approaches ceiling.
   k = ln(2) / half_life  (k s.t. half the remaining gap closes in half_life months)

3. Defect density learning  (most physically grounded)
   D₀(t) = D₀_initial × exp(−r × t)
   Y(t)  = Poisson yield at D₀(t)  = exp(−D₀(t) × A)
   Models process engineers actively reducing contamination and defects.

References
----------
[1] T. P. Wright, "Factors affecting the cost of airplanes,"
    J. Aeronautical Sci., 3(4):122–128, 1936.  (original learning curve)
[2] R. Goodall, R. Monahan, K. Mullen, B. Bhatt, L. Nurani,
    "Yield prediction methodologies for semiconductor manufacturing,"
    IEEE Trans. Semicond. Manuf., 10(4):451–463, 1997.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class LearningCurvePoint:
    month: int
    yield_fraction: float
    d0: float | None  # only set for d0_learning model


@dataclass
class LearningCurveResult:
    model:                   str
    current_yield:           float
    target_yield:            float
    y_max:                   float
    months_to_target:        float | None   # None if target unreachable
    improvement_rate:        float
    die_area_mm2:            float | None   # only for d0_learning
    initial_d0:              float | None
    final_d0:                float | None
    projected:               list[LearningCurvePoint]


def _poisson_yield(d0: float, area_cm2: float) -> float:
    return math.exp(-d0 * area_cm2)


def run_learning_curve(
    current_yield: float,
    target_yield: float,
    *,
    model: str               = "exponential",
    improvement_rate: float  = 0.02,
    y_max: float             = 0.98,
    n_months: int            = 24,
    die_area_mm2: float | None = None,
    initial_d0: float | None   = None,
) -> LearningCurveResult:
    """
    Project yield over time under a chosen learning model.

    Parameters
    ----------
    current_yield    : Starting yield fraction (0–1).
    target_yield     : Goal yield fraction (0–1).
    model            : "linear" | "exponential" | "d0_learning".
    improvement_rate : Meaning depends on model:
                       linear      → percentage-point gain per month (e.g. 0.02 = 2 pp/month)
                       exponential → fraction of remaining gap closed per month (0–1)
                       d0_learning → D₀ decay rate per month (e.g. 0.08 = 8 %/month)
    y_max            : Yield ceiling (default 0.98 — a perfect process still
                       has some random failures).
    n_months         : Projection horizon in months.
    die_area_mm2     : Required for d0_learning model.
    initial_d0       : Required for d0_learning model.

    Returns
    -------
    LearningCurveResult
    """
    if not 0.0 <= current_yield <= 1.0:
        raise ValueError(f"current_yield must be in [0, 1], got {current_yield}")
    if not 0.0 < target_yield <= 1.0:
        raise ValueError(f"target_yield must be in (0, 1], got {target_yield}")
    if model not in ("linear", "exponential", "d0_learning"):
        raise ValueError(f"Unknown model {model!r}. Choose linear | exponential | d0_learning")
    if model == "d0_learning" and (die_area_mm2 is None or initial_d0 is None):
        raise ValueError("d0_learning model requires die_area_mm2 and initial_d0")

    projected: list[LearningCurvePoint] = []
    months_to_target: float | None      = None

    if model == "linear":
        for t in range(n_months + 1):
            y = min(current_yield + improvement_rate * t, y_max)
            projected.append(LearningCurvePoint(month=t, yield_fraction=y, d0=None))
            if months_to_target is None and y >= target_yield:
                months_to_target = float(t)

        if months_to_target is None and improvement_rate > 0:
            delta = target_yield - current_yield
            if delta <= 0:
                months_to_target = 0.0
            elif target_yield <= y_max:
                months_to_target = delta / improvement_rate

    elif model == "exponential":
        # Y(t) = y_max - (y_max - Y₀) × (1 - rate)^t
        gap0 = y_max - current_yield
        for t in range(n_months + 1):
            remaining_gap = gap0 * ((1.0 - improvement_rate) ** t)
            y = min(y_max - remaining_gap, y_max)
            projected.append(LearningCurvePoint(month=t, yield_fraction=y, d0=None))
            if months_to_target is None and y >= target_yield:
                months_to_target = float(t)

        if months_to_target is None and target_yield <= y_max and improvement_rate > 0:
            gap_target = y_max - target_yield
            if gap0 > 0 and gap_target < gap0:
                months_to_target = math.log(gap_target / gap0) / math.log(1.0 - improvement_rate)

    else:  # d0_learning
        area_cm2  = die_area_mm2 / 100.0   # type: ignore[operator]
        d0_now    = initial_d0              # type: ignore[assignment]
        for t in range(n_months + 1):
            d0_t = d0_now * math.exp(-improvement_rate * t)
            y    = _poisson_yield(d0_t, area_cm2)
            y    = min(y, y_max)
            projected.append(LearningCurvePoint(month=t, yield_fraction=y, d0=d0_t))
            if months_to_target is None and y >= target_yield:
                months_to_target = float(t)

        if months_to_target is None and target_yield <= y_max and improvement_rate > 0:
            # Solve: exp(-d0_now × exp(-r×t) × A) = target_yield
            # → d0_now × exp(-r×t) × A = -ln(target_yield)
            # → exp(-r×t) = -ln(target_yield) / (d0_now × A)
            # → t = -ln(rhs) / r
            rhs = -math.log(max(target_yield, 1e-9)) / (d0_now * area_cm2)
            if 0 < rhs < 1.0:
                months_to_target = -math.log(rhs) / improvement_rate

    final_d0 = projected[-1].d0 if model == "d0_learning" else None

    return LearningCurveResult(
        model=model,
        current_yield=current_yield,
        target_yield=target_yield,
        y_max=y_max,
        months_to_target=round(months_to_target, 1) if months_to_target is not None else None,
        improvement_rate=improvement_rate,
        die_area_mm2=die_area_mm2,
        initial_d0=initial_d0,
        final_d0=final_d0,
        projected=projected,
    )
