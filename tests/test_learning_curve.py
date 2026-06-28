"""tests/test_learning_curve.py — Unit tests for openyield.simulation.learning_curve."""
import math
import pytest
from openyield.simulation.learning_curve import run_learning_curve, LearningCurveResult


# ---------------------------------------------------------------------------
# Basic sanity
# ---------------------------------------------------------------------------

def test_returns_result():
    r = run_learning_curve(0.55, 0.85, model="exponential")
    assert isinstance(r, LearningCurveResult)


def test_projected_length():
    r = run_learning_curve(0.5, 0.80, model="linear", n_months=24)
    assert len(r.projected) == 25  # month 0 … 24 inclusive


def test_month_zero_equals_current_yield():
    r = run_learning_curve(0.60, 0.85, model="exponential")
    assert abs(r.projected[0].yield_fraction - 0.60) < 1e-9


def test_yield_monotone_increasing_linear():
    r = run_learning_curve(0.40, 0.80, model="linear", improvement_rate=0.03)
    yields = [p.yield_fraction for p in r.projected]
    assert all(yields[i] <= yields[i + 1] for i in range(len(yields) - 1))


def test_yield_monotone_increasing_exponential():
    r = run_learning_curve(0.40, 0.80, model="exponential", improvement_rate=0.05)
    yields = [p.yield_fraction for p in r.projected]
    assert all(yields[i] <= yields[i + 1] for i in range(len(yields) - 1))


def test_yield_monotone_increasing_d0():
    r = run_learning_curve(
        0.40, 0.85, model="d0_learning", improvement_rate=0.08,
        die_area_mm2=56.25, initial_d0=1.0
    )
    yields = [p.yield_fraction for p in r.projected]
    assert all(yields[i] <= yields[i + 1] for i in range(len(yields) - 1))


# ---------------------------------------------------------------------------
# Ceiling
# ---------------------------------------------------------------------------

def test_linear_ceiling():
    r = run_learning_curve(0.70, 0.95, model="linear", improvement_rate=0.05,
                           y_max=0.90, n_months=12)
    assert max(p.yield_fraction for p in r.projected) <= 0.90 + 1e-9


def test_exponential_ceiling():
    r = run_learning_curve(0.50, 0.95, model="exponential", improvement_rate=0.2,
                           y_max=0.92, n_months=24)
    assert max(p.yield_fraction for p in r.projected) <= 0.92 + 1e-9


# ---------------------------------------------------------------------------
# months_to_target
# ---------------------------------------------------------------------------

def test_target_already_met():
    r = run_learning_curve(0.90, 0.80, model="linear")
    assert r.months_to_target == 0.0


def test_linear_months_to_target():
    # 0.55 → 0.75 at 0.02/month = 10 months
    r = run_learning_curve(0.55, 0.75, model="linear", improvement_rate=0.02, n_months=12)
    assert r.months_to_target is not None
    assert abs(r.months_to_target - 10.0) < 1.0


def test_exponential_months_to_target_found():
    r = run_learning_curve(0.40, 0.70, model="exponential", improvement_rate=0.10, n_months=24)
    assert r.months_to_target is not None
    assert r.months_to_target > 0


def test_unreachable_target():
    # Target above y_max is unreachable
    r = run_learning_curve(0.50, 0.99, model="linear", improvement_rate=0.01,
                           y_max=0.95, n_months=12)
    assert r.months_to_target is None


# ---------------------------------------------------------------------------
# D₀ learning model specific
# ---------------------------------------------------------------------------

def test_d0_learning_d0_field_set():
    r = run_learning_curve(0.40, 0.80, model="d0_learning", improvement_rate=0.08,
                           die_area_mm2=56.25, initial_d0=1.2, n_months=12)
    for p in r.projected:
        assert p.d0 is not None


def test_d0_learning_d0_decreasing():
    r = run_learning_curve(0.40, 0.80, model="d0_learning", improvement_rate=0.08,
                           die_area_mm2=56.25, initial_d0=1.2, n_months=12)
    d0s = [p.d0 for p in r.projected if p.d0 is not None]
    assert all(d0s[i] >= d0s[i + 1] for i in range(len(d0s) - 1))


def test_d0_learning_initial_d0_set():
    r = run_learning_curve(0.40, 0.80, model="d0_learning", improvement_rate=0.08,
                           die_area_mm2=56.25, initial_d0=1.0)
    assert r.initial_d0 == 1.0
    assert r.final_d0 is not None
    assert r.final_d0 < r.initial_d0


def test_d0_learning_yield_formula():
    d0 = 1.0
    area_mm2 = 56.25
    area_cm2 = area_mm2 / 100.0
    r = run_learning_curve(
        0.0, 0.99, model="d0_learning", improvement_rate=0.0,  # rate=0 means constant D₀
        die_area_mm2=area_mm2, initial_d0=d0, n_months=3,
    )
    # With rate=0, D₀ is constant → Poisson yield at every step
    expected = min(math.exp(-d0 * area_cm2), 0.98)
    for p in r.projected:
        assert abs(p.yield_fraction - expected) < 1e-6


def test_d0_learning_missing_params_raises():
    with pytest.raises(ValueError, match="d0_learning"):
        run_learning_curve(0.5, 0.8, model="d0_learning")


# ---------------------------------------------------------------------------
# echo-back fields
# ---------------------------------------------------------------------------

def test_echoes_model():
    r = run_learning_curve(0.5, 0.8, model="linear")
    assert r.model == "linear"


def test_echoes_yields():
    r = run_learning_curve(0.55, 0.85, model="exponential")
    assert r.current_yield == 0.55
    assert r.target_yield == 0.85


def test_improvement_rate_echoed():
    r = run_learning_curve(0.5, 0.8, model="linear", improvement_rate=0.03)
    assert r.improvement_rate == 0.03


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------

def test_invalid_model():
    with pytest.raises(ValueError, match="Unknown model"):
        run_learning_curve(0.5, 0.8, model="magic")


def test_current_yield_out_of_range():
    with pytest.raises(ValueError):
        run_learning_curve(1.5, 0.8, model="linear")


def test_target_yield_zero():
    with pytest.raises(ValueError):
        run_learning_curve(0.5, 0.0, model="linear")


# ---------------------------------------------------------------------------
# n_months horizon
# ---------------------------------------------------------------------------

def test_custom_horizon():
    r = run_learning_curve(0.5, 0.9, model="exponential", n_months=36)
    assert len(r.projected) == 37


def test_one_month():
    r = run_learning_curve(0.5, 0.9, model="linear", n_months=1)
    assert len(r.projected) == 2
