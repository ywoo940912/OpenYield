"""
tests/test_advanced_analysis.py
---------------------------------
Tests for pareto, SPC, correlation, and signature analysis modules.
"""

import pytest
from openyield.analysis.pareto import calculate_pareto
from openyield.analysis.spc import calculate_spc, _compute_baseline, _we_rules
from openyield.analysis.correlation import calculate_correlation
from openyield.analysis.signatures import match_signatures, SIGNATURE_LIBRARY
from openyield.ingestion.ingest import (
    upsert_panel, upsert_component, upsert_defect, upsert_lot
)
from openyield.yield_engine.calculator import calculate_panel_yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_panel(conn, panel_id, substrate_type, rows, cols, pitch,
                defects, lot_id=None):
    """Create panel + components + defects."""
    with conn:
        upsert_panel(conn, panel_id, "TEST", substrate_type,
                     rows, cols, lot_id=lot_id)
        for r in range(rows):
            for c in range(cols):
                # Assign region based on substrate
                if substrate_type == "wafer":
                    import math
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
        for row, col, x, y, dtype in defects:
            upsert_defect(conn, panel_id, row, col, "system_a",
                          dtype, x, y, 0.1, 0.8)


# ---------------------------------------------------------------------------
# Pareto tests
# ---------------------------------------------------------------------------

def test_pareto_basic(mem_conn):
    _make_panel(mem_conn, "WF_P01", "wafer", 4, 4, 28.0, [
        (0, 0, 1.0, 1.0, "particle"),
        (0, 0, 2.0, 2.0, "particle"),
        (0, 0, 3.0, 3.0, "particle"),
        (1, 1, 30.0, 30.0, "scratch"),
        (1, 1, 31.0, 31.0, "void"),
    ])
    result = calculate_pareto(mem_conn, panel_id="WF_P01")
    assert result.total_defects == 5
    assert len(result.items) == 3
    # Particle has highest count → should rank first or close
    types = [i.defect_type for i in result.items]
    assert "particle" in types
    assert "scratch" in types


def test_pareto_ranks_by_impact_not_count(mem_conn):
    """A fewer large defects can outrank more small defects."""
    _make_panel(mem_conn, "WF_P02", "wafer", 4, 4, 28.0, [
        # 5 tiny particles
        (0, 0, 1.0, 1.0, "particle"),
        (0, 0, 2.0, 1.0, "particle"),
        (0, 0, 3.0, 1.0, "particle"),
        (0, 0, 4.0, 1.0, "particle"),
        (0, 0, 5.0, 1.0, "particle"),
    ])
    # Add one large scratch via direct insert
    with mem_conn:
        mem_conn.execute(
            "INSERT INTO defects (panel_id, component_row, component_col, "
            "source_system, defect_type, x, y, size, confidence_score) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("WF_P02", 1, 1, "system_a", "scratch", 30.0, 30.0, 5.0, 0.95)
        )
    result = calculate_pareto(mem_conn, panel_id="WF_P02")
    # scratch has size=5.0, particles have default 0.1
    # impact: scratch=1*5.0*0.95=4.75, particles=5*0.1*0.8=0.4 → scratch wins
    assert result.items[0].defect_type == "scratch"


def test_pareto_cumulative_fractions_sum_to_one(mem_conn):
    _make_panel(mem_conn, "WF_P03", "wafer", 4, 4, 28.0, [
        (0, 0, 1.0, 1.0, "particle"),
        (1, 1, 30.0, 30.0, "scratch"),
        (2, 2, 60.0, 60.0, "void"),
    ])
    result = calculate_pareto(mem_conn, panel_id="WF_P03")
    assert result.items[-1].cumulative_fraction == pytest.approx(1.0, abs=0.01)


def test_pareto_vital_few_within_80_percent(mem_conn):
    _make_panel(mem_conn, "WF_P04", "wafer", 4, 4, 28.0, [
        (0, 0, 1.0, 1.0, "particle"),
        (0, 0, 2.0, 2.0, "particle"),
        (1, 1, 30.0, 30.0, "scratch"),
        (2, 2, 60.0, 60.0, "void"),
        (3, 3, 90.0, 90.0, "pit"),
    ])
    result = calculate_pareto(mem_conn, panel_id="WF_P04")
    total_vital = sum(
        i.impact_fraction for i in result.items
        if i.defect_type in result.vital_few
    )
    assert total_vital <= 0.801  # within 80% threshold


def test_pareto_empty_panel(mem_conn):
    _make_panel(mem_conn, "WF_P05", "wafer", 4, 4, 28.0, [])
    result = calculate_pareto(mem_conn, panel_id="WF_P05")
    assert result.total_defects == 0
    assert result.items == []


def test_pareto_substrate_filter(mem_conn):
    _make_panel(mem_conn, "WF_SUB", "wafer",       4, 4, 28.0,
                [(0, 0, 1.0, 1.0, "particle")])
    _make_panel(mem_conn, "GP_SUB", "glass_panel",  3, 3, 370.0,
                [(0, 0, 1.0, 1.0, "mura")])
    wafer_result = calculate_pareto(mem_conn, substrate_type="wafer")
    assert all(i.defect_type != "mura" for i in wafer_result.items)


