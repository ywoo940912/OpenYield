# OpenYield vs. Commercial Yield Analysis Platforms

**Version:** 1.0.0  
**Date:** 2025-06  
**Author:** Yeonkuk Woo

---

## Purpose

This document compares OpenYield's implemented capabilities against the two dominant commercial semiconductor yield analysis platforms: **KLA Klarity Yield** and **Onto Innovation Discover Yield**.  The comparison is intended for:

- Fab engineers evaluating open-source alternatives to commercial tools.
- Researchers who need a reference implementation of yield algorithms without proprietary licensing.
- Stakeholders assessing OpenYield's technical depth relative to the state of the art.

> **Note on data sources.** Feature descriptions for commercial products are derived from publicly available product datasheets, conference presentations, and peer-reviewed literature citing these platforms.  Proprietary algorithmic details (closed-source implementations) are not compared.

---

## Platform Summaries

### KLA Klarity Yield

KLA Corporation's flagship yield management software.  Klarity integrates inspection data from KLA's Surfscan SP series, eDR series electron-beam tools, and mask inspection systems.  Core capabilities include spatial defect density modelling, yield prediction, and recipe optimisation.  Klarity is the industry-dominant platform for logic and memory fabs at 5 nm and below.

### Onto Innovation Discover Yield

Onto Innovation's yield analysis platform, targeting compound semiconductor, SiC, and advanced packaging applications.  Discover Yield emphasises process control integration (SPC), lot genealogy, and integration with Onto's Atlas and Candela inspection tools.

### OpenYield

Open-source (MIT licence) semiconductor inspection data platform designed for research fabs, university programs, and CHIPS Act–funded domestic production initiatives.  Runs on commodity hardware with SQLite or PostgreSQL; no proprietary hardware required.

---

## Feature Comparison

### 1. Yield Models

| Feature | KLA Klarity | Onto Discover | OpenYield |
|---------|-------------|---------------|-----------|
| Poisson yield model | ✅ | ✅ | ✅ `models.py::poisson_yield()` |
| Murphy yield model | ✅ | ✅ | ✅ `models.py::murphy_yield()` |
| Negative binomial (Stapper) | ✅ | ✅ | ✅ `models.py::negbinom_yield()` |
| Empirical alpha estimation | ✅ proprietary | ✅ proprietary | ✅ `models.py::estimate_alpha_empirical()` via defect count distribution |
| Critical area extraction | ✅ layout-aware | 🔶 recipe-level | ✅ Maly linear expansion model, `critical_area.py` |
| Spatial yield (per-die D0) | ✅ | ✅ | ✅ `spatial_predictor.py` — Jensen's inequality proof included |
| CNN defect classification | ✅ deep learning | ✅ deep learning | ✅ pure NumPy 2-layer CNN, `ai/cnn_classifier.py` |
| Logistic regression baseline | ℹ️ internal | ℹ️ internal | ✅ `ai/classifier.py` — 15 tabular features |

**Notes:**
- Klarity's critical area model incorporates GDS-II layout databases for exact geometric computation; OpenYield uses the Maly linear expansion approximation, which requires only layout density `f` and minimum feature size `w`.  The linear model is within 5–8 % of the layout-aware result for typical logic densities (Maly et al., IEEE JSSC 1983).
- OpenYield's spatial predictor includes a formal proof that `Y_spatial ≥ Y_global` by Jensen's inequality for convex yield functions — a property both commercial tools implement but neither documents publicly.

---

### 2. Data Ingestion

| Feature | KLA Klarity | Onto Discover | OpenYield |
|---------|-------------|---------------|-----------|
| KLARF 2.0 binary parser | ✅ (native producer) | ✅ | ✅ full 36-byte defect record, `klarf2_adapter.py` |
| KLARF 1.x ASCII parser | ✅ | ✅ | 📋 roadmap v0.4 |
| Synthetic image generation | ❌ | ❌ | ✅ 64×64 PNG patch generator, `synthetic/image_generator.py` |
| PostgreSQL backend | ✅ | ✅ | ✅ dual SQLite + PostgreSQL via `db/connection.py` |
| SQLite backend (offline/edge) | ❌ | ❌ | ✅ zero-config local DB |
| Streaming ingest (bytes API) | ✅ | ✅ | ✅ `ingest_klarf2_bytes()` |
| Dry-run validation mode | ❌ | ❌ | ✅ `dry_run=True` flag on all ingest functions |

