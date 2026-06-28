"""
analysis/lot_tracker.py
------------------------
Author: Yeonkuk Woo

Lot-level yield tracking and excursion detection for OpenYield.

A lot is a group of panels (typically 25 wafers in a semiconductor fab)
processed together through the same equipment sequence. Tracking yield
and defect density at the lot level is how fabs detect process drift
before it impacts a large volume of product.

Lot status logic
----------------
  clean     — All panels within 2σ of the lot mean defect density.
               Normal process variation. No action required.

  watch     — One or more panels between 2σ and 3σ above the lot mean,
               OR one panel classified as 'systematic' by clustering.
               Monitor closely. Investigate if trend continues.

  excursion — One or more panels >3σ above the lot mean,
               OR any panel classified as 'excursion' by clustering,
               OR defect density exceeds the substrate profile threshold.
               Immediate engineering review required.

Fallback (< 3 panels in lot)
------------------------------
Statistical thresholds cannot be reliably computed with fewer than 3
panels. In this case the lot uses fixed thresholds from the substrate
profile (clustering_alpha_default as a proxy for expected variance).
"""

from __future__ import annotations

import math
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from openyield.db.connection import get_placeholder, is_postgres
from openyield.synthetic.substrate_profiles import get_profile

logger = logging.getLogger(__name__)

Connection = Any

# Fixed fallback density threshold (defects/mm²) when lot < 3 panels
_FALLBACK_EXCURSION_MULTIPLIER = 3.0   # 3x the profile mean_defect_count / die_area


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class PanelLotStats:
    panel_id:        str
    defect_density:  float
    yield_negbinom:  float | None
    cluster_class:   str | None   # 'random', 'systematic', 'excursion', or None


@dataclass
class LotSummary:
    lot_id:             str
    substrate_type:     str
    panel_count:        int
    panels:             list[PanelLotStats]
    avg_defect_density: float
    std_defect_density: float
    avg_yield_negbinom: float | None
    std_yield_negbinom: float | None
    excursion_count:    int
    lot_status:         str        # 'clean', 'watch', 'excursion'
    status_reason:      str


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------

def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: list[float], mean: float) -> float:
    if len(values) < 2:
        return 0.0
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


def _median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    return (s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0) if s else 0.0


def _mad(values: list[float], median: float) -> float:
    """
    Median Absolute Deviation — robust alternative to std dev.
    Resistant to outliers, standard practice in fab SPC.
    Equivalent std dev = 1.4826 * MAD (for normal distribution).
    """
    if len(values) < 2:
        return 0.0
    return _median([abs(v - median) for v in values]) * 1.4826


# ---------------------------------------------------------------------------
# Lot status classifier
# ---------------------------------------------------------------------------

def _classify_lot_status(
    panels: list[PanelLotStats],
    avg_density: float,
    std_density: float,
    substrate_type: str,
    use_statistical: bool,
) -> tuple[str, str]:
    """
    Return (lot_status, reason).

    Parameters
    ----------
    panels           : Per-panel stats
    avg_density      : Lot mean defect density
    std_density      : Lot std dev of defect density
    substrate_type   : For fallback threshold lookup
    use_statistical  : True if enough panels for σ-based thresholds
    """
    profile = get_profile(substrate_type)

    # Check clustering excursions first — highest priority
    excursion_panels = [
        p for p in panels if p.cluster_class == "excursion"
    ]
    systematic_panels = [
        p for p in panels if p.cluster_class == "systematic"
    ]

    if excursion_panels:
        return (
            "excursion",
            f"{len(excursion_panels)} panel(s) flagged as spatial excursion "
            f"by clustering analysis: "
            f"{', '.join(p.panel_id for p in excursion_panels)}"
        )

    if use_statistical:
        # MAD-based thresholds — robust to outliers, standard in fab SPC
        # Uses median + MAD rather than mean + std to avoid outlier inflation
        densities_list = [p.defect_density for p in panels]
        med = _median(densities_list)
        mad = _mad(densities_list, med)

        excursion_threshold = med + 3.0 * max(mad, med * 0.05)
        watch_threshold     = med + 2.0 * max(mad, med * 0.05)

        above_excursion = [
            p for p in panels if p.defect_density > excursion_threshold
        ]
        above_watch = [
            p for p in panels
            if watch_threshold < p.defect_density <= excursion_threshold
        ]

        if above_excursion:
            return (
                "excursion",
                f"{len(above_excursion)} panel(s) exceed 3×MAD density threshold "
                f"({excursion_threshold:.4f} def/mm²): "
                f"{', '.join(p.panel_id for p in above_excursion)}"
            )
        if above_watch or systematic_panels:
            reason_parts = []
            if above_watch:
                reason_parts.append(
                    f"{len(above_watch)} panel(s) between 2×MAD–3×MAD"
                )
            if systematic_panels:
                reason_parts.append(
                    f"{len(systematic_panels)} panel(s) show systematic clustering"
                )
            return ("watch", "; ".join(reason_parts))

    else:
        # Fallback fixed threshold
        die_area = profile.component_pitch_mm ** 2
        base_density = profile.mean_defect_count / die_area
        excursion_threshold = base_density * _FALLBACK_EXCURSION_MULTIPLIER

        above = [p for p in panels if p.defect_density > excursion_threshold]
        if above:
            return (
                "excursion",
                f"Density exceeds fixed threshold ({excursion_threshold:.4f} def/mm²) "
                f"[fallback — lot has < 3 panels]"
            )

        if systematic_panels:
            return (
                "watch",
                f"{len(systematic_panels)} panel(s) show systematic clustering"
            )

    return ("clean", "All panels within normal process variation.")


