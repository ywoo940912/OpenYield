"""
db/schema.py
------------
Author: Yeonkuk Woo

SQLite-first schema DDL and initializer.

For PostgreSQL-specific DDL (BIGSERIAL, TIMESTAMPTZ, NOW()),
use db/schema_pg.py which selects DDL at runtime based on the connection type.

This module owns:
  - All DDL strings for SQLite
  - initialize_schema(conn) — safe to call on an already-initialized database
"""

import sqlite3
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DDL_PANELS = """
CREATE TABLE IF NOT EXISTS panels (
    panel_id          TEXT PRIMARY KEY,
    product_type      TEXT NOT NULL,
    substrate_type    TEXT NOT NULL CHECK (substrate_type IN ('glass_panel', 'wafer')),
    rows              INTEGER NOT NULL CHECK (rows > 0),
    cols              INTEGER NOT NULL CHECK (cols > 0),
    lot_id            TEXT,
    created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

DDL_COMPONENTS = """
CREATE TABLE IF NOT EXISTS components (
    panel_id          TEXT    NOT NULL REFERENCES panels(panel_id),
    component_row     INTEGER NOT NULL CHECK (component_row >= 0),
    component_col     INTEGER NOT NULL CHECK (component_col >= 0),
    region_id         TEXT    NOT NULL,
    center_x          REAL    NOT NULL,
    center_y          REAL    NOT NULL,
    active            INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    PRIMARY KEY (panel_id, component_row, component_col)
);
"""

DDL_DEFECTS = """
CREATE TABLE IF NOT EXISTS defects (
    defect_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    panel_id          TEXT    NOT NULL REFERENCES panels(panel_id),
    component_row     INTEGER NOT NULL,
    component_col     INTEGER NOT NULL,
    source_system     TEXT    NOT NULL CHECK (source_system IN ('system_a','system_b')),
    defect_type       TEXT    NOT NULL,
    x                 REAL    NOT NULL,
    y                 REAL    NOT NULL,
    size              REAL    NOT NULL CHECK (size > 0),
    confidence_score  REAL    NOT NULL CHECK (confidence_score BETWEEN 0.0 AND 1.0),
    match_id          TEXT,
    created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (panel_id, component_row, component_col, source_system, defect_type, x, y)
);
"""

DDL_FILES = """
CREATE TABLE IF NOT EXISTS files (
    file_path    TEXT PRIMARY KEY,
    status       TEXT NOT NULL CHECK (status IN ('pending','processed','failed')),
    processed_at TIMESTAMP
);
"""

DDL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_defects_panel    ON defects (panel_id);",
    "CREATE INDEX IF NOT EXISTS idx_defects_system   ON defects (source_system);",
    "CREATE INDEX IF NOT EXISTS idx_defects_match    ON defects (match_id);",
    "CREATE INDEX IF NOT EXISTS idx_defects_type     ON defects (defect_type);",
    "CREATE INDEX IF NOT EXISTS idx_comp_panel       ON components (panel_id);",
    "CREATE INDEX IF NOT EXISTS idx_comp_region      ON components (panel_id, region_id);",
    "CREATE INDEX IF NOT EXISTS idx_panels_substrate ON panels (substrate_type);",
]


DDL_YIELD_ESTIMATES = """
CREATE TABLE IF NOT EXISTS yield_estimates (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    panel_id          TEXT    NOT NULL REFERENCES panels(panel_id),
    substrate_type    TEXT    NOT NULL CHECK (substrate_type IN ('glass_panel','wafer')),
    calculated_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
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

DDL_YIELD_INDEX = "CREATE INDEX IF NOT EXISTS idx_yield_panel ON yield_estimates (panel_id);"


DDL_LOTS = """
CREATE TABLE IF NOT EXISTS lots (
    lot_id            TEXT PRIMARY KEY,
    substrate_type    TEXT NOT NULL CHECK (substrate_type IN ('glass_panel','wafer')),
    product_type      TEXT NOT NULL,
    lot_size          INTEGER NOT NULL DEFAULT 25,
    status            TEXT NOT NULL DEFAULT 'active'
                      CHECK (status IN ('active','complete','hold')),
    created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

DDL_CLUSTER_RESULTS = """
CREATE TABLE IF NOT EXISTS cluster_results (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    panel_id          TEXT    NOT NULL REFERENCES panels(panel_id),
    calculated_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    n_clusters        INTEGER NOT NULL,
    n_noise           INTEGER NOT NULL,
    classification    TEXT    NOT NULL
                      CHECK (classification IN ('random','systematic','excursion')),
    largest_cluster   INTEGER NOT NULL,
    epsilon_mm        REAL    NOT NULL,
    min_samples       INTEGER NOT NULL,
    cluster_summary   TEXT
);
"""

DDL_DEFECT_CLUSTERS = """
CREATE TABLE IF NOT EXISTS defect_clusters (
    defect_id         INTEGER NOT NULL,
    panel_id          TEXT    NOT NULL,
    cluster_label     INTEGER NOT NULL,
    is_noise          INTEGER NOT NULL DEFAULT 0 CHECK (is_noise IN (0,1)),
    PRIMARY KEY (defect_id, panel_id)
);
"""

DDL_LOT_SUMMARIES = """
CREATE TABLE IF NOT EXISTS lot_summaries (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    lot_id            TEXT    NOT NULL REFERENCES lots(lot_id),
    calculated_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    panel_count       INTEGER NOT NULL,
    avg_defect_density REAL   NOT NULL,
    std_defect_density REAL   NOT NULL,
    avg_yield_negbinom REAL,
    std_yield_negbinom REAL,
    excursion_count   INTEGER NOT NULL DEFAULT 0,
    lot_status        TEXT    NOT NULL
                      CHECK (lot_status IN ('clean','watch','excursion'))
);
"""

DDL_LOT_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_panels_lot      ON panels (lot_id);",
    "CREATE INDEX IF NOT EXISTS idx_clusters_panel  ON cluster_results (panel_id);",
    "CREATE INDEX IF NOT EXISTS idx_defclust_panel  ON defect_clusters (panel_id);",
    "CREATE INDEX IF NOT EXISTS idx_lotsumm_lot     ON lot_summaries (lot_id);",
]


DDL_SPC_RESULTS = """
CREATE TABLE IF NOT EXISTS spc_results (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    lot_id            TEXT,
    substrate_type    TEXT,
    calculated_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    n_points          INTEGER NOT NULL,
    centerline        REAL    NOT NULL,
    sigma             REAL    NOT NULL,
    lambda_ewma       REAL    NOT NULL,
    L_ewma            REAL    NOT NULL,
    process_state     TEXT    NOT NULL
                      CHECK (process_state IN
                          ('in_control','warning','out_of_control')),
    cp                REAL,
    cpk               REAL,
    usl               REAL,
    lsl               REAL
);
"""

DDL_SPC_ALARMS = """
CREATE TABLE IF NOT EXISTS spc_alarms (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    spc_result_id     INTEGER NOT NULL REFERENCES spc_results(id),
    panel_id          TEXT    NOT NULL,
    sequence          INTEGER NOT NULL,
    chart_type        TEXT    NOT NULL
                      CHECK (chart_type IN
                          ('shewhart','ewma','cusum','imr')),
    rule_fired        TEXT    NOT NULL,
    value             REAL    NOT NULL,
    control_limit     REAL    NOT NULL,
    severity          TEXT    NOT NULL
                      CHECK (severity IN ('warning','out_of_control')),
    created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

DDL_SPC_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_spc_lot       ON spc_results (lot_id);",
    "CREATE INDEX IF NOT EXISTS idx_spc_sub       ON spc_results (substrate_type);",
    "CREATE INDEX IF NOT EXISTS idx_alarm_result  ON spc_alarms (spc_result_id);",
    "CREATE INDEX IF NOT EXISTS idx_alarm_panel   ON spc_alarms (panel_id);",
]


DDL_DEFECT_PREDICTIONS = """
CREATE TABLE IF NOT EXISTS defect_predictions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    defect_id         INTEGER NOT NULL,
    panel_id          TEXT    NOT NULL,
    model_version     TEXT    NOT NULL,
    predicted_type    TEXT    NOT NULL,
    confidence        REAL    NOT NULL CHECK (confidence BETWEEN 0.0 AND 1.0),
    true_type         TEXT,
    correct           INTEGER,
    calculated_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

DDL_MODEL_REGISTRY = """
CREATE TABLE IF NOT EXISTS model_registry (
    model_version     TEXT    PRIMARY KEY,
    model_type        TEXT    NOT NULL,
    substrate_type    TEXT,
    n_training_samples INTEGER NOT NULL,
    n_features        INTEGER NOT NULL,
    classes           TEXT    NOT NULL,
    accuracy          REAL,
    coefficients      TEXT    NOT NULL,
    feature_names     TEXT    NOT NULL,
    trained_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

DDL_AI_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_pred_panel  ON defect_predictions (panel_id);",
    "CREATE INDEX IF NOT EXISTS idx_pred_defect ON defect_predictions (defect_id);",
]


DDL_DEFECT_IMAGES = """
CREATE TABLE IF NOT EXISTS defect_images (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    defect_id         INTEGER NOT NULL,
    panel_id          TEXT    NOT NULL,
    image_path        TEXT    NOT NULL,
    width             INTEGER NOT NULL,
    height            INTEGER NOT NULL,
    format            TEXT    NOT NULL DEFAULT 'png',
    generator_version TEXT    NOT NULL DEFAULT 'v1',
    created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (defect_id, panel_id)
);
"""

DDL_IMAGE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_img_panel  ON defect_images (panel_id);",
    "CREATE INDEX IF NOT EXISTS idx_img_defect ON defect_images (defect_id);",
]

