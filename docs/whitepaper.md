# OpenYield: Open-Source Semiconductor Inspection Data Infrastructure for U.S. Domestic Manufacturing

**Technical Whitepaper — Petition Exhibit**

**Author:** Yeonkuk Woo
**Version:** 1.0
**Date:** June 2025

---

## Abstract

OpenYield is an open-source semiconductor inspection data platform developed by Yeonkuk Woo to address a critical infrastructure gap in U.S. domestic semiconductor manufacturing. The platform provides defect ingestion, yield modeling, data quality validation, spatial clustering analysis, statistical process control, and machine learning defect classification as open, vendor-neutral infrastructure available under the Apache License 2.0.

The work is directly relevant to the objectives of the CHIPS and Science Act of 2022 (Public Law 117-167), which directs approximately $52.7 billion toward domestic semiconductor manufacturing capacity. Realizing this investment requires that domestic manufacturers — including emerging fabs, national laboratories, and academic research institutions — have access to the yield management software infrastructure that production-scale manufacturing demands. OpenYield provides this infrastructure without the enterprise licensing costs ($500,000–$2,000,000 per site annually) of commercial alternatives such as KLA Klarity and Onto Discover Yield.

This whitepaper describes the technical design, capabilities, and beneficiary impact of OpenYield as a contribution of substantial merit to U.S. semiconductor manufacturing infrastructure.

---

## 1. Problem Statement

### 1.1 The Infrastructure Gap in Domestic Semiconductor Manufacturing

The CHIPS and Science Act of 2022 catalyzes a historic expansion of U.S. semiconductor manufacturing capacity. Announced investments include TSMC's $65 billion Arizona complex, Intel's $100 billion multi-state expansion, Samsung's $17 billion Taylor Texas facility, and Micron Technology's $40 billion U.S. memory manufacturing program. Beyond these anchor investments, the Act funds dozens of smaller domestic fabs, national laboratory semiconductor programs, and academic fabrication centers.

Every one of these facilities requires yield management software — the data infrastructure that ingests defect inspection results, computes yield, identifies process excursions, and guides engineering response. Without yield management infrastructure, a fab cannot operate at production scale; it cannot determine whether its process is in control, identify the root cause of yield loss, or demonstrate compliance with quality requirements to customers.

The two dominant commercial yield management platforms — KLA Klarity and Onto Discover Yield — are priced at enterprise licensing levels that are inaccessible to the majority of CHIPS Act beneficiaries:

- **Academic fabrication centers** at universities receiving CHIPS Act research funding cannot afford enterprise licensing
- **National laboratories** (Sandia, NREL, Lincoln Laboratory, Argonne) conducting semiconductor inspection research need open, reproducible tooling that can be published alongside research results
- **Emerging domestic fabs** in early qualification runs require yield management capabilities before they have the revenue to support enterprise contracts
- **Small and mid-size domestic manufacturers** in the glass substrate and PCB sectors — critical components of the domestic supply chain — are priced out of enterprise tools entirely

OpenYield directly addresses this gap.

### 1.2 The KLARF Format Lock-In Problem

All major semiconductor inspection tools (KLA, Onto, AMAT) produce output in the KLARF (KLA Results File) format. Accessing this data in open platforms requires a KLARF parser. The reference KLARF parser is proprietary software distributed by KLA and not available for open-source redistribution. This creates a lock-in: manufacturers can only use inspection data from KLA tools within KLA software.

OpenYield breaks this lock-in with a clean-room implementation of the KLARF 1.x ASCII parser (`ingestion/adapters/klarf_adapter.py`) written entirely from scratch without proprietary libraries. The parser handles the `DefectRecordSpec`-driven column ordering that varies across KLA, Onto, and AMAT tool outputs, and correctly converts micron-based KLARF coordinates to the millimeter-based OpenYield schema.

---

## 2. Technical Architecture

### 2.1 Design Principles

OpenYield was designed around four principles derived from the requirements of its target beneficiaries:

