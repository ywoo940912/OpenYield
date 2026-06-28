"""
db/migrate_sqlite_to_postgres.py
---------------------------------
Author: Yeonkuk Woo

One-time migration script: copies all data from the SQLite database
into a running PostgreSQL instance.

Prerequisites
-------------
  pip install psycopg2-binary
  # PostgreSQL database already created:
  createdb inspection

Usage
-----
  export DB_HOST=localhost DB_PORT=5432 DB_NAME=inspection \
         DB_USER=myuser DB_PASSWORD=mypassword
  python -m db.migrate_sqlite_to_postgres --sqlite ./inspection.db

What this script does
---------------------
  1. Opens source SQLite database (read-only)
  2. Opens target PostgreSQL database
  3. Initializes PostgreSQL schema (CREATE TABLE IF NOT EXISTS)
  4. Copies: panels → components → defects → files  (in FK order)
  5. Each table uses INSERT ... ON CONFLICT DO NOTHING (idempotent)
  6. Reports row counts before and after

Safe to re-run — duplicate rows are silently ignored.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)


# ---------------------------------------------------------------------------
# Upsert helpers — PostgreSQL syntax
# ---------------------------------------------------------------------------

def _copy_panels(src_conn: sqlite3.Connection, dst_cur) -> int:
    rows = src_conn.execute(
        "SELECT panel_id, product_type, substrate_type, rows, cols, created_at FROM panels"
    ).fetchall()
    dst_cur.executemany(
        """
        INSERT INTO panels (panel_id, product_type, substrate_type, rows, cols, created_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (panel_id) DO NOTHING
        """,
        [tuple(r) for r in rows],
    )
    return len(rows)


def _copy_components(src_conn: sqlite3.Connection, dst_cur) -> int:
    rows = src_conn.execute(
        """SELECT panel_id, component_row, component_col,
                  region_id, center_x, center_y, active
           FROM components"""
    ).fetchall()
    dst_cur.executemany(
        """
        INSERT INTO components
            (panel_id, component_row, component_col,
             region_id, center_x, center_y, active)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (panel_id, component_row, component_col) DO NOTHING
        """,
        [tuple(r) for r in rows],
    )
    return len(rows)


def _copy_defects(src_conn: sqlite3.Connection, dst_cur) -> int:
    rows = src_conn.execute(
        """SELECT panel_id, component_row, component_col,
                  source_system, defect_type, x, y, size,
                  confidence_score, match_id, created_at
           FROM defects"""
    ).fetchall()
    dst_cur.executemany(
        """
        INSERT INTO defects
            (panel_id, component_row, component_col, source_system,
             defect_type, x, y, size, confidence_score, match_id, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (panel_id, component_row, component_col,
                     source_system, defect_type, x, y) DO NOTHING
        """,
        [tuple(r) for r in rows],
    )
    return len(rows)


def _copy_files(src_conn: sqlite3.Connection, dst_cur) -> int:
    rows = src_conn.execute(
        "SELECT file_path, status, processed_at FROM files"
    ).fetchall()
    dst_cur.executemany(
        """
        INSERT INTO files (file_path, status, processed_at)
        VALUES (%s, %s, %s)
        ON CONFLICT (file_path) DO NOTHING
        """,
        [tuple(r) for r in rows],
    )
    return len(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def migrate(sqlite_path: str | Path) -> None:
    import psycopg2
    import psycopg2.extras
    import os

    sqlite_path = Path(sqlite_path)
    if not sqlite_path.exists():
        raise FileNotFoundError(f"SQLite database not found: {sqlite_path}")

    # Source
    src = sqlite3.connect(str(sqlite_path))
    src.row_factory = sqlite3.Row
    logger.info("Opened SQLite source: %s", sqlite_path)

    # Target
    dst = psycopg2.connect(
        host     = os.getenv("DB_HOST",     "localhost"),
        port     = int(os.getenv("DB_PORT", "5432")),
        dbname   = os.getenv("DB_NAME",     "inspection"),
        user     = os.getenv("DB_USER",     ""),
        password = os.getenv("DB_PASSWORD", ""),
        cursor_factory=psycopg2.extras.RealDictCursor,
    )
    dst.autocommit = False
    logger.info(
        "Opened PostgreSQL target: %s@%s/%s",
        os.getenv("DB_USER"), os.getenv("DB_HOST"), os.getenv("DB_NAME"),
    )

    # Initialize PostgreSQL schema
    from openyield.db.schema_pg import initialize_schema
    initialize_schema(dst)

    cur = dst.cursor()
    try:
        n_panels     = _copy_panels(src, cur)
        n_components = _copy_components(src, cur)
        n_defects    = _copy_defects(src, cur)
        n_files      = _copy_files(src, cur)
        dst.commit()
    except Exception:
        dst.rollback()
        cur.close()
        raise

    cur.close()
    src.close()
    dst.close()

    logger.info("Migration complete:")
    logger.info("  panels:     %d rows", n_panels)
    logger.info("  components: %d rows", n_components)
    logger.info("  defects:    %d rows", n_defects)
    logger.info("  files:      %d rows", n_files)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Migrate SQLite → PostgreSQL")
    p.add_argument("--sqlite", default="./inspection.db",
                   help="Path to source SQLite database")
    args = p.parse_args()
    migrate(args.sqlite)
