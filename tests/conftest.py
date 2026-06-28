"""
tests/conftest.py
-----------------
Shared pytest fixtures for the OpenYield test suite.
"""

import pytest
import sqlite3
import tempfile
from pathlib import Path

from openyield.db.schema import initialize_schema
from openyield.db.connection import get_connection
from openyield.ingestion.ingest import upsert_panel, upsert_component, upsert_defect
from openyield.synthetic.substrate_profiles import SubstrateType


# ---------------------------------------------------------------------------
# In-memory SQLite connection (fast, isolated per test)
# ---------------------------------------------------------------------------

@pytest.fixture
def mem_conn():
    """Fresh in-memory SQLite connection with schema initialized."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    initialize_schema(conn)
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Minimal panel + components + defects (used by multiple test modules)
# ---------------------------------------------------------------------------

@pytest.fixture
def seeded_db(mem_conn):
    """
    DB with one glass_panel panel, 4 components (2x2), and 4 defects
    (2 system_a + 2 system_b with match_ids).
    """
    conn = mem_conn
    with conn:
        upsert_panel(conn, "GP_TEST001", "TFT-LCD-G8", "glass_panel", 2, 2)
        for r in range(2):
            for c in range(2):
                upsert_component(conn, "GP_TEST001", r, c,
                                 f"region_{'NW' if r==0 and c==0 else 'NE' if r==0 else 'SW' if c==0 else 'SE'}",
                                 float(c * 370), float(r * 370))

        # Matched pair
        upsert_defect(conn, "GP_TEST001", 0, 0, "system_a", "particle",
                      10.0, 20.0, 0.5, 0.80, match_id="match_aabb")
        upsert_defect(conn, "GP_TEST001", 0, 0, "system_b", "particle",
                      10.1, 20.1, 0.5, 0.95, match_id="match_aabb")
        # Unmatched
        upsert_defect(conn, "GP_TEST001", 1, 1, "system_a", "scratch",
                      50.0, 60.0, 1.2, 0.65, match_id=None)
        upsert_defect(conn, "GP_TEST001", 0, 1, "system_b", "pinhole",
                      80.0, 90.0, 0.3, 0.91, match_id=None)
    return conn


# ---------------------------------------------------------------------------
# Temporary directory for CSV output
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_dir(tmp_path):
    return tmp_path
