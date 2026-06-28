# ADR-001: Substrate-Agnostic Unified Database Schema

**Author:** Yeonkuk Woo
**Status:** Accepted
**Date:** 2024-11-01

---

## Context

Semiconductor inspection data platforms are typically designed for a single substrate class. KLA Klarity targets silicon wafers. Orbotech targets flat-panel display glass. This substrate-specific architecture forces domestic manufacturers who operate across substrate classes — common in defense and aerospace supply chains — to maintain separate data systems with no shared tooling or cross-substrate analytics.

OpenYield serves multiple beneficiary categories including domestic glass substrate manufacturers, silicon wafer fabs, and defense-sector PCB producers. A core design question arose early: should the schema be substrate-specific (three separate schemas) or substrate-agnostic (one unified schema with a substrate_type discriminator)?

---

## Decision

OpenYield uses a single four-table core schema (`panels`, `components`, `defects`, `files`) with a `substrate_type` column as a discriminator. All substrate types share the same schema. Substrate-specific behavior is encoded entirely in `SubstrateProfile` dataclasses in the application layer; the database itself contains no substrate-specific columns, tables, or branching.

The `substrate_type` column is constrained to an enumerated set (`glass_panel`, `wafer`) enforced by a `CHECK` constraint at the database level.

---

## Rationale

**1. Cross-substrate analytics become possible without schema joins.**
A fab running both wafer and glass panel lines (e.g., a compound semiconductor producer or an integrated display manufacturer) can compare defect density trends across substrate types with a single query. With separate schemas, such comparisons require ETL pipelines that introduce latency and synchronization risk.

**2. A single ingestion pipeline handles all substrate types.**
The adapter layer (`ingestion/adapters/`) produces `NormalizedDefect` records that map to the same schema regardless of substrate. Adding a new substrate type requires defining a `SubstrateProfile` — it does not require schema migrations or new ingestion code paths.

**3. The yield engine operates on database records, not substrate-specific objects.**
All three yield models (Poisson, Murphy, Negative Binomial) accept `A` and `D₀` as inputs, which are derived from `defects` and `components` records using the same query for all substrate types. Substrate-specific behavior (e.g., edge exclusion for wafers) is applied at data generation time and persisted in `components.active`, not computed at query time.

**4. National laboratory and academic users benefit from schema stability.**
Researchers building ML pipelines on OpenYield data need a stable, consistent schema across datasets. A unified schema means a model trained on glass panel data and a model trained on wafer data share the same feature extraction code (`ai/classifier.py:_extract_features()`).

---

## Consequences

- `substrate_type` must be populated correctly at ingestion time. The validation suite check `component_coverage` indirectly validates this by verifying that the component grid matches the declared `rows × cols`.
- Spatial parameters (pitch, edge exclusion radius, region assignments) differ by substrate and are not stored in the database. They are re-derived from `SubstrateProfile` at analysis time. This is acceptable because the profiles are stable, versioned constants.
- The `CHECK (substrate_type IN ('glass_panel', 'wafer'))` constraint in SQLite does not prevent adding future substrate types; it requires a schema migration. This is the correct trade-off: schema constraints enforce correctness in production; new substrate types are rare and warrant a deliberate migration.

---

## Alternatives Considered

**Separate tables per substrate type** (`wafer_panels`, `glass_panels`, etc.): Rejected. Cross-substrate queries require UNION operations. Adding a substrate type requires schema changes to every table. Foreign key integrity becomes difficult to maintain across sibling tables.

**Inheritance via a base table and substrate-specific extension tables**: Rejected. This pattern (table-per-type inheritance) adds JOIN complexity to every defect query with no benefit for the current substrate set. The discriminator column approach is simpler and performs better for the query patterns OpenYield uses.

**Storing substrate profiles in the database**: Considered. Rejected because substrate profiles are engineering constants that do not change at runtime. Storing them in the database creates a dependency: code and database must be kept in sync. Encoding them in versioned Python dataclasses provides a single source of truth with full test coverage.
