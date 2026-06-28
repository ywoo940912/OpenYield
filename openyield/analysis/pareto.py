"""
analysis/pareto.py
------------------
Author: Yeonkuk Woo

Defect Pareto analysis weighted by yield impact for OpenYield.

Three Pareto modes
-------------------
1. Overall Pareto        — all defects, ranked by yield impact proxy
2. Zone Pareto           — separate ranking per spatial zone/region
3. System comparison     — system_a vs system_b side-by-side
                           reveals which types are real vs false positives

Yield-impact weighting
-----------------------
    impact_score = count × avg_size_mm × avg_confidence

This is a proxy for critical area contribution. avg_confidence acts as
a kill probability estimate — high-confidence defects are more likely
to be real killers. A future version will accept per-defect-type
critical_area_fraction once process layer data is available.

Lot trend Pareto
-----------------
Groups panels by lot and computes impact scores per lot, returning a
time-ordered sequence of Pareto snapshots. Shows whether a defect type
is improving or degrading across process time.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from openyield.db.connection import get_placeholder
from openyield.synthetic.substrate_profiles import get_profile

logger = logging.getLogger(__name__)
Connection = Any


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class DefectTypeStats:
    defect_type:          str
    count:                int
    avg_size_mm:          float
    avg_confidence:       float
    impact_score:         float
    impact_fraction:      float
    cumulative_fraction:  float
    yield_loss_estimate:  float
    rank:                 int


@dataclass
class ParetoResult:
    panel_id:        str | None
    substrate_type:  str | None
    source_system:   str
    calculated_at:   str
    total_defects:   int
    items:           list[DefectTypeStats]
    vital_few:       list[str]
    trivial_many:    list[str]
    zone:            str | None = None   # set when zone-filtered


@dataclass
class ZoneParetoResult:
    """Pareto broken down by spatial zone/region."""
    panel_id:        str | None
    substrate_type:  str | None
    source_system:   str
    calculated_at:   str
    zones:           dict[str, ParetoResult]   # zone_name → ParetoResult


@dataclass
class SystemComparisonItem:
    defect_type:       str
    count_a:           int
    count_b:           int
    impact_a:          float
    impact_b:          float
    match_rate:        float    # fraction of system_a confirmed by system_b
    likely_real:       bool     # True if system_b confirms significantly
    rank_a:            int
    rank_b:            int


@dataclass
class SystemComparisonResult:
    panel_id:         str | None
    substrate_type:   str | None
    calculated_at:    str
    total_a:          int
    total_b:          int
    items:            list[SystemComparisonItem]
    nuisance_suspects: list[str]   # types mostly in system_a, unconfirmed by b
    confirmed_killers: list[str]   # types with high system_b confirmation


@dataclass
class LotTrendPoint:
    lot_id:          str
    calculated_at:   str
    panel_count:     int
    defect_type:     str
    count:           int
    impact_score:    float
    impact_fraction: float
    yield_loss:      float


@dataclass
class LotTrendResult:
    substrate_type:  str | None
    defect_types:    list[str]          # all types seen across lots
    trend:           list[LotTrendPoint]  # ordered by lot creation time
    improving:       list[str]          # types with declining impact trend
    degrading:       list[str]          # types with increasing impact trend


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _get_die_area_and_active(
    conn: Connection,
    panel_id: str | None,
    substrate_type: str | None,
) -> tuple[float, int]:
    """Return (die_area_mm2, active_die_count)."""
    try:
        st = substrate_type
        if st is None and panel_id:
            row = conn.execute(
                "SELECT substrate_type FROM panels WHERE panel_id=?", (panel_id,)
            ).fetchone()
            st = row["substrate_type"] if row else None
        if st:
            profile  = get_profile(st)
            die_area = profile.component_pitch_mm ** 2
        else:
            die_area = 1.0

        if panel_id:
            active = conn.execute(
                "SELECT COUNT(*) FROM components WHERE panel_id=? AND active=1",
                (panel_id,)
            ).fetchone()[0]
        else:
            active = conn.execute(
                "SELECT COUNT(*) FROM components WHERE active=1"
            ).fetchone()[0]

        return die_area, max(active, 1)
    except Exception:
        return 1.0, 1


def _build_items(
    rows: list,
    die_area: float,
    total_area: float,
) -> tuple[list[DefectTypeStats], list[str], list[str]]:
    """Convert raw DB rows to ranked DefectTypeStats list."""
    if not rows:
        return [], [], []

    raw = []
    for r in rows:
        impact = r["count"] * r["avg_size_mm"] * r["avg_confidence"]
        raw.append({
            "defect_type":    r["defect_type"],
            "count":          r["count"],
            "avg_size_mm":    r["avg_size_mm"],
            "avg_confidence": r["avg_confidence"],
            "impact_score":   impact,
        })

    total_impact = max(sum(x["impact_score"] for x in raw), 1e-12)
    raw.sort(key=lambda x: x["impact_score"], reverse=True)

    items: list[DefectTypeStats] = []
    cumulative = 0.0
    vital_few:    list[str] = []
    trivial_many: list[str] = []

    for rank, item in enumerate(raw, start=1):
        frac       = item["impact_score"] / total_impact
        cumulative += frac
        density    = item["count"] / total_area
        yield_loss = 1.0 - math.exp(-die_area * density)

        ds = DefectTypeStats(
            defect_type=item["defect_type"],
            count=item["count"],
            avg_size_mm=round(item["avg_size_mm"], 4),
            avg_confidence=round(item["avg_confidence"], 4),
            impact_score=round(item["impact_score"], 4),
            impact_fraction=round(frac, 4),
            cumulative_fraction=round(min(cumulative, 1.0), 4),
            yield_loss_estimate=round(yield_loss, 6),
            rank=rank,
        )
        items.append(ds)

        if cumulative <= 0.801:
            vital_few.append(item["defect_type"])
        else:
            trivial_many.append(item["defect_type"])

    return items, vital_few, trivial_many


def _query_defect_agg(
    conn: Connection,
    source_system: str,
    panel_id: str | None,
    substrate_type: str | None,
    zone: str | None,
) -> list:
    """Aggregate defects by type with optional zone filter."""
    ph = get_placeholder(conn)
    filters = [f"d.source_system = {ph}"]
    params: list[Any] = [source_system]

    if panel_id:
        filters.append(f"d.panel_id = {ph}")
        params.append(panel_id)
    if substrate_type:
        filters.append(f"p.substrate_type = {ph}")
        params.append(substrate_type)
    if zone:
        filters.append(f"c.region_id = {ph}")
        params.append(zone)

    where = "WHERE " + " AND ".join(filters)
    sql = f"""
        SELECT
            d.defect_type,
            COUNT(*)                AS count,
            AVG(d.size)             AS avg_size_mm,
            AVG(d.confidence_score) AS avg_confidence
        FROM defects d
        JOIN panels p     ON p.panel_id = d.panel_id
        JOIN components c
          ON c.panel_id      = d.panel_id
         AND c.component_row = d.component_row
         AND c.component_col = d.component_col
        {where}
          AND c.active = 1
        GROUP BY d.defect_type
        ORDER BY count DESC
    """
    return conn.execute(sql, params).fetchall()


# ---------------------------------------------------------------------------
# 1. Overall Pareto
# ---------------------------------------------------------------------------

def calculate_pareto(
    conn: Connection,
    *,
    panel_id:       str | None = None,
    substrate_type: str | None = None,
    source_system:  str = "system_a",
) -> ParetoResult:
    """Yield-impact Pareto across all defects."""
    rows = _query_defect_agg(conn, source_system, panel_id, substrate_type, None)
    die_area, active = _get_die_area_and_active(conn, panel_id, substrate_type)
    items, vital, trivial = _build_items(rows, die_area, active * die_area)
    total = sum(i.count for i in items)

    logger.info(
        "Pareto [%s/%s/%s]: %d types | %d defects | vital=%s",
        panel_id or "all", substrate_type or "all", source_system,
        len(items), total, vital,
    )
    return ParetoResult(
        panel_id=panel_id,
        substrate_type=substrate_type,
        source_system=source_system,
        calculated_at=datetime.now(timezone.utc).isoformat(),
        total_defects=total,
        items=items,
        vital_few=vital,
        trivial_many=trivial,
    )


# ---------------------------------------------------------------------------
# 2. Zone Pareto
# ---------------------------------------------------------------------------

def calculate_zone_pareto(
    conn: Connection,
    *,
    panel_id:       str | None = None,
    substrate_type: str | None = None,
    source_system:  str = "system_a",
) -> ZoneParetoResult:
    """
    Separate Pareto per spatial zone/region.

    Wafer   : zone_center, zone_mid, zone_edge
    Glass   : region_NW, region_NE, region_SW, region_SE
    """
    ph = get_placeholder(conn)

    # Get all distinct zones in scope
    filters, params = ["c.active = 1"], []
    if panel_id:
        filters.append(f"c.panel_id = {ph}")
        params.append(panel_id)
    if substrate_type:
        filters.append(f"p.substrate_type = {ph}")
        params.append(substrate_type)

    where = "WHERE " + " AND ".join(filters)
    zone_rows = conn.execute(
        f"SELECT DISTINCT c.region_id FROM components c "
        f"JOIN panels p ON p.panel_id = c.panel_id {where}",
        params
    ).fetchall()
    zones = [r["region_id"] for r in zone_rows]

    die_area, active_total = _get_die_area_and_active(conn, panel_id, substrate_type)
    zone_results: dict[str, ParetoResult] = {}

    for zone in sorted(zones):
        rows = _query_defect_agg(
            conn, source_system, panel_id, substrate_type, zone
        )
        # Active dies in this zone
        zone_filter_params = params.copy()
        zone_filter_params.append(zone)
        zone_active = conn.execute(
            f"SELECT COUNT(*) FROM components c "
            f"JOIN panels p ON p.panel_id = c.panel_id "
            f"{where} AND c.region_id = {ph}",
            zone_filter_params
        ).fetchone()[0]

        total_area = max(zone_active * die_area, die_area)
        items, vital, trivial = _build_items(rows, die_area, total_area)
        total = sum(i.count for i in items)

        zone_results[zone] = ParetoResult(
            panel_id=panel_id,
            substrate_type=substrate_type,
            source_system=source_system,
            calculated_at=datetime.now(timezone.utc).isoformat(),
            total_defects=total,
            items=items,
            vital_few=vital,
            trivial_many=trivial,
            zone=zone,
        )

    logger.info(
        "Zone Pareto [%s/%s]: %d zones",
        panel_id or "all", substrate_type or "all", len(zones)
    )
    return ZoneParetoResult(
        panel_id=panel_id,
        substrate_type=substrate_type,
        source_system=source_system,
        calculated_at=datetime.now(timezone.utc).isoformat(),
        zones=zone_results,
    )


# ---------------------------------------------------------------------------
# 3. System A vs B comparison
# ---------------------------------------------------------------------------

def calculate_system_comparison(
    conn: Connection,
    *,
    panel_id:       str | None = None,
    substrate_type: str | None = None,
) -> SystemComparisonResult:
    """
    Side-by-side system_a vs system_b Pareto.

    match_rate = defects of this type in system_b / defects in system_a
    High match_rate → type is confirmed real by the review tool
    Low match_rate  → type is likely a false positive / nuisance in system_a
    """
    rows_a = _query_defect_agg(conn, "system_a", panel_id, substrate_type, None)
    rows_b = _query_defect_agg(conn, "system_b", panel_id, substrate_type, None)

    die_area, active = _get_die_area_and_active(conn, panel_id, substrate_type)
    total_area = max(active * die_area, die_area)

    # Build impact maps
    def _impact_map(rows):
        m = {}
        total_impact = max(
            sum(r["count"] * r["avg_size_mm"] * r["avg_confidence"] for r in rows),
            1e-12
        )
        for rank, r in enumerate(
            sorted(rows,
                   key=lambda x: x["count"]*x["avg_size_mm"]*x["avg_confidence"],
                   reverse=True),
            start=1
        ):
            impact = r["count"] * r["avg_size_mm"] * r["avg_confidence"]
            m[r["defect_type"]] = {
                "count": r["count"],
                "impact": round(impact, 4),
                "rank": rank,
                "frac": round(impact / total_impact, 4),
            }
        return m

    map_a = _impact_map(rows_a)
    map_b = _impact_map(rows_b)
    total_a = sum(v["count"] for v in map_a.values())
    total_b = sum(v["count"] for v in map_b.values())

    all_types = set(map_a) | set(map_b)
    items: list[SystemComparisonItem] = []

    for dtype in all_types:
        a = map_a.get(dtype, {"count": 0, "impact": 0.0, "rank": 999})
        b = map_b.get(dtype, {"count": 0, "impact": 0.0, "rank": 999})

        match_rate = (b["count"] / a["count"]) if a["count"] > 0 else 0.0
        match_rate = min(match_rate, 1.0)

        # "Likely real" if system_b confirms >30% of system_a detections
        likely_real = match_rate >= 0.30

        items.append(SystemComparisonItem(
            defect_type=dtype,
            count_a=a["count"],
            count_b=b["count"],
            impact_a=a["impact"],
            impact_b=b["impact"],
            match_rate=round(match_rate, 4),
            likely_real=likely_real,
            rank_a=a["rank"],
            rank_b=b["rank"],
        ))

    items.sort(key=lambda x: x.impact_a, reverse=True)

    nuisance_suspects = [
        i.defect_type for i in items
        if not i.likely_real and i.count_a >= 3
    ]
    confirmed_killers = [
        i.defect_type for i in items
        if i.likely_real and i.count_a >= 3
    ]

    logger.info(
        "System comparison [%s/%s]: %d types | confirmed=%d | nuisance=%d",
        panel_id or "all", substrate_type or "all",
        len(items), len(confirmed_killers), len(nuisance_suspects),
    )
    return SystemComparisonResult(
        panel_id=panel_id,
        substrate_type=substrate_type,
        calculated_at=datetime.now(timezone.utc).isoformat(),
        total_a=total_a,
        total_b=total_b,
        items=items,
        nuisance_suspects=nuisance_suspects,
        confirmed_killers=confirmed_killers,
    )


# ---------------------------------------------------------------------------
# 4. Lot trend Pareto
# ---------------------------------------------------------------------------

def calculate_lot_trend(
    conn: Connection,
    *,
    substrate_type: str | None = None,
    source_system:  str = "system_a",
    top_n_types:    int = 5,
) -> LotTrendResult:
    """
    Pareto trend across lots ordered by creation time.

    Returns per-lot impact scores for the top N defect types,
    enabling before/after comparison across process changes.
    """
    ph = get_placeholder(conn)

    # Get lots ordered by creation time
    if substrate_type:
        lot_rows = conn.execute(
            f"SELECT lot_id, created_at FROM lots "
            f"WHERE substrate_type={ph} ORDER BY created_at ASC",
            (substrate_type,)
        ).fetchall()
    else:
        lot_rows = conn.execute(
            "SELECT lot_id, created_at FROM lots ORDER BY created_at ASC"
        ).fetchall()

    if not lot_rows:
        return LotTrendResult(
            substrate_type=substrate_type,
            defect_types=[],
            trend=[],
            improving=[],
            degrading=[],
        )

    die_area, _ = _get_die_area_and_active(conn, None, substrate_type)

    # Get overall top N types first (to track consistently)
    overall = calculate_pareto(
        conn, substrate_type=substrate_type, source_system=source_system
    )
    top_types = [i.defect_type for i in overall.items[:top_n_types]]

    trend: list[LotTrendPoint] = []

    for lot_row in lot_rows:
        lot_id = lot_row["lot_id"]

        # Get panels in this lot
        panel_rows = conn.execute(
            f"SELECT panel_id FROM panels WHERE lot_id={ph}", (lot_id,)
        ).fetchall()
        if not panel_rows:
            continue

        panel_ids = [r["panel_id"] for r in panel_rows]
        panel_ph  = ", ".join([ph] * len(panel_ids))

        # Active dies in lot
        active_lot = conn.execute(
            f"SELECT COUNT(*) FROM components "
            f"WHERE panel_id IN ({panel_ph}) AND active=1",
            panel_ids
        ).fetchone()[0]
        total_area = max(active_lot * die_area, die_area)

        # Defect agg for this lot
        type_rows = conn.execute(
            f"""SELECT d.defect_type,
                       COUNT(*)                AS count,
                       AVG(d.size)             AS avg_size_mm,
                       AVG(d.confidence_score) AS avg_confidence
                FROM defects d
                JOIN components c
                  ON c.panel_id=d.panel_id
                 AND c.component_row=d.component_row
                 AND c.component_col=d.component_col
                WHERE d.panel_id IN ({panel_ph})
                  AND d.source_system={ph}
                  AND c.active=1
                GROUP BY d.defect_type""",
            panel_ids + [source_system]
        ).fetchall()

        type_map = {
            r["defect_type"]: r for r in type_rows
        }
        total_impact = max(
            sum(
                r["count"] * r["avg_size_mm"] * r["avg_confidence"]
                for r in type_rows
            ),
            1e-12
        )

        for dtype in top_types:
            r = type_map.get(dtype)
            count  = r["count"] if r else 0
            impact = (r["count"] * r["avg_size_mm"] * r["avg_confidence"]
                      if r else 0.0)
            density    = count / total_area
            yield_loss = 1.0 - math.exp(-die_area * density)

            trend.append(LotTrendPoint(
                lot_id=lot_id,
                calculated_at=str(lot_row["created_at"]),
                panel_count=len(panel_ids),
                defect_type=dtype,
                count=count,
                impact_score=round(impact, 4),
                impact_fraction=round(impact / total_impact, 4),
                yield_loss=round(yield_loss, 6),
            ))

    # Detect improving/degrading trends (simple first-half vs second-half)
    improving, degrading = _detect_trend_direction(trend, top_types, lot_rows)

    logger.info(
        "Lot trend [%s]: %d lots | %d types | improving=%s | degrading=%s",
        substrate_type or "all", len(lot_rows), len(top_types),
        improving, degrading,
    )
    return LotTrendResult(
        substrate_type=substrate_type,
        defect_types=top_types,
        trend=trend,
        improving=improving,
        degrading=degrading,
    )


def _detect_trend_direction(
    trend: list[LotTrendPoint],
    types: list[str],
    lot_rows: list,
) -> tuple[list[str], list[str]]:
    """Compare first half vs second half mean impact to detect trend."""
    improving, degrading = [], []
    n_lots = len(lot_rows)
    if n_lots < 2:
        return [], []

    mid = n_lots // 2
    first_lots  = {r["lot_id"] for r in lot_rows[:mid]}
    second_lots = {r["lot_id"] for r in lot_rows[mid:]}

    for dtype in types:
        first_pts  = [p for p in trend if p.defect_type == dtype
                      and p.lot_id in first_lots]
        second_pts = [p for p in trend if p.defect_type == dtype
                      and p.lot_id in second_lots]

        if not first_pts or not second_pts:
            continue

        avg_first  = sum(p.impact_fraction for p in first_pts) / len(first_pts)
        avg_second = sum(p.impact_fraction for p in second_pts) / len(second_pts)

        if avg_second < avg_first * 0.85:
            improving.append(dtype)
        elif avg_second > avg_first * 1.15:
            degrading.append(dtype)

    return improving, degrading


# ---------------------------------------------------------------------------
# Report printers
# ---------------------------------------------------------------------------

def print_pareto_report(result: ParetoResult) -> None:
    zone_str = f" | zone: {result.zone}" if result.zone else ""
    scope = result.panel_id or result.substrate_type or "all panels"
    print(f"\n{'='*72}")
    print(f"  DEFECT PARETO — {scope} | {result.source_system}{zone_str}")
    print(f"  Total defects: {result.total_defects}")
    print(f"{'='*72}")
    print(
        f"  {'Rank':<5} {'Type':<20} {'Count':>6} {'AvgSize':>8} "
        f"{'Conf':>6} {'Impact%':>8} {'Cum%':>7} {'YieldLoss':>10}"
    )
    print(f"  {'-'*5} {'-'*20} {'-'*6} {'-'*8} {'-'*6} {'-'*8} {'-'*7} {'-'*10}")
    for item in result.items:
        marker = " ◄" if item.defect_type in result.vital_few else ""
        print(
            f"  {item.rank:<5} {item.defect_type:<20} {item.count:>6} "
            f"{item.avg_size_mm:>8.3f} {item.avg_confidence:>6.3f} "
            f"{item.impact_fraction*100:>7.1f}% "
            f"{item.cumulative_fraction*100:>6.1f}% "
            f"{item.yield_loss_estimate*100:>9.2f}%{marker}"
        )
    print(f"{'='*72}")
    print(f"  Vital few  : {', '.join(result.vital_few) or 'none'}")
    print(f"  Trivial many: {', '.join(result.trivial_many) or 'none'}")
    print()


def print_zone_pareto_report(result: ZoneParetoResult) -> None:
    print(f"\n{'='*72}")
    print(f"  ZONE PARETO — {result.substrate_type or 'all'} | {result.source_system}")
    print(f"{'='*72}")
    for zone, pr in result.zones.items():
        print(f"\n  ── {zone.upper()} ── ({pr.total_defects} defects)")
        for item in pr.items[:5]:
            marker = " ◄" if item.defect_type in pr.vital_few else ""
            print(
                f"    {item.rank}. {item.defect_type:<20} "
                f"count={item.count:>4}  "
                f"impact={item.impact_fraction*100:>5.1f}%{marker}"
            )
    print()


def print_system_comparison_report(result: SystemComparisonResult) -> None:
    scope = result.panel_id or result.substrate_type or "all panels"
    print(f"\n{'='*72}")
    print(f"  SYSTEM A vs B COMPARISON — {scope}")
    print(f"  system_a: {result.total_a} defects | system_b: {result.total_b} defects")
    print(f"{'='*72}")
    print(
        f"  {'Type':<20} {'A count':>8} {'B count':>8} "
        f"{'Match%':>8} {'Real?':>6}  {'RankA':>6} {'RankB':>6}"
    )
    print(f"  {'-'*20} {'-'*8} {'-'*8} {'-'*8} {'-'*6}  {'-'*6} {'-'*6}")
    for item in result.items:
        real = "✓ yes" if item.likely_real else "? no "
        print(
            f"  {item.defect_type:<20} {item.count_a:>8} {item.count_b:>8} "
            f"{item.match_rate*100:>7.1f}% {real}  "
            f"{item.rank_a:>6} {item.rank_b:>6}"
        )
    print(f"{'='*72}")
    if result.confirmed_killers:
        print(f"  Confirmed killers  : {', '.join(result.confirmed_killers)}")
    if result.nuisance_suspects:
        print(f"  Nuisance suspects  : {', '.join(result.nuisance_suspects)}")
    print()


def print_lot_trend_report(result: LotTrendResult) -> None:
    print(f"\n{'='*72}")
    print(f"  LOT TREND PARETO — {result.substrate_type or 'all'}")
    print(f"  Tracking: {', '.join(result.defect_types)}")
    if result.improving:
        print(f"  Improving: {', '.join(result.improving)}")
    if result.degrading:
        print(f"  Degrading: {', '.join(result.degrading)}")
    print(f"{'='*72}")

    # Group by lot
    lots: dict[str, list[LotTrendPoint]] = {}
    for pt in result.trend:
        lots.setdefault(pt.lot_id, []).append(pt)

    for lot_id, points in lots.items():
        print(f"\n  Lot: {lot_id} ({points[0].panel_count} panels)")
        for pt in sorted(points, key=lambda x: x.impact_fraction, reverse=True):
            trend_flag = (
                " ↑" if pt.defect_type in result.degrading
                else " ↓" if pt.defect_type in result.improving
                else ""
            )
            print(
                f"    {pt.defect_type:<20} count={pt.count:>4}  "
                f"impact={pt.impact_fraction*100:>5.1f}%  "
                f"yield_loss={pt.yield_loss*100:>5.2f}%{trend_flag}"
            )
    print()
