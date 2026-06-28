"""
tests/test_yield_engine.py
---------------------------
Unit and integration tests for the yield calculation engine.
"""

import math
import pytest

from openyield.yield_engine.models import (
    poisson_yield,
    murphy_yield,
    negbinom_yield,
    estimate_alpha_empirical,
    select_recommended_model,
)
from openyield.yield_engine.calculator import (
    calculate_panel_yield,
    calculate_all_yields,
    print_yield_report,
)
from openyield.ingestion.ingest import (
    upsert_panel, upsert_component, upsert_defect
)


# ---------------------------------------------------------------------------
# Model unit tests — poisson_yield
# ---------------------------------------------------------------------------

def test_poisson_zero_defects():
    """Zero defect density → yield = 1.0"""
    assert poisson_yield(A=100.0, D0=0.0) == pytest.approx(1.0)


def test_poisson_known_value():
    """A=1, D0=1 → Y = e^-1 ≈ 0.3679"""
    assert poisson_yield(A=1.0, D0=1.0) == pytest.approx(math.exp(-1), rel=1e-6)


def test_poisson_high_density():
    """High defect density → yield approaches 0"""
    assert poisson_yield(A=100.0, D0=10.0) < 0.01


def test_poisson_invalid_area():
    with pytest.raises(ValueError):
        poisson_yield(A=0.0, D0=1.0)


def test_poisson_invalid_density():
    with pytest.raises(ValueError):
        poisson_yield(A=1.0, D0=-0.1)


# ---------------------------------------------------------------------------
# Model unit tests — murphy_yield
# ---------------------------------------------------------------------------

def test_murphy_zero_defects():
    """Zero defect density → yield = 1.0"""
    assert murphy_yield(A=100.0, D0=0.0) == pytest.approx(1.0)


def test_murphy_known_value():
    """A=1, D0=1 → Y = ((1-e^-1)/1)² ≈ 0.3996"""
    expected = ((1 - math.exp(-1)) / 1.0) ** 2
    assert murphy_yield(A=1.0, D0=1.0) == pytest.approx(expected, rel=1e-6)


def test_murphy_higher_than_poisson():
    """Murphy always gives higher yield than Poisson for same inputs (less pessimistic)"""
    for D0 in [0.001, 0.01, 0.1, 1.0]:
        assert murphy_yield(100.0, D0) > poisson_yield(100.0, D0)


def test_murphy_invalid_area():
    with pytest.raises(ValueError):
        murphy_yield(A=-1.0, D0=0.5)


# ---------------------------------------------------------------------------
# Model unit tests — negbinom_yield
# ---------------------------------------------------------------------------

def test_negbinom_zero_defects():
    assert negbinom_yield(A=100.0, D0=0.0, alpha=1.0) == pytest.approx(1.0)


def test_negbinom_large_alpha_approaches_poisson():
    """As alpha → ∞, negbinom → poisson"""
    A, D0 = 50.0, 0.002
    y_nb = negbinom_yield(A, D0, alpha=1e6)
    y_p  = poisson_yield(A, D0)
    assert abs(y_nb - y_p) < 1e-4


def test_negbinom_low_alpha_higher_yield():
    """Lower α (more clustered) → higher yield than Poisson"""
    A, D0 = 100.0, 0.005
    y_nb = negbinom_yield(A, D0, alpha=0.5)
    y_p  = poisson_yield(A, D0)
    assert y_nb > y_p


def test_negbinom_invalid_alpha():
    with pytest.raises(ValueError):
        negbinom_yield(A=1.0, D0=0.5, alpha=0.0)


def test_negbinom_yield_in_range():
    for alpha in [0.1, 0.5, 1.0, 5.0, 100.0]:
        y = negbinom_yield(100.0, 0.01, alpha)
        assert 0.0 <= y <= 1.0


# ---------------------------------------------------------------------------
# Alpha estimation
# ---------------------------------------------------------------------------