# ---------------------------------------------------------------------------
# Main lot tracker
# ---------------------------------------------------------------------------

def summarise_lot(
    conn: Connection,
    lot_id: str,
    *,
    persist: bool = True,
) -> LotSummary:
    """
    Compute lot-level yield and defect statistics.

    Pulls yield estimates and clustering results for all panels in the lot.
    Classifies the lot as clean, watch, or excursion.

    Parameters
    ----------
    conn     : Database connection
    lot_id   : Lot ID to summarise
    persist  : Save result to lot_summaries table

    Returns
    -------
    LotSummary
    """
    ph = get_placeholder(conn)

    # Verify lot exists
    lot = conn.execute(
        f"SELECT * FROM lots WHERE lot_id={ph}", (lot_id,)
    ).fetchone()
    if lot is None:
        raise ValueError(f"Lot not found: {lot_id!r}")

    substrate_type = lot["substrate_type"]

    # Get all panels in lot
    panel_rows = conn.execute(
        f"SELECT panel_id FROM panels WHERE lot_id={ph}", (lot_id,)
    ).fetchall()
    panel_ids = [r["panel_id"] for r in panel_rows]

    if not panel_ids:
        raise ValueError(f"Lot {lot_id!r} has no panels.")

    # Gather per-panel stats
    panels: list[PanelLotStats] = []
    for pid in panel_ids:
        # Latest yield estimate
        ye = conn.execute(
            f"SELECT defect_density, yield_negbinom FROM yield_estimates "
            f"WHERE panel_id={ph} ORDER BY calculated_at DESC LIMIT 1",
            (pid,)
        ).fetchone()

        # Latest clustering result
        cr = conn.execute(
            f"SELECT classification FROM cluster_results "
            f"WHERE panel_id={ph} ORDER BY calculated_at DESC LIMIT 1",
            (pid,)
        ).fetchone()

        density      = ye["defect_density"]  if ye else 0.0
        yield_nb     = ye["yield_negbinom"]  if ye else None
        cluster_cls  = cr["classification"]  if cr else None

        panels.append(PanelLotStats(
            panel_id=pid,
            defect_density=density,
            yield_negbinom=yield_nb,
            cluster_class=cluster_cls,
        ))

    # Lot-level statistics
    densities   = [p.defect_density  for p in panels]
    yields      = [p.yield_negbinom  for p in panels if p.yield_negbinom is not None]

    avg_density = _mean(densities)
    std_density = _std(densities, avg_density)
    avg_yield   = _mean(yields)   if yields else None
    std_yield   = _std(yields, avg_yield) if len(yields) >= 2 else None

    use_statistical = len(panels) >= 3

    lot_status, status_reason = _classify_lot_status(
        panels, avg_density, std_density, substrate_type, use_statistical
    )

    # Recompute excursion_count using same MAD logic as classifier
    densities_list = [p.defect_density for p in panels]
    if use_statistical:
        med_d = _median(densities_list)
        mad_d = _mad(densities_list, med_d)
        exc_thresh = med_d + 3.0 * max(mad_d, med_d * 0.05)
    else:
        profile_e = get_profile(substrate_type)
        die_area_e = profile_e.component_pitch_mm ** 2
        exc_thresh = (profile_e.mean_defect_count / die_area_e) * _FALLBACK_EXCURSION_MULTIPLIER

    excursion_count = sum(
        1 for p in panels
        if p.cluster_class == "excursion"
        or p.defect_density > exc_thresh
    )

    summary = LotSummary(
        lot_id=lot_id,
        substrate_type=substrate_type,
        panel_count=len(panels),
        panels=panels,
        avg_defect_density=avg_density,
        std_defect_density=std_density,
        avg_yield_negbinom=avg_yield,
        std_yield_negbinom=std_yield,
        excursion_count=excursion_count,
        lot_status=lot_status,
        status_reason=status_reason,
    )

    logger.info(
        "[Lot %s] %d panels | avg_density=%.4f | avg_yield=%.1f%% | "
        "status=%s",
        lot_id, len(panels), avg_density,
        (avg_yield or 0) * 100, lot_status.upper(),
    )

    if persist:
        _save_lot_summary(conn, summary)

    return summary