1. **Zero infrastructure dependency for entry-level use.** A user can run `pip install -e .` and have a fully functional yield management platform within five minutes on any Python 3.11+ workstation. No database server, no GPU, no enterprise license.

2. **Production-grade path for fab deployment.** PostgreSQL backend support, connection pooling, idempotent upserts, and a migration path from SQLite to PostgreSQL mean that a facility that starts with OpenYield in development can scale it to production without rebuilding their data pipeline.

3. **No proprietary data required.** The synthetic data generator produces realistic defect maps — Poisson-distributed defect counts with Gaussian spatial clustering — from configurable substrate profiles without requiring access to real inspection data. This enables academic researchers to work with OpenYield without access to fab data under NDA.

4. **Interpretable, auditable outputs.** Every analysis module produces results that can be traced to specific defect records and explained to a process engineer. The ML classifier uses logistic regression precisely because its coefficients are interpretable; DBSCAN clustering is chosen over k-means because it identifies noise points and does not require a pre-specified cluster count.

### 2.2 System Components

**Database Layer (`db/`)**

OpenYield implements a unified four-table core schema spanning all supported substrate types. The schema design decision — documented in ADR-001 — favors a substrate-agnostic design with a `substrate_type` discriminator over separate per-substrate schemas. This enables cross-substrate analytics (comparing defect trends across a glass panel line and a wafer line in the same facility) without ETL pipelines or JOIN complexity.

The database layer (`db/connection.py`) provides a backend-agnostic connection factory that returns a standard DB-API 2.0 connection for either SQLite or PostgreSQL. A two-function abstraction (`get_placeholder()`, `is_postgres()`) contains all backend-specific SQL syntax, ensuring that no module above the database layer needs to branch on backend type.

**Ingestion Layer (`ingestion/`)**

The ingestion layer implements idempotent upserts across all four tables: `INSERT OR IGNORE` for SQLite and `INSERT ... ON CONFLICT DO NOTHING` for PostgreSQL. Re-running ingestion on the same data never creates duplicate records — a property that is essential for production fab environments where inspection data is periodically re-exported for reprocessing.

Two file format adapters are implemented:

- **`CsvAdapter`**: Parses the OpenYield generic CSV format. Column order is header-driven (not positional). Validates all required columns at parse time.
- **`KlarfAdapter`**: Parses KLARF 1.x ASCII format. Reads `DefectRecordSpec` to determine column order dynamically. Handles multi-wafer files, unit conversion, and configurable class maps.

Both adapters inherit from `BaseAdapter`, which defines the `NormalizedDefect` canonical record type and the `parse()` contract. Adding support for a new file format requires only a new adapter subclass; the ingestion pipeline and database layer require no changes.

**Synthetic Data Generator (`synthetic/`)**

The generator (`synthetic/generator.py`) produces realistic defect datasets parameterized by `SubstrateProfile` dataclasses defined in `synthetic/substrate_profiles.py`. For each substrate type, the profile encodes:

- Component grid pitch (mm)
- Defect count distribution (Poisson λ per component)
- Spatial clustering parameters (number of cluster centers, cluster spread)
- Edge exclusion geometry (wafer only)
- Region assignment logic (zone_center / zone_mid / zone_edge for wafers; NW/NE/SW/SE for glass panels)
- Cross-system matching distance threshold

The generator supports configurable random seeds, making datasets fully reproducible. The image generator (`synthetic/image_generator.py`) produces 64×64 grayscale PNG patches for each defect, using morphological operations parameterized by defect type to produce visually distinct defect signatures.

**Validation Suite (`validation/`)**

The 10-check validation suite (`validation/checks.py`) runs a comprehensive data quality assessment against the database:

