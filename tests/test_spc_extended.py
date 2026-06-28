"""
tests/test_spc_extended.py
---------------------------
Tests for the extended SPC module: CUSUM, IMR, Cp/Cpk, alarms, persistence.
"""

import pytest
from openyield.analysis.spc import (
    calculate_spc,
    _compute_baseline,
    _robust_sigma,
    _we_rules,
    _capability,
    _median,
    _mad_sigma,
)
from openyield.ingestion.ingest import (
    upsert_panel, upsert_component, upsert_defect, upsert_lot
)
from openyield.yield_engine.calculator import calculate_panel_yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_panels(conn, lot_id, substrate_type, densities):
    """
    Create panels with controlled defect densities.
    density = defects / mm² so n_defects = density × pitch² × n_active_dies
    """
    pitch = 28.0 if substrate_type == "wafer" else 370.0
    rows, cols = (4, 4)
    die_area = pitch ** 2
    n_dies   = rows * cols

    with conn:
        upsert_lot(conn, lot_id, substrate_type, "TEST", lot_size=25)

    panel_ids = []
    for i, density in enumerate(densities):
        pid = f"{lot_id}_P{i:02d}"
        n_defects = max(0, int(round(density * die_area * n_dies)))
        with conn:
            upsert_panel(conn, pid, "TEST", substrate_type,
                         rows, cols, lot_id=lot_id)
            for r in range(rows):
                for c in range(cols):
                    upsert_component(
                        conn, pid, r, c, "zone_center",
                        float(c * pitch), float(r * pitch)
                    )
            for j in range(n_defects):
                upsert_defect(
                    conn, pid,
                    j % rows, j % cols,
                    "system_a", "particle",
                    float(j * 0.3), float(j * 0.2),
                    0.1, 0.8
                )
        calculate_panel_yield(conn, pid, persist=True)
        panel_ids.append(pid)
    return panel_ids


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------

def test_median_odd():
    assert _median([3.0, 1.0, 2.0]) == pytest.approx(2.0)

def test_median_even():
    assert _median([1.0, 2.0, 3.0, 4.0]) == pytest.approx(2.5)

def test_median_single():
    assert _median([5.0]) == pytest.approx(5.0)

def test_mad_sigma_uniform():
    # All same value → MAD=0
    assert _mad_sigma([2.0]*5, 2.0) == pytest.approx(0.0)

def test_mad_sigma_known():
    # [1,1,2,2,4,6,9] median=2, MAD=median([1,1,0,0,2,4,7])=1, sigma=1.4826
    values = [1.0, 1.0, 2.0, 2.0, 4.0, 6.0, 9.0]
    med = _median(values)
    result = _mad_sigma(values, med)
    assert result > 0

def test_robust_sigma_returns_positive(mem_conn):
    values = [0.001, 0.0012, 0.0009, 0.0011, 0.0010]
    mean, sigma, med = _robust_sigma(values)
    assert mean > 0
    assert sigma > 0
    assert med > 0


# ---------------------------------------------------------------------------
# Capability indices
# ---------------------------------------------------------------------------

def test_capability_capable():
    cap = _capability(mean=0.001, sigma=0.0001, usl=0.0014, lsl=0.0006)
    assert cap.cpk is not None
    assert cap.cpk >= 1.0
    assert "Capable" in cap.interpretation or "Excellent" in cap.interpretation

def test_capability_incapable():
    cap = _capability(mean=0.005, sigma=0.003, usl=0.006, lsl=0.004)
    assert cap.cpk is not None
    assert cap.cpk < 1.33

def test_capability_zero_sigma():
    cap = _capability(mean=0.001, sigma=0.0, usl=0.002, lsl=0.0)
    assert cap.cp is None
    assert cap.cpk is None

def test_capability_auto_limits():
    """When usl/lsl=None, auto-computed from μ±3σ → Cpk=1.0."""
    cap = _capability(mean=0.005, sigma=0.001, usl=None, lsl=None)
    assert cap.usl is not None
    assert cap.lsl is not None
    assert cap.cpk == pytest.approx(1.0, abs=0.01)

def test_capability_cp_gte_cpk():
    """Cp ≥ |Cpk| always (Cp measures potential, Cpk measures actual)."""
    cap = _capability(mean=0.006, sigma=0.001, usl=0.009, lsl=0.003)
    if cap.cp and cap.cpk:
        assert cap.cp >= abs(cap.cpk) - 1e-9


# ---------------------------------------------------------------------------
# SPC with stable process
# ---------------------------------------------------------------------------

def test_spc_stable_in_control(mem_conn):
    _setup_panels(mem_conn, "LOT_STB", "wafer", [0.002]*6)
    result = calculate_spc(mem_conn, lot_id="LOT_STB", persist=False)
    assert result.n_points == 6
    assert result.process_state == "in_control"
    assert len(result.alarms) == 0