# ---------------------------------------------------------------------------
# DOE support — process runs, events, DOE legs
# ---------------------------------------------------------------------------

DDL_PROCESS_RUNS = """
CREATE TABLE IF NOT EXISTS process_runs (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    panel_id                  TEXT    NOT NULL REFERENCES panels(panel_id),
    recipe_id                 TEXT    NOT NULL,
    lamination_temp_c         REAL,
    lamination_dwell_sec      INTEGER,
    lamination_ramp_c_per_min REAL,
    tgv_laser_power_w         REAL,
    tgv_pulse_freq_hz         INTEGER,
    tgv_focus_depth_um        REAL,
    operator_shift            TEXT    CHECK (operator_shift IN ('day','swing','grave')),
    recorded_at               TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (panel_id, recorded_at)
);
"""

DDL_EVENTS = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT    NOT NULL
                CHECK (event_type IN (
                    'maintenance','recipe_change','shift_change',
                    'tool_pm','material_lot_change','calibration'
                )),
    tool_id     TEXT,
    description TEXT,
    event_time  TIMESTAMP NOT NULL
);
"""

DDL_DOE_LEGS = """
CREATE TABLE IF NOT EXISTS doe_legs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    leg_id      TEXT    NOT NULL UNIQUE,
    doe_id      TEXT    NOT NULL,
    description TEXT,
    recipe_id   TEXT,
    n_panels    INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

