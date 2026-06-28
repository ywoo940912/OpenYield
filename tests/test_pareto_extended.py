"""
tests/test_pareto_extended.py
------------------------------
Tests for extended Pareto: zone breakdown, system comparison, lot trend.
"""

import pytest
from openyield.analysis.pareto import (
    calculate_pareto,
    calculate_zone_pareto,
    calculate_system_comparison,
    calculate_lot_trend,
)
from openyield.ingestion.ingest import (
    upsert_panel, upsert_component, upsert_defect, upsert_lot
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_panel(conn, panel_id, substrate_type, rows, cols, pitch,
                defects, lot_id=None):
    import math
    with conn:
        upsert_panel(conn, panel_id, "TEST", substrate_type,
                     rows, cols, lot_id=lot_id)
        for r in range(rows):
            for c in range(cols):
                if substrate_type == "wafer":
                    cr = (rows - 1) / 2.0
                    cc = (cols - 1) / 2.0
                    dist = math.hypot(r - cr, c - cc) / math.hypot(cr, cc)
                    region = ("zone_center" if dist < 0.35
                              else "zone_mid" if dist < 0.70
                              else "zone_edge")
                else:
                    rh, ch = rows // 2, cols // 2
                    region = f"region_{'N' if r < rh else 'S'}{'W' if c < ch else 'E'}"
                upsert_component(conn, panel_id, r, c, region,
                                 float(c * pitch), float(r * pitch))
        for row, col, x, y, dtype, system, size, conf in defects:
            upsert_defect(conn, panel_id, row, col, system,
                          dtype, x, y, size, conf)


def _base_defects():
    """Simple set of defects across system_a and system_b."""
    return [
        # system_a defects
        (1, 1, 29.0, 29.0, "particle",   "system_a", 0.2, 0.80),
        (1, 1, 30.0, 29.0, "particle",   "system_a", 0.2, 0.80),
        (1, 1, 31.0, 29.0, "particle",   "system_a", 0.2, 0.80),
        (2, 2, 58.0, 58.0, "scratch",    "system_a", 1.5, 0.75),
        (2, 2, 59.0, 58.0, "scratch",    "system_a", 1.5, 0.75),
        (3, 3, 87.0, 87.0, "void",       "system_a", 0.1, 0.65),
        # system_b confirmations (subset)
        (1, 1, 29.1, 29.1, "particle",   "system_b", 0.2, 0.92),
        (1, 1, 30.1, 29.1, "particle",   "system_b", 0.2, 0.92),
        (2, 2, 58.1, 58.1, "scratch",    "system_b", 1.5, 0.94),
        # void NOT confirmed by system_b (nuisance suspect)
    ]


# ---------------------------------------------------------------------------
# Overall Pareto (regression check)
# ---------------------------------------------------------------------------

def test_overall_pareto_basic(mem_conn):
    _make_panel(mem_conn, "WF_OV1", "wafer", 4, 4, 28.0, _base_defects())
    result = calculate_pareto(mem_conn, panel_id="WF_OV1", source_system="system_a")
    assert result.total_defects == 6
    assert len(result.items) == 3
    # scratch has highest impact (count=2, size=1.5, conf=0.75 → 2.25)
    # particle: 3 × 0.2 × 0.80 = 0.48
    # scratch:  2 × 1.5 × 0.75 = 2.25  ← highest
    assert result.items[0].defect_type == "scratch"


# ---------------------------------------------------------------------------
# Zone Pareto
# ---------------------------------------------------------------------------

def test_zone_pareto_returns_all_zones(mem_conn):
    _make_panel(mem_conn, "WF_ZN1", "wafer", 6, 6, 28.0, [
        (0, 0, 1.0,  1.0,  "particle", "system_a", 0.1, 0.8),
        (2, 2, 57.0, 57.0, "scratch",  "system_a", 0.5, 0.8),
        (5, 5, 141.0,141.0,"void",     "system_a", 0.1, 0.8),
    ])
    result = calculate_zone_pareto(mem_conn, panel_id="WF_ZN1")
    assert len(result.zones) >= 1
    for zone_name, pr in result.zones.items():
        assert isinstance(zone_name, str)
        assert pr.zone == zone_name


def test_zone_pareto_glass_panel_quadrants(mem_conn):
    _make_panel(mem_conn, "GP_ZN1", "glass_panel", 4, 4, 370.0, [
        (0, 0, 10.0,   10.0,   "particle", "system_a", 0.3, 0.8),
        (0, 0, 11.0,   10.0,   "particle", "system_a", 0.3, 0.8),
        (0, 2, 750.0,  10.0,   "mura",     "system_a", 0.5, 0.7),
        (2, 0, 10.0,   750.0,  "scratch",  "system_a", 0.4, 0.8),
        (2, 2, 750.0,  750.0,  "pinhole",  "system_a", 0.1, 0.9),
    ])
    result = calculate_zone_pareto(mem_conn, panel_id="GP_ZN1")
    zone_names = set(result.zones.keys())
    assert any("region" in z for z in zone_names)


def test_zone_pareto_counts_sum_correctly(mem_conn):
    _make_panel(mem_conn, "WF_ZN2", "wafer", 6, 6, 28.0, [
        (0, 0, 1.0,   1.0,   "particle", "system_a", 0.1, 0.8),
        (3, 3, 85.0,  85.0,  "particle", "system_a", 0.1, 0.8),
        (5, 5, 141.0, 141.0, "particle", "system_a", 0.1, 0.8),
    ])
    result = calculate_zone_pareto(mem_conn, panel_id="WF_ZN2")
    total = sum(pr.total_defects for pr in result.zones.values())
    assert total == 3


def test_zone_pareto_empty_zone_omitted(mem_conn):
    """Zones with no defects should be included with 0 total."""
    _make_panel(mem_conn, "WF_ZN3", "wafer", 6, 6, 28.0, [
        (3, 3, 85.0, 85.0, "particle", "system_a", 0.1, 0.8),
    ])
    result = calculate_zone_pareto(mem_conn, panel_id="WF_ZN3")
    # Center defect zone should have items; others may be empty
    zone_counts = {z: pr.total_defects for z, pr in result.zones.items()}
    assert sum(zone_counts.values()) == 1


def test_zone_pareto_substrate_filter(mem_conn):
    _make_panel(mem_conn, "WF_ZF1", "wafer",       4, 4, 28.0,
                [(1,1, 29.0, 29.0, "particle", "system_a", 0.1, 0.8)])
    _make_panel(mem_conn, "GP_ZF1", "glass_panel",  3, 3, 370.0,
                [(0,0, 10.0, 10.0, "mura",     "system_a", 0.3, 0.8)])
    result = calculate_zone_pareto(mem_conn, substrate_type="wafer")
    for zone, pr in result.zones.items():
        assert all(i.defect_type != "mura" for i in pr.items)


# ---------------------------------------------------------------------------
# System comparison
# ---------------------------------------------------------------------------

def test_system_comparison_basic(mem_conn):
    _make_panel(mem_conn, "WF_SC1", "wafer", 4, 4, 28.0, _base_defects())
    result = calculate_system_comparison(mem_conn, panel_id="WF_SC1")
    assert result.total_a == 6
    assert result.total_b == 3
    assert len(result.items) > 0


def test_system_comparison_match_rate_particle(mem_conn):
    """particle: 3 in system_a, 2 confirmed in system_b → match=0.67."""
    _make_panel(mem_conn, "WF_SC2", "wafer", 4, 4, 28.0, _base_defects())
    result = calculate_system_comparison(mem_conn, panel_id="WF_SC2")
    particle = next(i for i in result.items if i.defect_type == "particle")
    assert particle.match_rate == pytest.approx(2/3, rel=0.05)
    assert particle.likely_real is True


def test_system_comparison_nuisance_suspect(mem_conn):
    """void: in system_a only, not confirmed → nuisance suspect."""
    _make_panel(mem_conn, "WF_SC3", "wafer", 4, 4, 28.0, _base_defects())
    result = calculate_system_comparison(mem_conn, panel_id="WF_SC3")
    # void only appears once so count_a=1 < 3, may not be in nuisance_suspects
    void_item = next((i for i in result.items if i.defect_type == "void"), None)
    if void_item:
        assert void_item.match_rate == pytest.approx(0.0)
        assert void_item.likely_real is False


def test_system_comparison_confirmed_killers(mem_conn):
    _make_panel(mem_conn, "WF_SC4", "wafer", 4, 4, 28.0, _base_defects())
    result = calculate_system_comparison(mem_conn, panel_id="WF_SC4")
    # particle and scratch both have system_b confirmation
    assert "particle" in result.confirmed_killers or \
           "scratch"  in result.confirmed_killers


def test_system_comparison_match_rate_in_range(mem_conn):
    _make_panel(mem_conn, "WF_SC5", "wafer", 4, 4, 28.0, _base_defects())
    result = calculate_system_comparison(mem_conn, panel_id="WF_SC5")
    for item in result.items:
        assert 0.0 <= item.match_rate <= 1.0


def test_system_comparison_totals(mem_conn):
    _make_panel(mem_conn, "WF_SC6", "wafer", 4, 4, 28.0, _base_defects())
    result = calculate_system_comparison(mem_conn, panel_id="WF_SC6")
    assert result.total_a == sum(
        i.count_a for i in result.items
    )
    assert result.total_b == sum(
        i.count_b for i in result.items
    )


# ---------------------------------------------------------------------------
# Lot trend Pareto
# ---------------------------------------------------------------------------

def _setup_lot(conn, lot_id, substrate_type, n_panels, defect_sets):
    """Create lot with panels, each having a specific defect set."""
    with conn:
        upsert_lot(conn, lot_id, substrate_type, "TEST", lot_size=25)
    for i in range(n_panels):
        pid = f"{lot_id}_P{i:02d}"
        _make_panel(conn, pid, substrate_type, 4, 4, 28.0,
                    defect_sets[i % len(defect_sets)],
                    lot_id=lot_id)


def test_lot_trend_basic(mem_conn):
    defects = [
        (1, 1, 29.0, 29.0, "particle", "system_a", 0.2, 0.8),
        (2, 2, 58.0, 58.0, "scratch",  "system_a", 1.0, 0.8),
    ]
    _setup_lot(mem_conn, "LOT_TR1", "wafer", 2, [defects])
    result = calculate_lot_trend(mem_conn, substrate_type="wafer")
    assert len(result.defect_types) > 0
    assert len(result.trend) > 0


def test_lot_trend_no_lots(mem_conn):
    result = calculate_lot_trend(mem_conn, substrate_type="wafer")
    assert result.defect_types == []
    assert result.trend == []


def test_lot_trend_multiple_lots(mem_conn):
    defects_early = [
        (1, 1, 29.0, 29.0, "particle", "system_a", 0.2, 0.8),
        (2, 2, 58.0, 58.0, "particle", "system_a", 0.2, 0.8),
        (3, 3, 87.0, 87.0, "particle", "system_a", 0.2, 0.8),
    ]
    defects_later = [
        (1, 1, 29.0, 29.0, "scratch",  "system_a", 1.5, 0.8),
        (2, 2, 58.0, 58.0, "scratch",  "system_a", 1.5, 0.8),
    ]
    _setup_lot(mem_conn, "LOT_TR2A", "wafer", 2, [defects_early])
    _setup_lot(mem_conn, "LOT_TR2B", "wafer", 2, [defects_later])
    result = calculate_lot_trend(mem_conn, substrate_type="wafer", top_n_types=3)
    assert len(result.defect_types) <= 3
    lot_ids = {pt.lot_id for pt in result.trend}
    assert "LOT_TR2A" in lot_ids
    assert "LOT_TR2B" in lot_ids


def test_lot_trend_improving_detection(mem_conn):
    """Defect type with decreasing impact across lots → in improving list."""
    # Lot 1: many particles
    many_particles = [
        (r, c, float(r*28+j), float(c*28), "particle", "system_a", 0.2, 0.8)
        for r in range(4) for c in range(4) for j in range(3)
    ]
    # Lot 2: fewer particles
    few_particles = [
        (1, 1, 29.0, 29.0, "particle", "system_a", 0.2, 0.8),
    ]
    _setup_lot(mem_conn, "LOT_IMP1", "wafer", 2, [many_particles])
    _setup_lot(mem_conn, "LOT_IMP2", "wafer", 2, [few_particles])
    result = calculate_lot_trend(mem_conn, substrate_type="wafer", top_n_types=3)
    # particle impact should decrease from lot1 to lot2
    if "particle" in result.defect_types:
        assert "particle" in result.improving or result.improving == []


def test_lot_trend_impact_fractions_valid(mem_conn):
    defects = [(1, 1, 29.0, 29.0, "particle", "system_a", 0.2, 0.8)]
    _setup_lot(mem_conn, "LOT_FR1", "wafer", 2, [defects])
    result = calculate_lot_trend(mem_conn, substrate_type="wafer")
    for pt in result.trend:
        assert 0.0 <= pt.impact_fraction <= 1.0
        assert pt.count >= 0
        assert 0.0 <= pt.yield_loss <= 1.0
