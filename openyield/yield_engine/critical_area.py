"""
yield_engine/critical_area.py
------------------------------
Author: Yeonkuk Woo

Critical area extraction for semiconductor yield modeling.

Critical area is the fraction of die area where a defect of a given size
will cause the die to fail. Using the full die area (pitch²) in yield models
overestimates yield loss — especially at advanced nodes where significant
die area is occupied by non-critical structures such as power straps,
decoupling capacitor arrays, and filler cells.

Model: Maly linear expansion
----------------------------
For a die with layout density f and minimum feature dimension w, a defect
of size d has critical area fraction:

    Ac(d) / A_die = min(1.0, f × (1 + d / w))

    At d = 0:      Ac = f × A_die   (only actual feature area is sensitive)
    At d = w:      Ac = 2f × A_die  (kill zone doubles around each feature)
    Saturates at:  d* = w × (1/f − 1)  (full die becomes critical)

The mean critical area fraction is computed by averaging Ac(d)/A over the
observed defect size distribution:

    ca_fraction = E_d[min(1, f × (1 + d/w))]

The yield calculator then substitutes A_eff = ca_fraction × A_die for the
full die area in the Poisson, Murphy, and negative binomial models.

Reference: W. Maly, "Modeling of Lithography Related Yield Losses for CAD
of VLSI Circuits", IEEE Trans. CAD, 4(3), pp. 166–177, 1985.

Usage
-----
    from openyield.yield_engine.critical_area import compute_panel_critical_area
    from openyield.synthetic.substrate_profiles import get_profile

    profile = get_profile("wafer")
    result = compute_panel_critical_area(
        conn, "WF_ABC123",
        layout_density=profile.layout_density,
        min_feature_mm=profile.min_feature_mm,
    )
    print(f"CA fraction: {result.ca_fraction:.3f}")
    # A_eff = result.ca_fraction × die_area_mm2
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from openyield.db.connection import get_placeholder

logger = logging.getLogger(__name__)

Connection = Any


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class CriticalAreaResult:
    """
    Critical area extraction result for a single panel.

    Attributes
    ----------
    ca_fraction         : Mean critical area fraction in [0, 1]. Multiply
                          full die area by this to obtain A_eff for yield models.
    layout_density      : Fraction of die area covered by killable features (f).
    min_feature_mm      : Minimum critical feature dimension used in model (w).
    n_defects           : Number of system_a defect sizes sampled.
    mean_defect_size_mm : Mean observed defect size (mm).
    method              : Model name — always 'maly_linear'.
    """
    ca_fraction:          float
    layout_density:       float
    min_feature_mm:       float
    n_defects:            int
    mean_defect_size_mm:  float
    method:               str = field(default="maly_linear")


# ---------------------------------------------------------------------------
# Core geometric model
# ---------------------------------------------------------------------------

def _ca_for_size(d_mm: float, layout_density: float, min_feature_mm: float) -> float:
    """
    Critical area fraction for a single defect of diameter d_mm.

    Implements the Maly linear expansion model:
        Ac(d) / A_die = min(1.0, f × (1 + d / w))

    Parameters
    ----------
    d_mm           : Defect size (mm). Non-negative.
    layout_density : Fraction of die area with killable features, f ∈ (0, 1].
    min_feature_mm : Minimum feature dimension, w > 0 (mm).

    Returns
    -------
    float : Critical area fraction in [layout_density, 1.0].
    """
    if d_mm <= 0.0:
        return layout_density
    return min(1.0, layout_density * (1.0 + d_mm / min_feature_mm))


def compute_critical_area(
    defect_sizes_mm: list[float],
    layout_density: float,
    min_feature_mm: float,
) -> CriticalAreaResult:
    """
    Compute mean critical area fraction from a list of defect sizes.

    Averages the per-defect critical area fraction over the observed defect
    size distribution. If the list is empty, returns layout_density as a
    conservative lower bound (all defects too small to expand the kill zone).

    Parameters
    ----------
    defect_sizes_mm  : Observed defect sizes (mm). Values must be ≥ 0.
    layout_density   : Fraction of die area covered by killable features, f ∈ (0, 1].
    min_feature_mm   : Minimum critical feature dimension (mm). Must be > 0.

    Returns
    -------
    CriticalAreaResult

    Raises
    ------
    ValueError : If layout_density or min_feature_mm are out of range.
    """
    if not (0 < layout_density <= 1.0):
        raise ValueError(
            f"layout_density must be in (0, 1], got {layout_density}"
        )
    if min_feature_mm <= 0:
        raise ValueError(
            f"min_feature_mm must be > 0, got {min_feature_mm}"
        )

    if not defect_sizes_mm:
        logger.warning(
            "No defect sizes available — using layout_density=%.3f as CA fraction",
            layout_density,
        )
        return CriticalAreaResult(
            ca_fraction=layout_density,
            layout_density=layout_density,
            min_feature_mm=min_feature_mm,
            n_defects=0,
            mean_defect_size_mm=0.0,
        )

    ca_values = [
        _ca_for_size(d, layout_density, min_feature_mm)
        for d in defect_sizes_mm
    ]
    mean_ca   = sum(ca_values) / len(ca_values)
    mean_size = sum(defect_sizes_mm) / len(defect_sizes_mm)

    logger.info(
        "Critical area: f_layout=%.3f w_min=%.4f mm  n=%d defects  "
        "mean_size=%.4f mm  → ca_fraction=%.4f",
        layout_density, min_feature_mm,
        len(defect_sizes_mm), mean_size, mean_ca,
    )

    return CriticalAreaResult(
        ca_fraction=round(mean_ca, 6),
        layout_density=layout_density,
        min_feature_mm=min_feature_mm,
        n_defects=len(defect_sizes_mm),
        mean_defect_size_mm=round(mean_size, 6),
    )


# ---------------------------------------------------------------------------
# Database integration
# ---------------------------------------------------------------------------

def compute_panel_critical_area(
    conn: Connection,
    panel_id: str,
    layout_density: float,
    min_feature_mm: float,
) -> CriticalAreaResult:
    """
    Compute critical area fraction for a panel using its stored defect sizes.

    Reads system_a defect sizes from the defects table. System B defects are
    excluded because they are a sub-sampled, re-detected subset of the same
    physical defects and would bias the size distribution.

    Parameters
    ----------
    conn           : Database connection (SQLite or PostgreSQL).
    panel_id       : Target panel ID.
    layout_density : Fraction of die area with killable features.
    min_feature_mm : Minimum critical feature dimension (mm).

    Returns
    -------
    CriticalAreaResult
    """
    ph = get_placeholder(conn)
    rows = conn.execute(
        f"SELECT size FROM defects "
        f"WHERE panel_id = {ph} AND source_system = 'system_a'",
        (panel_id,),
    ).fetchall()

    defect_sizes = [float(row["size"]) for row in rows]
    return compute_critical_area(defect_sizes, layout_density, min_feature_mm)
