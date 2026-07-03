# OpenYield

[![CI](https://github.com/ywoo940912/OpenYield/actions/workflows/ci.yml/badge.svg)](https://github.com/ywoo940912/OpenYield/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](pyproject.toml)

**Open-source semiconductor inspection data platform for domestic wafer fabs, glass substrate manufacturers, and defense-sector electronics producers.**

Developed by Yeonkuk Woo as vendor-neutral infrastructure for U.S. semiconductor manufacturing under the **CHIPS and Science Act of 2022** (Public Law 117-167). OpenYield provides the defect inspection data pipeline that domestic manufacturers require to operate at production scale without dependency on proprietary enterprise yield management systems (KLA Klarity, Onto Discover Yield).

---

## Motivation and Policy Context

The CHIPS and Science Act directs approximately $52.7 billion toward domestic semiconductor manufacturing capacity. Successful execution of this investment depends on the availability of open, interoperable data infrastructure that small and mid-size fabs, national laboratories, and academic institutions can adopt without enterprise licensing barriers.

OpenYield directly addresses this infrastructure gap. It provides:

- A standardized ingestion pipeline for the KLARF 1.x industry format used by KLA, Onto, and AMAT inspection tools
- A unified four-table database schema spanning glass panel, silicon wafer, and PCB substrate classes
- Industry-standard yield models (Poisson, Murphy, Negative Binomial) implemented in pure Python
- A 10-check data quality validation suite covering defect integrity, spatial consistency, and dual-system balance
- Statistical analysis capabilities (DBSCAN clustering, SPC, Pareto, wafer-to-wafer correlation) that are otherwise locked behind commercial tool licenses
- A REST API with 39 endpoints enabling integration with laboratory information management systems (LIMS) and manufacturing execution systems (MES)

### Primary Beneficiary Categories

| Category | How OpenYield Serves Them |
|---|---|
| **Domestic glass substrate manufacturers** | Unified AOI + confocal review pipeline; OLED/LCD panel defect taxonomy; Pareto and spatial signature analysis without KLA Klarity dependency |
| **Silicon wafer fabs (advanced logic, memory, analog)** | KLARF 1.x ingestion from optical scanner and e-beam review tools; Negative Binomial yield modeling for clustered defects at advanced nodes; edge-exclusion-aware die counting |
| **National laboratories (e.g., Sandia, NREL, Lincoln Laboratory)** | Reproducible open-source toolchain for inspection research; synthetic data generator requires no proprietary data; Apache 2.0 license permits unrestricted research use |
| **Academic ML researchers** | Labeled synthetic defect datasets for training and benchmarking; reference implementation of multinomial logistic regression classifier trained without GPU or external ML framework |
| **SEMI consortium members** | KLARF format compliance layer; open reference implementation for SEMI standards interoperability testing |
| **Defense and aerospace electronics producers** | PCB and glass panel substrate support; dual-system inspection model (automated scanner + review tool) matching defense inspection workflow architecture |
| **Emerging domestic fabs (Intel, TSMC Arizona, Samsung Texas, Micron)** | Zero-cost yield management baseline for new fab qualification runs before enterprise system procurement |

---

## Capabilities

| Module | Function | Technical Approach |
|---|---|---|
| `db/` | Schema and connection management | SQLite (development), PostgreSQL (production); single factory; WAL journaling |
| `ingestion/` | Defect record ingestion | Idempotent upserts; `INSERT OR IGNORE` / `ON CONFLICT DO NOTHING` |
| `ingestion/adapters/` | File format parsing | Generic CSV; KLARF 1.x ASCII with µm→mm unit conversion and class mapping |
| `synthetic/` | Synthetic dataset generation | Poisson defect counts; Gaussian spatial clustering; greedy nearest-neighbor cross-system matching |
| `validation/` | Data quality assurance | 10 checks: orphan defects, duplicates, confidence range, system balance, match symmetry |
| `yield_engine/` | Yield modeling | Poisson (`e^{-AD₀}`), Murphy (triangular distribution), Negative Binomial (Seeds/Stapper model) with empirical α estimation |
| `analysis/` | Process analysis | DBSCAN clustering (pure Python); lot-level excursion detection; yield-weighted Pareto; EWMA/CUSUM/Shewhart SPC; wafer-to-wafer correlation; spatial signature matching |
| `ai/` | ML defect classification | Multinomial logistic regression; batch gradient descent; L2 regularization; no sklearn dependency |
| `api/` | REST API | FastAPI; 39 routes; SQLite and PostgreSQL backends; OpenAPI/Swagger documentation |

---

## Installation

**Requirements:** Python 3.11+, NumPy

```bash
git clone https://github.com/openyield/openyield.git
cd openyield
pip install -e .
```

**With PostgreSQL support:**
```bash
pip install -e ".[postgres]"
```

**For development (includes pytest and ruff):**
```bash
pip install -e ".[dev]"
```

---

## Quick Start

Run the full end-to-end pipeline across all supported substrate types:

```bash
python run_pipeline.py
```

This generates synthetic inspection data, ingests it into the database, runs all 10 validation checks, computes yield estimates, and prints a full diagnostic report. Output is written to `./output/`; the database is written to `./inspection.db`.

**Substrate-specific runs:**
```bash
python run_pipeline.py --substrate wafer      --rows 10 --cols 10 --panels 3
python run_pipeline.py --substrate glass_panel --rows 6  --cols 6  --panels 5
python run_pipeline.py --substrate glass_panel --seed 99 --db ./fab_dev.db
```

**Start the REST API server:**
```bash
python serve.py
# Interactive docs: http://localhost:8000/docs
```

---

## Architecture

```
OpenYield/
├── openyield/
│   ├── db/
│   │   ├── schema.py                     # SQLite DDL + initialize_schema()
│   │   ├── schema_pg.py                  # PostgreSQL DDL (BIGSERIAL, TIMESTAMPTZ)
│   │   ├── connection.py                 # Backend factory, get_placeholder(), is_postgres()
│   │   └── migrate_sqlite_to_postgres.py # One-time SQLite → PostgreSQL migration
│   ├── ingestion/
│   │   ├── ingest.py                     # upsert_panel/component/defect, ingest_csv()
│   │   └── adapters/
│   │       ├── base.py                   # NormalizedDefect dataclass + BaseAdapter ABC
│   │       ├── csv_adapter.py            # Generic CSV → NormalizedDefect
│   │       └── klarf_adapter.py          # KLARF 1.x ASCII → NormalizedDefect
│   ├── synthetic/
│   │   ├── generator.py                  # generate_panel(), match_defects(), CSV export
│   │   ├── substrate_profiles.py         # SubstrateProfile dataclasses (all substrate types)
│   │   └── image_generator.py            # 64×64 grayscale PNG defect image patches
│   ├── validation/
│   │   └── checks.py                     # 10-check validation suite + report printer
│   ├── yield_engine/
│   │   ├── models.py                     # Poisson, Murphy, Negative Binomial + α estimation
│   │   └── calculator.py                 # Per-panel yield orchestration + persistence
│   ├── analysis/
│   │   ├── clustering.py                 # Pure-Python DBSCAN + excursion classification
│   │   ├── lot_tracker.py                # Lot-level yield summary + excursion detection
│   │   ├── pareto.py                     # Yield-weighted Pareto (overall, zone, system)
│   │   ├── spc.py                        # Shewhart, EWMA, CUSUM, I-MR charts
│   │   ├── correlation.py                # Wafer-to-wafer repeated defect correlation
│   │   └── signatures.py                 # Spatial pattern signature library
│   ├── ai/
│   │   └── classifier.py                 # Multinomial logistic regression defect classifier
│   └── api/
│       ├── main.py                       # FastAPI application entry point (39 routes)
│       ├── app.py                        # Legacy application entry point
│       ├── dependencies.py               # FastAPI dependency injection (DB per request)
│       ├── schemas.py                    # Pydantic request/response models
│       └── routers/                      # Route modules by domain
├── tests/                                # 314 tests (pytest)
├── run_pipeline.py                       # End-to-end pipeline CLI
├── serve.py                              # API server launcher
├── generate_images.py                    # Standalone image generation CLI
└── pyproject.toml
```

---

## Database Schema

All substrate types share the same four operational tables plus extended analysis tables:

**Core tables:**
```
panels           — panel_id, product_type, substrate_type, rows, cols, lot_id
components       — panel_id, component_row, component_col, region_id, center_x, center_y, active
defects          — defect_id, panel_id, component_row/col, source_system, defect_type,
                   x, y, size, confidence_score, match_id
files            — file_path, status (pending/processed/failed), processed_at
```

**Analysis tables:**
```
yield_estimates  — per-panel Poisson/Murphy/NegBinom results + clustering alpha
cluster_results  — DBSCAN output per panel (n_clusters, classification, epsilon)
defect_clusters  — per-defect cluster label assignments
lot_summaries    — lot-level aggregates and excursion status
spc_results      — SPC chart state (Cp, Cpk, EWMA lambda, process_state)
spc_alarms       — individual rule violations with severity
model_registry   — trained classifier versions with coefficients and accuracy
defect_predictions — per-defect ML predictions with confidence scores
defect_images    — synthetic image patch paths and metadata
```

All coordinates are in **millimeters**. Substrate types are constrained to `glass_panel` and `wafer`. Source systems are constrained to `system_a` (automated scanner) and `system_b` (review tool).

---

## Dual Inspection System Model

OpenYield models the dual-system inspection workflow standard in modern semiconductor and flat-panel display fabs:

- **system_a** — high-throughput automated optical scanner (AOI for glass; optical/e-beam for wafer). Higher defect sensitivity, higher false-positive rate.
- **system_b** — verification review tool (confocal review for glass; e-beam review for wafer). Lower throughput, higher classification accuracy.

Cross-system defect matching uses a **greedy nearest-neighbor spatial algorithm** parameterized by substrate-specific distance thresholds. Matched defects share a `match_id`; the validation suite verifies match symmetry (every matched `system_a` defect has a corresponding `system_b` record and vice versa).

---

## Ingestion Adapters

### Generic CSV

```python
from openyield.ingestion.adapters.csv_adapter import CsvAdapter
from openyield.ingestion.ingest import upsert_defect

adapter = CsvAdapter()
defects = adapter.parse("path/to/defects.csv")
for d in defects:
    upsert_defect(conn, **d.__dict__)
```

Required CSV columns: `panel_id`, `component_row`, `component_col`, `source_system`, `defect_type`, `x`, `y`, `size`, `confidence_score`

### KLARF 1.x ASCII

```python
from openyield.ingestion.adapters.klarf_adapter import KlarfAdapter

adapter = KlarfAdapter(source_system="system_a", confidence_score=0.75)
defects = adapter.parse("inspection_result.001")
```

| KLARF field | OpenYield field | Notes |
|---|---|---|
| WaferID | panel_id | LotID used as fallback |
| XREL / YREL (µm) | x / y (mm) | Unit conversion applied |
| DIEROW / DIECOL | component_row / component_col | Zero-based die grid |
| DEFECTSIZE (µm) | size (mm) | Unit conversion applied |
| CLASSNUMBER | defect_type | Via configurable class map |

---

## Yield Models

Three industry-standard models are implemented. All are parameterized by die critical area `A` (mm²) and defect density `D₀` (defects/mm²):

| Model | Formula | When to Use |
|---|---|---|
| **Poisson** | `Y = exp(−A·D₀)` | Mature nodes (180nm+); random defect distribution; conservative yield floor |
| **Murphy** | `Y = ((1−exp(−A·D₀)) / (A·D₀))²` | Mid-range nodes (28–180nm); non-uniform defect density across wafer |
| **Negative Binomial** | `Y = (1 + A·D₀/α)^(−α)` | Advanced nodes (<28nm); clustered defects; α estimated empirically from die variance |

The clustering parameter α is estimated from observed per-die defect variance using the **method of moments**. When variance ≤ mean (Poisson-like distribution), α defaults to 50 (near-random regime).

---

## Backend Configuration

### SQLite (default — development and testing)

```python
from openyield.db.connection import get_connection
from openyield.db.schema import initialize_schema

conn = get_connection(path="./inspection.db")
initialize_schema(conn)
```

### PostgreSQL (production)

```bash
export DB_BACKEND=postgres
export DB_HOST=localhost
export DB_PORT=5432
export DB_NAME=inspection
export DB_USER=myuser
export DB_PASSWORD=mypassword
```

```python
from openyield.db.connection import get_connection
from openyield.db.schema_pg import initialize_schema

conn = get_connection()
initialize_schema(conn)
```

### Migrate SQLite → PostgreSQL

```bash
export DB_HOST=localhost DB_USER=myuser DB_PASSWORD=mypassword
python -m openyield.db.migrate_sqlite_to_postgres --sqlite ./inspection.db
```

---

## Validation Suite

```python
from openyield.validation.checks import run_all_checks, print_validation_report

results = run_all_checks(conn)
print_validation_report(results)
```

| Check | What It Verifies |
|---|---|
| `row_count:panels` | Total panel count |
| `row_count:components` | Total component count |
| `row_count:defects` | Total defect count |
| `row_count:files` | Total file tracking count |
| `duplicate_defects` | Near-duplicate defects (rounded x, y coordinates) |
| `orphan_defects` | Defects with no matching component record |
| `component_coverage` | Panels with incorrect component count vs. declared grid |
| `confidence_range` | Confidence scores outside [0.0, 1.0] |
| `system_balance` | system_b defect count exceeding system_a (unexpected) |
| `match_symmetry` | match_ids present in only one system (broken pair) |

---

## Analysis Modules

### DBSCAN Spatial Clustering

```python
from openyield.analysis.clustering import cluster_panel

result = cluster_panel(conn, panel_id="PANEL_001")
# result.classification: 'random' | 'systematic' | 'excursion'
```

Classification logic: single dominant cluster (>30% of all defects) → `excursion`; multiple roughly equal clusters → `systematic`; no significant clusters → `random`.

### Statistical Process Control

```python
from openyield.analysis.spc import run_spc

result = run_spc(conn, lot_id="LOT_001")
# result.process_state: 'in_control' | 'warning' | 'out_of_control'
```

Four chart types: Shewhart X-bar (Western Electric rules WE1–WE4), EWMA (λ=0.2, L=3.0), CUSUM, I-MR. All persist alarm records to `spc_alarms`.

### Defect Pareto

```python
from openyield.analysis.pareto import calculate_pareto

result = calculate_pareto(conn, panel_id="PANEL_001")
# Ranked by yield-impact proxy: count × avg_size × avg_confidence
```

---

## AI Defect Classifier

```python
from openyield.ai.classifier import train_classifier, predict_panel

training = train_classifier(conn, substrate_type="wafer")
predictions = predict_panel(conn, panel_id="PANEL_001")
```

Multinomial logistic regression trained by batch gradient descent on softmax cross-entropy loss with L2 regularization. 15 features including spatial coordinates, zone indicators, substrate type, and confidence scores. No sklearn, PyTorch, or TensorFlow dependency — the full training loop is implemented in pure Python.

---

## Running Tests

```bash
pip install -e ".[dev]"
pytest                                        # 314 tests
pytest --cov=openyield --cov-report=term-missing
```

---

## Contributing

Contributions are welcome. Please open an issue before submitting a pull request for non-trivial changes.

This project follows the [Contributor Covenant](https://www.contributor-covenant.org/) code of conduct.

---

## License

Apache License 2.0 — see [LICENSE](LICENSE) for full terms.

---

## Author

**Yeonkuk Woo**
Semiconductor inspection data infrastructure for U.S. domestic manufacturing.

---

## Acknowledgements

Developed as open infrastructure in the context of the **CHIPS and Science Act of 2022** (Public Law 117-167), with the objective of providing rigorous, vendor-neutral defect inspection data tooling to domestic wafer fabs, flat-panel display manufacturers, national laboratories, and defense-sector electronics producers who require production-grade yield management capabilities without enterprise licensing costs.
