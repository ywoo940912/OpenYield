"""
tests/test_spatial_predictor.py
---------------------------------
Tests for yield_engine/spatial_predictor.py — spatial yield prediction.
"""

import math
import pytest

from openyield.yield_engine.spatial_predictor import (
    compute_spatial_yield,
    _population_std,
    SpatialYieldResult,
    DieYield,
)
from openyield.yield_engine.models import poisson_yield, negbinom_yield
from openyield.ingestion.ingest import upsert_panel, upsert_component, upsert_defect


# ---------------------------------------------------------------------------
# _population_std unit tests
# ---------------------------------------------------------------------------

def test_population_std_empty():
    assert _population_std([]) == pytest.approx(0.0)


def test_population_std_single():
    assert _population_std([5.0]) == pytest.approx(0.0)


def test_population_std_uniform():
    """All identical values → zero std."""
    assert _population_std([3.0, 3.0, 3.0, 3.0]) == pytest.approx(0.0)


def test_population_std_known():
    """[2, 4, 4, 4, 5, 5, 7, 9] → population std = 2.0."""
    values = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
    assert _population_std(values) == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mem_conn(tmp_path):
    from openyield.db.connection import get_connection
    from openyield.db.schema import initialize_schema
    conn = get_connection(tmp_path / "test.db")
    initialize_schema(conn)
    return conn


def _make_panel(conn, panel_id, substrate, defect_grid):
    """
    Set up a panel whose die-level defect counts are specified by defect_grid.

    defect_grid : dict[(row, col)] = defect_count (int)
    Rows × cols is inferred from the keys; all listed positions are active.
    """
    pitch = 28.0 if substrate == "wafer" else 370.0
    rows_set = {r for (r, _) in defect_grid}
    cols_set = {c for (_, c) in defect_grid}
    n_rows = max(rows_set) + 1
    n_cols = max(cols_set) + 1

    upsert_panel(conn, panel_id, substrate,
                 rows=n_rows, cols=n_cols,
                 lot_id="LOT_SP01",
                 component_pitch_mm=pitch,
                 product_type="TEST")

    for (r, c), count in defect_grid.items():
        upsert_component(conn, panel_id, r, c,
                         float(c * pitch), float(r * pitch), active=True)
        for i in range(count):
            upsert_defect(conn, panel_id, r, c,
                          "system_a", "particle",
                          float(c * pitch + i * 0.1),
                          float(r * pitch + i * 0.1),
                          0.05, 0.80)


# ---------------------------------------------------------------------------
# Uniform defect density
# ---------------------------------------------------------------------------

def test_uniform_density_spatial_equals_global(mem_conn):
    """
    When all dies have identical defect counts, spatial yield = global yield.
    (No gain from die-level disaggregation.)
    """
    grid = {(r, c): 2 for r in range(3) for c in range(3)}
    _make_panel(mem_conn, "WF_UNIFORM", "wafer", grid)

    result = compute_spatial_yield(mem_conn, "WF_UNIFORM", use_critical_area=False)

    assert result.cv_d0 == pytest.approx(0.0, abs=1e-9)
    assert result.yield_gain_poisson  == pytest.approx(0.0, abs=1e-6)
    assert result.yield_gain_negbinom == pytest.approx(0.0, abs=1e-6)
    assert result.spatial_yield_poisson  == pytest.approx(result.global_yield_poisson,  rel=1e-5)
    assert result.spatial_yield_negbinom == pytest.approx(result.global_yield_negbinom, rel=1e-5)


# ---------------------------------------------------------------------------
# Non-uniform defect density — Jensen's inequality
# ---------------------------------------------------------------------------

def test_nonuniform_spatial_yield_geq_global(mem_conn):
    """
    Jensen's inequality: for convex yield functions, spatial ≥ global
    when density is non-uniform.
    """
    # One die with many defects, others clean — extreme non-uniformity
    grid = {(0, 0): 10, (0, 1): 0, (1, 0): 0, (1, 1): 0}
    _make_panel(mem_conn, "WF_NONU", "wafer", grid)

    result = compute_spatial_yield(mem_conn, "WF_NONU", use_critical_area=False)

    assert result.spatial_yield_poisson  >= result.global_yield_poisson  - 1e-9
    assert result.spatial_yield_negbinom >= result.global_yield_negbinom - 1e-9
    assert result.yield_gain_poisson  >= -1e-9
    assert result.yield_gain_negbinom >= -1e-9


def test_nonuniform_cv_positive(mem_conn):
    """Non-uniform density gives positive coefficient of variation."""
    grid = {(0, 0): 5, (0, 1): 1, (1, 0): 0, (1, 1): 2}
    _make_panel(mem_conn, "WF_CV", "wafer", grid)

    result = compute_spatial_yield(mem_conn, "WF_CV", use_critical_area=False)
    assert result.cv_d0 > 0.0


# ---------------------------------------------------------------------------
# Zero-defect panel
# ---------------------------------------------------------------------------

def test_zero_defects_all_yields_one(mem_conn):
    """Panel with no defects → all yields = 1.0, D0 = 0."""
    grid = {(r, c): 0 for r in range(3) for c in range(3)}
    _make_panel(mem_conn, "WF_ZERO", "wafer", grid)

    result = compute_spatial_yield(mem_conn, "WF_ZERO", use_critical_area=False)

    assert result.spatial_yield_poisson  == pytest.approx(1.0)
    assert result.spatial_yield_murphy   == pytest.approx(1.0)
    assert result.spatial_yield_negbinom == pytest.approx(1.0)
    assert result.global_yield_poisson   == pytest.approx(1.0)
    assert result.mean_d0  == pytest.approx(0.0)
    assert result.std_d0   == pytest.approx(0.0)
    assert result.cv_d0    == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Single die
