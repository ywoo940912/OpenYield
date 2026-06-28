"""
tests/test_pipeline.py
-----------------------
Integration tests — full end-to-end pipeline across all three substrate types.
"""

import pytest
import sqlite3
from pathlib import Path

from openyield.db.schema import initialize_schema
from openyield.ingestion.ingest import upsert_panel, upsert_component, ingest_csv, upsert_defect
from openyield.ingestion.adapters.csv_adapter import CsvAdapter
from openyield.synthetic.generator import generate_panel, write_defects_csv, write_components_csv
from openyield.validation.checks import run_all_checks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_substrate_pipeline(conn, substrate_type, tmp_dir, rows, cols, seed):
    """Generate → write CSV → upsert panel/components → ingest → return defect count."""
    panel = generate_panel(rows=rows, cols=cols, substrate_type=substrate_type, seed=seed)

    defect_csv = tmp_dir / f"{panel.panel_id}_defects.csv"
    write_defects_csv(panel, defect_csv)
    write_components_csv(panel, tmp_dir / f"{panel.panel_id}_components.csv")

    with conn:
        upsert_panel(conn,
            panel_id=panel.panel_id,
            product_type=panel.product_type,
            substrate_type=panel.substrate_type,
            rows=panel.rows,
            cols=panel.cols,
        )
        for c in panel.components:
            upsert_component(conn,
                panel_id=c.panel_id,
                component_row=c.component_row,
                component_col=c.component_col,
                region_id=c.region_id,
                center_x=c.center_x,
                center_y=c.center_y,
                active=c.active,
            )

    ingested = ingest_csv(conn, defect_csv, skip_if_processed=True)
    return panel, defect_csv, ingested


# ---------------------------------------------------------------------------
# Per-substrate end-to-end
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("substrate,rows,cols", [
    ("glass_panel", 6, 6),
    ("wafer",       10, 10),
])
def test_full_pipeline_single_substrate(mem_conn, tmp_dir, substrate, rows, cols):
    panel, csv_path, ingested = run_substrate_pipeline(
        mem_conn, substrate, tmp_dir, rows, cols, seed=42
    )
    assert ingested > 0
    assert ingested == len(panel.defects)

    db_count = mem_conn.execute("SELECT COUNT(*) FROM defects").fetchone()[0]
    assert db_count == ingested


@pytest.mark.parametrize("substrate,rows,cols", [
    ("glass_panel", 6, 6),
    ("wafer",       10, 10),
])
def test_idempotency_per_substrate(mem_conn, tmp_dir, substrate, rows, cols):
    _, csv_path, first = run_substrate_pipeline(
        mem_conn, substrate, tmp_dir, rows, cols, seed=7
    )
    second = ingest_csv(mem_conn, csv_path, skip_if_processed=True)
    assert second == 0


@pytest.mark.parametrize("substrate,rows,cols", [
    ("glass_panel", 6, 6),
    ("wafer",       10, 10),
])
def test_validation_passes_after_pipeline(mem_conn, tmp_dir, substrate, rows, cols):
    run_substrate_pipeline(mem_conn, substrate, tmp_dir, rows, cols, seed=99)
    results = run_all_checks(mem_conn)
    failed = [r for r in results if not r.passed]
    assert failed == [], f"Validation failures: {[(r.check_name, r.detail) for r in failed]}"


# ---------------------------------------------------------------------------
# All three substrates in one run (mirrors run_pipeline.py)
# ---------------------------------------------------------------------------

def test_all_substrates_combined(mem_conn, tmp_dir):
    substrates = [
        ("glass_panel", 6,  6),
        ("wafer",       10, 10),
    ]
    total_ingested = 0
    for idx, (substrate, rows, cols) in enumerate(substrates):
        _, _, ingested = run_substrate_pipeline(
            mem_conn, substrate, tmp_dir, rows, cols, seed=42 + idx * 100
        )
        total_ingested += ingested

    assert total_ingested > 0

    panel_count = mem_conn.execute("SELECT COUNT(*) FROM panels").fetchone()[0]
    assert panel_count == 2

    results = run_all_checks(mem_conn)
    failed = [r for r in results if not r.passed]
    assert failed == [], f"Validation failures: {[(r.check_name, r.detail) for r in failed]}"