def test_alpha_empirical_random_distribution():
    """Near-Poisson distribution → large alpha returned"""
    # Poisson with λ=2: variance ≈ mean
    counts = [2, 1, 3, 2, 2, 1, 3, 2, 2, 1, 2, 3, 1, 2, 2]
    alpha = estimate_alpha_empirical(counts, die_area_mm2=784.0, D0=0.003)
    assert alpha > 5.0  # near-random → large alpha


def test_alpha_empirical_clustered_distribution():
    """Highly clustered distribution → small alpha"""
    # High variance relative to mean
    counts = [0, 0, 0, 0, 0, 0, 15, 0, 0, 0, 0, 12, 0, 0, 0, 0, 0, 0, 10, 0]
    alpha = estimate_alpha_empirical(counts, die_area_mm2=784.0, D0=0.002)
    assert alpha < 2.0


def test_alpha_empirical_too_few_dies():
    """Fewer than 4 dies → fallback α=1.0"""
    alpha = estimate_alpha_empirical([2, 1, 0], die_area_mm2=784.0, D0=0.001)
    assert alpha == pytest.approx(1.0)


def test_alpha_empirical_all_zero():
    """All dies have zero defects → fallback α=1.0"""
    alpha = estimate_alpha_empirical([0]*20, die_area_mm2=784.0, D0=0.0)
    assert alpha == pytest.approx(1.0)


def test_alpha_result_positive():
    counts = [1, 2, 0, 3, 1, 5, 0, 2, 1, 0, 4, 2, 1, 0, 3]
    alpha = estimate_alpha_empirical(counts, 784.0, 0.002)
    assert alpha > 0


# ---------------------------------------------------------------------------
# Model selector
# ---------------------------------------------------------------------------

def test_selector_wafer_clustered():
    model, notes = select_recommended_model("wafer", alpha=0.4, AD=0.5)
    assert model == "negbinom"
    assert "cluster" in notes.lower()


def test_selector_wafer_moderate():
    model, notes = select_recommended_model("wafer", alpha=3.0, AD=0.5)
    assert model == "murphy"


def test_selector_wafer_random():
    model, notes = select_recommended_model("wafer", alpha=50.0, AD=0.5)
    assert model == "poisson"


def test_selector_glass_low_loss():
    model, notes = select_recommended_model("glass_panel", alpha=1.0, AD=0.1)
    assert model == "poisson"


def test_selector_glass_high_loss():
    model, notes = select_recommended_model("glass_panel", alpha=1.0, AD=1.5)
    assert model == "murphy"


# ---------------------------------------------------------------------------
# Calculator integration tests
# ---------------------------------------------------------------------------

def _setup_panel(conn, panel_id, substrate_type, rows, cols, pitch,
                 defects_per_die):
    """Helper: insert panel, components, and defects."""
    with conn:
        upsert_panel(conn, panel_id, "TEST-PRODUCT", substrate_type, rows, cols)
        idx = 0
        for r in range(rows):
            for c in range(cols):
                upsert_component(
                    conn, panel_id, r, c,
                    "zone_center",
                    float(c * pitch), float(r * pitch),
                    active=True
                )
                n_defects = defects_per_die[idx % len(defects_per_die)]
                for i in range(n_defects):
                    upsert_defect(
                        conn, panel_id, r, c,
                        "system_a", "particle",
                        float(c * pitch + i * 0.1),
                        float(r * pitch + i * 0.1),
                        0.05, 0.75
                    )
                idx += 1


def test_calculate_panel_yield_wafer(mem_conn):
    _setup_panel(
        mem_conn, "WF_TEST01", "wafer", 4, 4, 28.0,
        [0, 1, 2, 0, 1, 0, 3, 1, 0, 2, 1, 0, 0, 1, 2, 1]
    )
    est = calculate_panel_yield(mem_conn, "WF_TEST01", persist=True)

    assert est.panel_id == "WF_TEST01"
    assert est.substrate_type == "wafer"
    assert est.die_area_mm2 == pytest.approx(28.0 ** 2)
    assert est.inspected_dies == 16
    assert est.defect_count == sum([0,1,2,0,1,0,3,1,0,2,1,0,0,1,2,1])
    assert 0.0 <= est.yield_poisson <= 1.0
    assert 0.0 <= est.yield_murphy <= 1.0
    assert 0.0 <= est.yield_negbinom <= 1.0
    assert est.clustering_alpha > 0
    assert est.alpha_method == "empirical"
    assert est.recommended_model in ("poisson", "murphy", "negbinom")

    # Verify persisted to DB
    row = mem_conn.execute(
        "SELECT * FROM yield_estimates WHERE panel_id = 'WF_TEST01'"
    ).fetchone()
    assert row is not None
    assert abs(row["yield_poisson"] - est.yield_poisson) < 1e-4


