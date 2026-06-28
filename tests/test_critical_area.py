"""
tests/test_critical_area.py
----------------------------
Tests for yield_engine/critical_area.py — Maly linear expansion model.
"""

import math
import pytest

from openyield.yield_engine.critical_area import (
    _ca_for_size,
    compute_critical_area,
    compute_panel_critical_area,
    CriticalAreaResult,
)
from openyield.synthetic.substrate_profiles import get_profile
from openyield.ingestion.ingest import upsert_panel, upsert_component, upsert_defect


# ---------------------------------------------------------------------------
# _ca_for_size — unit tests
# ---------------------------------------------------------------------------

def test_ca_zero_defect_size_returns_layout_density():
    """A defect of size 0 contributes no expansion — CA = layout density."""
    assert _ca_for_size(0.0, layout_density=0.30, min_feature_mm=0.050) == pytest.approx(0.30)


def test_ca_negative_size_treated_as_zero():
    """Negative sizes (invalid input) are treated as d=0."""
    assert _ca_for_size(-0.01, 0.30, 0.050) == pytest.approx(0.30)


def test_ca_small_defect_below_saturation():
    """Defect smaller than saturation point: CA = f × (1 + d/w)."""
    # f=0.30, w=0.050, d=0.025 → 0.30 × (1 + 0.5) = 0.45
    assert _ca_for_size(0.025, 0.30, 0.050) == pytest.approx(0.45)


def test_ca_saturation_point():
    """At d = w × (1/f - 1), CA exactly reaches 1.0."""
    f, w = 0.30, 0.050
    d_sat = w * (1.0 / f - 1.0)       # 0.050 × 2.333... = 0.1167mm
    ca = _ca_for_size(d_sat, f, w)
    assert ca == pytest.approx(1.0, abs=1e-9)


def test_ca_beyond_saturation_clamped_to_one():
    """Defects larger than d* are clamped to 1.0."""
    assert _ca_for_size(1.0, layout_density=0.30, min_feature_mm=0.050) == pytest.approx(1.0)


def test_ca_full_layout_density_always_one():
    """layout_density=1.0 means any defect size gives CA=1.0."""
    assert _ca_for_size(0.001, 1.0, 0.050) == pytest.approx(1.0)
    assert _ca_for_size(0.0,   1.0, 0.050) == pytest.approx(1.0)


def test_ca_monotone_increasing_in_defect_size():
    """Critical area fraction is non-decreasing as defect size grows."""
    f, w = 0.30, 0.050
    sizes = [0.0, 0.010, 0.025, 0.050, 0.080, 0.150, 1.0]
    ca_vals = [_ca_for_size(d, f, w) for d in sizes]
    for i in range(len(ca_vals) - 1):
        assert ca_vals[i] <= ca_vals[i + 1]


def test_ca_linear_below_saturation():
    """CA grows linearly with d below saturation: Δ(CA) ∝ Δd."""
    f, w = 0.30, 0.050
    d1, d2 = 0.010, 0.020           # both below saturation
    ca1 = _ca_for_size(d1, f, w)
    ca2 = _ca_for_size(d2, f, w)
    # expected slope: f/w = 0.30/0.050 = 6 per mm
    assert (ca2 - ca1) == pytest.approx((d2 - d1) * f / w)


# ---------------------------------------------------------------------------
# compute_critical_area — aggregation tests
# ---------------------------------------------------------------------------

def test_empty_sizes_returns_layout_density():
    """No defects → fallback to layout_density as conservative CA fraction."""
    result = compute_critical_area([], layout_density=0.30, min_feature_mm=0.050)
    assert result.ca_fraction == pytest.approx(0.30)
    assert result.n_defects == 0
    assert result.mean_defect_size_mm == pytest.approx(0.0)
    assert result.method == "maly_linear"


def test_uniform_sizes_mean_matches_single():
    """N identical defects → same CA fraction as computing one."""
    f, w = 0.30, 0.050
    d = 0.040
    single_ca = _ca_for_size(d, f, w)
    result = compute_critical_area([d] * 20, f, w)
    assert result.ca_fraction == pytest.approx(single_ca)


def test_mean_defect_size_computed_correctly():
    """mean_defect_size_mm is the arithmetic mean of input sizes."""
    sizes = [0.01, 0.05, 0.09]
    result = compute_critical_area(sizes, 0.30, 0.050)
    assert result.mean_defect_size_mm == pytest.approx(sum(sizes) / len(sizes))


def test_n_defects_reported_correctly():
    sizes = [0.05] * 7
    result = compute_critical_area(sizes, 0.30, 0.050)
    assert result.n_defects == 7


def test_ca_fraction_in_zero_one_range():
    """CA fraction must always be in [0, 1]."""
    result = compute_critical_area([0.5, 1.0, 2.0], 0.30, 0.050)
    assert 0.0 <= result.ca_fraction <= 1.0


def test_large_defects_push_ca_toward_one():
    """Very large defects → CA fraction approaches 1.0."""
    result = compute_critical_area([100.0] * 10, 0.30, 0.050)
    assert result.ca_fraction == pytest.approx(1.0)


def test_ca_fraction_increases_with_larger_defects():
    """Dataset with larger defects → higher mean CA fraction."""
    small = compute_critical_area([0.010] * 50, 0.30, 0.050)
    large = compute_critical_area([0.100] * 50, 0.30, 0.050)
    assert large.ca_fraction > small.ca_fraction