def test_all_substrates_idempotency(mem_conn, tmp_dir):
    """Re-running the full pipeline must produce zero new records."""
    substrates = [
        ("glass_panel", 6,  6),
        ("wafer",       10, 10),
    ]
    csv_files = []
    for idx, (substrate, rows, cols) in enumerate(substrates):
        _, csv_path, _ = run_substrate_pipeline(
            mem_conn, substrate, tmp_dir, rows, cols, seed=10 + idx * 100
        )
        csv_files.append(csv_path)

    reruns = sum(ingest_csv(mem_conn, f, skip_if_processed=True) for f in csv_files)
    assert reruns == 0


# ---------------------------------------------------------------------------
# CsvAdapter round-trip
# ---------------------------------------------------------------------------

def test_csv_adapter_round_trip(mem_conn, tmp_dir):
    """generate_panel → write CSV → CsvAdapter.parse() → upsert → validate."""
    panel = generate_panel(rows=4, cols=4, substrate_type="glass_panel", seed=55)

    defect_csv = tmp_dir / f"{panel.panel_id}_defects.csv"
    write_defects_csv(panel, defect_csv)

    with mem_conn:
        upsert_panel(mem_conn,
            panel_id=panel.panel_id,
            product_type=panel.product_type,
            substrate_type=panel.substrate_type,
            rows=panel.rows,
            cols=panel.cols,
        )
        for c in panel.components:
            upsert_component(mem_conn,
                panel_id=c.panel_id,
                component_row=c.component_row,
                component_col=c.component_col,
                region_id=c.region_id,
                center_x=c.center_x,
                center_y=c.center_y,
                active=c.active,
            )

    # Parse via adapter
    adapter = CsvAdapter()
    records = adapter.parse(defect_csv)
    assert len(records) == len(panel.defects)
    assert all(r.match_id is None for r in records)

    # Upsert via adapter records
    with mem_conn:
        for r in records:
            upsert_defect(mem_conn,
                panel_id=r.panel_id,
                component_row=r.component_row,
                component_col=r.component_col,
                source_system=r.source_system,
                defect_type=r.defect_type,
                x=r.x,
                y=r.y,
                size=r.size,
                confidence_score=r.confidence_score,
                match_id=r.match_id,
            )

    db_count = mem_conn.execute("SELECT COUNT(*) FROM defects").fetchone()[0]
    assert db_count == len(panel.defects)

    results = run_all_checks(mem_conn)
    failed = [r for r in results if not r.passed]
    assert failed == [], f"Validation failures after adapter round-trip: {failed}"


# ---------------------------------------------------------------------------
# Multiple panels per substrate
# ---------------------------------------------------------------------------

def test_multiple_panels_same_substrate(mem_conn, tmp_dir):
    for i in range(3):
        run_substrate_pipeline(mem_conn, "wafer", tmp_dir, 6, 6, seed=i * 10)

    panel_count = mem_conn.execute("SELECT COUNT(*) FROM panels").fetchone()[0]
    assert panel_count == 3

    defect_count = mem_conn.execute("SELECT COUNT(*) FROM defects").fetchone()[0]
    assert defect_count > 0

    results = run_all_checks(mem_conn)
    failed = [r for r in results if not r.passed]
    assert failed == [], f"Failures with multiple panels: {[(r.check_name, r.detail) for r in failed]}"


def test_wafer_edge_exclusion_in_pipeline(mem_conn, tmp_dir):
    """Wafer panels should have inactive components that produce no defects."""
    panel = generate_panel(rows=10, cols=10, substrate_type="wafer", seed=0)

    inactive = [c for c in panel.components if not c.active]
    assert len(inactive) > 0

    # Defects must only come from active components
    active_keys = {(c.component_row, c.component_col) for c in panel.components if c.active}
    for d in panel.defects:
        assert (d.component_row, d.component_col) in active_keys
