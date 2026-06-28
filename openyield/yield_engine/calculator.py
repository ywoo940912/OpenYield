"""
yield_engine/calculator.py
---------------------------
Author: Yeonkuk Woo

Orchestrates yield calculation for a panel stored in the OpenYield database.

Workflow per panel
------------------
1. Load panel metadata (substrate type, rows, cols, pitch)
2. Count active dies and system_a defects on those dies
3. Compute die area and defect density
4. Estimate clustering α (empirical for wafer, profile value for glass panel)
5. Run all three yield models (Poisson, Murphy, Negative Binomial)
6. Select recommended model
7. Persist result to yield_estimates table
8. Return YieldEstimate dataclass

Usage
-----
    from openyield.yield_engine.calculator import calculate_panel_yield

    estimate = calculate_panel_yield(conn, panel_id="WF_ACA38DAA")
    print(f"Yield (neg. binomial): {estimate.yield_negbinom:.1%}")
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from openyield.db.connection import get_placeholder, is_postgres
from openyield.synthetic.substrate_profiles import get_profile
from openyield.yield_engine.critical_area import compute_panel_critical_area
from openyield.yield_engine.models import (
    YieldEstimate,
    poisson_yield,
    murphy_yield,
    negbinom_yield,
    estimate_alpha_empirical,
    select_recommended_model,
)

logger = logging.getLogger(__name__)

Connection = Any


# ---------------------------------------------------------------------------
# Data fetchers
# ---------------------------------------------------------------------------

def _fetch_panel(conn: Connection, panel_id: str) -> dict:
    """Return panel row as dict. Raises ValueError if not found."""
    ph = get_placeholder(conn)
    row = conn.execute(
        f"SELECT * FROM panels WHERE panel_id = {ph}", (panel_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Panel not found: {panel_id!r}")
    return dict(row)


def _fetch_active_die_count(conn: Connection, panel_id: str) -> int:
    """Count active components (edge-excluded dies are inactive)."""
    ph = get_placeholder(conn)
    row = conn.execute(
        f"SELECT COUNT(*) FROM components WHERE panel_id = {ph} AND active = 1",
        (panel_id,)
    ).fetchone()
    return row[0]


def _fetch_defects_per_die(
    conn: Connection,
    panel_id: str,
) -> list[int]:
    """
    Return system_a defect counts per active die as a list.
    Dies with zero defects are included (needed for variance calculation).
    """
    ph = get_placeholder(conn)

    # Get all active (row, col) combinations
    active_dies = conn.execute(
        f"SELECT component_row, component_col FROM components "
        f"WHERE panel_id = {ph} AND active = 1",
        (panel_id,)
    ).fetchall()

    if not active_dies:
        return []

    # Get system_a defect counts per die
    defect_counts = conn.execute(
        f"SELECT component_row, component_col, COUNT(*) as n "
        f"FROM defects "
        f"WHERE panel_id = {ph} AND source_system = 'system_a' "
        f"GROUP BY component_row, component_col",
        (panel_id,)
    ).fetchall()

    count_map = {(r["component_row"], r["component_col"]): r["n"]
                 for r in defect_counts}

    # Include zeros for dies with no defects
    return [
        count_map.get((row["component_row"], row["component_col"]), 0)
        for row in active_dies
    ]


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _save_estimate(conn: Connection, est: YieldEstimate) -> None:
    """Insert yield estimate into yield_estimates table."""
    ph = get_placeholder(conn)
    now = datetime.now(timezone.utc).isoformat()

    if is_postgres(conn):
        sql = (
            f"INSERT INTO yield_estimates "
            f"(panel_id, substrate_type, calculated_at, die_area_mm2, "
            f"inspected_dies, defect_count, defect_density, "
            f"yield_poisson, yield_murphy, yield_negbinom, "
            f"clustering_alpha, alpha_method, model_notes) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})"
        )
    else:
        sql = (
            f"INSERT INTO yield_estimates "
            f"(panel_id, substrate_type, calculated_at, die_area_mm2, "
            f"inspected_dies, defect_count, defect_density, "
            f"yield_poisson, yield_murphy, yield_negbinom, "
            f"clustering_alpha, alpha_method, model_notes) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})"
        )

    conn.execute(sql, (
        est.panel_id,
        est.substrate_type,
        now,
        round(est.die_area_mm2, 6),
        est.inspected_dies,
        est.defect_count,
        round(est.defect_density, 8),
        round(est.yield_poisson, 6),
        round(est.yield_murphy, 6),
        round(est.yield_negbinom, 6),
        round(est.clustering_alpha, 6),
        est.alpha_method,
        est.model_notes,
    ))


# ---------------------------------------------------------------------------
# Main calculator
# ---------------------------------------------------------------------------

def calculate_panel_yield(
    conn: Connection,
    panel_id: str,
    *,
    persist: bool = True,
    use_critical_area: bool = True,
) -> YieldEstimate:
    """
    Calculate yield for a single panel and optionally persist to database.

    Parameters
    ----------
    conn               : Database connection (SQLite or PostgreSQL)
    panel_id           : Panel ID to calculate yield for
    persist            : If True, save result to yield_estimates table (default True)
    use_critical_area  : If True (default), apply Maly critical area correction —
                         yield models receive A_eff = ca_fraction × die_area_mm2
                         instead of the full die area. Existing records in
                         yield_estimates are preserved unchanged.

    Returns
    -------
    YieldEstimate : Complete yield calculation results

    Raises
    ------
    ValueError : If panel not found or has no active dies
    """
    panel = _fetch_panel(conn, panel_id)
    substrate_type = panel["substrate_type"]
    profile = get_profile(substrate_type)

    # Full die area from component pitch (used for D0 and stored in DB)
    die_area_mm2 = profile.component_pitch_mm ** 2

    # Active die count
    inspected_dies = _fetch_active_die_count(conn, panel_id)
    if inspected_dies == 0:
        raise ValueError(f"Panel {panel_id!r} has no active dies.")

    # Per-die defect counts (system_a only)
    defects_per_die = _fetch_defects_per_die(conn, panel_id)
    defect_count = sum(defects_per_die)

    # Defect density D0 — always over full die area (physical measurement)
    total_area_mm2 = inspected_dies * die_area_mm2
    D0 = defect_count / total_area_mm2 if total_area_mm2 > 0 else 0.0

    # Clustering alpha
    if profile.use_empirical_alpha:
        alpha = estimate_alpha_empirical(defects_per_die, die_area_mm2, D0)
        alpha_method = "empirical"
    else:
        alpha = profile.clustering_alpha_default
        alpha_method = "profile"

    # Critical area correction — A_eff replaces full die area in yield models
    ca_fraction: float | None = None
    if use_critical_area:
        ca_result = compute_panel_critical_area(
            conn, panel_id,
            layout_density=profile.layout_density,
            min_feature_mm=profile.min_feature_mm,
        )
        ca_fraction = ca_result.ca_fraction
        effective_area_mm2 = ca_fraction * die_area_mm2
    else:
        effective_area_mm2 = die_area_mm2

    # Yield models use A_eff (CA-corrected or full, depending on flag)
    y_poisson  = poisson_yield(effective_area_mm2, D0)
    y_murphy   = murphy_yield(effective_area_mm2, D0)
    y_negbinom = negbinom_yield(effective_area_mm2, D0, alpha)

    # Model recommendation based on effective A × D0 product
    AD = effective_area_mm2 * D0
    recommended_model, model_notes = select_recommended_model(
        substrate_type, alpha, AD
    )

    estimate = YieldEstimate(
        panel_id=panel_id,
        substrate_type=substrate_type,
        die_area_mm2=die_area_mm2,
        inspected_dies=inspected_dies,
        defect_count=defect_count,
        defect_density=D0,
        yield_poisson=y_poisson,
        yield_murphy=y_murphy,
        yield_negbinom=y_negbinom,
        clustering_alpha=alpha,
        alpha_method=alpha_method,
        recommended_model=recommended_model,
        model_notes=model_notes,
        critical_area_fraction=ca_fraction,
    )

    if persist:
        with conn:
            _save_estimate(conn, estimate)
        ca_str = f"{ca_fraction:.3f}" if ca_fraction is not None else "off"
        logger.info(
            "[%s] Yield calculated — D0=%.4f/mm² | CA=%.3s | "
            "Poisson=%.1f%% | Murphy=%.1f%% | NegBinom=%.1f%% | "
            "α=%.3f (%s) | recommended=%s",
            panel_id, D0, ca_str,
            y_poisson * 100, y_murphy * 100, y_negbinom * 100,
            alpha, alpha_method, recommended_model,
        )

    return estimate


# ---------------------------------------------------------------------------
# Batch calculator
# ---------------------------------------------------------------------------

def calculate_all_yields(
    conn: Connection,
    *,
    substrate_type: str | None = None,
    persist: bool = True,
) -> list[YieldEstimate]:
    """
    Calculate yield for all panels, optionally filtered by substrate type.

    Parameters
    ----------
    conn           : Database connection
    substrate_type : Filter to one substrate type (None = all panels)
    persist        : Save results to yield_estimates table

    Returns
    -------
    list[YieldEstimate] : One result per panel
    """
    ph = get_placeholder(conn)

    if substrate_type:
        rows = conn.execute(
            f"SELECT panel_id FROM panels WHERE substrate_type = {ph}",
            (substrate_type,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT panel_id FROM panels").fetchall()

    panel_ids = [r["panel_id"] for r in rows]
    estimates: list[YieldEstimate] = []

    for pid in panel_ids:
        try:
            est = calculate_panel_yield(conn, pid, persist=persist)
            estimates.append(est)
        except Exception as exc:
            logger.error("Yield calculation failed for %s: %s", pid, exc)

    return estimates


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------

def print_yield_report(estimates: list[YieldEstimate]) -> None:
    """Print a formatted yield report to stdout."""
    if not estimates:
        print("No yield estimates to report.")
        return

    print(f"\n{'='*72}")
    print(f"  YIELD REPORT  ({len(estimates)} panel(s))")
    print(f"{'='*72}")
    print(f"  {'Panel ID':<20} {'Sub':<12} {'Dies':>5} {'D0/mm²':>10} "
          f"{'Poisson':>8} {'Murphy':>8} {'NegBinom':>9} {'α':>7} {'Rec.':<10}")
    print(f"  {'-'*20} {'-'*12} {'-'*5} {'-'*10} {'-'*8} {'-'*8} {'-'*9} {'-'*7} {'-'*10}")

    for e in estimates:
        print(
            f"  {e.panel_id:<20} {e.substrate_type:<12} {e.inspected_dies:>5} "
            f"{e.defect_density:>10.4f} "
            f"{e.yield_poisson*100:>7.1f}% "
            f"{e.yield_murphy*100:>7.1f}% "
            f"{e.yield_negbinom*100:>8.1f}% "
            f"{e.clustering_alpha:>7.3f} "
            f"{e.recommended_model:<10}"
        )

    print(f"{'='*72}")

    # Summary stats
    if len(estimates) > 1:
        avg_negbinom = sum(e.yield_negbinom for e in estimates) / len(estimates)
        avg_density  = sum(e.defect_density for e in estimates) / len(estimates)
        print(f"\n  Avg defect density : {avg_density:.4f} defects/mm²")
        print(f"  Avg yield (NegBinom): {avg_negbinom*100:.1f}%")

    print()