# ---------------------------------------------------------------------------

def test_single_die_spatial_equals_die_yield(mem_conn):
    """With one active die, spatial yield = that die's individual yield."""
    grid = {(0, 0): 3}
    _make_panel(mem_conn, "WF_ONE", "wafer", grid)

    result = compute_spatial_yield(mem_conn, "WF_ONE", use_critical_area=False)

    assert result.n_active_dies == 1
    assert result.spatial_yield_poisson == pytest.approx(result.global_yield_poisson, rel=1e-5)
    assert len(result.die_yields) == 1


# ---------------------------------------------------------------------------
# Mean D0 equals global D0
# ---------------------------------------------------------------------------

def test_mean_d0_equals_global_d0(mem_conn):
    """mean_d0 over active dies = total_defects / (n_dies × die_area)."""
    grid = {(0, 0): 3, (0, 1): 1, (1, 0): 4, (1, 1): 2}
    _make_panel(mem_conn, "WF_D0", "wafer", grid)

    result = compute_spatial_yield(mem_conn, "WF_D0", use_critical_area=False)

    expected_d0 = 10 / (4 * 28.0 ** 2)
    assert result.mean_d0 == pytest.approx(expected_d0, rel=1e-5)


# ---------------------------------------------------------------------------
# Per-die yields output
# ---------------------------------------------------------------------------

def test_die_yields_cover_all_components(mem_conn):
    """die_yields list contains one entry per component (active and inactive)."""
    grid = {(0, 0): 2, (0, 1): 0, (1, 0): 3, (1, 1): 1}
    _make_panel(mem_conn, "WF_COMP", "wafer", grid)

    result = compute_spatial_yield(mem_conn, "WF_COMP", use_critical_area=False)
    assert len(result.die_yields) == 4


def test_die_yield_active_flag(mem_conn):
    """All dies in _make_panel are active — all die_yields.active = True."""
    grid = {(0, 0): 1, (0, 1): 2}
    _make_panel(mem_conn, "WF_ACT", "wafer", grid)

    result = compute_spatial_yield(mem_conn, "WF_ACT", use_critical_area=False)
    for dy in result.die_yields:
        assert dy.active is True


def test_die_yield_counts_match_inserted(mem_conn):
    """die_yield.defect_count matches what was inserted."""
    grid = {(0, 0): 5, (0, 1): 3, (1, 0): 7}
    _make_panel(mem_conn, "WF_CNT", "wafer", grid)

    result = compute_spatial_yield(mem_conn, "WF_CNT", use_critical_area=False)
    counts = {(d.row, d.col): d.defect_count for d in result.die_yields}
    assert counts[(0, 0)] == 5
    assert counts[(0, 1)] == 3
    assert counts[(1, 0)] == 7


def test_die_yield_all_in_zero_one(mem_conn):
    """All per-die yield values must be in [0, 1]."""
    grid = {(r, c): (r * 3 + c) for r in range(3) for c in range(3)}
    _make_panel(mem_conn, "WF_RANGE", "wafer", grid)

    result = compute_spatial_yield(mem_conn, "WF_RANGE", use_critical_area=False)
    for dy in result.die_yields:
        if dy.active:
            assert 0.0 <= dy.yield_poisson  <= 1.0
            assert 0.0 <= dy.yield_murphy   <= 1.0
            assert 0.0 <= dy.yield_negbinom <= 1.0


# ---------------------------------------------------------------------------
# Substrate types
# ---------------------------------------------------------------------------

def test_wafer_substrate_reported(mem_conn):
    grid = {(0, 0): 1, (0, 1): 2}
    _make_panel(mem_conn, "WF_SUB", "wafer", grid)
    result = compute_spatial_yield(mem_conn, "WF_SUB", use_critical_area=False)
    assert result.substrate_type == "wafer"
    assert result.die_area_mm2 == pytest.approx(28.0 ** 2)


def test_glass_panel_substrate_reported(mem_conn):
    grid = {(0, 0): 3, (0, 1): 5, (1, 0): 2, (1, 1): 4}
    _make_panel(mem_conn, "GP_SUB", "glass_panel", grid)
    result = compute_spatial_yield(mem_conn, "GP_SUB", use_critical_area=False)
    assert result.substrate_type == "glass_panel"
    assert result.die_area_mm2 == pytest.approx(370.0 ** 2)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_panel_not_found_raises(mem_conn):
    with pytest.raises(ValueError, match="not found"):
        compute_spatial_yield(mem_conn, "DOES_NOT_EXIST")


def test_no_active_dies_raises(mem_conn):
    """A panel with all inactive dies should raise ValueError."""
    pitch = 28.0
    upsert_panel(mem_conn, "WF_INACT", "wafer", rows=2, cols=2,
                 lot_id="LOT_SP02", component_pitch_mm=pitch, product_type="TEST")
    upsert_component(mem_conn, "WF_INACT", 0, 0, 0.0, 0.0, active=False)
    upsert_component(mem_conn, "WF_INACT", 0, 1, pitch, 0.0, active=False)

    with pytest.raises(ValueError, match="no active dies"):
        compute_spatial_yield(mem_conn, "WF_INACT")


# ---------------------------------------------------------------------------
# Murphy beats Poisson (same property holds per-die and after averaging)
# ---------------------------------------------------------------------------

def test_spatial_murphy_geq_poisson(mem_conn):
    """Murphy spatial yield should be ≥ Poisson spatial yield (same inputs)."""
    grid = {(0, 0): 2, (0, 1): 4, (1, 0): 1, (1, 1): 3}
    _make_panel(mem_conn, "WF_MP", "wafer", grid)
    result = compute_spatial_yield(mem_conn, "WF_MP", use_critical_area=False)
    assert result.spatial_yield_murphy >= result.spatial_yield_poisson