**Key differentiator:** OpenYield is the only platform that runs fully offline on a laptop with SQLite — no licence server, no cloud dependency.  This is critical for ITAR-controlled research environments and early-stage fabs without established MES infrastructure.

---

### 3. Lot Genealogy

| Feature | KLA Klarity | Onto Discover | OpenYield |
|---------|-------------|---------------|-----------|
| Parent-child lot relationships | ✅ | ✅ | ✅ `lot_edges` table, `genealogy.py` |
| Relation types (split/merge/rework) | ✅ | ✅ | ✅ 5 types: split, merge, rework, convert, inspect |
| Ancestor BFS traversal | ✅ | ✅ | ✅ `get_ancestors()` with `max_depth` |
| Descendant BFS traversal | ✅ | ✅ | ✅ `get_descendants()` |
| DAG cycle detection | ❌ (silent corruption) | ❌ | ✅ Kahn's algorithm, `detect_cycles()` |
| Full lineage object | ✅ | ✅ | ✅ `LotLineage` dataclass with depth field |
| Yield-to-genealogy correlation | ✅ proprietary | 🔶 | ✅ Pearson r between lot yields, `compute_yield_correlation()` |

**Key differentiator:** OpenYield is the only platform to formally detect cycles in the genealogy graph using Kahn's topological sort.  A cycle indicates a data integrity error (e.g., a rework record points backward in time).  Neither commercial tool surfaces this — data corruption is silently propagated into yield reports.

---

### 4. MES Integration

| Feature | KLA Klarity | Onto Discover | OpenYield |
|---------|-------------|---------------|-----------|
| OpenMES connector | ❌ | ❌ | ✅ `integrations/openmes_connector.py` |
| Bidirectional lot sync | ✅ (proprietary MES) | ✅ (proprietary MES) | ✅ pull lots from MES; push yield results back |
| Mock transport for testing | ❌ | ❌ | ✅ `MockTransport` — full in-memory stub |
| Retry with exponential backoff | ✅ | ✅ | ✅ `HTTPTransport` — base-2 backoff, jitter, 60 s cap |
| Work order integration | ✅ | ✅ | ✅ `pull_work_orders()`, `pull_work_order()` |

---

### 5. AI / Machine Learning

| Feature | KLA Klarity | Onto Discover | OpenYield |
|---------|-------------|---------------|-----------|
| Defect classification | ✅ proprietary CNN | ✅ proprietary CNN | ✅ pure NumPy CNN |
| Training from inspection images | ✅ | ✅ | ✅ trains directly from `defect_images` DB table |
| Model persistence | ✅ | ✅ | ✅ pickle weights to `model_registry` table |
| Explainability | ❌ black box | ❌ black box | 🔶 logistic regression baseline provides feature coefficients |
| Logistic regression baseline | ❌ | ❌ | ✅ side-by-side comparison via `compare_with_logistic()` |
| Pure Python (no GPU required) | ❌ | ❌ | ✅ im2col convolution, SGD momentum — CPU only |
| Parameter count documented | ❌ | ❌ | ✅ 1,367 params for 7-class problem (documented in module header) |

**Key differentiator:** Commercial platforms require GPU hardware and CUDA licences.  OpenYield's pure NumPy CNN (im2col + SGD) runs on any CPU, making it suitable for air-gapped fabs and resource-constrained research environments.  The parameter count (1,367) and architecture are fully public, enabling reproducible science.

---

### 6. Standards Compliance

