"""
analysis/trend.py
-----------------
Author: Yeonkuk Woo

Multi-lot yield and defect density trend analysis for OpenYield.

Tracks how defect density and yield evolve across sequential lots.
Detects process drift (monotonic degradation), recovery, and stability.

Trend detection
---------------
Linear regression (pure Python, no numpy required) fits a line to the
lot-ordered defect density time series. The slope determines direction:

  improving   : slope < -threshold  (density decreasing)
  degrading   : slope > +threshold  (density increasing — process drift)
  stable      : |slope| <= threshold

R² quantifies how well the linear model fits the data.
A high R² with a large slope indicates a genuine monotonic trend;
a low R² indicates noisy variation without clear direction.

Threshold is set at 5% of the mean density per lot step.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from openyield.db.connection import get_placeholder

logger = logging.getLogger(__name__)

Connection = Any


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class TrendPoint:
    lot_id:             str
    sequence:           int       # 1-based chronological order
    created_at:         str
    substrate_type:     str
    avg_defect_density: float
    avg_yield_negbinom: float | None
    excursion_count:    int
    lot_status:         str       # 'clean', 'watch', 'excursion'


@dataclass
class TrendResult:
    substrate_type:  str
    n_lots:          int
    data_points:     list[TrendPoint]
    # Regression on defect density
    slope:           float        # defects/mm² per lot step
    intercept:       float
    r_squared:       float
    direction:       str          # 'improving', 'degrading', 'stable'
    # Summary stats
    mean_density:    float
    mean_yield:      float | None
    first_lot_id:    str | None
    last_lot_id:     str | None


# ---------------------------------------------------------------------------
# Pure-Python linear regression
# ---------------------------------------------------------------------------

def _linear_regression(x: list[float], y: list[float]) -> tuple[float, float, float]:
    """
    Fit y = slope * x + intercept using ordinary least squares.

    Returns (slope, intercept, r_squared).
    Returns (0.0, mean(y), 0.0) if degenerate (n < 2 or zero variance in x).
    """
    n = len(x)
    if n < 2:
        mean_y = sum(y) / n if n else 0.0
        return 0.0, mean_y, 0.0

    mean_x = sum(x) / n
    mean_y = sum(y) / n

    ss_xx = sum((xi - mean_x) ** 2 for xi in x)
    ss_xy = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    ss_yy = sum((yi - mean_y) ** 2 for yi in y)

    if ss_xx < 1e-12:
        return 0.0, mean_y, 0.0

    slope     = ss_xy / ss_xx
    intercept = mean_y - slope * mean_x
    r_squared = (ss_xy ** 2 / (ss_xx * ss_yy)) if ss_yy > 1e-12 else 1.0

    return round(slope, 8), round(intercept, 8), round(r_squared, 6)


# ---------------------------------------------------------------------------
# Main trend function
# ---------------------------------------------------------------------------

def compute_trend(
    conn: Connection,
    substrate_type: str | None = None,
    *,
    limit: int = 50,
) -> TrendResult:
    """
    Compute defect density and yield trend across lots ordered by creation time.

    Parameters
    ----------
    conn           : Database connection
    substrate_type : Filter to a specific substrate ('glass_panel' or 'wafer').
                     If None, returns trend across all substrates.
    limit          : Maximum number of lots to include (most recent N).

    Returns
    -------
    TrendResult
    """
    ph = get_placeholder(conn)

    # Fetch lots ordered by creation time
    if substrate_type:
        lot_rows = conn.execute(
            f"SELECT lot_id, substrate_type, created_at FROM lots "
            f"WHERE substrate_type={ph} ORDER BY created_at ASC LIMIT {ph}",
            (substrate_type, limit)
        ).fetchall()
    else:
        lot_rows = conn.execute(
            f"SELECT lot_id, substrate_type, created_at FROM lots "
            f"ORDER BY created_at ASC LIMIT {ph}",
            (limit,)
        ).fetchall()

    if not lot_rows:
        sub = substrate_type or "all"
        return TrendResult(
            substrate_type=sub, n_lots=0, data_points=[],
            slope=0.0, intercept=0.0, r_squared=0.0, direction="stable",
            mean_density=0.0, mean_yield=None,
            first_lot_id=None, last_lot_id=None,
        )

    data_points: list[TrendPoint] = []

    for seq, lot_row in enumerate(lot_rows, start=1):
        lot_id   = lot_row["lot_id"]
        sub_type = lot_row["substrate_type"]
        created  = str(lot_row["created_at"])

        # Use latest persisted lot summary; compute on-the-fly if absent
        ls = conn.execute(
            f"SELECT avg_defect_density, avg_yield_negbinom, "
            f"excursion_count, lot_status "
            f"FROM lot_summaries WHERE lot_id={ph} "
            f"ORDER BY calculated_at DESC LIMIT 1",
            (lot_id,)
        ).fetchone()

        if ls:
            avg_density  = ls["avg_defect_density"]
            avg_yield    = ls["avg_yield_negbinom"]
            exc_count    = ls["excursion_count"]
            lot_status   = ls["lot_status"]
        else:
            # Fall back to averaging yield_estimates for panels in this lot
            ye_rows = conn.execute(
                f"""SELECT AVG(ye.defect_density) as avg_d,
                           AVG(ye.yield_negbinom) as avg_y,
                           COUNT(*) as n
                    FROM yield_estimates ye
                    JOIN panels p ON p.panel_id = ye.panel_id
                    WHERE p.lot_id = {ph}""",
                (lot_id,)
            ).fetchone()
            avg_density = ye_rows["avg_d"] if ye_rows and ye_rows["avg_d"] else 0.0
            avg_yield   = ye_rows["avg_y"] if ye_rows else None
            exc_count   = 0
            lot_status  = "clean"

        data_points.append(TrendPoint(
            lot_id=lot_id,
            sequence=seq,
            created_at=created,
            substrate_type=sub_type,
            avg_defect_density=round(avg_density, 8),
            avg_yield_negbinom=round(avg_yield, 6) if avg_yield else None,
            excursion_count=exc_count,
            lot_status=lot_status,
        ))

    # Linear regression on defect density vs. sequence number
    xs = [float(p.sequence)      for p in data_points]
    ys = [p.avg_defect_density   for p in data_points]
    slope, intercept, r_sq = _linear_regression(xs, ys)

    # Direction threshold: 5% of mean density per lot step
    mean_density = sum(ys) / len(ys) if ys else 0.0
    threshold    = 0.05 * mean_density if mean_density > 0 else 1e-6

    if slope > threshold:
        direction = "degrading"
    elif slope < -threshold:
        direction = "improving"
    else:
        direction = "stable"

    yields = [p.avg_yield_negbinom for p in data_points if p.avg_yield_negbinom]
    mean_yield = sum(yields) / len(yields) if yields else None

    sub_label = substrate_type or "all"

    logger.info(
        "Trend [%s]: %d lots | slope=%.6f | direction=%s | R²=%.3f",
        sub_label, len(data_points), slope, direction, r_sq,
    )

    return TrendResult(
        substrate_type=sub_label,
        n_lots=len(data_points),
        data_points=data_points,
        slope=slope,
        intercept=intercept,
        r_squared=r_sq,
        direction=direction,
        mean_density=round(mean_density, 8),
        mean_yield=round(mean_yield, 6) if mean_yield else None,
        first_lot_id=data_points[0].lot_id  if data_points else None,
        last_lot_id=data_points[-1].lot_id  if data_points else None,
    )