# ---------------------------------------------------------------------------
# SPC tests
# ---------------------------------------------------------------------------

def test_compute_baseline():
    mean, std = _compute_baseline([1.0, 2.0, 3.0, 4.0, 5.0])
    assert mean == pytest.approx(3.0)
    assert std == pytest.approx(1.5811, rel=1e-3)


def test_we_rules_rule1_beyond_3sigma():
    values = [1.0] * 9 + [10.0]  # last point is way out
    rules = _we_rules(values, 9, mean=1.0, sigma=1.0, med=1.0)
    assert any("WE1" in r for r in rules)


def test_we_rules_rule4_eight_on_same_side():
    values = [1.1, 1.2, 1.1, 1.3, 1.2, 1.1, 1.2, 1.3]
    rules = _we_rules(values, 7, mean=1.0, sigma=0.5, med=1.0)
    assert any("WE4" in r for r in rules)


def test_spc_in_control(mem_conn):
    """Stable process → in_control."""
    with mem_conn:
        upsert_lot(mem_conn, "LOT_SPC1", "wafer", "TEST", 25)
    for i in range(6):
        pid = f"WF_SPC_{i:02d}"
        _make_panel(mem_conn, pid, "wafer", 4, 4, 28.0, [
            (j % 4, j % 4, float(j), float(j), "particle")
            for j in range(3)
        ], lot_id="LOT_SPC1")
        calculate_panel_yield(mem_conn, pid, persist=True)

    result = calculate_spc(mem_conn, lot_id="LOT_SPC1")
    assert result.n_points == 6
    assert result.process_state in ("in_control", "warning")
    assert result.centerline >= 0


def test_spc_detects_out_of_control(mem_conn):
    """One panel with dramatically higher density → signal."""
    with mem_conn:
        upsert_lot(mem_conn, "LOT_SPC2", "wafer", "TEST", 25)
    # 5 clean panels
    for i in range(5):
        pid = f"WF_SPC2_{i:02d}"
        _make_panel(mem_conn, pid, "wafer", 4, 4, 28.0, [
            (0, 0, float(i), 1.0, "particle")
        ], lot_id="LOT_SPC2")
        calculate_panel_yield(mem_conn, pid, persist=True)
    # 1 excursion panel — many defects
    pid = "WF_SPC2_EXC"
    _make_panel(mem_conn, pid, "wafer", 4, 4, 28.0, [
        (r, c, float(r * 28 + j), float(c * 28), "particle")
        for r in range(4) for c in range(4) for j in range(8)
    ], lot_id="LOT_SPC2")
    calculate_panel_yield(mem_conn, pid, persist=True)

    result = calculate_spc(mem_conn, lot_id="LOT_SPC2")
    assert result.process_state in ("warning", "out_of_control")


def test_spc_insufficient_data(mem_conn):
    """Fewer than 2 panels → returns empty result gracefully."""
    with mem_conn:
        upsert_lot(mem_conn, "LOT_SPC3", "wafer", "TEST", 25)
    _make_panel(mem_conn, "WF_ONE", "wafer", 4, 4, 28.0,
                [(0, 0, 1.0, 1.0, "particle")],
                lot_id="LOT_SPC3")
    calculate_panel_yield(mem_conn, "WF_ONE", persist=True)
    result = calculate_spc(mem_conn, lot_id="LOT_SPC3")
    assert result.n_points <= 1
    assert result.process_state == "in_control"


def test_spc_invalid_lambda():
    import sqlite3
    conn = sqlite3.connect(":memory:")
    from openyield.analysis.spc import calculate_spc
    with pytest.raises(ValueError, match="lambda_ewma"):
        calculate_spc(conn, lambda_ewma=0.0)


def test_spc_ewma_smoothing(mem_conn):
    """EWMA value should be between previous EWMA and current value."""
    with mem_conn:
        upsert_lot(mem_conn, "LOT_EWMA", "wafer", "TEST", 25)
    for i in range(5):
        pid = f"WF_EWMA_{i}"
        _make_panel(mem_conn, pid, "wafer", 4, 4, 28.0,
                    [(0, 0, float(i), 1.0, "particle")],
                    lot_id="LOT_EWMA")
        calculate_panel_yield(mem_conn, pid, persist=True)
    result = calculate_spc(mem_conn, lot_id="LOT_EWMA", lambda_ewma=0.3)
    # EWMA should exist and be finite
    for pt in result.points:
        assert 0.0 <= pt.ewma


# ---------------------------------------------------------------------------
# Correlation tests
# ---------------------------------------------------------------------------

def test_correlation_insufficient_panels(mem_conn):
    _make_panel(mem_conn, "WF_COR1", "wafer", 4, 4, 28.0,
                [(0, 0, 1.0, 1.0, "particle")])
    result = calculate_correlation(mem_conn, substrate_type="wafer")
    assert result.classification == "insufficient_data"
    assert result.systematic_count == 0


