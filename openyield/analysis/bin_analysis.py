"""
analysis/bin_analysis.py
-------------------------
Author: Yeonkuk Woo

Spatial bin analysis for OpenYield wafer/panel map visualization.

Aggregates defect counts, yield estimates, and cluster labels at the
component-cell level to produce a grid representation suitable for
frontend heatmap rendering.

Each cell in the returned map corresponds to one (component_row,
component_col) die site on the panel. Inactive dies (edge-excluded
wafer sites) are marked active=False and rendered differently in the UI.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from openyield.db.connection import get_placeholder

logger = logging.getLogger(__name__)

Connection = Any


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class MapCell:
    row:           int
    col:           int
    active:        bool
    region_id:     str
    defect_count:  int            # system_a defects on this die
    defect_types:  dict           # {defect_type: count}
    cluster_label: int | None     # DBSCAN label; -1=noise, None=not clustered
    yield_poisson: float | None   # per-die Poisson yield estimate


@dataclass
class PanelMap:
    panel_id:       str
    substrate_type: str
    rows:           int
    cols:           int
    cells:          list[MapCell]
    # panel-level summary
    total_defects:      int
    active_dies:        int
    defect_density:     float | None
    yield_poisson:      float | None
    yield_murphy:       float | None
    yield_negbinom:     float | None
    clustering_class:   str | None


# ---------------------------------------------------------------------------
# Main function
# ---------------------------------------------------------------------------

def build_panel_map(conn: Connection, panel_id: str) -> PanelMap:
    """
    Build a full spatial map of a panel for frontend rendering.

    Queries components, defects, cluster labels, and yield estimates
    and assembles a per-die grid.

    Parameters
    ----------
    conn     : Database connection
    panel_id : Panel to map

    Returns
    -------
    PanelMap
    """
    ph = get_placeholder(conn)

    # Panel metadata
    panel = conn.execute(
        f"SELECT * FROM panels WHERE panel_id={ph}", (panel_id,)
    ).fetchone()
    if panel is None:
        raise ValueError(f"Panel not found: {panel_id!r}")

    rows      = panel["rows"]
    cols      = panel["cols"]
    substrate = panel["substrate_type"]

    # All components
    comp_rows = conn.execute(
        f"SELECT component_row, component_col, region_id, active "
        f"FROM components WHERE panel_id={ph}",
        (panel_id,)
    ).fetchall()
    comp_map = {
        (r["component_row"], r["component_col"]): r
        for r in comp_rows
    }

    # Defect counts per (row, col) — system_a only
    defect_rows = conn.execute(
        f"""SELECT component_row, component_col, defect_type, COUNT(*) as cnt
            FROM defects
            WHERE panel_id={ph} AND source_system='system_a'
            GROUP BY component_row, component_col, defect_type""",
        (panel_id,)
    ).fetchall()

    # Aggregate: {(row, col): {type: count}}
    defect_map: dict[tuple, dict] = {}
    for dr in defect_rows:
        key = (dr["component_row"], dr["component_col"])
        defect_map.setdefault(key, {})[dr["defect_type"]] = dr["cnt"]

    # Cluster labels per defect — map to die level
    cluster_rows = conn.execute(
        f"""SELECT d.component_row, d.component_col, dc.cluster_label
            FROM defect_clusters dc
            JOIN defects d ON d.defect_id = dc.defect_id
            WHERE dc.panel_id={ph} AND d.source_system='system_a'""",
        (panel_id,)
    ).fetchall()

    # For each die, the dominant cluster label (most common non-noise label,
    # or -1 if all noise, or None if no cluster data)
    die_cluster: dict[tuple, list[int]] = {}
    for cr in cluster_rows:
        key = (cr["component_row"], cr["component_col"])
        die_cluster.setdefault(key, []).append(cr["cluster_label"])

    def dominant_label(labels: list[int]) -> int:
        non_noise = [l for l in labels if l >= 0]
        if non_noise:
            return max(set(non_noise), key=non_noise.count)
        return -1

    # Latest yield estimate for panel-level summary
    ye = conn.execute(
        f"SELECT * FROM yield_estimates WHERE panel_id={ph} "
        f"ORDER BY calculated_at DESC LIMIT 1",
        (panel_id,)
    ).fetchone()

    # Latest clustering classification
    cr_row = conn.execute(
        f"SELECT classification FROM cluster_results WHERE panel_id={ph} "
        f"ORDER BY calculated_at DESC LIMIT 1",
        (panel_id,)
    ).fetchone()
    clustering_class = cr_row["classification"] if cr_row else None

    # Build cells
    cells: list[MapCell] = []
    total_defects = 0
    active_dies   = 0

    for r in range(rows):
        for c in range(cols):
            comp = comp_map.get((r, c))
            active    = bool(comp["active"])    if comp else False
            region_id = comp["region_id"]       if comp else ""

            d_types   = defect_map.get((r, c), {})
            d_count   = sum(d_types.values())
            c_labels  = die_cluster.get((r, c))
            c_label   = dominant_label(c_labels) if c_labels is not None else None

            # Per-die Poisson yield estimate (rough: use panel D0 and die area)
            die_yield = None
            if ye and active:
                import math
                die_area = ye["die_area_mm2"]
                d0       = ye["defect_density"]
                if die_area > 0 and d0 >= 0:
                    die_yield = round(math.exp(-die_area * d0), 6)

            cells.append(MapCell(
                row=r, col=c,
                active=active, region_id=region_id,
                defect_count=d_count, defect_types=d_types,
                cluster_label=c_label,
                yield_poisson=die_yield,
            ))

            total_defects += d_count
            if active:
                active_dies += 1

    return PanelMap(
        panel_id=panel_id,
        substrate_type=substrate,
        rows=rows, cols=cols,
        cells=cells,
        total_defects=total_defects,
        active_dies=active_dies,
        defect_density=ye["defect_density"]   if ye else None,
        yield_poisson=ye["yield_poisson"]     if ye else None,
        yield_murphy=ye["yield_murphy"]       if ye else None,
        yield_negbinom=ye["yield_negbinom"]   if ye else None,
        clustering_class=clustering_class,
    )
