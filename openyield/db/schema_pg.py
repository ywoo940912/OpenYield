"""
db/schema_pg.py
---------------
Author: Yeonkuk Woo

PostgreSQL-specific DDL for OpenYield. SQLite-equivalent schema lives in db/schema.py.

Key cross-backend differences handled here
-------------------------------------------
| Concern           | SQLite                        | PostgreSQL              |
|-------------------|-------------------------------|-------------------------|
| Auto-increment PK | INTEGER PRIMARY KEY AUTOINCR. | BIGSERIAL / IDENTITY    |
| Timestamp type    | TIMESTAMP (stored as text)    | TIMESTAMPTZ             |
| Upsert syntax     | INSERT OR IGNORE              | INSERT … ON CONFLICT    |
| FK enforcement    | PRAGMA foreign_keys=ON        | On by default           |
| Concurrency       | PRAGMA journal_mode=WAL       | Native MVCC             |
| CHECK constraint  | Supported                     | Supported (identical)   |

The DDL uses the LOWEST common denominator wherever possible so the same
string works on both backends. Where it cannot (AUTOINCREMENT vs SERIAL),
separate DDL strings are used and selected at runtime.
"""

import logging
from typing import Any

from .connection import get_connection, is_postgres, Backend

logger = logging.getLogger(__name__)

Connection = Any


# ---------------------------------------------------------------------------
# Cross-backend DDL helpers
# ---------------------------------------------------------------------------

def _panels_ddl(postgres: bool) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS panels (
    panel_id          TEXT        PRIMARY KEY,
    product_type      TEXT        NOT NULL,
    substrate_type    TEXT        NOT NULL
                      CHECK (substrate_type IN ('glass_panel','wafer')),
    rows              INTEGER     NOT NULL CHECK (rows > 0),
    cols              INTEGER     NOT NULL CHECK (cols > 0),
    created_at        {'TIMESTAMPTZ' if postgres else 'TIMESTAMP'}
                      NOT NULL DEFAULT {'NOW()' if postgres else 'CURRENT_TIMESTAMP'}
);
"""


def _components_ddl(_postgres: bool) -> str:
    return """
CREATE TABLE IF NOT EXISTS components (
    panel_id          TEXT        NOT NULL REFERENCES panels(panel_id),
    component_row     INTEGER     NOT NULL CHECK (component_row >= 0),
    component_col     INTEGER     NOT NULL CHECK (component_col >= 0),
    region_id         TEXT        NOT NULL,
    center_x          REAL        NOT NULL,
    center_y          REAL        NOT NULL,
    active            INTEGER     NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    PRIMARY KEY (panel_id, component_row, component_col)
);
"""


def _defects_ddl(postgres: bool) -> str:
    pk = "BIGSERIAL PRIMARY KEY" if postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
    ts = "TIMESTAMPTZ" if postgres else "TIMESTAMP"
    now = "NOW()" if postgres else "CURRENT_TIMESTAMP"
    return f"""
CREATE TABLE IF NOT EXISTS defects (
    defect_id         {pk},
    panel_id          TEXT        NOT NULL REFERENCES panels(panel_id),
    component_row     INTEGER     NOT NULL,
    component_col     INTEGER     NOT NULL,
    source_system     TEXT        NOT NULL
                      CHECK (source_system IN ('system_a','system_b')),
    defect_type       TEXT        NOT NULL,
    x                 REAL        NOT NULL,
    y                 REAL        NOT NULL,
    size              REAL        NOT NULL CHECK (size > 0),
    confidence_score  REAL        NOT NULL
                      CHECK (confidence_score BETWEEN 0.0 AND 1.0),
    match_id          TEXT,
    created_at        {ts}        NOT NULL DEFAULT {now},
    UNIQUE (panel_id, component_row, component_col,
            source_system, defect_type, x, y)
);
"""


def _files_ddl(postgres: bool) -> str:
    ts = "TIMESTAMPTZ" if postgres else "TIMESTAMP"
    return f"""
CREATE TABLE IF NOT EXISTS files (
    file_path    TEXT PRIMARY KEY,
    status       TEXT NOT NULL CHECK (status IN ('pending','processed','failed')),
    processed_at {ts}
);
"""



def _yield_estimates_ddl(postgres: bool) -> str:
    pk = "BIGSERIAL PRIMARY KEY" if postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
    ts = "TIMESTAMPTZ" if postgres else "TIMESTAMP"
    now = "NOW()" if postgres else "CURRENT_TIMESTAMP"
    return f"""
