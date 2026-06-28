"""
tests/test_checks.py
---------------------
Unit tests for validation/checks.py — each check in isolation.
"""

import pytest
from openyield.validation.checks import (
    check_row_counts,
    check_duplicate_defects,
    check_orphan_defects,
    check_component_coverage,
    check_confidence_range,
    check_system_balance,
    check_match_symmetry,
    run_all_checks,
    ValidationResult,
)
from openyield.ingestion.ingest import (
    upsert_panel, upsert_component, upsert_defect
)


# ---------------------------------------------------------------------------
# check_row_counts
# ---------------------------------------------------------------------------

def test_row_counts_returns_four_results(seeded_db):
    results = check_row_counts(seeded_db)
    assert len(results) == 4
    tables = {r.check_name.split(":")[1] for r in results}
    assert tables == {"panels", "components", "defects", "files"}


def test_row_counts_all_pass(seeded_db):
    results = check_row_counts(seeded_db)
    assert all(r.passed for r in results)


def test_row_counts_correct_values(seeded_db):
    results = {r.check_name: r for r in check_row_counts(seeded_db)}
    assert results["row_count:panels"].metric == 1
    assert results["row_count:components"].metric == 4
    assert results["row_count:defects"].metric == 4


def test_row_counts_empty_db(mem_conn):
    results = check_row_counts(mem_conn)
    assert all(r.metric == 0 for r in results)
    assert all(r.passed for r in results)


# ---------------------------------------------------------------------------
# check_duplicate_defects
# ---------------------------------------------------------------------------

def test_duplicate_defects_passes_clean_db(seeded_db):
    result = check_duplicate_defects(seeded_db)
    assert result.passed
    assert result.metric == 0


def test_duplicate_defects_detects_near_duplicates(mem_conn):
    with mem_conn:
        upsert_panel(mem_conn, "P1", "TFT", "glass_panel", 2, 2)
        upsert_component(mem_conn, "P1", 0, 0, "region_NW", 0.0, 0.0)
        # Two defects that round to same (x,y) at 1 decimal
        upsert_defect(mem_conn, "P1", 0, 0, "system_a", "particle", 10.01, 20.01, 0.5, 0.8)
        upsert_defect(mem_conn, "P1", 0, 0, "system_a", "particle", 10.04, 20.04, 0.6, 0.8)
    result = check_duplicate_defects(mem_conn)
    assert not result.passed
    assert result.metric >= 1


# ---------------------------------------------------------------------------
# check_orphan_defects
# ---------------------------------------------------------------------------

def test_orphan_defects_passes_clean_db(seeded_db):
    result = check_orphan_defects(seeded_db)
    assert result.passed
    assert result.metric == 0


def test_orphan_defects_detects_orphan(mem_conn):
    with mem_conn:
        upsert_panel(mem_conn, "P1", "TFT", "glass_panel", 2, 2)
        # Insert defect for component (5,5) which does not exist
        mem_conn.execute(
            "INSERT INTO defects (panel_id, component_row, component_col, "
            "source_system, defect_type, x, y, size, confidence_score) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("P1", 5, 5, "system_a", "particle", 1.0, 1.0, 0.5, 0.8)
        )
    result = check_orphan_defects(mem_conn)
    assert not result.passed
    assert result.metric >= 1


# ---------------------------------------------------------------------------
# check_component_coverage
# ---------------------------------------------------------------------------

def test_component_coverage_passes_clean_db(seeded_db):
    result = check_component_coverage(seeded_db)
    assert result.passed
    assert result.metric == 0


def test_component_coverage_detects_missing_components(mem_conn):
    with mem_conn:
        # Panel expects 2x2=4 components but we only add 2
        upsert_panel(mem_conn, "P1", "TFT", "glass_panel", 2, 2)
        upsert_component(mem_conn, "P1", 0, 0, "region_NW", 0.0, 0.0)
        upsert_component(mem_conn, "P1", 0, 1, "region_NE", 1.0, 0.0)
    result = check_component_coverage(mem_conn)
    assert not result.passed
    assert result.metric >= 1


# ---------------------------------------------------------------------------
# check_confidence_range
# ---------------------------------------------------------------------------

def test_confidence_range_passes_clean_db(seeded_db):
    result = check_confidence_range(seeded_db)
    assert result.passed
    assert result.metric == 0


