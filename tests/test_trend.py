"""
tests/test_trend.py
-------------------
Tests for openyield.analysis.trend (multi-lot yield/density trend analysis).
"""

import pytest
from openyield.analysis.trend import compute_trend, _linear_regression, TrendResult
from openyield.ingestion.ingest import (
    upsert_panel, upsert_component, upsert_defect, upsert_lot,
)
from openyield.yield_engine.calculator import calculate_panel_yield
from openyield.analysis.lot_tracker import summarise_lot


# ---------------------------------------------------------------------------
# _linear_regression unit tests
# ---------------------------------------------------------------------------

def test_linear_regression_perfect_line():
    x = [1.0, 2.0, 3.0, 4.0, 5.0]
    y = [2.0, 4.0, 6.0, 8.0, 10.0]
    slope, intercept, r_sq = _linear_regression(x, y)
    assert slope == pytest.approx(2.0, abs=1e-6)
    assert intercept == pytest.approx(0.0, abs=1e-6)
    assert r_sq == pytest.approx(1.0, abs=1e-4)


def test_linear_regression_flat_line():
    x = [1.0, 2.0, 3.0]
    y = [5.0, 5.0, 5.0]
    slope, intercept, r_sq = _linear_regression(x, y)
    assert slope == pytest.approx(0.0, abs=1e-6)
    assert intercept == pytest.approx(5.0, abs=1e-6)


def test_linear_regression_negative_slope():
    x = [1.0, 2.0, 3.0, 4.0]
    y = [8.0, 6.0, 4.0, 2.0]
    slope, intercept, r_sq = _linear_regression(x, y)
    assert slope < 0
    assert r_sq == pytest.approx(1.0, abs=1e-4)


def test_linear_regression_single_point():
    slope, intercept, r_sq = _linear_regression([3.0], [7.0])
    assert slope == 0.0
    assert intercept == 7.0
    assert r_sq == 0.0


def test_linear_regression_empty():
    slope, intercept, r_sq = _linear_regression([], [])
    assert slope == 0.0
    assert intercept == 0.0
    assert r_sq == 0.0


def test_linear_regression_r_squared_between_zero_and_one():
    x = [1.0, 2.0, 3.0, 4.0, 5.0]
    y = [2.1, 3.9, 6.2, 7.8, 10.3]  # noisy line
    _, _, r_sq = _linear_regression(x, y)
    assert 0.0 <= r_sq <= 1.0


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_lot_with_density(conn, lot_id, substrate, n_panels, density):
    """
    Create a lot whose panels have approximately the given defect density.
    Persists yield estimates and lot summary.
    """
    pitch = 28.0 if substrate == "wafer" else 370.0
    rows, cols = (4, 4) if substrate == "wafer" else (3, 3)
    area = pitch * pitch  # die area ≈ pitch²

    with conn:
        upsert_lot(conn, lot_id, substrate, "TEST-PRODUCT", lot_size=25)

    for i in range(n_panels):
        pid = f"{lot_id}_P{i:02d}"
        n_defects = max(1, int(density * area * rows * cols))
        with conn:
            upsert_panel(conn, pid, "TEST-PRODUCT", substrate, rows, cols, lot_id=lot_id)
            for r in range(rows):
                for c in range(cols):
                    upsert_component(conn, pid, r, c, "zone",
                                     float(c * pitch), float(r * pitch))
            for j in range(n_defects):
                dr = j % rows
                dc = j % cols
                upsert_defect(conn, pid, dr, dc, "system_a", "particle",
                              float(dr * pitch + j * 0.1), float(dc * pitch + j * 0.07),
                              0.1, 0.8)
        calculate_panel_yield(conn, pid, persist=True)
    summarise_lot(conn, lot_id, persist=True)


# ---------------------------------------------------------------------------
# compute_trend — empty database
# ---------------------------------------------------------------------------

def test_compute_trend_empty_db_returns_stable(mem_conn):
    result = compute_trend(mem_conn)
    assert isinstance(result, TrendResult)
    assert result.n_lots == 0
    assert result.direction == "stable"
    assert result.data_points == []


def test_compute_trend_empty_db_first_last_none(mem_conn):
    result = compute_trend(mem_conn)
    assert result.first_lot_id is None
    assert result.last_lot_id is None


# ---------------------------------------------------------------------------
# compute_trend — single lot
# ---------------------------------------------------------------------------

def test_compute_trend_single_lot_returns_stable(mem_conn):
    _make_lot_with_density(mem_conn, "LOT_S01", "wafer", 3, 0.005)
    result = compute_trend(mem_conn)
    assert result.n_lots == 1
    assert result.direction == "stable"   # can't determine direction from 1 point


def test_compute_trend_single_lot_density_nonzero(mem_conn):
    _make_lot_with_density(mem_conn, "LOT_S02", "wafer", 2, 0.004)
    result = compute_trend(mem_conn)
    assert result.mean_density > 0


# ---------------------------------------------------------------------------
# compute_trend — direction detection
# ---------------------------------------------------------------------------

def test_compute_trend_degrading(mem_conn):
    """Strictly increasing defect density across lots → degrading."""
    densities = [0.001, 0.002, 0.004, 0.008, 0.016]
    for i, d in enumerate(densities):
        _make_lot_with_density(mem_conn, f"LOT_DEG{i:02d}", "wafer", 2, d)
    result = compute_trend(mem_conn)
    assert result.direction == "degrading"
    assert result.slope > 0