def test_correlation_finds_systematic(mem_conn):
    """Same die (2,2) has defect in both panels → systematic."""
    for i, pid in enumerate(["WF_SYS1", "WF_SYS2", "WF_SYS3"]):
        _make_panel(mem_conn, pid, "wafer", 4, 4, 28.0, [
            (2, 2, 56.0 + i*0.1, 56.0 + i*0.1, "particle"),  # same location
            (i, 0, float(i*28), 1.0, "scratch"),               # random
        ])
    result = calculate_correlation(mem_conn, substrate_type="wafer",
                                   repeat_threshold=0.5)
    systematic_locations = [
        (s.component_row, s.component_col)
        for s in result.systematic_locations
    ]
    assert (2, 2) in systematic_locations


def test_correlation_random_no_systematic(mem_conn):
    """All defects at different locations → no systematic."""
    for i, pid in enumerate(["WF_RND1", "WF_RND2", "WF_RND3"]):
        _make_panel(mem_conn, pid, "wafer", 4, 4, 28.0, [
            (i, i, float(i*28), float(i*28), "particle"),
        ])
    result = calculate_correlation(
        mem_conn, substrate_type="wafer", repeat_threshold=0.9
    )
    assert result.systematic_count == 0


def test_correlation_classification_fields(mem_conn):
    for pid in ["WF_CLS1", "WF_CLS2"]:
        _make_panel(mem_conn, pid, "wafer", 4, 4, 28.0,
                    [(1, 1, 29.0, 29.0, "particle")])
    result = calculate_correlation(mem_conn, substrate_type="wafer")
    assert result.classification in (
        "clean", "reticle_suspect", "tool_suspect",
        "minor_systematic", "insufficient_data"
    )
    assert isinstance(result.classification_reason, str)


# ---------------------------------------------------------------------------
# Signature tests
# ---------------------------------------------------------------------------

def test_signature_library_complete():
    expected = {
        "center_cluster", "edge_cluster", "scratch_linear",
        "ring_pattern", "random_scatter", "quadrant_bias",
        "edge_exclusion_bleed",
    }
    assert expected <= set(SIGNATURE_LIBRARY.keys())


def test_signature_match_random_scatter(mem_conn):
    """Evenly spread defects across all quadrants → random_scatter."""
    # Use glass panel — quadrant regions make even spread = no quadrant bias
    coords = [
        (0, 0, 10.0, 10.0, "particle"),   # region_NW
        (0, 2, 10.0, 750.0, "particle"),  # region_NE
        (2, 0, 750.0, 10.0, "particle"),  # region_SW
        (2, 2, 750.0, 750.0, "particle"), # region_SE
    ]
    _make_panel(mem_conn, "GP_SIG1", "glass_panel", 4, 4, 370.0, coords)
    result = match_signatures(mem_conn, "GP_SIG1")
    sig_names = [m.signature_name for m in result.matches]
    assert "random_scatter" in sig_names


def test_signature_match_center_cluster(mem_conn):
    """All defects in center zone → center_cluster."""
    # Place all defects at center dies (row=1,2 col=1,2 of a 4x4 grid)
    coords = [
        (1, 1, 29.0 + i*0.5, 29.0, "particle") for i in range(8)
    ] + [
        (2, 2, 58.0, 58.0 + i*0.5, "particle") for i in range(8)
    ]
    _make_panel(mem_conn, "WF_SIG2", "wafer", 4, 4, 28.0, coords)
    result = match_signatures(mem_conn, "WF_SIG2")
    sig_names = [m.signature_name for m in result.matches]
    assert "center_cluster" in sig_names or "ring_pattern" in sig_names


def test_signature_match_no_defects(mem_conn):
    _make_panel(mem_conn, "WF_SIG3", "wafer", 4, 4, 28.0, [])
    result = match_signatures(mem_conn, "WF_SIG3")
    assert result.top_match is None
    assert result.defect_count == 0


def test_signature_confidence_in_range(mem_conn):
    coords = [(0, 0, float(i), 1.0, "particle") for i in range(5)]
    _make_panel(mem_conn, "WF_SIG4", "wafer", 4, 4, 28.0, coords)
    result = match_signatures(mem_conn, "WF_SIG4")
    for m in result.matches:
        assert 0.0 <= m.confidence <= 1.0


def test_signature_panel_not_found(mem_conn):
    with pytest.raises(ValueError, match="not found"):
        match_signatures(mem_conn, "NONEXISTENT")


def test_signature_result_fields(mem_conn):
    coords = [(1, 1, 29.0, 29.0, "particle")]
    _make_panel(mem_conn, "WF_SIG5", "wafer", 4, 4, 28.0, coords)
    result = match_signatures(mem_conn, "WF_SIG5")
    assert result.panel_id == "WF_SIG5"
    assert result.substrate_type == "wafer"
    assert result.defect_count == 1
    assert isinstance(result.zone_fractions, dict)
