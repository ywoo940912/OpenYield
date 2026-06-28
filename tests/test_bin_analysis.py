"""
tests/test_bin_analysis.py
--------------------------
Tests for openyield.analysis.bin_analysis (spatial bin map builder).
"""

import pytest
from openyield.analysis.bin_analysis import build_panel_map, MapCell, PanelMap
from openyield.ingestion.ingest import (
    upsert_panel, upsert_component, upsert_defect,
)
from openyield.yield_engine.calculator import calculate_panel_yield
from openyield.analysis.clustering import cluster_panel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_panel(conn, panel_id, rows=3, cols=3, substrate="wafer",
                n_defects=0, defect_row=0, defect_col=0, defect_type="particle"):
    """Create a panel with a uniform grid of components and optional defects."""
    pitch = 28.0 if substrate == "wafer" else 370.0
    with conn:
        upsert_panel(conn, panel_id, "TEST-PRODUCT", substrate, rows, cols)
        for r in range(rows):
            for c in range(cols):
                upsert_component(
                    conn, panel_id, r, c,
                    f"region_{r}_{c}",
                    float(c * pitch), float(r * pitch),
                )
        for i in range(n_defects):
            upsert_defect(
                conn, panel_id, defect_row, defect_col,
                "system_a", defect_type,
                float(i * 0.5), float(i * 0.3),
                0.1, 0.75,
            )


# ---------------------------------------------------------------------------
# Basic structure
# ---------------------------------------------------------------------------

def test_build_panel_map_returns_panel_map(mem_conn):
    _make_panel(mem_conn, "WF_MAP01")
    result = build_panel_map(mem_conn, "WF_MAP01")
    assert isinstance(result, PanelMap)


def test_build_panel_map_cell_count_matches_grid(mem_conn):
    _make_panel(mem_conn, "WF_MAP02", rows=4, cols=5)
    result = build_panel_map(mem_conn, "WF_MAP02")
    assert result.rows == 4
    assert result.cols == 5
    assert len(result.cells) == 4 * 5


def test_build_panel_map_panel_id_propagated(mem_conn):
    _make_panel(mem_conn, "WF_MAP03")
    result = build_panel_map(mem_conn, "WF_MAP03")
    assert result.panel_id == "WF_MAP03"


def test_build_panel_map_substrate_type_propagated(mem_conn):
    _make_panel(mem_conn, "GP_MAP01", substrate="glass_panel")
    result = build_panel_map(mem_conn, "GP_MAP01")
    assert result.substrate_type == "glass_panel"


def test_build_panel_map_cells_are_map_cell_instances(mem_conn):
    _make_panel(mem_conn, "WF_MAP04")
    result = build_panel_map(mem_conn, "WF_MAP04")
    for cell in result.cells:
        assert isinstance(cell, MapCell)


def test_build_panel_map_raises_on_missing_panel(mem_conn):
    with pytest.raises(ValueError, match="not found"):
        build_panel_map(mem_conn, "NONEXISTENT_PANEL")


# ---------------------------------------------------------------------------
# Zero-defect panel
# ---------------------------------------------------------------------------

def test_zero_defect_panel_total_defects_zero(mem_conn):
    _make_panel(mem_conn, "WF_ZERO")
    result = build_panel_map(mem_conn, "WF_ZERO")
    assert result.total_defects == 0


def test_zero_defect_panel_all_cells_zero_count(mem_conn):
    _make_panel(mem_conn, "WF_ZERO2")
    result = build_panel_map(mem_conn, "WF_ZERO2")
    for cell in result.cells:
        assert cell.defect_count == 0


def test_zero_defect_panel_no_cluster_labels(mem_conn):
    _make_panel(mem_conn, "WF_ZERO3")
    result = build_panel_map(mem_conn, "WF_ZERO3")
    for cell in result.cells:
        assert cell.cluster_label is None


# ---------------------------------------------------------------------------
# Defect aggregation
# ---------------------------------------------------------------------------

