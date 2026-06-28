"""
analysis/correlation.py
------------------------
Author: Yeonkuk Woo

Wafer-to-wafer defect correlation for OpenYield.

Finds defects that repeat at the same die coordinates across multiple
panels in a lot or substrate group. Repeated defects at the same location
are systematic — caused by a fixed source such as:

  - Reticle defect    : same (row, col) every wafer, same defect type
  - Chuck particle    : same location, may vary by type
  - Edge ring wear    : pattern of repeated edge-zone defects
  - Mask defect       : regular grid pattern of repeating locations

Correlation metrics
--------------------
For each (component_row, component_col) die position:

  repeat_count   : how many panels show a defect at this location
  repeat_rate    : repeat_count / total_panels (0–1)
  dominant_type  : most common defect type at this location
  type_consistency: fraction of occurrences with the dominant type

A location is flagged as systematic if:
  repeat_rate >= threshold (default 0.5 — appears in >50% of panels)

This directly replicates KLA Klarity's "systematic defect separation"
which is one of its highest-value features for advanced node fabs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from openyield.db.connection import get_placeholder

logger = logging.getLogger(__name__)
Connection = Any


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class RepeatLocation:
    component_row:    int
    component_col:    int
    region_id:        str
    repeat_count:     int       # panels with defect here
    repeat_rate:      float     # repeat_count / total_panels
    dominant_type:    str       # most frequent defect type here
    type_consistency: float     # fraction of occurrences = dominant_type
    panel_ids:        list[str] # which panels had defects here


@dataclass
class CorrelationResult:
    lot_id:           str | None
    substrate_type:   str | None
    total_panels:     int
    total_locations:  int       # total unique die positions checked
    repeat_threshold: float
    systematic_locations: list[RepeatLocation]
    systematic_count: int
    systematic_rate:  float     # systematic_locations / total_locations
    calculated_at:    str
    classification:   str       # 'clean', 'reticle_suspect', 'tool_suspect'
    classification_reason: str


# ---------------------------------------------------------------------------
# Core calculation
# ---------------------------------------------------------------------------

def calculate_correlation(
    conn: Connection,
    *,
    lot_id:           str | None = None,
    substrate_type:   str | None = None,
    source_system:    str = "system_a",
    repeat_threshold: float = 0.5,
) -> CorrelationResult:
    """
    Find systematically repeating defect locations across panels.

    Parameters
    ----------
    conn             : Database connection
    lot_id           : Restrict to panels in a specific lot
    substrate_type   : Restrict to one substrate type
    source_system    : Which inspection system to analyse (default: system_a)
    repeat_threshold : Min fraction of panels for a location to be systematic
                       (default 0.5 = appears in >50% of panels)

    Returns
    -------
    CorrelationResult
    """
    ph = get_placeholder(conn)

    # Get panels in scope
    filters, params = [], []
    if lot_id:
        filters.append(f"lot_id = {ph}")
        params.append(lot_id)
    if substrate_type:
        filters.append(f"substrate_type = {ph}")
        params.append(substrate_type)

    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    panel_rows = conn.execute(
        f"SELECT panel_id FROM panels {where}", params
    ).fetchall()
    panel_ids  = [r["panel_id"] for r in panel_rows]
    total_panels = len(panel_ids)

    if total_panels < 2:
        return CorrelationResult(
            lot_id=lot_id,
            substrate_type=substrate_type,
            total_panels=total_panels,
            total_locations=0,
            repeat_threshold=repeat_threshold,
            systematic_locations=[],
            systematic_count=0,
            systematic_rate=0.0,
            calculated_at=datetime.now(timezone.utc).isoformat(),
            classification="insufficient_data",
            classification_reason=(
                f"Need at least 2 panels for correlation analysis "
                f"(found {total_panels})."
            ),
        )

    # Build placeholders for IN clause
    panel_ph = ", ".join([ph] * len(panel_ids))

    # Aggregate defects per (panel, row, col, type)
    sql = f"""
        SELECT
            d.panel_id,
            d.component_row,
            d.component_col,
            d.defect_type,
            c.region_id,
            COUNT(*) as n
        FROM defects d
        JOIN components c
          ON c.panel_id      = d.panel_id
         AND c.component_row = d.component_row
         AND c.component_col = d.component_col
        WHERE d.panel_id IN ({panel_ph})
          AND d.source_system = {ph}
          AND c.active = 1
        GROUP BY
            d.panel_id, d.component_row, d.component_col, d.defect_type
    """
    rows = conn.execute(sql, panel_ids + [source_system]).fetchall()

    # Build location map: (row, col) → {panel_id: [defect_types]}
    loc_map: dict[tuple, dict] = {}
    region_map: dict[tuple, str] = {}

    for row in rows:
        key = (row["component_row"], row["component_col"])
        region_map[key] = row["region_id"]
        if key not in loc_map:
            loc_map[key] = {}
        pid = row["panel_id"]
        if pid not in loc_map[key]:
            loc_map[key][pid] = []
        loc_map[key][pid].append(row["defect_type"])

    total_locations = len(loc_map)

    # Find systematic locations
    systematic: list[RepeatLocation] = []

    for (r, c), panel_defects in loc_map.items():
        repeat_count = len(panel_defects)
        repeat_rate  = repeat_count / total_panels

        if repeat_rate < repeat_threshold:
            continue

        # Find dominant defect type
        type_counts: dict[str, int] = {}
        for types in panel_defects.values():
            for t in types:
                type_counts[t] = type_counts.get(t, 0) + 1

        total_type_obs = sum(type_counts.values())
        dominant_type  = max(type_counts, key=type_counts.get)
        type_consistency = type_counts[dominant_type] / max(total_type_obs, 1)

        systematic.append(RepeatLocation(
            component_row=r,
            component_col=c,
            region_id=region_map.get((r, c), "unknown"),
            repeat_count=repeat_count,
            repeat_rate=round(repeat_rate, 4),
            dominant_type=dominant_type,
            type_consistency=round(type_consistency, 4),
            panel_ids=list(panel_defects.keys()),
        ))

    # Sort by repeat_rate descending
    systematic.sort(key=lambda x: x.repeat_rate, reverse=True)
    systematic_count = len(systematic)
    systematic_rate  = systematic_count / max(total_locations, 1)

    # Classify the pattern
    classification, reason = _classify_correlation(
        systematic, total_panels, systematic_rate
    )

    logger.info(
        "Correlation [lot=%s sub=%s]: %d panels | %d locations | "
        "%d systematic (%.1f%%) | class=%s",
        lot_id, substrate_type, total_panels, total_locations,
        systematic_count, systematic_rate * 100, classification,
    )

    return CorrelationResult(
        lot_id=lot_id,
        substrate_type=substrate_type,
        total_panels=total_panels,
        total_locations=total_locations,
        repeat_threshold=repeat_threshold,
        systematic_locations=systematic,
        systematic_count=systematic_count,
        systematic_rate=round(systematic_rate, 4),
        calculated_at=datetime.now(timezone.utc).isoformat(),
        classification=classification,
        classification_reason=reason,
    )


def _classify_correlation(
    systematic: list[RepeatLocation],
    total_panels: int,
    systematic_rate: float,
) -> tuple[str, str]:
    """Classify the systematic defect pattern."""
    if not systematic:
        return (
            "clean",
            "No systematic repeating locations found. "
            "Defect distribution appears random across panels."
        )

    # Check for reticle-like pattern: multiple locations with same type,
    # very high consistency, appearing in all or nearly all panels
    high_consistency = [
        s for s in systematic
        if s.type_consistency > 0.8 and s.repeat_rate > 0.7
    ]

    # Check for type consistency across systematic locations
    if high_consistency:
        types = [s.dominant_type for s in high_consistency]
        if len(set(types)) == 1:
            return (
                "reticle_suspect",
                f"{len(high_consistency)} location(s) with same defect type "
                f"'{types[0]}' repeating at >{70:.0f}% rate with "
                f">{80:.0f}% type consistency. Consistent with reticle defect."
            )

    # Tool/chuck particle: multiple locations, varying types
    if systematic_rate > 0.05:
        return (
            "tool_suspect",
            f"{len(systematic)} systematic location(s) ({systematic_rate*100:.1f}% "
            f"of die positions). Varying defect types suggest tool or "
            f"chuck contamination rather than a reticle defect."
        )

    return (
        "minor_systematic",
        f"{len(systematic)} systematic location(s) found but rate is low "
        f"({systematic_rate*100:.1f}%). Monitor for trend."
    )


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------

def print_correlation_report(result: CorrelationResult) -> None:
    scope = result.lot_id or result.substrate_type or "all panels"
    print(f"\n{'='*72}")
    print(f"  WAFER-TO-WAFER CORRELATION — {scope}")
    print(
        f"  Panels: {result.total_panels} | "
        f"Locations checked: {result.total_locations} | "
        f"Threshold: {result.repeat_threshold*100:.0f}%"
    )
    print(f"  Classification: {result.classification.upper()}")
    print(f"  {result.classification_reason}")
    print(f"{'='*72}")

    if not result.systematic_locations:
        print("  No systematic locations found.\n")
        return

    print(
        f"  {'Row':>4} {'Col':>4} {'Region':<14} {'Rate':>6} "
        f"{'Panels':>6} {'DomType':<18} {'Consist':>8}"
    )
    print(
        f"  {'-'*4} {'-'*4} {'-'*14} {'-'*6} "
        f"{'-'*6} {'-'*18} {'-'*8}"
    )

    for loc in result.systematic_locations[:20]:  # top 20
        print(
            f"  {loc.component_row:>4} {loc.component_col:>4} "
            f"{loc.region_id:<14} {loc.repeat_rate*100:>5.1f}% "
            f"{loc.repeat_count:>6} {loc.dominant_type:<18} "
            f"{loc.type_consistency*100:>7.1f}%"
        )

    if len(result.systematic_locations) > 20:
        print(f"  ... and {len(result.systematic_locations)-20} more locations")

    print(f"{'='*72}\n")