def summarise_all_lots(
    conn: Connection,
    *,
    substrate_type: str | None = None,
    persist: bool = True,
) -> list[LotSummary]:
    """Summarise all lots, optionally filtered by substrate type."""
    ph = get_placeholder(conn)
    if substrate_type:
        rows = conn.execute(
            f"SELECT lot_id FROM lots WHERE substrate_type={ph}",
            (substrate_type,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT lot_id FROM lots").fetchall()

    summaries = []
    for row in rows:
        try:
            s = summarise_lot(conn, row["lot_id"], persist=persist)
            summaries.append(s)
        except Exception as exc:
            logger.error("Lot summary failed for %s: %s", row["lot_id"], exc)
    return summaries


def auto_create_lot(
    conn: Connection,
    panel_id: str,
    substrate_type: str,
    product_type: str,
    lot_size: int = 25,
) -> str:
    """
    Assign a panel to a lot automatically.

    Finds an active lot of the same substrate/product type that has
    not yet reached lot_size. Creates a new lot if none exists.

    Returns the lot_id assigned.
    """
    from openyield.ingestion.ingest import upsert_lot
    ph = get_placeholder(conn)

    # Find an active lot with capacity
    row = conn.execute(
        f"""SELECT l.lot_id,
               COUNT(p.panel_id) as panel_count
            FROM lots l
            LEFT JOIN panels p ON p.lot_id = l.lot_id
            WHERE l.substrate_type={ph}
              AND l.product_type={ph}
              AND l.status='active'
            GROUP BY l.lot_id
            HAVING panel_count < l.lot_size
            ORDER BY l.created_at ASC
            LIMIT 1""",
        (substrate_type, product_type)
    ).fetchone()

    if row:
        lot_id = row["lot_id"]
        logger.debug("Assigned panel %s to existing lot %s", panel_id, lot_id)
    else:
        # Create a new lot
        prefix = "WL" if substrate_type == "wafer" else "GL"
        lot_id = f"{prefix}_{uuid.uuid4().hex[:8].upper()}"
        with conn:
            upsert_lot(conn, lot_id, substrate_type, product_type, lot_size)
        logger.info("Created new lot %s for %s / %s", lot_id, substrate_type, product_type)

    return lot_id


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _save_lot_summary(conn: Connection, summary: LotSummary) -> None:
    ph = get_placeholder(conn)
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute(
            f"INSERT INTO lot_summaries "
            f"(lot_id, calculated_at, panel_count, avg_defect_density, "
            f"std_defect_density, avg_yield_negbinom, std_yield_negbinom, "
            f"excursion_count, lot_status) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
            (
                summary.lot_id, now,
                summary.panel_count,
                round(summary.avg_defect_density, 8),
                round(summary.std_defect_density, 8),
                round(summary.avg_yield_negbinom, 6) if summary.avg_yield_negbinom else None,
                round(summary.std_yield_negbinom, 6) if summary.std_yield_negbinom else None,
                summary.excursion_count,
                summary.lot_status,
            )
        )


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------

def print_lot_report(summaries: list[LotSummary]) -> None:
    if not summaries:
        print("No lot summaries to report.")
        return

    print(f"\n{'='*78}")
    print(f"  LOT TRACKING REPORT  ({len(summaries)} lot(s))")
    print(f"{'='*78}")
    print(
        f"  {'Lot ID':<16} {'Sub':<12} {'Panels':>6} "
        f"{'Avg D0':>10} {'±σ':>8} {'Avg Yield':>10} {'Excur':>6}  Status"
    )
    print(
        f"  {'-'*16} {'-'*12} {'-'*6} "
        f"{'-'*10} {'-'*8} {'-'*10} {'-'*6}  {'-'*12}"
    )

    for s in summaries:
        status_flag = (
            "🚨 EXCURSION" if s.lot_status == "excursion"
            else "⚠  watch"    if s.lot_status == "watch"
            else "✓  clean"
        )
        avg_yield_str = (
            f"{s.avg_yield_negbinom*100:>9.1f}%"
            if s.avg_yield_negbinom is not None else "        N/A"
        )
        print(
            f"  {s.lot_id:<16} {s.substrate_type:<12} {s.panel_count:>6} "
            f"{s.avg_defect_density:>10.4f} "
            f"{s.std_defect_density:>8.4f} "
            f"{avg_yield_str} "
            f"{s.excursion_count:>6}  {status_flag}"
        )

    excursions = sum(1 for s in summaries if s.lot_status == "excursion")
    watches    = sum(1 for s in summaries if s.lot_status == "watch")
    print(f"{'='*78}")
    if excursions:
        print(f"  ⚠ {excursions} lot(s) require immediate engineering review.")
    elif watches:
        print(f"  ~ {watches} lot(s) require monitoring.")
    else:
        print(f"  ✓ All lots within normal process variation.")
    print()