def test_defect_count_aggregated_to_correct_cell(mem_conn):
    _make_panel(mem_conn, "WF_DEF01", rows=3, cols=3,
                n_defects=4, defect_row=1, defect_col=2)
    result = build_panel_map(mem_conn, "WF_DEF01")
    cell = next(c for c in result.cells if c.row == 1 and c.col == 2)
    assert cell.defect_count == 4


def test_other_cells_have_zero_defects(mem_conn):
    _make_panel(mem_conn, "WF_DEF02", rows=3, cols=3,
                n_defects=3, defect_row=0, defect_col=0)
    result = build_panel_map(mem_conn, "WF_DEF02")
    for cell in result.cells:
        if cell.row == 0 and cell.col == 0:
            assert cell.defect_count == 3
        else:
            assert cell.defect_count == 0


def test_total_defects_sum_of_cells(mem_conn):
    _make_panel(mem_conn, "WF_DEF03", rows=3, cols=3,
                n_defects=7, defect_row=2, defect_col=1)
    result = build_panel_map(mem_conn, "WF_DEF03")
    assert result.total_defects == sum(c.defect_count for c in result.cells)


def test_defect_types_dict_populated(mem_conn):
    _make_panel(mem_conn, "WF_DEF04", rows=2, cols=2,
                n_defects=3, defect_row=0, defect_col=0, defect_type="scratch")
    result = build_panel_map(mem_conn, "WF_DEF04")
    cell = next(c for c in result.cells if c.row == 0 and c.col == 0)
    assert "scratch" in cell.defect_types
    assert cell.defect_types["scratch"] == 3


def test_system_b_defects_not_counted(mem_conn):
    """Only system_a defects should appear in the map."""
    with mem_conn:
        upsert_panel(mem_conn, "WF_SYS_B", "TEST", "wafer", 2, 2)
        for r in range(2):
            for c in range(2):
                upsert_component(mem_conn, "WF_SYS_B", r, c, "zone", float(c*28), float(r*28))
        upsert_defect(mem_conn, "WF_SYS_B", 0, 0, "system_b", "particle", 1.0, 1.0, 0.1, 0.9)
    result = build_panel_map(mem_conn, "WF_SYS_B")
    assert result.total_defects == 0


# ---------------------------------------------------------------------------
# Active dies
# ---------------------------------------------------------------------------

def test_active_dies_count(mem_conn):
    _make_panel(mem_conn, "WF_ACT01", rows=3, cols=3)
    result = build_panel_map(mem_conn, "WF_ACT01")
    # All components added without inactive flag → all active
    assert result.active_dies == 9


def test_inactive_die_active_flag_false(mem_conn):
    """Component with active=0 should produce a MapCell with active=False."""
    with mem_conn:
        upsert_panel(mem_conn, "WF_INACT", "TEST", "wafer", 2, 2)
        upsert_component(mem_conn, "WF_INACT", 0, 0, "zone", 0.0, 0.0)
        upsert_component(mem_conn, "WF_INACT", 0, 1, "zone", 28.0, 0.0, active=False)
        upsert_component(mem_conn, "WF_INACT", 1, 0, "zone", 0.0, 28.0)
        upsert_component(mem_conn, "WF_INACT", 1, 1, "zone", 28.0, 28.0)
    result = build_panel_map(mem_conn, "WF_INACT")
    inactive_cells = [c for c in result.cells if not c.active]
    assert len(inactive_cells) == 1
    assert inactive_cells[0].row == 0 and inactive_cells[0].col == 1


# ---------------------------------------------------------------------------
# Yield estimates integrated into map
# ---------------------------------------------------------------------------

def test_yield_estimates_propagated_to_panel_map(mem_conn):
    _make_panel(mem_conn, "WF_YLD01", rows=3, cols=3,
                n_defects=5, defect_row=1, defect_col=1)
    calculate_panel_yield(mem_conn, "WF_YLD01", persist=True)
    result = build_panel_map(mem_conn, "WF_YLD01")
    assert result.yield_poisson is not None
    assert result.yield_murphy is not None
    assert result.yield_negbinom is not None
    assert result.defect_density is not None