| Check | What It Verifies |
|---|---|
| `row_count:panels` | Total panel count (baseline integrity) |
| `row_count:components` | Total component count (baseline integrity) |
| `row_count:defects` | Total defect count (baseline integrity) |
| `row_count:files` | File tracking integrity |
| `duplicate_defects` | Near-duplicate defects (spatial deduplication) |
| `orphan_defects` | Defects with no matching component record (referential integrity) |
| `component_coverage` | Panels with incorrect component count vs. declared grid |
| `confidence_range` | Confidence scores outside [0.0, 1.0] |
| `system_balance` | system_b count exceeding system_a (anomalous) |
| `match_symmetry` | match_ids appearing in only one system (broken cross-system pair) |

**Yield Engine (`yield_engine/`)**

Three industry-standard yield models are implemented and reported simultaneously per panel:

- **Poisson model**: `Y = exp(−A·D₀)`. Conservative lower bound. Appropriate for random defect distributions.
- **Murphy model**: `Y = ((1−exp(−A·D₀)) / (A·D₀))²`. Assumes triangular defect density distribution. More realistic for production processes.
- **Negative Binomial model (Seeds/Stapper)**: `Y = (1 + A·D₀/α)^(−α)`. Most accurate for clustered defects. Parameterized by clustering factor α.

The clustering parameter α is estimated empirically from observed per-die defect variance using the method of moments. When the observed distribution is Poisson-like, the model automatically selects α = 50 (near-random limit). A recommended model is selected per panel based on substrate type, empirical α, and the A·D₀ product, with a human-readable explanation.

The design rationale for implementing all three models simultaneously — rather than selecting one — is documented in ADR-005. Briefly: reporting all three allows the process engineer to assess model sensitivity, and the spread between Poisson and Negative Binomial estimates quantifies yield uncertainty due to defect clustering.

**Analysis Layer (`analysis/`)**

Six analysis modules provide process control and root cause investigation capabilities:

- **`clustering.py`**: DBSCAN spatial clustering (pure Python, no sklearn). Classifies defect patterns as `random`, `systematic`, or `excursion`. Defaults epsilon to the substrate profile match distance threshold.
- **`lot_tracker.py`**: Lot-level yield summary and excursion detection. Aggregates per-panel yield estimates and flags lots where any panel is more than 2σ above the lot mean defect density.
- **`pareto.py`**: Yield-weighted Pareto analysis. Ranks defect types by `count × avg_size × avg_confidence`. Provides overall, per-zone, and system-comparison (system_a vs. system_b) Pareto views.
- **`spc.py`**: Statistical Process Control with four chart types — Shewhart X-bar (Western Electric rules WE1–WE4), EWMA (λ=0.2, L=3.0), CUSUM, and I-MR. Persists alarm records with severity classification.
- **`correlation.py`**: Wafer-to-wafer repeated defect correlation. Identifies die coordinates that show repeated defects across multiple panels, indicating systematic sources (reticle defects, chuck contamination, mask defects).
- **`signatures.py`**: Spatial pattern signature library. Matches observed defect spatial distributions against a library of known process-driven failure signatures (edge ring wear, scratch, center spot, quadrant asymmetry, reticle repeat).

**AI Module (`ai/`)**

The multinomial logistic regression classifier (`ai/classifier.py`) predicts defect type from 14 tabular features extracted from defect records. The classifier is implemented in approximately 250 lines of pure Python with no external ML framework dependency.

Key design decisions (documented in ADR-006):

- **Interpretable per-feature coefficients**: A fab engineer can inspect the weight matrix to understand why a defect was assigned a particular class.
- **Convex loss with deterministic convergence**: The softmax cross-entropy objective has a unique global minimum. Given fixed hyperparameters and data, training converges to the same weights on every run — a reproducibility requirement for technical evidence.
- **No GPU requirement**: The classifier trains to convergence in under 10 seconds on a standard workstation.

The model is persisted to the `model_registry` table as a JSON-serialized coefficient matrix, enabling model versioning and audit trail.

**REST API (`api/`)**

The FastAPI application (`api/main.py`) provides 39 routes across eight domains:

