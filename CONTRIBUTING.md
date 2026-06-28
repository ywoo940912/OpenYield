# Contributing to OpenYield

Thank you for your interest in contributing to OpenYield — an open-source
semiconductor inspection data platform designed to serve domestic fabs,
national laboratories, and academic researchers working under the
CHIPS and Science Act of 2022.

---

## Table of Contents

1. [Code of Conduct](#code-of-conduct)
2. [Getting Started](#getting-started)
3. [Development Setup](#development-setup)
4. [Project Structure](#project-structure)
5. [Contribution Workflow](#contribution-workflow)
6. [Coding Standards](#coding-standards)
7. [Testing](#testing)
8. [Architecture Decisions](#architecture-decisions)
9. [Submitting Changes](#submitting-changes)

---

## Code of Conduct

OpenYield follows the [Contributor Covenant](https://www.contributor-covenant.org/)
Code of Conduct. All participants are expected to uphold respectful, inclusive
collaboration across the semiconductor research and manufacturing communities
this project serves.

---

## Getting Started

Good first contributions:

- **New ingestion adapter** — add a parser for a new inspection file format
  (e.g., KLARF 2.0, AEI XML, custom CSV variant) following the
  `BaseAdapter` interface in `openyield/ingestion/adapters/base.py`
- **Yield model** — implement an additional yield model in
  `openyield/yield_engine/models.py` following the existing Poisson/Murphy/
  Negative Binomial pattern
- **Analysis module** — add a new analysis function to
  `openyield/analysis/` (e.g., spatial autocorrelation, defect size
  distribution, critical area estimation)
- **Frontend page** — extend the React dashboard in `frontend/src/pages/`
- **Documentation** — improve docstrings, add examples, or expand `docs/`

---

## Development Setup

### Prerequisites

- Python 3.11 or later
- Node.js 20 or later (for the frontend)
- `git`

### Python environment

```bash
git clone https://github.com/yeonkukwoo/openyield.git
cd openyield

python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install -e ".[dev]"
```

### Frontend environment

```bash
cd frontend
npm install
```

### Verify setup

```bash
# Backend tests
python -m pytest

# Frontend dev server (requires backend on port 8000)
cd frontend && npm run dev
```

All 363 tests must pass before submitting a pull request.

---

## Project Structure

```
openyield/
├── db/              — Schema DDL, connection abstraction, PostgreSQL migration
├── ingestion/       — Pipeline orchestration + format adapters
│   └── adapters/    — BaseAdapter, KLARFAdapter, CSVAdapter
├── synthetic/       — Data and image generators for testing
├── validation/      — Data quality checks (coordinate bounds, duplicates, etc.)
├── yield_engine/    — Poisson / Murphy / Negative Binomial yield models
├── analysis/        — DBSCAN clustering, lot tracking, SPC, Pareto,
│                      bin analysis, trend analysis, signatures, correlation
├── ai/              — Multinomial logistic regression defect classifier
└── api/             — FastAPI application + routers
    └── routers/

frontend/            — React + Vite dashboard
docs/
├── adr/             — Architecture Decision Records (ADR-001 to ADR-006)
├── whitepaper.md    — Technical exhibit
└── roadmap.md       — Phase 1/2/3 deliverable plan
tests/               — pytest suite (363 tests)
```

---

## Contribution Workflow

1. **Open an issue first** for non-trivial changes to discuss the approach
   before investing implementation time.
2. **Fork** the repository and create a feature branch:
   ```
   git checkout -b feature/klarf-2-adapter
   ```
3. **Implement** your change following the coding standards below.
4. **Test** — all existing tests must pass, and new functionality must have
   corresponding tests.
5. **Open a pull request** against `main` with a clear title and description.

---

## Coding Standards

### Dependencies

OpenYield's defining constraint is **minimal external dependencies**.
The core library (`openyield/`) must remain importable with only the
Python standard library plus the packages listed in `pyproject.toml`
(FastAPI, Pydantic, uvicorn, Pillow). In particular:

- **No NumPy, pandas, or scikit-learn** in core modules. Pure Python
  implementations are required (see ADR-004 and ADR-006 in `docs/adr/`).
- New dependencies require explicit ADR justification.

### Style

- Python 3.11+ type hints throughout
- `from __future__ import annotations` in all modules
- Docstrings on public functions (one-line summary + parameters/returns
  for non-trivial signatures)
- Module-level docstring with `Author: Yeonkuk Woo` header (project convention)
- Line length: 100 characters

### Database

- All SQL must use `get_placeholder(conn)` from `openyield.db.connection`
  — never hardcode `?` or `%s`
- All writes must use idempotent upserts (`INSERT OR IGNORE` / `ON CONFLICT
  DO NOTHING`) — the pipeline is designed to be re-run safely
- Schema changes require a corresponding migration in `db/migrate_sqlite_to_postgres.py`

### API

- New endpoints go in the appropriate router under `api/routers/`
- Request/response models live in `api/schemas.py` (simple models) or
  inline Pydantic classes in the router (complex, router-specific models)
- No breaking changes to existing endpoint paths without a version bump

---

## Testing

```bash
# Run all tests
python -m pytest

# Run a specific module
python -m pytest tests/test_bin_analysis.py -v

# Run with coverage
python -m pytest --cov=openyield
```

### Test requirements

- Each new public function or class must have at least one test
- Tests use in-memory SQLite via the `mem_conn` fixture in `tests/conftest.py`
- No network calls, no filesystem I/O outside `tmp_path`, no mocked DB
- Test names must describe behavior: `test_cluster_panel_excursion`, not
  `test_cluster_2`

---

## Architecture Decisions

Significant decisions are documented as Architecture Decision Records in
`docs/adr/`. Before making a change that affects one of these decisions
(e.g., adding a new yield model, changing the schema, introducing a
framework dependency), read the relevant ADR.

To propose a change that conflicts with an existing ADR, open an issue
linking to the ADR and explain the rationale for revisiting the decision.

Current ADRs:

| ADR | Title |
|-----|-------|
| [ADR-001](docs/adr/ADR-001-substrate-agnostic-unified-schema.md) | Substrate-agnostic unified schema |
| [ADR-002](docs/adr/ADR-002-sqlite-postgresql-dual-backend.md) | SQLite + PostgreSQL dual backend |
| [ADR-003](docs/adr/ADR-003-klarf-native-parser-no-proprietary-libraries.md) | Native KLARF parser |
| [ADR-004](docs/adr/ADR-004-pure-python-dbscan-no-sklearn.md) | Pure-Python DBSCAN |
| [ADR-005](docs/adr/ADR-005-three-yield-models-with-empirical-alpha.md) | Three yield models with empirical α |
| [ADR-006](docs/adr/ADR-006-logistic-regression-over-deep-learning-for-classifier.md) | Logistic regression classifier |

---

## Submitting Changes

Pull request checklist:

- [ ] `python -m pytest` passes with no regressions
- [ ] New public functions have tests
- [ ] New modules have a module-level docstring with `Author:` line
- [ ] Breaking API changes are noted in `CHANGELOG.md`
- [ ] ADR created or updated if an architectural decision was made

---

**Principal Author:** Yeonkuk Woo  
**License:** Apache 2.0  
