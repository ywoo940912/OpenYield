"""
ingestion/ingest.py
-------------------
Author: Yeonkuk Woo

Idempotent ingestion for panels, components, and defects.

All writes use INSERT OR IGNORE (SQLite) or INSERT ... ON CONFLICT DO NOTHING
(PostgreSQL) — safe to re-run on the same data without creating duplicates.

All query code is backend-agnostic via get_placeholder() and is_postgres()
from db.connection.  No module other than this one needs to branch on backend.
"""

import csv
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openyield.db.connection import get_placeholder, is_postgres

logger = logging.getLogger(__name__)

Connection = Any


def upsert_lot(
    conn: Connection,
    lot_id: str,
    substrate_type: str,
    product_type: str,
    lot_size: int = 25,
    status: str = "active",
) -> None:
    """Insert a lot record if it does not already exist."""
    ph = get_placeholder(conn)
    if is_postgres(conn):
        sql = (f"INSERT INTO lots (lot_id, substrate_type, product_type, lot_size, status) "
               f"VALUES ({ph},{ph},{ph},{ph},{ph}) ON CONFLICT (lot_id) DO NOTHING")
    else:
        sql = (f"INSERT OR IGNORE INTO lots "
               f"(lot_id, substrate_type, product_type, lot_size, status) "
               f"VALUES ({ph},{ph},{ph},{ph},{ph})")
    conn.execute(sql, (lot_id, substrate_type, product_type, lot_size, status))


def upsert_panel(
    conn: Connection,
    panel_id: str,
    product_type: str,
    substrate_type: str,
    rows: int,
    cols: int,
    lot_id: str | None = None,
    created_at: datetime | None = None,
) -> None:
    if created_at is None:
        created_at = datetime.now(timezone.utc)
    ph = get_placeholder(conn)
    if is_postgres(conn):
        sql = (f"INSERT INTO panels (panel_id, product_type, substrate_type, rows, cols, lot_id, created_at) "
               f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph}) ON CONFLICT (panel_id) DO NOTHING")
    else:
        sql = (f"INSERT OR IGNORE INTO panels "
               f"(panel_id, product_type, substrate_type, rows, cols, lot_id, created_at) "
               f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph})")
    conn.execute(sql, (panel_id, product_type, substrate_type, rows, cols,
                       lot_id, created_at.isoformat()))


def upsert_component(
    conn: Connection,
    panel_id: str,
    component_row: int,
    component_col: int,
    region_id: str,
    center_x: float,
    center_y: float,
    active: bool = True,
) -> None:
    ph = get_placeholder(conn)
    cols_list = "panel_id, component_row, component_col, region_id, center_x, center_y, active"
    vals = f"{ph},{ph},{ph},{ph},{ph},{ph},{ph}"
    if is_postgres(conn):
        sql = (f"INSERT INTO components ({cols_list}) VALUES ({vals}) "
               f"ON CONFLICT (panel_id, component_row, component_col) DO NOTHING")
    else:
        sql = f"INSERT OR IGNORE INTO components ({cols_list}) VALUES ({vals})"
    conn.execute(sql, (panel_id, component_row, component_col,
                       region_id, center_x, center_y, int(active)))


def upsert_defect(
    conn: Connection,
    panel_id: str,
    component_row: int,
    component_col: int,
    source_system: str,
    defect_type: str,
    x: float,
    y: float,
    size: float,
    confidence_score: float,
    match_id: str | None = None,
    created_at: datetime | None = None,
) -> None:
    if created_at is None:
        created_at = datetime.now(timezone.utc)
    ph = get_placeholder(conn)
    cols_list = ("panel_id, component_row, component_col, source_system, "
                 "defect_type, x, y, size, confidence_score, match_id, created_at")
    vals = ",".join([ph] * 11)
    conflict_key = "panel_id, component_row, component_col, source_system, defect_type, x, y"
    if is_postgres(conn):
        sql = (f"INSERT INTO defects ({cols_list}) VALUES ({vals}) "
               f"ON CONFLICT ({conflict_key}) DO NOTHING")
    else:
        sql = f"INSERT OR IGNORE INTO defects ({cols_list}) VALUES ({vals})"
    conn.execute(sql, (panel_id, component_row, component_col, source_system,
                       defect_type, x, y, size, confidence_score, match_id,
                       created_at.isoformat()))


def is_file_processed(conn: Connection, file_path: str | Path) -> bool:
    ph = get_placeholder(conn)
    row = conn.execute(
        f"SELECT status FROM files WHERE file_path={ph}", (str(file_path),)
    ).fetchone()
    return row is not None and row["status"] == "processed"


def mark_file_processed(
    conn: Connection,
    file_path: str | Path,
    status: str = "processed",
) -> None:
    ph = get_placeholder(conn)
    now = datetime.now(timezone.utc).isoformat()
    if is_postgres(conn):
        sql = (f"INSERT INTO files (file_path, status, processed_at) VALUES ({ph},{ph},{ph}) "
               f"ON CONFLICT (file_path) DO UPDATE SET "
               f"status=EXCLUDED.status, processed_at=EXCLUDED.processed_at")
    else:
        sql = (f"INSERT INTO files (file_path, status, processed_at) VALUES ({ph},{ph},{ph}) "
               f"ON CONFLICT(file_path) DO UPDATE SET "
               f"status=excluded.status, processed_at=excluded.processed_at")
    conn.execute(sql, (str(file_path), status, now))