def test_per_die_yield_poisson_populated_when_yield_exists(mem_conn):
    _make_panel(mem_conn, "WF_YLD02", rows=2, cols=2,
                n_defects=3, defect_row=0, defect_col=0)
    calculate_panel_yield(mem_conn, "WF_YLD02", persist=True)
    result = build_panel_map(mem_conn, "WF_YLD02")
    active_cells = [c for c in result.cells if c.active]
    assert all(c.yield_poisson is not None for c in active_cells)


def test_per_die_yield_between_zero_and_one(mem_conn):
    _make_panel(mem_conn, "WF_YLD03", rows=2, cols=2,
                n_defects=4, defect_row=0, defect_col=0)
    calculate_panel_yield(mem_conn, "WF_YLD03", persist=True)
    result = build_panel_map(mem_conn, "WF_YLD03")
    for cell in result.cells:
        if cell.yield_poisson is not None:
            assert 0.0 <= cell.yield_poisson <= 1.0


def test_no_yield_estimates_yields_none(mem_conn):
    _make_panel(mem_conn, "WF_NOYE", rows=2, cols=2)
    result = build_panel_map(mem_conn, "WF_NOYE")
    assert result.yield_poisson is None
    assert result.yield_murphy is None
    assert result.yield_negbinom is None


# ---------------------------------------------------------------------------
# Clustering labels
# ---------------------------------------------------------------------------

def test_cluster_labels_assigned_to_cells(mem_conn):
    """Panels with clustered defects should have cluster labels on affected cells."""
    with mem_conn:
        upsert_panel(mem_conn, "WF_CLU01", "TEST", "wafer", 4, 4)
        for r in range(4):
            for c in range(4):
                upsert_component(mem_conn, "WF_CLU01", r, c, "zone",
                                 float(c * 28), float(r * 28))
        # Tight cluster in die (2, 2)
        for i in range(6):
            upsert_defect(mem_conn, "WF_CLU01", 2, 2, "system_a", "particle",
                          56.0 + i * 0.1, 56.0 + i * 0.1, 0.1, 0.8)
    cluster_panel(mem_conn, "WF_CLU01", persist=True)
    result = build_panel_map(mem_conn, "WF_CLU01")
    cell_2_2 = next(c for c in result.cells if c.row == 2 and c.col == 2)
    assert cell_2_2.cluster_label is not None


def test_clustering_class_propagated(mem_conn):
    """clustering_class should reflect the persisted cluster_results row."""
    with mem_conn:
        upsert_panel(mem_conn, "WF_CLU02", "TEST", "wafer", 4, 4)
        for r in range(4):
            for c in range(4):
                upsert_component(mem_conn, "WF_CLU02", r, c, "zone",
                                 float(c * 28), float(r * 28))
        for i in range(8):
            upsert_defect(mem_conn, "WF_CLU02", 2, 2, "system_a", "particle",
                          56.0 + i * 0.1, 56.0 + i * 0.1, 0.1, 0.8)
    cluster_panel(mem_conn, "WF_CLU02", persist=True)
    result = build_panel_map(mem_conn, "WF_CLU02")
    assert result.clustering_class in ("random", "systematic", "excursion")


def test_no_cluster_results_gives_none_class(mem_conn):
    _make_panel(mem_conn, "WF_NOCLU", rows=2, cols=2)
    result = build_panel_map(mem_conn, "WF_NOCLU")
    assert result.clustering_class is None


# ---------------------------------------------------------------------------
# Cell coordinate ordering
# ---------------------------------------------------------------------------

def test_cells_cover_all_row_col_combinations(mem_conn):
    rows, cols = 3, 4
    _make_panel(mem_conn, "WF_GRID", rows=rows, cols=cols)
    result = build_panel_map(mem_conn, "WF_GRID")
    coords = {(c.row, c.col) for c in result.cells}
    expected = {(r, c) for r in range(rows) for c in range(cols)}
    assert coords == expected