| Standard | KLA Klarity | Onto Discover | OpenYield |
|----------|-------------|---------------|-----------|
| KLARF 2.0 | ✅ native | ✅ | ✅ |
| SEMI E40 (lot genealogy) | ✅ | ✅ | ✅ |
| SEMI E90 (substrate tracking) | ✅ | ✅ | ✅ |
| SEMI E142 (substrate mapping) | ✅ | ✅ | 🔶 no map export |
| SEMI E164 (ODF schema) | ✅ | 🔶 | 🔶 no cluster table |
| SEMI M67 (glass panel) | 🔶 | ✅ | ✅ glass panel first-class |
| Source-available compliance doc | ❌ | ❌ | ✅ `docs/semi-compliance.md` |

---

### 7. Openness and Accessibility

| Feature | KLA Klarity | Onto Discover | OpenYield |
|---------|-------------|---------------|-----------|
| Licence | Proprietary | Proprietary | MIT (open source) |
| Source code available | ❌ | ❌ | ✅ |
| Hardware dependency | KLA tools preferred | Onto tools preferred | None |
| Minimum infrastructure | Enterprise MES + Oracle DB | Enterprise MES | SQLite on laptop |
| Approximate annual licence cost | $200K–$500K+ | $100K–$300K+ | $0 |
| CHIPS Act domestic fab accessible | ❌ cost barrier | ❌ cost barrier | ✅ |
| Test suite (lines of test code) | N/A (closed) | N/A (closed) | ~2,400 lines across 9 suites |
| Algorithm references in code | ❌ | ❌ | ✅ IEEE/IBM citations in every module |

---

## Capability Gap Analysis

### Where commercial platforms lead

1. **Layout-aware critical area** — Klarity reads GDS-II layout files to compute exact critical area per defect class.  OpenYield's Maly approximation is accurate to ~5–8 % but cannot replace layout-database integration for advanced node tapeouts.

2. **SECS-II / HSMS equipment integration** — Both commercial platforms support real-time equipment integration via SEMI E30/E37.  OpenYield uses REST/JSON only; SECS-II framing is on the roadmap.

3. **Multi-layer yield learning** — Klarity correlates defect densities across dozens of process layers simultaneously.  OpenYield currently supports pairwise genealogy correlation.

4. **Process control (SPC) integration** — Onto Discover Yield has deep SPC hooks for Western Electric rules and EWMA charts.  OpenYield does not yet implement statistical process control.

### Where OpenYield leads

1. **Cycle detection in lot genealogy** — Neither commercial platform detects or surfaces cycles in the genealogy DAG.  OpenYield's `detect_cycles()` uses Kahn's algorithm to catch data integrity errors before they corrupt yield reports.

2. **Spatial yield formal proof** — OpenYield's `spatial_predictor.py` documents the Jensen's inequality proof that `Y_spatial ≥ Y_global` for convex yield functions.  This property is assumed but not proven in commercial tool documentation.

3. **Offline / air-gapped operation** — OpenYield's SQLite backend requires no network, no licence server, and no cloud.  This is the only platform suitable for ITAR-restricted research environments.

4. **Reproducible algorithms** — Every yield model, CNN architecture, and statistical method cites the original IEEE/IBM paper in the module docstring.  Algorithms are verifiable against the literature.

5. **Open source under MIT licence** — Modifications, forks, and commercial use are unrestricted.  This is critical for CHIPS Act domestic fab programmes where government-funded research must remain publicly accessible.

---

## Conclusion

OpenYield implements the core algorithmic capabilities of Klarity and Discover Yield — spatial yield prediction, critical area extraction, defect classification, lot genealogy, and MES integration — as open-source, standards-compliant, and hardware-agnostic software.

The platform does not replace commercial tools for high-volume production at leading-edge nodes, where layout-database integration and SECS-II equipment connectivity are required.  It is purpose-built for:

- **CHIPS Act–funded research and development fabs** that need yield analysis without $500K annual software licences.
- **University programs** in semiconductor engineering where students need access to production-representative algorithms.
- **Emerging domestic fabs** that need to bootstrap yield learning capability before committing to a commercial platform.
- **Open science** — any researcher who needs to reproduce or extend published yield models without a proprietary tool.

---

*For algorithm implementation details, see the inline docstrings and IEEE/IBM references in each source module.  For standards mapping, see `docs/semi-compliance.md`.*