| Domain | Routes | Key Capabilities |
|---|---|---|
| Panels | 3 | List, retrieve, get components |
| Defects | 1 | Cross-panel defect query |
| Yield | 4 | List, retrieve, calculate, calculate-all |
| Ingestion | 2 | Upload CSV, list tracked files |
| Validation | 1 | Run full validation suite |
| Analysis | 7 | Cluster, lot track, Pareto, SPC, correlation, signatures |
| AI | 3 | Train classifier, predict panel, evaluate |
| Images | 4 | Generate, list, retrieve, delete |

All routes are documented via OpenAPI/Swagger at `/docs`. The API supports both SQLite and PostgreSQL backends via the same connection factory used by the pipeline.

---

## 3. Implementation Quality

### 3.1 Test Suite

OpenYield has **314 passing tests** organized across 13 test modules covering all components from unit tests on individual functions to end-to-end integration tests that run the full pipeline from synthetic data generation through yield calculation and validation.

Test coverage includes:
- All three yield models and α estimation edge cases
- Idempotent upsert behavior (re-run produces no duplicate records)
- KLARF parser with multi-wafer files, unit variations, and malformed records
- DBSCAN correctness against known cluster configurations
- SPC alarm detection for all four chart types
- Classifier training convergence and prediction accuracy
- Image generation for all defect types and substrate classes

### 3.2 Code Quality

The codebase follows consistent conventions throughout:
- `from __future__ import annotations` for forward reference compatibility
- Explicit type annotations on all public function signatures
- Dataclasses for all result types (`YieldEstimate`, `ClusterResult`, `TrainingResult`, `Prediction`)
- Logging via `logging.getLogger(__name__)` in every module
- No mutable default arguments; no global state outside logging configuration

### 3.3 Dependency Minimalism

Mandatory dependencies are limited to NumPy (for Gaussian distribution sampling in the synthetic generator) and FastAPI/uvicorn (for the REST API). All analysis algorithms — DBSCAN, SPC charts, Pareto ranking, yield models, logistic regression — are implemented in pure Python. PostgreSQL support, development tools (pytest, ruff), and optional backend dependencies are isolated to optional dependency groups in `pyproject.toml`.

---

## 4. Beneficiary Impact and CHIPS Act Relevance

### 4.1 Direct Beneficiary Categories

**Domestic glass substrate manufacturers** (e.g., Corning, AGC, Nippon Electric Glass U.S. operations): OpenYield provides the defect inspection pipeline — AOI and confocal review ingestion, spatial clustering, Pareto analysis — that glass panel manufacturers require for quality control at production scale. The glass panel substrate profile encodes the AOI inspection parameters and OLED-relevant defect taxonomy.

**Silicon wafer fabs** (TSMC Arizona, Samsung Texas, Intel Ohio/Arizona/Oregon, Micron Idaho/Virginia): OpenYield provides KLARF ingestion from optical scanner and e-beam review tools, Negative Binomial yield modeling for clustered defects at advanced nodes, edge-exclusion-aware die counting, and wafer-to-wafer correlation for systematic defect detection.

**National laboratories** (Sandia National Laboratories, NREL, MIT Lincoln Laboratory, Argonne National Laboratory): OpenYield provides a reproducible, open-source toolchain for inspection research. The synthetic data generator requires no proprietary data, and the Apache 2.0 license permits unrestricted research use and publication.

**Academic ML researchers**: OpenYield provides labeled synthetic defect datasets for training and benchmarking ML classifiers. The reference implementation of multinomial logistic regression trained on defect features serves as a reproducible baseline for comparison against more advanced models.

**SEMI consortium members**: The KLARF adapter provides a reference implementation for KLARF 1.x format compliance testing. The open-source nature of the implementation supports the SEMI standards interoperability objective.

**Defense and aerospace electronics producers**: Glass panel and PCB substrate support, combined with the dual-system inspection model matching defense inspection workflow architecture, makes OpenYield applicable to flat-panel display inspection for defense avionics and aerospace electronics.

### 4.2 Economic Impact Estimate

