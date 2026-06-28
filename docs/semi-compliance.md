# OpenYield — SEMI Standards Compliance Reference

**Version:** 1.0.0  
**Date:** 2025-06  
**Author:** Yeonkuk Woo  
**Project:** OpenYield — Open-Source Semiconductor Inspection Data Platform

---

## Overview

This document maps OpenYield's implemented features to the SEMI standards that govern semiconductor inspection data interchange, lot tracking, substrate identification, and yield analysis.  It is intended for:

- **Fab integration engineers** evaluating OpenYield's compatibility with existing MES / equipment infrastructure.
- **Compliance auditors** verifying alignment with industry interchange standards before adopting OpenYield in a production line.
- **Researchers and open-source contributors** who need a normative reference for the data models, file formats, and API conventions used throughout the codebase.

Standards coverage is classified as:

| Symbol | Meaning |
|--------|---------|
| ✅ | Fully implemented |
| 🔶 | Partially implemented (known gaps listed) |
| 📋 | Planned (roadmap item) |
| ℹ️  | Informatively referenced (not required for OpenYield's use case) |

---

## 1. SEMI E10 — Equipment Reliability, Availability, and Maintainability (RAM)

**Full title:** SEMI E10-1112, *Specification for Definition and Measurement of Equipment Reliability, Availability, and Maintainability (RAM)*

**Relevance to OpenYield:** Inspection tool downtime directly affects defect density measurements — a tool that enters maintenance state mid-lot produces incomplete defect maps that bias yield estimates.  OpenYield's data ingestion layer must correctly handle partial inspection data.

| Requirement | OpenYield Feature | Status |
|-------------|-------------------|--------|
| E10 §5.2 — Equipment states (productive/standby/unscheduled downtime) | `ingestion/adapters/klarf2_adapter.py` skips incomplete WAFER\_INFO blocks and logs a warning rather than silently dropping defect data | ✅ |
| E10 §6.1 — Partial lot processing must be flagged | KLARF 2.0 parser warns when `num_defects` in WAFER\_INFO exceeds actual records in the paired DEFECT\_LIST block (`_parse_defect_list`, line ~174) | ✅ |
| E10 §8 — RAM metrics reporting | Not in scope for yield analysis platform | ℹ️ |

---

## 2. SEMI E30 — Generic Equipment Model (GEM)

**Full title:** SEMI E30-0618, *Generic Model for Communications and Control of SEMI Equipment (GEM)*

**Relevance to OpenYield:** GEM defines the lot and substrate attribute schema that KLA and other inspection tools use when posting results to a host computer.  The KLARF 2.0 format embeds GEM lot attributes (lot ID, product ID, equipment ID, recipe ID) in its header blocks.

| Requirement | OpenYield Feature | Status |
|-------------|-------------------|--------|
| E30 §6.2 — Lot ID as primary lot identifier | `lot_id` is the primary key in `panels` and `lot_nodes` tables; all defect queries join on `lot_id` | ✅ |
| E30 §6.3 — Process program (recipe) identification | `recipe_id` field in `MESProcessStep` and `Klarf2SetupInfo` dataclasses | ✅ |
| E30 §6.4 — Equipment ID in lot history | `equipment_id` in `MESProcessStep`; stored in `lot_nodes.metadata_json` | ✅ |
| E30 §8 — SECS-II message framing | Not implemented; OpenYield uses REST/JSON, not SECS | 📋 |
| E30 §10 — Alarm management | Not in scope | ℹ️ |

---

## 3. SEMI E40 — Standard for Processing Management

**Full title:** SEMI E40-0308, *Standard for Processing Management*

**Relevance to OpenYield:** E40 defines lot genealogy semantics — how split, merge, and rework relationships between lots must be recorded and queried.  OpenYield's `analysis/genealogy.py` is directly modelled on E40's lot lifecycle state machine.

| Requirement | OpenYield Feature | Status |
|-------------|-------------------|--------|
| E40 §5 — Lot lifecycle states (queued/active/hold/complete/scrapped) | `MESLot.status` field; synced from OpenMES via `OpenMESConnector.pull_lot()` | ✅ |
| E40 §6.1 — Parent-child lot traceability | `lot_edges` table with `parent_lot_id`, `child_lot_id`, and `relation_type` | ✅ |
| E40 §6.2 — Relation types (split/merge/rework) | `VALID_RELATION_TYPES = {"split","merge","rework","convert","inspect"}` in `genealogy.py` | ✅ |
| E40 §6.3 — Ancestry traversal | `get_ancestors()` — BFS up parent links with optional `max_depth` | ✅ |
| E40 §6.4 — Descendant enumeration | `get_descendants()` — BFS down child links | ✅ |
| E40 §7 — Cycle detection in genealogy graph | `detect_cycles()` — Kahn's topological sort; returns lot IDs in any cycle | ✅ |
| E40 §9 — Work order management | `MESWorkOrder` dataclass; `pull_work_orders()` / `pull_work_order()` in connector | ✅ |

---

## 4. SEMI E90 — Substrate Tracking

**Full title:** SEMI E90-0712, *Specification for Substrate Tracking*

**Relevance to OpenYield:** E90 defines how individual substrates (wafers, glass panels) within a lot are identified and tracked through the fab.  OpenYield's `components` table (rows/cols grid) is the per-substrate tracking layer.

| Requirement | OpenYield Feature | Status |
|-------------|-------------------|--------|
| E90 §4 — Substrate unique ID | `component_row`, `component_col` coordinates within `panel_id` form a unique substrate identifier (surrogate for slot/cassette position) | ✅ |
| E90 §5.1 — Substrate state (active/inactive/scrap) | `components.active` boolean column; edge-excluded dies set to `active=FALSE` | ✅ |
| E90 §5.2 — Substrate location tracking | `components.x_mm`, `components.y_mm` — physical centroid coordinates | ✅ |
| E90 §6 — Lot-to-substrate association | Every defect record references `(panel_id, component_row, component_col)` — three-level hierarchy: lot → substrate → defect | ✅ |

---

## 5. SEMI E142 — Substrate Mapping

**Full title:** SEMI E142-0309, *Specification for Substrate Mapping*

**Relevance to OpenYield:** E142 standardises wafer map coordinate systems, die grid layouts, and exclusion zone definitions.  The yield calculator and spatial predictor both operate on the E142 die grid.

| Requirement | OpenYield Feature | Status |
|-------------|-------------------|--------|
| E142 §4.1 — Cartesian die coordinate system (row, col origin at bottom-left) | `components` table uses (row, col) addressing; `upsert_component()` in `ingestion/ingest.py` | ✅ |
| E142 §4.2 — Edge exclusion zone | `components.active=FALSE` for excluded dies; `spatial_predictor.py` skips inactive dies in yield averaging | ✅ |
| E142 §5.1 — Die pitch (uniform square pitch) | `panels.component_pitch_mm`; wafer default 28 mm, glass panel default 370 mm in `substrate_profiles.py` | ✅ |
| E142 §6 — Pass/fail bin per substrate | `components.active` represents the binary pass/fail outcome; multi-bin support planned | 🔶 |
| E142 §7 — Map interchange format | Wafer map export not yet implemented; internal DB is ground truth | 📋 |

---

## 6. SEMI E164 — ODF (Open Database Format)

**Full title:** SEMI E164-0916, *Specification for Open Database Format for Semiconductor Yield Data*

**Relevance to OpenYield:** ODF defines a relational schema for semiconductor defect and yield data that multiple vendors can import and export.  OpenYield's schema was designed with E164 alignment in mind.

| E164 Entity | OpenYield Table / Column | Status |
|-------------|--------------------------|--------|
| `LOT` | `panels.lot_id` | ✅ |
| `WAFER` | `panels` (one panel = one substrate) | ✅ |
| `DIE` | `components` (row, col, x_mm, y_mm, active) | ✅ |
| `DEFECT` | `defects` (defect_type, x_mm, y_mm, size_mm, confidence, source_system) | ✅ |
| `INSPECTION_RECIPE` | `Klarf2SetupInfo.recipe_id` (in-memory; not persisted to DB) | 🔶 |
| `YIELD_SUMMARY` | `yield_estimates` (yield_poisson, yield_murphy, yield_negbinom, D0, alpha) | ✅ |
| `CLUSTER` | Defect clustering tracked via `defects.cluster_number` from KLARF 2.0; not yet aggregated to a separate cluster table | 🔶 |

---

## 7. KLARF — KLA Results File Format

**Standard:** KLA-Tencor KLARF Specification Rev 2.0 (internal industry standard, widely implemented)

**Relevance to OpenYield:** KLARF is the de-facto standard interchange format for wafer and panel inspection results.  OpenYield implements a full binary KLARF 2.0 parser.

| KLARF 2.0 Block | OpenYield Implementation | Status |
|-----------------|--------------------------|--------|
| `FILE_INFO` | `Klarf2FileInfo` dataclass; station ID, timestamp, inspector version | ✅ |
| `LOT_INFO` | `Klarf2LotInfo` dataclass; lot ID, step ID, device ID, process step | ✅ |
| `SETUP_INFO` | `Klarf2SetupInfo` dataclass; recipe, pixel size, die dimensions, class count | ✅ |
| `WAFER_INFO` | `Klarf2Wafer` dataclass; wafer ID, slot, type, orientation, defect count | ✅ |
| `DEFECT_LIST` | `Klarf2Defect` dataclass; 36-byte binary record: XY, size, class, bin, cluster, confidence | ✅ |
| `SUMMARY` | `Klarf2Summary` dataclass; total wafers, total defects, mean defects per wafer | ✅ |
| KLARF 1.x ASCII | Not implemented; only binary 2.0 format | 📋 |

**Binary encoding conformance:**
- Little-endian byte order (ENDIAN\_MARK = `0x4949`) ✅
- 8-byte magic `KLARF200` ✅
- Block TLV framing: `uint16` type + `uint32` length + data ✅
- Defect record size: exactly 36 bytes per `_DEFECT_STRUCT` assertion ✅
- Unknown block types skipped with `DEBUG`-level log ✅

---

## 8. SEMI M1 — 300 mm Silicon Wafer

**Full title:** SEMI M1-0302, *Standard for 300 mm Polished Single Crystal Silicon*

**Relevance to OpenYield:** M1 defines wafer dimensions and notch orientation conventions that affect die coordinate systems.

| Requirement | OpenYield Feature | Status |
|-------------|-------------------|--------|
| M1 §3.2 — 300 mm diameter | Wafer substrate profile: `component_pitch_mm=28.0` (die pitch); wafer diameter is implicit in the die grid layout | ✅ |
| M1 §3.4 — Flat/notch orientation | `Klarf2Wafer.orientation` field (degrees) from KLARF 2.0; orientation-aware coordinate transforms not yet implemented | 🔶 |

---

## 9. SEMI M67 — Glass Substrate for FPD

**Full title:** SEMI M67-0306, *Specification for Glass Substrates for Flat Panel Displays*

**Relevance to OpenYield:** OpenYield supports glass panel substrates (TFT-LCD, OLED) as a first-class substrate type alongside silicon wafers.

| Requirement | OpenYield Feature | Status |
|-------------|-------------------|--------|
| M67 §4 — Panel dimensions (Gen 8: 2200×2500 mm) | Glass panel substrate profile: `component_pitch_mm=370.0` (TFT die pitch); panel area modelled as die grid | ✅ |
| M67 §5 — Defect type classification | Glass panel defect types: particle, scratch, pinhole, mura, line\_defect, open\_circuit, short\_circuit — matches M67 defect taxonomy | ✅ |
| M67 §6 — Critical dimension (minimum feature) | `SubstrateProfile.min_feature_mm=0.100` for glass; used in Maly critical area extraction | ✅ |

---

## 10. Yield Model Standards Alignment

OpenYield implements the three yield models that appear in the SEMI and academic literature as de facto standards for compound semiconductor and silicon yield prediction.

| Model | Formula | Reference | OpenYield Function |
|-------|---------|-----------|-------------------|
| Poisson | `Y = exp(−A·D₀)` | Seeds (1927); standard baseline | `yield_engine/models.py::poisson_yield()` |
| Murphy | `Y = [(1−exp(−A·D₀))/(A·D₀)]²` | Murphy, *Proc. IEEE*, 52(12), 1964 | `models.py::murphy_yield()` |
| Negative Binomial | `Y = (1 + A·D₀/α)^(−α)` | Stapper, *IBM J. Res. Dev.*, 27(6), 1983 | `models.py::negbinom_yield()` |

**Critical area correction (Maly linear expansion model):**

```
Ac(d)/A = min(1, f × (1 + d/w))
```

where `f` = layout density, `w` = minimum feature size, `d` = defect diameter.
Reference: Maly et al., *IEEE JSSC*, 18(6), 1983.

Implemented in `yield_engine/critical_area.py::compute_critical_area()`.

**Spatial yield (Jensen's inequality):**

```
Y_spatial = mean_i[ Y_model(A_eff, D0_i) ] ≥ Y_model(A_eff, mean(D0_i))
```

Implemented in `yield_engine/spatial_predictor.py::compute_spatial_yield()`.

---

## 11. API Standards

OpenYield's REST API follows conventions aligned with the SEMI Equipment Data Acquisition (EDA) / Interface A specification for data query services.

| Convention | Implementation |
|------------|---------------|
| JSON:API-compatible response envelopes | FastAPI Pydantic response models in `api/schemas.py` |
| Panel/lot/defect URI hierarchy | `/yield/{panel_id}`, `/yield/{panel_id}/critical-area`, `/yield/{panel_id}/spatial` |
| ISO-8601 timestamps | All `created_at`, `trained_at`, `timestamp` fields in UTC |
| Dual SQLite/PostgreSQL backend | `get_placeholder()` abstraction in `db/connection.py` |

---

## 12. Summary Table

| SEMI Standard | Scope | Compliance |
|---------------|-------|-----------|
| E10 (RAM) | Equipment downtime handling in data ingestion | 🔶 Partial |
| E30 (GEM) | Lot / recipe / equipment attributes | 🔶 Partial (no SECS-II) |
| E40 (Process Mgmt) | Lot genealogy, work orders | ✅ Full |
| E90 (Substrate Tracking) | Per-substrate state and location | ✅ Full |
| E142 (Substrate Mapping) | Die grid, edge exclusion, coordinate system | 🔶 Partial (no map export) |
| E164 (ODF) | Yield/defect relational schema | 🔶 Partial (no cluster table) |
| KLARF 2.0 | Binary defect file format | ✅ Full |
| M1 (300 mm Wafer) | Wafer dimensions and orientation | 🔶 Partial (no orientation transform) |
| M67 (Glass Substrate) | FPD panel substrate support | ✅ Full |
| Yield models | Poisson, Murphy, NegBinom, Critical Area, Spatial | ✅ Full |

---

## 13. Known Gaps and Roadmap

| Gap | Priority | Planned Release |
|-----|----------|----------------|
| KLARF 1.x ASCII parser | Medium | v0.4 |
| Multi-bin wafer map export (E142 §7) | Medium | v0.4 |
| Defect cluster table (E164) | Low | v0.5 |
| SECS-II / HSMS transport (E30 / E37) | Low | v0.6 |
| Wafer notch orientation transform (M1 §3.4) | Low | v0.5 |

---

*This compliance reference is maintained alongside the source code.  To verify implementation against a specific standard section, the test suite provides traceability: `tests/test_klarf2_adapter.py`, `tests/test_genealogy.py`, `tests/test_critical_area.py`, and `tests/test_spatial_predictor.py`.*
