"""
tests/test_ingest.py
--------------------
Unit tests for ingestion/ingest.py — upsert functions and idempotency.
"""

import csv
import pytest
from pathlib import Path
from datetime import datetime, timezone
from openyield.ingestion.ingest import (
    upsert_panel, upsert_component, upsert_defect,
    is_file_processed, mark_file_processed, ingest_csv,
    upsert_process_run, upsert_event, upsert_doe_leg,
)


# ---------------------------------------------------------------------------
# upsert_panel
# ---------------------------------------------------------------------------

def test_upsert_panel_inserts(mem_conn):
    upsert_panel(mem_conn, "P001", "TFT-LCD-G8", "glass_panel", 6, 6)
    with mem_conn:
        row = mem_conn.execute("SELECT * FROM panels WHERE panel_id='P001'").fetchone()
    assert row is not None
    assert row["substrate_type"] == "glass_panel"

def test_upsert_panel_idempotent(mem_conn):
    with mem_conn:
        upsert_panel(mem_conn, "P001", "TFT-LCD-G8", "glass_panel", 6, 6)
        upsert_panel(mem_conn, "P001", "TFT-LCD-G8", "glass_panel", 6, 6)
    count = mem_conn.execute("SELECT COUNT(*) FROM panels").fetchone()[0]
    assert count == 1


# ---------------------------------------------------------------------------
# upsert_component
# ---------------------------------------------------------------------------

def test_upsert_component_inserts(mem_conn):
    with mem_conn:
        upsert_panel(mem_conn, "P001", "TFT-LCD-G8", "glass_panel", 2, 2)
        upsert_component(mem_conn, "P001", 0, 0, "region_NW", 0.0, 0.0)
    row = mem_conn.execute("SELECT * FROM components WHERE panel_id='P001'").fetchone()
    assert row is not None
    assert row["region_id"] == "region_NW"

def test_upsert_component_idempotent(mem_conn):
    with mem_conn:
        upsert_panel(mem_conn, "P001", "TFT-LCD-G8", "glass_panel", 2, 2)
        upsert_component(mem_conn, "P001", 0, 0, "region_NW", 0.0, 0.0)
        upsert_component(mem_conn, "P001", 0, 0, "region_NW", 0.0, 0.0)
    count = mem_conn.execute("SELECT COUNT(*) FROM components").fetchone()[0]
    assert count == 1


# ---------------------------------------------------------------------------
# upsert_defect
# ---------------------------------------------------------------------------

def test_upsert_defect_inserts(mem_conn):
    with mem_conn:
        upsert_panel(mem_conn, "P001", "TFT-LCD-G8", "glass_panel", 2, 2)
        upsert_component(mem_conn, "P001", 0, 0, "region_NW", 0.0, 0.0)
        upsert_defect(mem_conn, "P001", 0, 0, "system_a", "particle",
                      10.0, 20.0, 0.5, 0.80)
    row = mem_conn.execute("SELECT * FROM defects").fetchone()
    assert row is not None
    assert row["defect_type"] == "particle"
    assert row["match_id"] is None

def test_upsert_defect_idempotent(mem_conn):
    with mem_conn:
        upsert_panel(mem_conn, "P001", "TFT-LCD-G8", "glass_panel", 2, 2)
        upsert_component(mem_conn, "P001", 0, 0, "region_NW", 0.0, 0.0)
        for _ in range(3):
            upsert_defect(mem_conn, "P001", 0, 0, "system_a", "particle",
                          10.0, 20.0, 0.5, 0.80)
    count = mem_conn.execute("SELECT COUNT(*) FROM defects").fetchone()[0]
    assert count == 1

def test_upsert_defect_different_systems_both_stored(mem_conn):
    with mem_conn:
        upsert_panel(mem_conn, "P001", "TFT-LCD-G8", "glass_panel", 2, 2)
        upsert_component(mem_conn, "P001", 0, 0, "region_NW", 0.0, 0.0)
        upsert_defect(mem_conn, "P001", 0, 0, "system_a", "particle", 10.0, 20.0, 0.5, 0.80)
        upsert_defect(mem_conn, "P001", 0, 0, "system_b", "particle", 10.0, 20.0, 0.5, 0.95)
    count = mem_conn.execute("SELECT COUNT(*) FROM defects").fetchone()[0]
    assert count == 2


# ---------------------------------------------------------------------------
# File tracking
# ---------------------------------------------------------------------------

def test_mark_and_check_processed(mem_conn):
    with mem_conn:
        mark_file_processed(mem_conn, "/data/file.csv", "processed")
    assert is_file_processed(mem_conn, "/data/file.csv")

def test_not_processed_by_default(mem_conn):
    assert not is_file_processed(mem_conn, "/data/nonexistent.csv")

def test_mark_failed_not_processed(mem_conn):
    with mem_conn:
        mark_file_processed(mem_conn, "/data/bad.csv", "failed")
    assert not is_file_processed(mem_conn, "/data/bad.csv")