def test_layout_density_one_always_returns_one():
    """layout_density=1.0 saturates immediately — any size gives CA=1.0."""
    result = compute_critical_area([0.001, 0.01, 0.05], 1.0, 0.050)
    assert result.ca_fraction == pytest.approx(1.0)


def test_invalid_layout_density_raises():
    with pytest.raises(ValueError, match="layout_density"):
        compute_critical_area([0.05], layout_density=0.0, min_feature_mm=0.05)

    with pytest.raises(ValueError, match="layout_density"):
        compute_critical_area([0.05], layout_density=1.5, min_feature_mm=0.05)


def test_invalid_min_feature_raises():
    with pytest.raises(ValueError, match="min_feature_mm"):
        compute_critical_area([0.05], layout_density=0.30, min_feature_mm=0.0)

    with pytest.raises(ValueError, match="min_feature_mm"):
        compute_critical_area([0.05], layout_density=0.30, min_feature_mm=-0.01)


# ---------------------------------------------------------------------------
# Substrate profile defaults
# ---------------------------------------------------------------------------

def test_wafer_profile_has_ca_fields():
    profile = get_profile("wafer")
    assert 0 < profile.layout_density <= 1
    assert profile.min_feature_mm > 0


def test_glass_panel_profile_has_ca_fields():
    profile = get_profile("glass_panel")
    assert 0 < profile.layout_density <= 1
    assert profile.min_feature_mm > 0


def test_wafer_layout_density_lower_than_glass():
    """Logic die has lower routing density than TFT array panel."""
    wafer  = get_profile("wafer")
    glass  = get_profile("glass_panel")
    assert wafer.layout_density < glass.layout_density


def test_wafer_min_feature_smaller_than_glass():
    """Wafer critical features are finer than TFT panel features."""
    wafer = get_profile("wafer")
    glass = get_profile("glass_panel")
    assert wafer.min_feature_mm < glass.min_feature_mm


# ---------------------------------------------------------------------------
# compute_panel_critical_area — DB integration
# ---------------------------------------------------------------------------

@pytest.fixture
def mem_conn(tmp_path):
    from openyield.db.connection import get_connection
    from openyield.db.schema import initialize_schema
    conn = get_connection(tmp_path / "test.db")
    initialize_schema(conn)
    return conn


def _make_panel(conn, panel_id, substrate, defect_sizes):
    """Insert a panel with one active die and defects of the given sizes."""
    pitch = 28.0 if substrate == "wafer" else 370.0
    upsert_panel(conn, panel_id, substrate, rows=2, cols=2,
                 lot_id="LOT_CA01", component_pitch_mm=pitch,
                 product_type="TEST")
    upsert_component(conn, panel_id, 0, 0, 0.0, 0.0, active=True)
    for i, sz in enumerate(defect_sizes):
        upsert_defect(conn, panel_id, 0, 0,
                      "system_a", "particle",
                      float(i) * 0.1, 0.0, sz, 0.80)


def test_panel_ca_no_defects_returns_layout_density(mem_conn):
    upsert_panel(mem_conn, "WF_NXDF", "wafer", rows=2, cols=2,
                 lot_id="LOT_CA02", component_pitch_mm=28.0, product_type="TEST")
    upsert_component(mem_conn, "WF_NXDF", 0, 0, 0.0, 0.0, active=True)
    profile = get_profile("wafer")
    result = compute_panel_critical_area(
        mem_conn, "WF_NXDF",
        layout_density=profile.layout_density,
        min_feature_mm=profile.min_feature_mm,
    )
    assert result.ca_fraction == pytest.approx(profile.layout_density)
    assert result.n_defects == 0


def test_panel_ca_uses_system_a_only(mem_conn):
    """system_b defects must not affect the CA computation."""
    _make_panel(mem_conn, "WF_SYS", "wafer", [0.05, 0.10])
    # Add system_b defects with a very large size — should be ignored
    upsert_defect(mem_conn, "WF_SYS", 0, 0,
                  "system_b", "particle", 5.0, 0.0, 99.9, 0.90)

    profile = get_profile("wafer")
    result = compute_panel_critical_area(
        mem_conn, "WF_SYS",
        layout_density=profile.layout_density,
        min_feature_mm=profile.min_feature_mm,
    )
    assert result.n_defects == 2   # only system_a counted


def test_panel_ca_wafer_fraction_below_one(mem_conn):
    """With typical wafer defect sizes, CA fraction should be < 1.0."""
    _make_panel(mem_conn, "WF_SUB", "wafer", [0.05, 0.06, 0.04, 0.07])
    profile = get_profile("wafer")
    result = compute_panel_critical_area(
        mem_conn, "WF_SUB",
        layout_density=profile.layout_density,
        min_feature_mm=profile.min_feature_mm,
    )
    assert result.ca_fraction < 1.0
    assert result.ca_fraction > profile.layout_density


def test_panel_ca_result_fields_populated(mem_conn):
    _make_panel(mem_conn, "WF_FLD", "wafer", [0.05])
    profile = get_profile("wafer")
    result = compute_panel_critical_area(
        mem_conn, "WF_FLD",
        layout_density=profile.layout_density,
        min_feature_mm=profile.min_feature_mm,
    )
    assert isinstance(result, CriticalAreaResult)
    assert result.layout_density == profile.layout_density
    assert result.min_feature_mm == profile.min_feature_mm
    assert result.method == "maly_linear"
    assert result.mean_defect_size_mm == pytest.approx(0.05)
    assert result.n_defects == 1
