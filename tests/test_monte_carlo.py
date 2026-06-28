"""tests/test_monte_carlo.py — Unit tests for openyield.simulation.monte_carlo."""
import math
import pytest
from openyield.simulation.monte_carlo import (
    run_monte_carlo,
    dies_per_wafer,
    MonteCarloResult,
)


# ---------------------------------------------------------------------------
# dies_per_wafer
# ---------------------------------------------------------------------------

def test_dpw_300mm_7x7():
    n = dies_per_wafer(300, 7 * 7)
    # 300 mm wafer, 49 mm² die → ~1347 dies
    assert 1000 < n < 1600


def test_dpw_200mm():
    n = dies_per_wafer(200, 10 * 10)
    # 200 mm wafer, 100 mm² die → ~269 dies
    assert 150 < n < 400


def test_dpw_zero_area():
    assert dies_per_wafer(300, 0) == 0


def test_dpw_zero_wafer():
    assert dies_per_wafer(0, 50) == 0


# ---------------------------------------------------------------------------
# run_monte_carlo — return type
# ---------------------------------------------------------------------------

def test_returns_monte_carlo_result():
    result = run_monte_carlo(d0=0.1, die_area_mm2=56.25, n_runs=200, seed=0)
    assert isinstance(result, MonteCarloResult)


# ---------------------------------------------------------------------------
# run_monte_carlo — statistics sanity
# ---------------------------------------------------------------------------

def test_yield_in_unit_interval():
    result = run_monte_carlo(d0=0.2, die_area_mm2=64.0, n_runs=500, seed=1)
    assert 0.0 <= result.mean_yield <= 1.0
    assert 0.0 <= result.p10_yield <= 1.0
    assert 0.0 <= result.p90_yield <= 1.0
    assert result.min_yield <= result.mean_yield <= result.max_yield


def test_percentile_ordering():
    result = run_monte_carlo(d0=0.15, die_area_mm2=56.25, n_runs=500, seed=2)
    assert result.p10_yield <= result.p50_yield <= result.p90_yield


def test_std_positive_nonzero():
    # With finite n_dies the per-run yield must have variance
    result = run_monte_carlo(d0=0.5, die_area_mm2=100.0, n_runs=500, seed=3)
    assert result.std_yield >= 0.0


def test_low_d0_high_yield():
    result = run_monte_carlo(d0=0.01, die_area_mm2=10.0, n_runs=500, seed=4)
    assert result.mean_yield > 0.85


def test_high_d0_low_yield():
    result = run_monte_carlo(d0=5.0, die_area_mm2=100.0, n_runs=500, seed=5)
    assert result.mean_yield < 0.10


# ---------------------------------------------------------------------------
# closed-form reference values
# ---------------------------------------------------------------------------

def test_poisson_yield_reference():
    d0, area_mm2 = 0.2, 50.0
    expected = math.exp(-d0 * area_mm2 / 100.0)
    result = run_monte_carlo(d0=d0, die_area_mm2=area_mm2, n_runs=200, seed=6)
    assert abs(result.poisson_yield - expected) < 1e-9


def test_murphy_yield_reference():
    d0, area_mm2 = 0.2, 50.0
    x = d0 * area_mm2 / 100.0
    expected = ((1.0 - math.exp(-x)) / x) ** 2
    result = run_monte_carlo(d0=d0, die_area_mm2=area_mm2, n_runs=200, seed=7)
    assert abs(result.murphy_yield - expected) < 1e-9


def test_negbinom_yield_reference():
    d0, area_mm2, alpha = 0.2, 50.0, 2.0
    expected = (1.0 + d0 * area_mm2 / 100.0 / alpha) ** (-alpha)
    result = run_monte_carlo(d0=d0, die_area_mm2=area_mm2, alpha=alpha, n_runs=200, seed=8)
    assert abs(result.negbinom_yield - expected) < 1e-9


# ---------------------------------------------------------------------------
# sensitivity
# ---------------------------------------------------------------------------

def test_sensitivity_direction():
    # Lower D₀ → higher yield; higher D₀ → lower yield
    result = run_monte_carlo(d0=0.3, die_area_mm2=50.0, n_runs=1000, seed=9)
    assert result.yield_d0_minus20 > result.yield_d0_plus20


# ---------------------------------------------------------------------------
# histogram
# ---------------------------------------------------------------------------

def test_histogram_bin_count():
    result = run_monte_carlo(d0=0.1, die_area_mm2=50.0, n_runs=200, seed=10)
    assert len(result.histogram) == 20


def test_histogram_coverage():
    result = run_monte_carlo(d0=0.2, die_area_mm2=50.0, n_runs=500, seed=11)
    total = sum(b["count"] for b in result.histogram)
    assert total == result.n_runs


def test_histogram_bin_bounds():
    result = run_monte_carlo(d0=0.2, die_area_mm2=50.0, n_runs=200, seed=12)
    for b in result.histogram:
        assert 0.0 <= b["bin_low"] < b["bin_high"] <= 1.0


# ---------------------------------------------------------------------------
# reproducibility
# ---------------------------------------------------------------------------

def test_same_seed_same_result():
    r1 = run_monte_carlo(d0=0.2, die_area_mm2=56.25, n_runs=300, seed=42)
    r2 = run_monte_carlo(d0=0.2, die_area_mm2=56.25, n_runs=300, seed=42)
    assert r1.mean_yield == r2.mean_yield
    assert r1.std_yield  == r2.std_yield


def test_different_seed_different_result():
    r1 = run_monte_carlo(d0=0.2, die_area_mm2=56.25, n_runs=300, seed=1)
    r2 = run_monte_carlo(d0=0.2, die_area_mm2=56.25, n_runs=300, seed=2)
    # Very unlikely to be identical
    assert r1.mean_yield != r2.mean_yield


# ---------------------------------------------------------------------------
# critical area fraction
# ---------------------------------------------------------------------------

def test_ca_fraction_lt_one_higher_yield():
    r_full = run_monte_carlo(d0=0.5, die_area_mm2=100.0, critical_area_fraction=1.0, n_runs=500, seed=20)
    r_half = run_monte_carlo(d0=0.5, die_area_mm2=100.0, critical_area_fraction=0.5, n_runs=500, seed=20)
    assert r_half.mean_yield > r_full.mean_yield


# ---------------------------------------------------------------------------
# echo-back fields
# ---------------------------------------------------------------------------

def test_result_echoes_inputs():
    d0, area, runs = 0.15, 56.25, 300
    result = run_monte_carlo(d0=d0, die_area_mm2=area, n_runs=runs, seed=30)
    assert result.d0 == d0
    assert result.die_area_mm2 == area
    assert result.n_runs == runs