def test_confidence_range_detects_out_of_range(mem_conn):
    # Insert a valid defect then corrupt via a no-CHECK connection to same DB
    import sqlite3 as _sqlite3
    with mem_conn:
        upsert_panel(mem_conn, "P1", "TFT", "glass_panel", 2, 2)
        upsert_component(mem_conn, "P1", 0, 0, "region_NW", 0.0, 0.0)
        upsert_defect(mem_conn, "P1", 0, 0, "system_a", "particle", 1.0, 1.0, 0.5, 0.8)
    # Use a raw connection without CHECK enforcement to corrupt the value
    raw = _sqlite3.connect(":memory:")  # can not share mem_conn so we fake it:
    # Directly test the SQL logic by injecting a known-bad row via raw SQL
    # with PRAGMA ignore_check_constraints (SQLite >= 3.42) not always available.
    # Instead, disable the check at the connection level:
    mem_conn.execute("PRAGMA ignore_check_constraints=ON")
    mem_conn.execute("UPDATE defects SET confidence_score = 1.5")
    mem_conn.commit()
    result = check_confidence_range(mem_conn)
    # Restore
    mem_conn.execute("PRAGMA ignore_check_constraints=OFF")
    assert not result.passed
    assert result.metric >= 1


# ---------------------------------------------------------------------------
# check_system_balance
# ---------------------------------------------------------------------------

def test_system_balance_passes_when_a_gte_b(seeded_db):
    # seeded_db has 2 system_a and 2 system_b — equal is passing
    result = check_system_balance(seeded_db)
    assert result.passed


def test_system_balance_fails_when_b_exceeds_a(mem_conn):
    with mem_conn:
        upsert_panel(mem_conn, "P1", "TFT", "glass_panel", 2, 2)
        upsert_component(mem_conn, "P1", 0, 0, "region_NW", 0.0, 0.0)
        upsert_component(mem_conn, "P1", 0, 1, "region_NE", 1.0, 0.0)
        # 1 system_a, 3 system_b
        upsert_defect(mem_conn, "P1", 0, 0, "system_a", "particle", 1.0, 1.0, 0.5, 0.8)
        upsert_defect(mem_conn, "P1", 0, 0, "system_b", "particle", 1.1, 1.1, 0.5, 0.9)
        upsert_defect(mem_conn, "P1", 0, 1, "system_b", "scratch",  2.0, 2.0, 0.6, 0.9)
        upsert_defect(mem_conn, "P1", 0, 1, "system_b", "pinhole",  3.0, 3.0, 0.3, 0.9)
    result = check_system_balance(mem_conn)
    assert not result.passed


# ---------------------------------------------------------------------------
# check_match_symmetry
# ---------------------------------------------------------------------------

def test_match_symmetry_passes_clean_db(seeded_db):
    result = check_match_symmetry(seeded_db)
    assert result.passed
    assert result.metric == 0


def test_match_symmetry_fails_one_sided_match(mem_conn):
    with mem_conn:
        upsert_panel(mem_conn, "P1", "TFT", "glass_panel", 2, 2)
        upsert_component(mem_conn, "P1", 0, 0, "region_NW", 0.0, 0.0)
        # match_id only in system_a, not in system_b
        upsert_defect(mem_conn, "P1", 0, 0, "system_a", "particle",
                      1.0, 1.0, 0.5, 0.8, match_id="orphan_match")
    result = check_match_symmetry(mem_conn)
    assert not result.passed
    assert result.metric >= 1


def test_match_symmetry_passes_symmetric_match(mem_conn):
    with mem_conn:
        upsert_panel(mem_conn, "P1", "TFT", "glass_panel", 2, 2)
        upsert_component(mem_conn, "P1", 0, 0, "region_NW", 0.0, 0.0)
        upsert_defect(mem_conn, "P1", 0, 0, "system_a", "particle",
                      1.0, 1.0, 0.5, 0.8, match_id="good_match")
        upsert_defect(mem_conn, "P1", 0, 0, "system_b", "particle",
                      1.1, 1.1, 0.5, 0.9, match_id="good_match")
    result = check_match_symmetry(mem_conn)
    assert result.passed


# ---------------------------------------------------------------------------
# run_all_checks
# ---------------------------------------------------------------------------

def test_run_all_checks_returns_list(seeded_db):
    results = run_all_checks(seeded_db)
    assert isinstance(results, list)
    assert len(results) > 0
    assert all(isinstance(r, ValidationResult) for r in results)


def test_run_all_checks_count(seeded_db):
    results = run_all_checks(seeded_db)
    # 4 row_count + 6 other checks = 10 total
    assert len(results) == 10


def test_run_all_checks_clean_db_all_pass(seeded_db):
    results = run_all_checks(seeded_db)
    failed = [r for r in results if not r.passed]
    assert failed == [], f"Unexpected failures: {[r.check_name for r in failed]}"