def test_mark_processed_updates_status(mem_conn):
    with mem_conn:
        mark_file_processed(mem_conn, "/data/file.csv", "failed")
        mark_file_processed(mem_conn, "/data/file.csv", "processed")
    assert is_file_processed(mem_conn, "/data/file.csv")


# ---------------------------------------------------------------------------
# ingest_csv
# ---------------------------------------------------------------------------

def _write_defect_csv(path: Path, rows: list[dict]) -> Path:
    fieldnames = [
        "panel_id", "component_row", "component_col", "source_system",
        "defect_type", "x", "y", "size", "confidence_score", "match_id", "created_at"
    ]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path

def _sample_row(**overrides):
    base = {
        "panel_id": "P001", "component_row": 0, "component_col": 0,
        "source_system": "system_a", "defect_type": "particle",
        "x": 10.0, "y": 20.0, "size": 0.5, "confidence_score": 0.8,
        "match_id": "", "created_at": ""
    }
    base.update(overrides)
    return base

def test_ingest_csv_basic(mem_conn, tmp_dir):
    with mem_conn:
        upsert_panel(mem_conn, "P001", "TFT-LCD-G8", "glass_panel", 2, 2)
        upsert_component(mem_conn, "P001", 0, 0, "region_NW", 0.0, 0.0)
    path = _write_defect_csv(tmp_dir / "defects.csv", [_sample_row()])
    count = ingest_csv(mem_conn, path)
    assert count == 1

def test_ingest_csv_idempotent(mem_conn, tmp_dir):
    with mem_conn:
        upsert_panel(mem_conn, "P001", "TFT-LCD-G8", "glass_panel", 2, 2)
        upsert_component(mem_conn, "P001", 0, 0, "region_NW", 0.0, 0.0)
    path = _write_defect_csv(tmp_dir / "defects.csv", [_sample_row()])
    ingest_csv(mem_conn, path)
    second = ingest_csv(mem_conn, path, skip_if_processed=True)
    assert second == 0

def test_ingest_csv_skips_processed(mem_conn, tmp_dir):
    path = _write_defect_csv(tmp_dir / "defects.csv", [_sample_row()])
    with mem_conn:
        mark_file_processed(mem_conn, path, "processed")
    result = ingest_csv(mem_conn, path, skip_if_processed=True)
    assert result == 0


# ---------------------------------------------------------------------------
# upsert_process_run
# ---------------------------------------------------------------------------

_TS = datetime(2025, 6, 1, 8, 0, 0, tzinfo=timezone.utc)


def test_upsert_process_run_inserts(mem_conn):
    with mem_conn:
        upsert_panel(mem_conn, "P001", "TFT-LCD-G8", "glass_panel", 6, 6)
        upsert_process_run(mem_conn, "P001", "recipe_A", recorded_at=_TS)
    row = mem_conn.execute("SELECT * FROM process_runs WHERE panel_id='P001'").fetchone()
    assert row is not None
    assert row["recipe_id"] == "recipe_A"


def test_upsert_process_run_idempotent(mem_conn):
    with mem_conn:
        upsert_panel(mem_conn, "P001", "TFT-LCD-G8", "glass_panel", 6, 6)
        upsert_process_run(mem_conn, "P001", "recipe_A", recorded_at=_TS)
        upsert_process_run(mem_conn, "P001", "recipe_A", recorded_at=_TS)
    count = mem_conn.execute("SELECT COUNT(*) FROM process_runs").fetchone()[0]
    assert count == 1


def test_upsert_process_run_glass_params(mem_conn):
    with mem_conn:
        upsert_panel(mem_conn, "P001", "TFT-LCD-G8", "glass_panel", 6, 6)
        upsert_process_run(
            mem_conn, "P001", "recipe_TGV_v3",
            lamination_temp_c=185.0,
            lamination_dwell_sec=300,
            lamination_ramp_c_per_min=5.0,
            tgv_laser_power_w=12.5,
            tgv_pulse_freq_hz=50000,
            tgv_focus_depth_um=150.0,
            operator_shift="day",
            recorded_at=_TS,
        )
    row = mem_conn.execute("SELECT * FROM process_runs").fetchone()
    assert row["lamination_temp_c"] == 185.0
    assert row["tgv_laser_power_w"] == 12.5
    assert row["operator_shift"] == "day"


def test_upsert_process_run_nullable_glass_params(mem_conn):
    """Wafer-only runs can omit all glass-specific parameters."""
    with mem_conn:
        upsert_panel(mem_conn, "W001", "logic_28nm", "wafer", 10, 10)
        upsert_process_run(mem_conn, "W001", "recipe_std", recorded_at=_TS)
    row = mem_conn.execute("SELECT * FROM process_runs").fetchone()
    assert row["tgv_laser_power_w"] is None
    assert row["lamination_temp_c"] is None