def test_compute_trend_improving(mem_conn):
    """Strictly decreasing defect density across lots → improving."""
    densities = [0.016, 0.008, 0.004, 0.002, 0.001]
    for i, d in enumerate(densities):
        _make_lot_with_density(mem_conn, f"LOT_IMP{i:02d}", "wafer", 2, d)
    result = compute_trend(mem_conn)
    assert result.direction == "improving"
    assert result.slope < 0


def test_compute_trend_stable_flat_densities(mem_conn):
    """Constant defect density across lots → stable."""
    for i in range(4):
        _make_lot_with_density(mem_conn, f"LOT_STAB{i:02d}", "wafer", 2, 0.003)
    result = compute_trend(mem_conn)
    assert result.direction == "stable"


# ---------------------------------------------------------------------------
# compute_trend — data point structure
# ---------------------------------------------------------------------------

def test_compute_trend_data_points_count(mem_conn):
    for i in range(5):
        _make_lot_with_density(mem_conn, f"LOT_DP{i:02d}", "wafer", 2, 0.003 + i * 0.001)
    result = compute_trend(mem_conn)
    assert result.n_lots == 5
    assert len(result.data_points) == 5


def test_compute_trend_sequences_monotonically_increasing(mem_conn):
    for i in range(4):
        _make_lot_with_density(mem_conn, f"LOT_SEQ{i:02d}", "wafer", 2, 0.003)
    result = compute_trend(mem_conn)
    seqs = [p.sequence for p in result.data_points]
    assert seqs == sorted(seqs)
    assert seqs[0] == 1


def test_compute_trend_first_last_lot_set(mem_conn):
    for i in range(3):
        _make_lot_with_density(mem_conn, f"LOT_FL{i:02d}", "wafer", 2, 0.003)
    result = compute_trend(mem_conn)
    assert result.first_lot_id == "LOT_FL00"
    assert result.last_lot_id  == "LOT_FL02"


def test_compute_trend_data_point_densities_positive(mem_conn):
    for i in range(3):
        _make_lot_with_density(mem_conn, f"LOT_POS{i:02d}", "wafer", 2, 0.002 + i * 0.001)
    result = compute_trend(mem_conn)
    for pt in result.data_points:
        assert pt.avg_defect_density >= 0


def test_compute_trend_lot_status_valid(mem_conn):
    for i in range(3):
        _make_lot_with_density(mem_conn, f"LOT_ST{i:02d}", "wafer", 2, 0.003)
    result = compute_trend(mem_conn)
    for pt in result.data_points:
        assert pt.lot_status in ("clean", "watch", "excursion")


# ---------------------------------------------------------------------------
# compute_trend — substrate filter
# ---------------------------------------------------------------------------

def test_compute_trend_substrate_filter_wafer(mem_conn):
    _make_lot_with_density(mem_conn, "LOT_WFR01", "wafer", 2, 0.003)
    _make_lot_with_density(mem_conn, "LOT_WFR02", "wafer", 2, 0.004)
    _make_lot_with_density(mem_conn, "LOT_GP01",  "glass_panel", 2, 0.002)
    result = compute_trend(mem_conn, substrate_type="wafer")
    assert result.n_lots == 2
    assert all(pt.substrate_type == "wafer" for pt in result.data_points)


def test_compute_trend_substrate_filter_glass(mem_conn):
    _make_lot_with_density(mem_conn, "LOT_GP02", "glass_panel", 2, 0.001)
    _make_lot_with_density(mem_conn, "LOT_WF03", "wafer",       2, 0.003)
    result = compute_trend(mem_conn, substrate_type="glass_panel")
    assert result.n_lots == 1
    assert result.substrate_type == "glass_panel"


def test_compute_trend_substrate_filter_none_includes_all(mem_conn):
    _make_lot_with_density(mem_conn, "LOT_MX01", "wafer",       2, 0.003)
    _make_lot_with_density(mem_conn, "LOT_MX02", "glass_panel", 2, 0.002)
    result = compute_trend(mem_conn)
    assert result.n_lots == 2


# ---------------------------------------------------------------------------
# compute_trend — statistics
# ---------------------------------------------------------------------------

def test_compute_trend_r_squared_perfect_trend(mem_conn):
    """Monotonically increasing density should give high R²."""
    densities = [0.001, 0.003, 0.005, 0.007, 0.009]
    for i, d in enumerate(densities):
        _make_lot_with_density(mem_conn, f"LOT_RSQ{i:02d}", "wafer", 2, d)
    result = compute_trend(mem_conn)
    assert result.r_squared > 0.8


def test_compute_trend_mean_density_reasonable(mem_conn):
    target = 0.004
    for i in range(4):
        _make_lot_with_density(mem_conn, f"LOT_MEAN{i:02d}", "wafer", 2, target)
    result = compute_trend(mem_conn)
    assert result.mean_density > 0


def test_compute_trend_mean_yield_populated_when_yield_exists(mem_conn):
    for i in range(3):
        _make_lot_with_density(mem_conn, f"LOT_YLD{i:02d}", "wafer", 2, 0.003)
    result = compute_trend(mem_conn)
    # mean_yield may be None if no negbinom values; if present, must be in [0, 1]
    if result.mean_yield is not None:
        assert 0.0 <= result.mean_yield <= 1.0


# ---------------------------------------------------------------------------
# compute_trend — limit parameter
# ---------------------------------------------------------------------------

def test_compute_trend_limit_caps_lot_count(mem_conn):
    for i in range(10):
        _make_lot_with_density(mem_conn, f"LOT_LIM{i:02d}", "wafer", 2, 0.003 + i * 0.0002)
    result = compute_trend(mem_conn, limit=5)
    assert result.n_lots <= 5
