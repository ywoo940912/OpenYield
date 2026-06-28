"""
validation/checks.py
---------------------
Author: Yeonkuk Woo

Data quality checks for the inspection platform database.

Each check returns a ValidationResult (passed, metric, detail).
The run_all_checks() function aggregates results into a summary report.
"""

from __future__ import annotations

import sqlite3
from typing import Any

Connection = Any
import logging
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    check_name: str
    passed: bool
    metric: int | float | None
    detail: str


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def check_row_counts(conn: Connection) -> list[ValidationResult]:
    """Report total row count for each core table."""
    results = []
    for table in ("panels", "components", "defects", "files"):
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        results.append(ValidationResult(
            check_name=f"row_count:{table}",
            passed=count >= 0,
            metric=count,
            detail=f"{table} contains {count} rows",
        ))
    return results


def check_duplicate_defects(conn: Connection) -> ValidationResult:
    """
    Detect near-duplicate defects: same panel/component/system/type with
    identical (x, y) coordinates. The UNIQUE constraint prevents exact
    duplicates; this check surfaces floating-point near-duplicates.
    """
    sql = """
        SELECT
            panel_id, component_row, component_col,
            source_system, defect_type,
            ROUND(x, 1) AS rx, ROUND(y, 1) AS ry,
            COUNT(*) AS n
        FROM defects
        GROUP BY
            panel_id, component_row, component_col,
            source_system, defect_type, rx, ry
        HAVING n > 1
    """
    rows = conn.execute(sql).fetchall()
    n_groups = len(rows)
    return ValidationResult(
        check_name="duplicate_defects",
        passed=n_groups == 0,
        metric=n_groups,
        detail=(
            f"No near-duplicate defect groups found."
            if n_groups == 0
            else f"{n_groups} near-duplicate defect group(s) detected."
        ),
    )


def check_orphan_defects(conn: Connection) -> ValidationResult:
    """
    Detect defects whose (panel_id, component_row, component_col) does not
    correspond to any component in the components table.
    """
    sql = """
        SELECT COUNT(*) FROM defects d
        WHERE NOT EXISTS (
            SELECT 1 FROM components c
            WHERE c.panel_id      = d.panel_id
              AND c.component_row = d.component_row
              AND c.component_col = d.component_col
        )
    """
    n_orphans = conn.execute(sql).fetchone()[0]
    return ValidationResult(
        check_name="orphan_defects",
        passed=n_orphans == 0,
        metric=n_orphans,
        detail=(
            "All defects have valid component references."
            if n_orphans == 0
            else f"{n_orphans} orphan defect(s) found (no matching component)."
        ),
    )


def check_component_coverage(conn: Connection) -> ValidationResult:
    """
    Ensure every panel has the expected number of components (rows × cols).
    Returns the count of panels where component count mismatches rows*cols.
    """
    sql = """
        SELECT
            p.panel_id,
            p.rows * p.cols AS expected,
            COUNT(c.component_row) AS actual
        FROM panels p
        LEFT JOIN components c ON c.panel_id = p.panel_id
        GROUP BY p.panel_id
        HAVING expected <> actual
    """
    mismatched = conn.execute(sql).fetchall()
    n = len(mismatched)
    return ValidationResult(
        check_name="component_coverage",
        passed=n == 0,
        metric=n,
        detail=(
            "All panels have expected component counts."
            if n == 0
            else f"{n} panel(s) have mismatched component counts."
        ),
    )


def check_confidence_range(conn: Connection) -> ValidationResult:
    """Verify all confidence scores are in [0.0, 1.0]."""
    sql = """
        SELECT COUNT(*) FROM defects
        WHERE confidence_score < 0.0 OR confidence_score > 1.0
    """
    out_of_range = conn.execute(sql).fetchone()[0]
    return ValidationResult(
        check_name="confidence_range",
        passed=out_of_range == 0,
        metric=out_of_range,
        detail=(
            "All confidence scores are within [0.0, 1.0]."
            if out_of_range == 0
            else f"{out_of_range} defect(s) have invalid confidence scores."
        ),
    )


def check_system_balance(conn: Connection) -> ValidationResult:
    """
    Warn if system_b has more defects than system_a (unexpected for this model).
    """
    sql = """
        SELECT source_system, COUNT(*) AS n
        FROM defects
        GROUP BY source_system
    """
    rows = {r["source_system"]: r["n"] for r in conn.execute(sql).fetchall()}
    n_a = rows.get("system_a", 0)
    n_b = rows.get("system_b", 0)
    passed = n_a >= n_b
    return ValidationResult(
        check_name="system_balance",
        passed=passed,
        metric=n_b - n_a if not passed else n_a - n_b,
        detail=(
            f"system_a ({n_a}) >= system_b ({n_b}) as expected."
            if passed
            else f"Unexpected: system_b ({n_b}) > system_a ({n_a})."
        ),
    )


def check_match_symmetry(conn: Connection) -> ValidationResult:
    """
    Each match_id should appear in both system_a and system_b.
    Detect match_ids that only appear in one system.
    """
    sql = """
        SELECT match_id, COUNT(DISTINCT source_system) AS sys_count
        FROM defects
        WHERE match_id IS NOT NULL AND match_id != ''
        GROUP BY match_id
        HAVING sys_count < 2
    """
    asymmetric = conn.execute(sql).fetchall()
    n = len(asymmetric)
    return ValidationResult(
        check_name="match_symmetry",
        passed=n == 0,
        metric=n,
        detail=(
            "All match_ids appear in both systems."
            if n == 0
            else f"{n} match_id(s) found in only one system."
        ),
    )


# ---------------------------------------------------------------------------
# Aggregate runner
# ---------------------------------------------------------------------------

def run_all_checks(conn: Connection) -> list[ValidationResult]:
    """
    Run the full validation suite and return all results.
    """
    results: list[ValidationResult] = []
    results.extend(check_row_counts(conn))
    results.append(check_duplicate_defects(conn))
    results.append(check_orphan_defects(conn))
    results.append(check_component_coverage(conn))
    results.append(check_confidence_range(conn))
    results.append(check_system_balance(conn))
    results.append(check_match_symmetry(conn))
    return results


def print_validation_report(results: list[ValidationResult]) -> None:
    """Print a formatted validation report to stdout."""
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    print(f"\n{'='*60}")
    print(f"  VALIDATION REPORT  ({passed}/{total} checks passed)")
    print(f"{'='*60}")
    for r in results:
        status = "✓ PASS" if r.passed else "✗ FAIL"
        metric_str = f"[{r.metric}]" if r.metric is not None else ""
        print(f"  {status:<8} {r.check_name:<28} {metric_str:<10} {r.detail}")
    print(f"{'='*60}\n")