def upsert_process_run(
    conn: Connection,
    panel_id: str,
    recipe_id: str,
    *,
    lamination_temp_c: float | None        = None,
    lamination_dwell_sec: int | None       = None,
    lamination_ramp_c_per_min: float | None = None,
    tgv_laser_power_w: float | None        = None,
    tgv_pulse_freq_hz: int | None          = None,
    tgv_focus_depth_um: float | None       = None,
    operator_shift: str | None             = None,
    recorded_at: datetime | None           = None,
) -> None:
    """
    Record a process run for a panel. Idempotent on (panel_id, recorded_at).

    Glass-substrate parameters (lamination_*, tgv_*) are all nullable so the
    table accommodates wafer-only runs without schema changes. For glass panel
    DOE legs, populate as many parameters as the process control system exposes.
    """
    if recorded_at is None:
        recorded_at = datetime.now(timezone.utc)
    ph = get_placeholder(conn)
    cols = (
        "panel_id, recipe_id, lamination_temp_c, lamination_dwell_sec, "
        "lamination_ramp_c_per_min, tgv_laser_power_w, tgv_pulse_freq_hz, "
        "tgv_focus_depth_um, operator_shift, recorded_at"
    )
    vals = ",".join([ph] * 10)
    if is_postgres(conn):
        sql = (f"INSERT INTO process_runs ({cols}) VALUES ({vals}) "
               f"ON CONFLICT (panel_id, recorded_at) DO NOTHING")
    else:
        sql = f"INSERT OR IGNORE INTO process_runs ({cols}) VALUES ({vals})"
    conn.execute(sql, (
        panel_id, recipe_id,
        lamination_temp_c, lamination_dwell_sec, lamination_ramp_c_per_min,
        tgv_laser_power_w, tgv_pulse_freq_hz, tgv_focus_depth_um,
        operator_shift, recorded_at.isoformat(),
    ))


def upsert_event(
    conn: Connection,
    event_type: str,
    event_time: datetime,
    *,
    tool_id: str | None     = None,
    description: str | None = None,
) -> None:
    """
    Record a fab event (maintenance, recipe change, shift change, etc.).

    Events have no natural uniqueness constraint — the same maintenance action
    may appear multiple times if logged from different sources. Each call
    inserts a new row. Use event_time + tool_id for downstream correlation.
    """
    ph = get_placeholder(conn)
    cols = "event_type, tool_id, description, event_time"
    vals = ",".join([ph] * 4)
    conn.execute(
        f"INSERT INTO events ({cols}) VALUES ({vals})",
        (event_type, tool_id, description, event_time.isoformat()),
    )


def upsert_doe_leg(
    conn: Connection,
    leg_id: str,
    doe_id: str,
    *,
    description: str | None = None,
    recipe_id: str | None   = None,
    n_panels: int           = 0,
) -> None:
    """
    Insert or update a DOE leg record.

    leg_id is the unique identifier within a DOE study (doe_id groups legs).
    n_panels is updated if the leg already exists — call this after assigning
    panels to a leg to keep the count current.
    """
    if is_postgres(conn):
        ph = get_placeholder(conn)
        sql = (
            f"INSERT INTO doe_legs (leg_id, doe_id, description, recipe_id, n_panels) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph}) "
            f"ON CONFLICT (leg_id) DO UPDATE SET "
            f"n_panels=EXCLUDED.n_panels, recipe_id=EXCLUDED.recipe_id"
        )
    else:
        sql = (
            "INSERT INTO doe_legs (leg_id, doe_id, description, recipe_id, n_panels) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(leg_id) DO UPDATE SET "
            "n_panels=excluded.n_panels, recipe_id=excluded.recipe_id"
        )
    conn.execute(sql, (leg_id, doe_id, description, recipe_id, n_panels))


def ingest_csv(
    conn: Connection,
    csv_path: str | Path,
    *,
    skip_if_processed: bool = True,
) -> int:
    """
    Ingest a defect CSV into the database. Returns records ingested (0 if skipped).

    For format-specific parsing (KLARF, vendor exports), use the adapter layer
    in ingestion/adapters/ which returns NormalizedDefect objects.
    """
    csv_path = Path(csv_path)
    if skip_if_processed and is_file_processed(conn, csv_path):
        logger.info("Skipping already-processed file: %s", csv_path)
        return 0

    ingested = 0
    try:
        with conn:
            with open(csv_path, newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    upsert_defect(
                        conn,
                        panel_id=row["panel_id"],
                        component_row=int(row["component_row"]),
                        component_col=int(row["component_col"]),
                        source_system=row["source_system"],
                        defect_type=row["defect_type"],
                        x=float(row["x"]),
                        y=float(row["y"]),
                        size=float(row["size"]),
                        confidence_score=float(row["confidence_score"]),
                        match_id=row.get("match_id") or None,
                    )
                    ingested += 1
            mark_file_processed(conn, csv_path, "processed")
    except Exception as exc:
        mark_file_processed(conn, csv_path, "failed")
        logger.error("Ingestion failed for %s: %s", csv_path, exc)
        raise

    logger.info("Ingested %d records from %s", ingested, csv_path)
    return ingested