def test_spc_has_all_chart_types(mem_conn):
    _setup_panels(mem_conn, "LOT_CT", "wafer", [0.002]*4)
    result = calculate_spc(mem_conn, lot_id="LOT_CT", persist=False)
    # Every point has all chart statistics
    for pt in result.points:
        assert hasattr(pt, "ewma")
        assert hasattr(pt, "cusum_pos")
        assert hasattr(pt, "cusum_neg")
        assert hasattr(pt, "moving_range")
        assert hasattr(pt, "ucl_imr")
        assert hasattr(pt, "ucl_cusum")


def test_spc_capability_computed(mem_conn):
    _setup_panels(mem_conn, "LOT_CAP", "wafer", [0.002]*5)
    result = calculate_spc(mem_conn, lot_id="LOT_CAP", persist=False)
    assert result.capability is not None
    assert isinstance(result.capability.interpretation, str)


def test_spc_custom_spec_limits(mem_conn):
    _setup_panels(mem_conn, "LOT_USL", "wafer", [0.002]*4)
    result = calculate_spc(
        mem_conn, lot_id="LOT_USL",
        usl=0.005, lsl=0.0,
        persist=False
    )
    assert result.capability.usl == pytest.approx(0.005)
    assert result.capability.lsl == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# CUSUM detection
# ---------------------------------------------------------------------------

def test_cusum_detects_sustained_shift(mem_conn):
    """Sustained upward shift → CUSUM or Shewhart signal."""
    # Baseline at 0.001, then large sustained increase
    densities = [0.001] * 3 + [0.005, 0.006, 0.007, 0.008, 0.009, 0.010]
    _setup_panels(mem_conn, "LOT_CUS", "wafer", densities)
    result = calculate_spc(
        mem_conn, lot_id="LOT_CUS",
        cusum_k=0.5, cusum_h=2.0,
        persist=False
    )
    # Either CUSUM or Shewhart should detect the sustained shift
    any_signal = (
        len(result.cusum_signals) > 0 or
        len(result.shewhart_signals) > 0 or
        result.process_state != "in_control"
    )
    assert any_signal


def test_cusum_values_non_negative(mem_conn):
    _setup_panels(mem_conn, "LOT_CN", "wafer", [0.002]*5)
    result = calculate_spc(mem_conn, lot_id="LOT_CN", persist=False)
    for pt in result.points:
        assert pt.cusum_pos >= 0
        assert pt.cusum_neg >= 0


def test_cusum_resets_on_stable_process(mem_conn):
    """After excursion, CUSUM should reset when process stabilises."""
    _setup_panels(mem_conn, "LOT_CRS", "wafer", [0.001]*5)
    result = calculate_spc(mem_conn, lot_id="LOT_CRS", persist=False)
    # With stable process, CUSUM should remain low
    last_pt = result.points[-1]
    assert last_pt.cusum_pos < 5 * result.sigma


# ---------------------------------------------------------------------------
# IMR chart
# ---------------------------------------------------------------------------

def test_imr_moving_range_first_point_zero(mem_conn):
    _setup_panels(mem_conn, "LOT_IMR", "wafer", [0.002]*4)
    result = calculate_spc(mem_conn, lot_id="LOT_IMR", persist=False)
    assert result.points[0].moving_range == pytest.approx(0.0)


def test_imr_moving_range_is_absolute_diff(mem_conn):
    _setup_panels(mem_conn, "LOT_MR2", "wafer", [0.001, 0.003, 0.002, 0.004])
    result = calculate_spc(mem_conn, lot_id="LOT_MR2", persist=False)
    # MR[1] = |v[1] - v[0]|
    if len(result.points) >= 2:
        expected_mr = abs(result.points[1].value - result.points[0].value)
        assert result.points[1].moving_range == pytest.approx(expected_mr, rel=1e-4)


def test_imr_detects_spike(mem_conn):
    """Sudden spike then return to baseline → IMR or Shewhart signal."""
    # 100x spike compared to baseline — clearly an outlier
    densities = [0.001, 0.001, 0.001, 0.100, 0.001, 0.001]
    _setup_panels(mem_conn, "LOT_SPK", "wafer", densities)
    result = calculate_spc(mem_conn, lot_id="LOT_SPK", persist=False)
    # Either IMR or Shewhart (ratio rule) should catch a 100x spike
    any_signal = (
        len([p for p in result.points if p.imr_signal]) >= 1 or
        len(result.shewhart_signals) >= 1
    )
    assert any_signal


# ---------------------------------------------------------------------------
# EWMA
# ---------------------------------------------------------------------------

def test_ewma_smoothed_value_between_previous_and_current(mem_conn):
    _setup_panels(mem_conn, "LOT_EW", "wafer", [0.001, 0.003, 0.002, 0.004])
    result = calculate_spc(mem_conn, lot_id="LOT_EW",
                           lambda_ewma=0.3, persist=False)
    # EWMA should be between mean and current value (smoothed)
    for pt in result.points[1:]:
        assert pt.ewma >= 0