CREATE TABLE IF NOT EXISTS yield_estimates (
    id                {pk},
    panel_id          TEXT    NOT NULL REFERENCES panels(panel_id),
    substrate_type    TEXT    NOT NULL
                      CHECK (substrate_type IN ('glass_panel','wafer')),
    calculated_at     {ts}   NOT NULL DEFAULT {now},
    die_area_mm2      REAL    NOT NULL CHECK (die_area_mm2 > 0),
    inspected_dies    INTEGER NOT NULL CHECK (inspected_dies > 0),
    defect_count      INTEGER NOT NULL CHECK (defect_count >= 0),
    defect_density    REAL    NOT NULL CHECK (defect_density >= 0),
    yield_poisson     REAL    NOT NULL CHECK (yield_poisson BETWEEN 0.0 AND 1.0),
    yield_murphy      REAL    NOT NULL CHECK (yield_murphy BETWEEN 0.0 AND 1.0),
    yield_negbinom    REAL    NOT NULL CHECK (yield_negbinom BETWEEN 0.0 AND 1.0),
    clustering_alpha  REAL    NOT NULL CHECK (clustering_alpha > 0),
    alpha_method      TEXT    NOT NULL CHECK (alpha_method IN ('empirical','profile')),
    model_notes       TEXT
);
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_defects_panel    ON defects (panel_id);",
    "CREATE INDEX IF NOT EXISTS idx_defects_system   ON defects (source_system);",
    "CREATE INDEX IF NOT EXISTS idx_defects_match    ON defects (match_id);",
    "CREATE INDEX IF NOT EXISTS idx_defects_type     ON defects (defect_type);",
    "CREATE INDEX IF NOT EXISTS idx_comp_panel       ON components (panel_id);",
    "CREATE INDEX IF NOT EXISTS idx_comp_region      ON components (panel_id, region_id);",
    "CREATE INDEX IF NOT EXISTS idx_panels_substrate ON panels (substrate_type);",
]


# ---------------------------------------------------------------------------
# DOE support — process runs, events, DOE legs
# ---------------------------------------------------------------------------

def _process_runs_ddl(postgres: bool) -> str:
    pk  = "BIGSERIAL PRIMARY KEY" if postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
    ts  = "TIMESTAMPTZ" if postgres else "TIMESTAMP"
    now = "NOW()" if postgres else "CURRENT_TIMESTAMP"
    return f"""
CREATE TABLE IF NOT EXISTS process_runs (
    id                        {pk},
    panel_id                  TEXT    NOT NULL REFERENCES panels(panel_id),
    recipe_id                 TEXT    NOT NULL,
    lamination_temp_c         REAL,
    lamination_dwell_sec      INTEGER,
    lamination_ramp_c_per_min REAL,
    tgv_laser_power_w         REAL,
    tgv_pulse_freq_hz         INTEGER,
    tgv_focus_depth_um        REAL,
    operator_shift            TEXT    CHECK (operator_shift IN ('day','swing','grave')),
    recorded_at               {ts}   NOT NULL DEFAULT {now},
    UNIQUE (panel_id, recorded_at)
);
"""


def _events_ddl(postgres: bool) -> str:
    pk = "BIGSERIAL PRIMARY KEY" if postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
    ts = "TIMESTAMPTZ" if postgres else "TIMESTAMP"
    return f"""
CREATE TABLE IF NOT EXISTS events (
    id          {pk},
    event_type  TEXT    NOT NULL
                CHECK (event_type IN (
                    'maintenance','recipe_change','shift_change',
                    'tool_pm','material_lot_change','calibration'
                )),
    tool_id     TEXT,
    description TEXT,
    event_time  {ts}   NOT NULL
);
"""


def _doe_legs_ddl(postgres: bool) -> str:
    pk  = "BIGSERIAL PRIMARY KEY" if postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
    ts  = "TIMESTAMPTZ" if postgres else "TIMESTAMP"
    now = "NOW()" if postgres else "CURRENT_TIMESTAMP"
    return f"""
CREATE TABLE IF NOT EXISTS doe_legs (
    id          {pk},
    leg_id      TEXT    NOT NULL UNIQUE,
    doe_id      TEXT    NOT NULL,
    description TEXT,
    recipe_id   TEXT,
    n_panels    INTEGER NOT NULL DEFAULT 0,
    created_at  {ts}   NOT NULL DEFAULT {now}
);
"""


DOE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_process_runs_panel  ON process_runs (panel_id);",
    "CREATE INDEX IF NOT EXISTS idx_process_runs_recipe ON process_runs (recipe_id);",
    "CREATE INDEX IF NOT EXISTS idx_events_time         ON events (event_time);",
    "CREATE INDEX IF NOT EXISTS idx_events_tool         ON events (tool_id);",
    "CREATE INDEX IF NOT EXISTS idx_doe_legs_doe        ON doe_legs (doe_id);",
]


# ---------------------------------------------------------------------------
# Public initializer
# ---------------------------------------------------------------------------

def initialize_schema(conn: Connection) -> None:
    """
    Create all tables and indexes on the connected backend.
    Safe to call on an already-initialized database (IF NOT EXISTS everywhere).
    """
    pg = is_postgres(conn)
    ddl_statements = [
        _panels_ddl(pg),
        _components_ddl(pg),
        _defects_ddl(pg),
        _files_ddl(pg),
        _yield_estimates_ddl(pg),
        _process_runs_ddl(pg),
        _events_ddl(pg),
        _doe_legs_ddl(pg),
        *INDEXES,
        "CREATE INDEX IF NOT EXISTS idx_yield_panel ON yield_estimates (panel_id);",
        *DOE_INDEXES,
    ]

    if pg:
        cur = conn.cursor()
        for stmt in ddl_statements:
            cur.execute(stmt)
        conn.commit()
        cur.close()
    else:
        with conn:
            for stmt in ddl_statements:
                conn.execute(stmt)

    logger.info(
        "Schema initialized on %s backend.",
        "PostgreSQL" if pg else "SQLite",
    )