DDL_DOE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_process_runs_panel  ON process_runs (panel_id);",
    "CREATE INDEX IF NOT EXISTS idx_process_runs_recipe ON process_runs (recipe_id);",
    "CREATE INDEX IF NOT EXISTS idx_events_time         ON events (event_time);",
    "CREATE INDEX IF NOT EXISTS idx_events_tool         ON events (tool_id);",
    "CREATE INDEX IF NOT EXISTS idx_doe_legs_doe        ON doe_legs (doe_id);",
]

ALL_DDL = [
    DDL_LOTS, DDL_PANELS, DDL_COMPONENTS, DDL_DEFECTS, DDL_FILES,
    DDL_YIELD_ESTIMATES, DDL_CLUSTER_RESULTS, DDL_DEFECT_CLUSTERS,
    DDL_LOT_SUMMARIES, DDL_SPC_RESULTS, DDL_SPC_ALARMS,
    DDL_DEFECT_PREDICTIONS, DDL_MODEL_REGISTRY, DDL_DEFECT_IMAGES,
    DDL_PROCESS_RUNS, DDL_EVENTS, DDL_DOE_LEGS,
] + DDL_INDEXES + [DDL_YIELD_INDEX] + DDL_LOT_INDEXES + DDL_SPC_INDEXES + DDL_AI_INDEXES + DDL_IMAGE_INDEXES + DDL_DOE_INDEXES


def initialize_schema(conn: sqlite3.Connection) -> None:
    """
    Create all tables and indexes.
    Safe to call on an already-initialized database (IF NOT EXISTS everywhere).
    """
    with conn:
        for statement in ALL_DDL:
            conn.execute(statement)
    logger.info("SQLite schema initialized successfully.")