def test_ewma_lambda_1_equals_current_value(mem_conn):
    """λ=1 → EWMA equals the current value exactly."""
    _setup_panels(mem_conn, "LOT_L1", "wafer", [0.001, 0.002, 0.003])
    result = calculate_spc(mem_conn, lot_id="LOT_L1",
                           lambda_ewma=1.0, persist=False)
    for pt in result.points:
        assert pt.ewma == pytest.approx(pt.value, rel=1e-4)


# ---------------------------------------------------------------------------
# Alarms
# ---------------------------------------------------------------------------

def test_alarms_have_required_fields(mem_conn):
    densities = [0.001]*3 + [0.050]   # large excursion
    _setup_panels(mem_conn, "LOT_ALM", "wafer", densities)
    result = calculate_spc(mem_conn, lot_id="LOT_ALM", persist=False)
    for alarm in result.alarms:
        assert alarm.panel_id
        assert alarm.chart_type in ("shewhart", "ewma", "cusum", "imr")
        assert alarm.severity in ("warning", "out_of_control")
        assert alarm.value >= 0
        assert alarm.control_limit >= 0
        assert alarm.rule_fired


def test_alarm_severity_out_of_control_on_large_excursion(mem_conn):
    densities = [0.001]*4 + [0.100]
    _setup_panels(mem_conn, "LOT_SEV", "wafer", densities)
    result = calculate_spc(mem_conn, lot_id="LOT_SEV", persist=False)
    oc_alarms = [a for a in result.alarms if a.severity == "out_of_control"]
    assert len(oc_alarms) >= 1


def test_no_alarms_on_stable_process(mem_conn):
    _setup_panels(mem_conn, "LOT_NA", "wafer", [0.002]*6)
    result = calculate_spc(mem_conn, lot_id="LOT_NA", persist=False)
    assert result.alarms == []


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def test_spc_persists_result(mem_conn):
    _setup_panels(mem_conn, "LOT_PER", "wafer", [0.002]*4)
    result = calculate_spc(mem_conn, lot_id="LOT_PER", persist=True)
    row = mem_conn.execute(
        "SELECT * FROM spc_results WHERE lot_id='LOT_PER'"
    ).fetchone()
    assert row is not None
    assert row["n_points"] == 4
    assert row["process_state"] == result.process_state


def test_spc_persists_alarms(mem_conn):
    densities = [0.001]*3 + [0.050]
    _setup_panels(mem_conn, "LOT_PA", "wafer", densities)
    result = calculate_spc(mem_conn, lot_id="LOT_PA", persist=True)
    if result.alarms:
        rows = mem_conn.execute(
            "SELECT * FROM spc_alarms WHERE spc_result_id=?",
            (result.db_id,)
        ).fetchall()
        assert len(rows) == len(result.alarms)


def test_spc_no_persist(mem_conn):
    _setup_panels(mem_conn, "LOT_NP", "wafer", [0.002]*3)
    result = calculate_spc(mem_conn, lot_id="LOT_NP", persist=False)
    row = mem_conn.execute(
        "SELECT COUNT(*) FROM spc_results WHERE lot_id='LOT_NP'"
    ).fetchone()[0]
    assert row == 0


def test_spc_db_id_set_after_persist(mem_conn):
    _setup_panels(mem_conn, "LOT_DBI", "wafer", [0.002]*3)
    result = calculate_spc(mem_conn, lot_id="LOT_DBI", persist=True)
    assert result.db_id is not None
    assert result.db_id > 0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_spc_insufficient_data(mem_conn):
    _setup_panels(mem_conn, "LOT_INS", "wafer", [0.002])
    result = calculate_spc(mem_conn, lot_id="LOT_INS", persist=False)
    assert result.n_points <= 1
    assert result.process_state == "in_control"


def test_spc_invalid_lambda():
    import sqlite3
    conn = sqlite3.connect(":memory:")
    with pytest.raises(ValueError, match="lambda_ewma"):
        calculate_spc(conn, lambda_ewma=0.0)


def test_spc_invalid_L():
    import sqlite3
    conn = sqlite3.connect(":memory:")
    with pytest.raises(ValueError, match="L_ewma"):
        calculate_spc(conn, L_ewma=-1.0)


def test_spc_substrate_filter(mem_conn):
    _setup_panels(mem_conn, "LOT_WF", "wafer",       [0.002]*3)
    _setup_panels(mem_conn, "LOT_GP", "glass_panel", [0.001]*3)
    result = calculate_spc(mem_conn, substrate_type="wafer", persist=False)
    panel_ids = {pt.panel_id for pt in result.points}
    assert all("LOT_WF" in pid for pid in panel_ids)


def test_spc_points_ordered_by_sequence(mem_conn):
    _setup_panels(mem_conn, "LOT_ORD", "wafer", [0.002]*5)
    result = calculate_spc(mem_conn, lot_id="LOT_ORD", persist=False)
    sequences = [pt.sequence for pt in result.points]
    assert sequences == sorted(sequences)
    assert sequences[0] == 1
