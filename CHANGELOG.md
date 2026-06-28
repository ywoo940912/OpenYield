# Changelog

All notable changes to OpenYield are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Added
- GitHub Actions CI workflow (`.github/workflows/ci.yml`) — planned

---

## [0.4.0] — 2026-06

### Added
- **React + Vite dashboard** (`frontend/`) — full dark-theme SPA with sidebar
  navigation, KPI cards, Recharts trend lines, wafer/panel map heatmap
- **Spatial bin analysis** (`openyield/analysis/bin_analysis.py`) — per-die
  defect aggregation, cluster label assignment, and Poisson yield at the
  die level for frontend heatmap rendering
- **Multi-lot trend analysis** (`openyield/analysis/trend.py`) — pure-Python
  OLS linear regression over lot-ordered defect density; classifies drift as
  `improving`, `stable`, or `degrading`
- API endpoints: `GET /panels/{id}/map` and `GET /trends` via
  `api/routers/analysis_router.py`
- Pydantic response models: `PanelMapResponse`, `TrendResultResponse`
- Tests for `bin_analysis` and `trend` modules (49 new tests; total 363)
- ADR-001 through ADR-006 in `docs/adr/`
- `docs/whitepaper.md` — technical exhibit for EB-2 NIW petition
- `docs/roadmap.md` — Phase 1/2/3 deliverable tracking

### Changed
- Author headers (`Author: Yeonkuk Woo`) added to all 51 Python source files
- `README.md` — complete rewrite with formal language, named beneficiary
  categories, architecture tree, yield model comparison, and schema summary

---

## [0.3.0] — 2025-Q4

### Added
- **Multinomial logistic regression classifier** (`openyield/ai/classifier.py`)
  — pure Python softmax + batch gradient descent + L2 regularization;
  no external ML framework dependency
- **Synthetic inspection image generator** (`openyield/synthetic/image_generator.py`)
  — generates PNG wafer map thumbnails for classifier training
- `generate_images.py` — CLI entry point for bulk image generation
- FastAPI application (`openyield/api/`) with routers for panels, defects,
  yield, clustering, lot tracking, AI classification, and validation
- `serve.py` — development server launcher

### Changed
- `api/routers/` modularized into per-domain router files

---

## [0.2.0] — 2025-Q3

### Added
- **SPC analysis** (`openyield/analysis/spc.py`) — EWMA, Shewhart, CUSUM,
  I-MR charts; Western Electric rules; Cp/Cpk capability indices
- **Pareto analysis** (`openyield/analysis/pareto.py`) — defect type frequency
  ranking with 80th-percentile cutoff
- **Spatial correlation** (`openyield/analysis/correlation.py`) — defect
  co-occurrence matrix between source systems
- **Defect signature matching** (`openyield/analysis/signatures.py`) — pattern
  library comparison for known failure modes
- **Lot tracking** (`openyield/analysis/lot_tracker.py`) — lot summarization,
  excursion detection, SPC alarm propagation
- **PostgreSQL migration path** (`openyield/db/migrate_sqlite_to_postgres.py`)
- `docs/adr/` directory (placeholder)

### Changed
- Database connection abstraction (`get_placeholder`, `is_postgres`) extracted
  to `openyield/db/connection.py` for SQLite/PostgreSQL dual-backend support

---

## [0.1.0] — 2025-Q2

### Added
- Initial project scaffold (`pyproject.toml`, `openyield/` package)
- **SQLite schema** with core tables: `panels`, `components`, `defects`,
  `files`; analysis tables: `yield_estimates`, `cluster_results`,
  `defect_clusters`, `lot_summaries`, `spc_results`, `spc_alarms`
- **KLARF 1.x adapter** (`openyield/ingestion/adapters/klarf_adapter.py`) —
  clean-room ASCII parser; `DefectRecordSpec`-driven column ordering;
  unit conversion at parse time; no proprietary dependencies
- **CSV adapter** (`openyield/ingestion/adapters/csv_adapter.py`)
- **Synthetic data generator** (`openyield/synthetic/generator.py`) with
  substrate-specific profiles (`substrate_profiles.py`)
- **Yield models** (`openyield/yield_engine/`) — Poisson, Murphy, and
  Negative Binomial; empirical α via method of moments; simultaneous
  three-model output
- **DBSCAN clustering** (`openyield/analysis/clustering.py`) — pure Python,
  no sklearn; `random`/`systematic`/`excursion` classification
- **Validation suite** (`openyield/validation/checks.py`) — coordinate bounds,
  duplicate detection, confidence score range, referential integrity
- **Pipeline runner** (`run_pipeline.py`) — orchestrates ingest → yield →
  clustering → lot tracking
- Initial test suite (314 tests)

---

[Unreleased]: https://github.com/yeonkukwoo/openyield/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/yeonkukwoo/openyield/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/yeonkukwoo/openyield/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/yeonkukwoo/openyield/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/yeonkukwoo/openyield/releases/tag/v0.1.0