def test_upsert_process_run_different_timestamps_both_stored(mem_conn):
    ts2 = datetime(2025, 6, 1, 16, 0, 0, tzinfo=timezone.utc)
    with mem_conn:
        upsert_panel(mem_conn, "P001", "TFT-LCD-G8", "glass_panel", 6, 6)
        upsert_process_run(mem_conn, "P001", "recipe_A", recorded_at=_TS)
        upsert_process_run(mem_conn, "P001", "recipe_A", recorded_at=ts2)
    count = mem_conn.execute("SELECT COUNT(*) FROM process_runs").fetchone()[0]
    assert count == 2


# ---------------------------------------------------------------------------
# upsert_event
# ---------------------------------------------------------------------------

def test_upsert_event_inserts(mem_conn):
    with mem_conn:
        upsert_event(mem_conn, "maintenance", _TS, tool_id="CVD-01",
                     description="Quarterly PM on CVD chamber")
    row = mem_conn.execute("SELECT * FROM events").fetchone()
    assert row is not None
    assert row["event_type"] == "maintenance"
    assert row["tool_id"] == "CVD-01"


def test_upsert_event_all_types(mem_conn):
    types = ["maintenance", "recipe_change", "shift_change",
             "tool_pm", "material_lot_change", "calibration"]
    with mem_conn:
        for i, et in enumerate(types):
            ts = datetime(2025, 6, 1, i, 0, 0, tzinfo=timezone.utc)
            upsert_event(mem_conn, et, ts)
    count = mem_conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert count == len(types)


def test_upsert_event_invalid_type_rejected(mem_conn):
    import sqlite3
    with mem_conn:
        try:
            upsert_event(mem_conn, "earthquake", _TS)
            mem_conn.commit()
            raised = False
        except (sqlite3.IntegrityError, Exception):
            raised = True
    assert raised, "DB should reject unknown event_type"


def test_upsert_event_optional_fields_nullable(mem_conn):
    with mem_conn:
        upsert_event(mem_conn, "shift_change", _TS)
    row = mem_conn.execute("SELECT * FROM events").fetchone()
    assert row["tool_id"] is None
    assert row["description"] is None


# ---------------------------------------------------------------------------
# upsert_doe_leg
# ---------------------------------------------------------------------------

def test_upsert_doe_leg_inserts(mem_conn):
    with mem_conn:
        upsert_doe_leg(mem_conn, "leg_A", "DOE_TGV_2025",
                       description="Baseline laser power",
                       recipe_id="recipe_TGV_v1", n_panels=5)
    row = mem_conn.execute("SELECT * FROM doe_legs WHERE leg_id='leg_A'").fetchone()
    assert row is not None
    assert row["doe_id"] == "DOE_TGV_2025"
    assert row["n_panels"] == 5


def test_upsert_doe_leg_idempotent(mem_conn):
    with mem_conn:
        upsert_doe_leg(mem_conn, "leg_A", "DOE_TGV_2025", n_panels=5)
        upsert_doe_leg(mem_conn, "leg_A", "DOE_TGV_2025", n_panels=5)
    count = mem_conn.execute("SELECT COUNT(*) FROM doe_legs").fetchone()[0]
    assert count == 1


def test_upsert_doe_leg_updates_n_panels(mem_conn):
    with mem_conn:
        upsert_doe_leg(mem_conn, "leg_A", "DOE_TGV_2025", n_panels=5)
        upsert_doe_leg(mem_conn, "leg_A", "DOE_TGV_2025", n_panels=8)
    row = mem_conn.execute("SELECT * FROM doe_legs WHERE leg_id='leg_A'").fetchone()
    assert row["n_panels"] == 8


def test_upsert_doe_leg_updates_recipe_id(mem_conn):
    with mem_conn:
        upsert_doe_leg(mem_conn, "leg_A", "DOE_TGV_2025",
                       recipe_id="recipe_v1", n_panels=5)
        upsert_doe_leg(mem_conn, "leg_A", "DOE_TGV_2025",
                       recipe_id="recipe_v2", n_panels=5)
    row = mem_conn.execute("SELECT * FROM doe_legs WHERE leg_id='leg_A'").fetchone()
    assert row["recipe_id"] == "recipe_v2"


def test_upsert_doe_multiple_legs_same_doe(mem_conn):
    with mem_conn:
        upsert_doe_leg(mem_conn, "leg_A", "DOE_CTE_2025", n_panels=6)
        upsert_doe_leg(mem_conn, "leg_B", "DOE_CTE_2025", n_panels=6)
        upsert_doe_leg(mem_conn, "leg_C", "DOE_CTE_2025", n_panels=6)
    count = mem_conn.execute(
        "SELECT COUNT(*) FROM doe_legs WHERE doe_id='DOE_CTE_2025'"
    ).fetchone()[0]
    assert count == 3