Enterprise yield management platform licensing costs range from $500,000 to $2,000,000 per site annually for large fabs, and $50,000 to $200,000 per year for smaller facilities. The CHIPS Act funds at least 15 new domestic semiconductor facilities that will require yield management infrastructure. OpenYield, as open-source infrastructure, eliminates this cost for facilities that adopt it, redirecting capital to equipment, talent, and process development.

For academic and national laboratory users — who typically cannot access enterprise yield management software at any cost — OpenYield enables capabilities that were previously unavailable to the research community.

### 4.3 Knowledge Transfer and Workforce Development

OpenYield's complete, documented implementation of yield management algorithms serves as a technical reference for semiconductor engineering education. The codebase covers:

- Industry-standard yield models (Poisson, Murphy, Negative Binomial) with references to the original publications
- KLARF format parsing, documented against the KLARF 1.x specification
- DBSCAN spatial clustering with a full algorithm implementation traceable to the Ester et al. 1996 paper
- Statistical process control charts (Shewhart, EWMA, CUSUM, I-MR) with correct alarm rule implementations

This makes OpenYield a learning resource for semiconductor engineers, graduate students, and domain practitioners entering the workforce as part of the CHIPS Act talent development initiative.

---

## 5. Roadmap and Future Development

OpenYield Phase 1 (current) establishes the core data platform. Phases 2 and 3 are defined in the project roadmap (`docs/roadmap.md`).

**Phase 2** (2025–2026): Advanced defect inspection capabilities
- Convolutional neural network classifier trained on synthetic defect images
- Wafer map visualization API (defect density heatmaps, clustering overlays)
- Critical area extraction for advanced-node yield modeling
- Multi-lot trend analysis and process drift detection

**Phase 3** (2026–2027): Integration and ecosystem development
- Spatial yield prediction integrating yield models over within-wafer defect density maps
- Lot genealogy tracking (equipment-to-defect attribution)
- KLARF 2.0 binary format support
- Integration connectors for open-source MES platforms (OpenMES, Apache OFBiz)

---

## 6. Conclusion

OpenYield demonstrates that production-grade semiconductor inspection data infrastructure can be built as open-source software and made freely available to the domestic manufacturing community. The platform addresses the infrastructure gap that limits adoption of yield management capabilities by CHIPS Act beneficiaries who cannot access enterprise tools.

The technical contributions described in this whitepaper — the unified substrate-agnostic schema, the clean-room KLARF parser, the pure-Python DBSCAN implementation, the multi-model yield engine with empirical clustering-alpha estimation, and the interpretable logistic regression classifier — represent original engineering work by Yeonkuk Woo with direct application to the domestic semiconductor manufacturing sector.

OpenYield is released under the Apache License 2.0, ensuring that its infrastructure remains freely available to the U.S. semiconductor manufacturing ecosystem without restriction.

---

## References

1. C.H. Stapper, "Modeling of Integrated Circuit Defect Sensitivities," *IBM Journal of Research and Development*, 27(6):549–557, 1983.
2. W. Maly, "Modeling of Lithography Related Yield Losses for CAD of VLSI Circuits," *IEEE Transactions on Computer-Aided Design*, 4(3):166–177, 1985.
3. M. Ester, H.-P. Kriegel, J. Sander, X. Xu, "A Density-Based Algorithm for Discovering Clusters in Large Spatial Databases with Noise," *Proceedings of the 2nd International Conference on Knowledge Discovery and Data Mining (KDD-96)*, pp. 226–231, 1996.
4. SEMI Standard E10-0211, *Specification for Definition and Measurement of Equipment Reliability, Availability, and Maintainability (RAM)*.
5. KLA-Tencor, *KLARF File Format Specification*, Version 1.x, 2001.
6. CHIPS and Science Act of 2022, Public Law 117-167, 136 Stat. 1366.

---

*This document is a technical exhibit prepared in support of an EB-2 National Interest Waiver petition filed on behalf of Yeonkuk Woo. All technical claims are verifiable against the OpenYield source code repository.*