def test_calculate_panel_yield_glass(mem_conn):
    _setup_panel(
        mem_conn, "GP_TEST01", "glass_panel", 3, 3, 370.0,
        [2, 3, 1, 4, 2, 3, 1, 2, 3]
    )
    est = calculate_panel_yield(mem_conn, "GP_TEST01", persist=True)

    assert est.substrate_type == "glass_panel"
    assert est.alpha_method == "profile"
    assert est.clustering_alpha == pytest.approx(1.0)
    assert 0.0 <= est.yield_negbinom <= 1.0


def test_calculate_panel_yield_no_persist(mem_conn):
    _setup_panel(mem_conn, "WF_NOPER", "wafer", 2, 2, 28.0, [1, 0, 2, 1])
    est = calculate_panel_yield(mem_conn, "WF_NOPER", persist=False)
    assert est is not None
    row = mem_conn.execute(
        "SELECT COUNT(*) FROM yield_estimates WHERE panel_id='WF_NOPER'"
    ).fetchone()[0]
    assert row == 0


def test_calculate_panel_not_found(mem_conn):
    with pytest.raises(ValueError, match="not found"):
        calculate_panel_yield(mem_conn, "NONEXISTENT", persist=False)


def test_calculate_all_yields(mem_conn):
    _setup_panel(mem_conn, "WF_A01", "wafer",       4, 4, 28.0,  [1,0,2,1]*4)
    _setup_panel(mem_conn, "GP_A01", "glass_panel", 3, 3, 370.0, [2,1,3]*3)

    estimates = calculate_all_yields(mem_conn, persist=True)
    assert len(estimates) == 2
    pids = {e.panel_id for e in estimates}
    assert "WF_A01" in pids
    assert "GP_A01" in pids


def test_calculate_all_yields_filtered(mem_conn):
    _setup_panel(mem_conn, "WF_B01", "wafer",       4, 4, 28.0,  [1,0,2,1]*4)
    _setup_panel(mem_conn, "GP_B01", "glass_panel", 3, 3, 370.0, [2,1,3]*3)

    estimates = calculate_all_yields(mem_conn, substrate_type="wafer", persist=False)
    assert len(estimates) == 1
    assert estimates[0].substrate_type == "wafer"


def test_zero_defects_yield_is_one(mem_conn):
    """Panel with no defects should have 100% yield across all models."""
    _setup_panel(mem_conn, "WF_CLEAN", "wafer", 4, 4, 28.0, [0]*16)
    est = calculate_panel_yield(mem_conn, "WF_CLEAN", persist=False)
    assert est.yield_poisson  == pytest.approx(1.0)
    assert est.yield_murphy   == pytest.approx(1.0)
    assert est.yield_negbinom == pytest.approx(1.0)
    assert est.defect_density == pytest.approx(0.0)


def test_murphy_higher_than_poisson_integration(mem_conn):
    """Murphy always >= Poisson for same inputs."""
    _setup_panel(mem_conn, "WF_CMP", "wafer", 4, 4, 28.0, [2,1,3,0]*4)
    est = calculate_panel_yield(mem_conn, "WF_CMP", persist=False)
    assert est.yield_murphy >= est.yield_poisson


def test_print_yield_report_runs(mem_conn, capsys):
    _setup_panel(mem_conn, "WF_PRN", "wafer", 4, 4, 28.0, [1,2,0,1]*4)
    estimates = calculate_all_yields(mem_conn, persist=False)
    print_yield_report(estimates)
    captured = capsys.readouterr()
    assert "YIELD REPORT" in captured.out
    assert "WF_PRN" in captured.out
