"""
analysis/spc.py
---------------
Author: Yeonkuk Woo

Statistical Process Control (SPC) for OpenYield.

Four chart types
-----------------
Shewhart X-bar    : Classic ±3σ with Western Electric rules WE1–WE4.
                    Sensitive to large sudden shifts.

EWMA              : Exponentially Weighted Moving Average.
                    Sensitive to small sustained drift.
                    λ=0.2, L=3.0 by default (NIST recommended).

CUSUM             : Cumulative Sum chart.
                    Most sensitive to small persistent shifts.
                    k=0.5σ reference value, h=5σ decision interval.

IMR               : Individual Moving Range chart.
                    Companion to X-bar. Detects process instability
                    (variance change) rather than mean shift.
                    UCL_MR = 3.267 × MR-bar (d2 unbiasing constant).

Process capability
-------------------
    Cp  = (USL - LSL) / (6σ)           — potential capability
    Cpk = min((USL-μ)/3σ, (μ-LSL)/3σ) — actual capability
    Target: Cpk ≥ 1.33 (4σ process)

USL/LSL can be supplied by the caller or auto-computed as
μ ± 3σ (natural process limits) when not provided.

Alarm escalation
-----------------
Every signal is recorded as a structured SpcAlarm with:
- which panel triggered it
- which chart and rule fired
- the observed value vs control limit
- severity: warning (2σ–3σ) or out_of_control (>3σ or CUSUM/EWMA breach)

Persistence
-----------
Results and alarms are saved to spc_results and spc_alarms tables,
enabling historical signal tracking and before/after process comparison.

References
----------
Montgomery, D.C., "Introduction to Statistical Quality Control", 8th ed.
NIST/SEMATECH e-Handbook of Statistical Methods, §6.3.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from openyield.db.connection import get_placeholder, is_postgres

logger = logging.getLogger(__name__)
Connection = Any

# IMR unbiasing constant for n=2 (consecutive pairs)
D2 = 1.128
D3 = 0.0
D4 = 3.267   # UCL multiplier for moving range


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SpcAlarm:
    panel_id:      str
    sequence:      int
    chart_type:    str     # 'shewhart', 'ewma', 'cusum', 'imr'
    rule_fired:    str
    value:         float
    control_limit: float
    severity:      str     # 'warning' or 'out_of_control'


@dataclass
class ControlPoint:
    panel_id:        str
    sequence:        int
    value:           float
    moving_range:    float         # |value - previous value|
    ewma:            float
    cusum_pos:       float         # upper CUSUM
    cusum_neg:       float         # lower CUSUM
    ucl_shewhart:    float
    lcl_shewhart:    float
    ucl_ewma:        float
    lcl_ewma:        float
    ucl_cusum:       float
    ucl_imr:         float
    shewhart_signal: bool
    ewma_signal:     bool
    cusum_signal:    bool
    imr_signal:      bool
    we_rules:        list[str]


@dataclass
class CapabilityIndices:
    cp:   float | None
    cpk:  float | None
    usl:  float | None
    lsl:  float | None
    interpretation: str


@dataclass
class SPCResult:
    lot_id:           str | None
    substrate_type:   str | None
    calculated_at:    str
    n_points:         int
    centerline:       float
    sigma:            float
    lambda_ewma:      float
    L_ewma:           float
    cusum_k:          float
    cusum_h:          float
    points:           list[ControlPoint]
    alarms:           list[SpcAlarm]
    shewhart_signals: list[str]
    ewma_signals:     list[str]
    cusum_signals:    list[str]
    imr_signals:      list[str]
    process_state:    str
    capability:       CapabilityIndices
    db_id:            int | None = None   # set after persistence


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------

def _median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    return (s[n//2] if n % 2 else (s[n//2-1] + s[n//2]) / 2.0) if s else 0.0


def _mad_sigma(values: list[float], median: float) -> float:
    if len(values) < 2:
        return 0.0
    return _median([abs(v - median) for v in values]) * 1.4826


def _compute_baseline(values: list[float]) -> tuple[float, float]:
    n = len(values)
    if n < 2:
        return values[0] if values else 0.0, 0.0
    mean = sum(values) / n
    std  = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))
    return mean, std


def _robust_sigma(values: list[float]) -> tuple[float, float, float]:
    """
    Return (mean, sigma, median) using MAD-based robust sigma.
    Falls back to classic sigma with a floor when MAD=0.
    """
    mean, sigma_classic = _compute_baseline(values)
    med   = _median(values)
    sigma = _mad_sigma(values, med)

    if sigma == 0.0 and sigma_classic > 0.0:
        sigma = max(sigma_classic, med * 0.10) if med > 0 else sigma_classic
    if sigma == 0.0:
        sigma = mean * 0.10 if mean > 0 else 1e-10

    return mean, sigma, med


# ---------------------------------------------------------------------------
# Western Electric rules
# ---------------------------------------------------------------------------

def _we_rules(
    values: list[float],
    idx:    int,
    mean:   float,
    sigma:  float,
    med:    float,
) -> list[str]:
    if sigma == 0:
        return []

    fired = []
    window = values[:idx + 1]
    v = values[idx]

    # Rule 1: 1 point beyond ±3σ
    if abs(v - mean) > 3 * sigma:
        fired.append("WE1: beyond 3σ")

    # Ratio rule: value > 5× median
    if med > 0 and v > med * 5.0:
        fired.append("RATIO: >5× median")

    # Value beyond UCL directly
    ucl = med + 3 * sigma
    if v > ucl and "WE1: beyond 3σ" not in fired:
        fired.append("WE1: beyond 3σ")

    # Rule 2: 2 of last 3 beyond ±2σ (same side)
    if len(window) >= 3:
        last3 = window[-3:]
        if (sum(1 for x in last3 if x > mean + 2*sigma) >= 2 or
                sum(1 for x in last3 if x < mean - 2*sigma) >= 2):
            fired.append("WE2: 2/3 beyond 2σ")

    # Rule 3: 4 of last 5 beyond ±1σ (same side)
    if len(window) >= 5:
        last5 = window[-5:]
        if (sum(1 for x in last5 if x > mean + sigma) >= 4 or
                sum(1 for x in last5 if x < mean - sigma) >= 4):
            fired.append("WE3: 4/5 beyond 1σ")

    # Rule 4: 8 consecutive on same side
    if len(window) >= 8:
        last8 = window[-8:]
        if all(x > mean for x in last8) or all(x < mean for x in last8):
            fired.append("WE4: 8 on same side")

    return fired


# ---------------------------------------------------------------------------
# Process capability
# ---------------------------------------------------------------------------

def _capability(
    mean:  float,
    sigma: float,
    usl:   float | None,
    lsl:   float | None,
) -> CapabilityIndices:
    # Auto-compute natural limits if not provided
    if usl is None:
        usl = mean + 3 * sigma
    if lsl is None:
        lsl = max(0.0, mean - 3 * sigma)

    if sigma <= 0 or usl <= lsl:
        return CapabilityIndices(
            cp=None, cpk=None, usl=usl, lsl=lsl,
            interpretation="Cannot compute — sigma=0 or USL≤LSL"
        )

    cp  = (usl - lsl) / (6 * sigma)
    cpu = (usl - mean) / (3 * sigma)
    cpl = (mean - lsl) / (3 * sigma)
    cpk = min(cpu, cpl)

    if cpk >= 1.67:
        interp = f"Excellent (Cpk={cpk:.2f} ≥ 1.67) — 5σ process capability."
    elif cpk >= 1.33:
        interp = f"Capable (Cpk={cpk:.2f} ≥ 1.33) — industry target met."
    elif cpk >= 1.00:
        interp = f"Marginal (Cpk={cpk:.2f}, 1.0–1.33) — process improvement needed."
    else:
        interp = f"Incapable (Cpk={cpk:.2f} < 1.0) — process out of spec."

    return CapabilityIndices(
        cp=round(cp, 4), cpk=round(cpk, 4),
        usl=round(usl, 6), lsl=round(lsl, 6),
        interpretation=interp,
    )


# ---------------------------------------------------------------------------
# Main SPC function
# ---------------------------------------------------------------------------

def calculate_spc(
    conn: Connection,
    *,
    lot_id:         str | None = None,
    substrate_type: str | None = None,
    lambda_ewma:    float = 0.2,
    L_ewma:         float = 3.0,
    cusum_k:        float = 0.5,
    cusum_h:        float = 5.0,
    usl:            float | None = None,
    lsl:            float | None = None,
    persist:        bool = True,
) -> SPCResult:
    """
    Calculate Shewhart, EWMA, CUSUM, and IMR control charts.

    Parameters
    ----------
    conn           : Database connection
    lot_id         : Restrict to panels in a specific lot
    substrate_type : Restrict to one substrate type
    lambda_ewma    : EWMA smoothing (0 < λ ≤ 1, default 0.2)
    L_ewma         : EWMA limit width in sigma (default 3.0)
    cusum_k        : CUSUM reference value in sigma units (default 0.5)
    cusum_h        : CUSUM decision interval in sigma units (default 5.0)
    usl            : Upper spec limit for Cp/Cpk (auto if None)
    lsl            : Lower spec limit for Cp/Cpk (auto if None)
    persist        : Save to spc_results + spc_alarms tables

    Returns
    -------
    SPCResult
    """
    if not (0 < lambda_ewma <= 1):
        raise ValueError(f"lambda_ewma must be in (0,1], got {lambda_ewma}")
    if L_ewma <= 0:
        raise ValueError(f"L_ewma must be > 0, got {L_ewma}")

    ph = get_placeholder(conn)

    # Fetch yield estimates ordered by time
    filters, params = [], []
    if lot_id:
        filters.append(f"p.lot_id = {ph}")
        params.append(lot_id)
    if substrate_type:
        filters.append(f"pan.substrate_type = {ph}")
        params.append(substrate_type)

    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    join  = "JOIN panels p ON p.panel_id = ye.panel_id" if lot_id else ""

    sql = f"""
        SELECT ye.panel_id, ye.defect_density, ye.calculated_at
        FROM yield_estimates ye
        JOIN panels pan ON pan.panel_id = ye.panel_id
        {join}
        {where}
        ORDER BY ye.calculated_at ASC
    """
    all_rows = conn.execute(sql, params).fetchall()

    # Deduplicate — latest estimate per panel
    seen: dict[str, dict] = {}
    for row in all_rows:
        pid = row["panel_id"]
        if pid not in seen or row["calculated_at"] > seen[pid]["calculated_at"]:
            seen[pid] = dict(row)

    ordered    = sorted(seen.values(), key=lambda r: r["calculated_at"])
    empty_cap  = CapabilityIndices(None, None, usl, lsl, "Insufficient data")

    if len(ordered) < 2:
        return SPCResult(
            lot_id=lot_id, substrate_type=substrate_type,
            calculated_at=datetime.now(timezone.utc).isoformat(),
            n_points=len(ordered), centerline=0.0, sigma=0.0,
            lambda_ewma=lambda_ewma, L_ewma=L_ewma,
            cusum_k=cusum_k, cusum_h=cusum_h,
            points=[], alarms=[],
            shewhart_signals=[], ewma_signals=[],
            cusum_signals=[], imr_signals=[],
            process_state="in_control", capability=empty_cap,
        )

    values    = [r["defect_density"] for r in ordered]
    panel_ids = [r["panel_id"]       for r in ordered]

    mean, sigma, med = _robust_sigma(values)

    # Control limits
    ucl_sh  = med + 3 * sigma
    lcl_sh  = max(0.0, med - 3 * sigma)
    # Use median MR (robust) instead of mean MR to prevent outliers inflating UCL
    moving_ranges = [abs(values[i] - values[i-1]) for i in range(1, len(values))]
    mr_bar  = _median(moving_ranges) if moving_ranges else 0.0
    ucl_imr = D4 * mr_bar if mr_bar > 0 else sigma * D4

    # CUSUM thresholds
    k_abs   = cusum_k * sigma
    h_abs   = cusum_h * sigma

    # Capability
    cap = _capability(mean, sigma, usl, lsl)

    # Build per-point statistics
    ewma_val  = mean
    cusum_pos = 0.0
    cusum_neg = 0.0
    points:   list[ControlPoint] = []
    alarms:   list[SpcAlarm]     = []

    sh_signals:    list[str] = []
    ewma_signals:  list[str] = []
    cusum_signals: list[str] = []
    imr_signals:   list[str] = []

    for i, (pid, val) in enumerate(zip(panel_ids, values), start=1):
        # Moving range
        mr = abs(val - values[i-2]) if i >= 2 else 0.0

        # EWMA
        ewma_val = lambda_ewma * val + (1 - lambda_ewma) * ewma_val
        ewma_var = math.sqrt(
            (lambda_ewma / (2 - lambda_ewma)) *
            (1 - (1 - lambda_ewma) ** (2 * i))
        )
        ucl_ewma = mean + L_ewma * sigma * ewma_var
        lcl_ewma = max(0.0, mean - L_ewma * sigma * ewma_var)

        # CUSUM
        cusum_pos = max(0.0, cusum_pos + val - mean - k_abs)
        cusum_neg = max(0.0, cusum_neg - val + mean - k_abs)

        # Signals
        we      = _we_rules(values, i-1, mean, sigma, med)
        sh_sig  = len(we) > 0
        ew_sig  = ewma_val > ucl_ewma or ewma_val < lcl_ewma
        cu_sig  = cusum_pos > h_abs or cusum_neg > h_abs
        imr_sig = mr > ucl_imr

        if sh_sig:
            sh_signals.append(pid)
            for rule in we:
                severity = "out_of_control" if "WE1" in rule or "RATIO" in rule \
                           else "warning"
                alarms.append(SpcAlarm(
                    panel_id=pid, sequence=i, chart_type="shewhart",
                    rule_fired=rule, value=round(val, 8),
                    control_limit=round(ucl_sh, 8), severity=severity,
                ))

        if ew_sig:
            ewma_signals.append(pid)
            alarms.append(SpcAlarm(
                panel_id=pid, sequence=i, chart_type="ewma",
                rule_fired="EWMA beyond control limit",
                value=round(ewma_val, 8),
                control_limit=round(ucl_ewma, 8),
                severity="warning",
            ))

        if cu_sig:
            cusum_signals.append(pid)
            direction = "positive" if cusum_pos > h_abs else "negative"
            alarms.append(SpcAlarm(
                panel_id=pid, sequence=i, chart_type="cusum",
                rule_fired=f"CUSUM {direction} arm exceeds h={cusum_h}σ",
                value=round(max(cusum_pos, cusum_neg), 8),
                control_limit=round(h_abs, 8),
                severity="out_of_control",
            ))

        if imr_sig:
            imr_signals.append(pid)
            alarms.append(SpcAlarm(
                panel_id=pid, sequence=i, chart_type="imr",
                rule_fired=f"Moving range exceeds UCL_MR={D4}×MR-bar",
                value=round(mr, 8),
                control_limit=round(ucl_imr, 8),
                severity="warning",
            ))

        points.append(ControlPoint(
            panel_id=pid, sequence=i,
            value=round(val, 8),
            moving_range=round(mr, 8),
            ewma=round(ewma_val, 8),
            cusum_pos=round(cusum_pos, 8),
            cusum_neg=round(cusum_neg, 8),
            ucl_shewhart=round(ucl_sh, 8),
            lcl_shewhart=round(lcl_sh, 8),
            ucl_ewma=round(ucl_ewma, 8),
            lcl_ewma=round(lcl_ewma, 8),
            ucl_cusum=round(h_abs, 8),
            ucl_imr=round(ucl_imr, 8),
            shewhart_signal=sh_sig,
            ewma_signal=ew_sig,
            cusum_signal=cu_sig,
            imr_signal=imr_sig,
            we_rules=we,
        ))

    # Overall process state
    out_of_control = [
        p for p in points
        if p.value > p.ucl_shewhart or p.cusum_signal
    ]
    any_signal = sh_signals or ewma_signals or cusum_signals or imr_signals
    if out_of_control:
        process_state = "out_of_control"
    elif any_signal:
        process_state = "warning"
    else:
        process_state = "in_control"

    logger.info(
        "SPC [lot=%s sub=%s]: %d pts | μ=%.4f σ=%.4f | "
        "SH=%d EWMA=%d CUSUM=%d IMR=%d | state=%s | Cpk=%s",
        lot_id, substrate_type, len(points), mean, sigma,
        len(sh_signals), len(ewma_signals),
        len(cusum_signals), len(imr_signals),
        process_state,
        f"{cap.cpk:.3f}" if cap.cpk is not None else "N/A",
    )

    result = SPCResult(
        lot_id=lot_id, substrate_type=substrate_type,
        calculated_at=datetime.now(timezone.utc).isoformat(),
        n_points=len(points),
        centerline=round(mean, 8),
        sigma=round(sigma, 8),
        lambda_ewma=lambda_ewma, L_ewma=L_ewma,
        cusum_k=cusum_k, cusum_h=cusum_h,
        points=points, alarms=alarms,
        shewhart_signals=sh_signals,
        ewma_signals=ewma_signals,
        cusum_signals=cusum_signals,
        imr_signals=imr_signals,
        process_state=process_state,
        capability=cap,
    )

    if persist:
        _save_spc_result(conn, result)

    return result


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _save_spc_result(conn: Connection, result: SPCResult) -> None:
    ph  = get_placeholder(conn)
    now = datetime.now(timezone.utc).isoformat()

    with conn:
        cur = conn.execute(
            f"INSERT INTO spc_results "
            f"(lot_id, substrate_type, calculated_at, n_points, centerline, "
            f"sigma, lambda_ewma, L_ewma, process_state, cp, cpk, usl, lsl) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
            (
                result.lot_id, result.substrate_type, now,
                result.n_points, result.centerline, result.sigma,
                result.lambda_ewma, result.L_ewma, result.process_state,
                result.capability.cp, result.capability.cpk,
                result.capability.usl, result.capability.lsl,
            )
        )
        # Get inserted row id
        if is_postgres(conn):
            row_id = conn.execute("SELECT lastval()").fetchone()[0]
        else:
            row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        result.db_id = row_id

        # Save alarms
        if result.alarms:
            conn.executemany(
                f"INSERT INTO spc_alarms "
                f"(spc_result_id, panel_id, sequence, chart_type, "
                f"rule_fired, value, control_limit, severity, created_at) "
                f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
                [
                    (row_id, a.panel_id, a.sequence, a.chart_type,
                     a.rule_fired, a.value, a.control_limit, a.severity, now)
                    for a in result.alarms
                ]
            )


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------

def print_spc_report(result: SPCResult) -> None:
    scope = result.lot_id or result.substrate_type or "all panels"
    print(f"\n{'='*76}")
    print(f"  SPC CONTROL CHARTS — {scope}")
    print(
        f"  μ={result.centerline:.4f}  σ={result.sigma:.4f}  "
        f"n={result.n_points}  state={result.process_state.upper()}"
    )
    if result.capability.cpk is not None:
        print(
            f"  Cp={result.capability.cp:.3f}  "
            f"Cpk={result.capability.cpk:.3f}  "
            f"USL={result.capability.usl:.4f}  "
            f"LCL={result.capability.lsl:.4f}"
        )
        print(f"  {result.capability.interpretation}")
    print(f"{'='*76}")
    print(
        f"  {'#':>3} {'Panel':<18} {'Value':>10} {'EWMA':>10} "
        f"{'CUSUM+':>8} {'MR':>8}  Flags"
    )
    print(
        f"  {'-'*3} {'-'*18} {'-'*10} {'-'*10} "
        f"{'-'*8} {'-'*8}  {'-'*24}"
    )
    for p in result.points:
        flags = []
        if p.shewhart_signal:
            flags.append("SH")
        if p.ewma_signal:
            flags.append("EWMA")
        if p.cusum_signal:
            flags.append("CUSUM⚠")
        if p.imr_signal:
            flags.append("IMR")
        flag_str = " ".join(flags) if flags else "—"
        print(
            f"  {p.sequence:>3} {p.panel_id:<18} "
            f"{p.value:>10.4f} {p.ewma:>10.4f} "
            f"{p.cusum_pos:>8.4f} {p.moving_range:>8.4f}  {flag_str}"
        )
    print(f"{'='*76}")
    total_alarms = len(result.alarms)
    if total_alarms:
        print(f"\n  ALARMS ({total_alarms} total):")
        for a in result.alarms[:10]:
            print(
                f"    [{a.severity.upper():<15}] "
                f"{a.chart_type:<10} {a.panel_id:<18} "
                f"{a.rule_fired}"
            )
        if total_alarms > 10:
            print(f"    ... and {total_alarms-10} more alarms")
    else:
        print("  ✓ No alarms fired. Process in statistical control.")
    print()
