# ADR-002: SQLite as Development Backend with PostgreSQL Production Path

**Author:** Yeonkuk Woo
**Status:** Accepted
**Date:** 2024-11-08

---

## Context

OpenYield is designed to serve beneficiaries across a wide range of operational contexts: a graduate research lab running a single-node workstation, a national laboratory running a shared server, and a production fab running a cloud-hosted database cluster. These contexts have fundamentally different database requirements.

The project has no heavyweight infrastructure dependency. Requiring a running PostgreSQL instance for development or testing would create a significant barrier to adoption for academic users and individual researchers — the two categories most likely to evaluate an open-source tool before organizational procurement.

At the same time, a production fab deployment cannot rely on SQLite. SQLite does not support concurrent multi-writer access, lacks connection pooling, and has no replication path.

---

## Decision

OpenYield supports two database backends selected at runtime by the `DB_BACKEND` environment variable:

- **SQLite** (default): zero-configuration, file-based, suitable for development, testing, and single-user deployments
- **PostgreSQL**: production backend for multi-user and cloud-hosted deployments

A single connection factory (`db/connection.py:get_connection()`) returns a connection for either backend. All query code uses a **parameter placeholder abstraction** (`get_placeholder(conn)`) that returns `?` for SQLite and `%s` for PostgreSQL. All upsert logic uses a **backend discriminator** (`is_postgres(conn)`) to select between `INSERT OR IGNORE` (SQLite) and `INSERT ... ON CONFLICT DO NOTHING` (PostgreSQL).

No module other than `db/connection.py` needs to branch on backend type.

---

## Rationale

**1. Eliminating infrastructure dependency lowers adoption cost for the CHIPS Act beneficiary base.**
Domestic glass substrate manufacturers, national laboratories, and academic ML researchers evaluating OpenYield should be able to run `pytest` and `python run_pipeline.py` immediately after `pip install -e .`. A PostgreSQL dependency at the development level would add 10–30 minutes of setup time and require database administration knowledge that is outside the scope of many research users.

**2. The DB-API 2.0 interface is the correct abstraction boundary.**
Both `sqlite3` (standard library) and `psycopg2` implement the Python DB-API 2.0 specification. The parameter placeholder and upsert syntax are the only points of divergence for the query patterns OpenYield uses. Wrapping these two points in `get_placeholder()` and `is_postgres()` contains all backend-specific logic in two functions totaling approximately 20 lines.

**3. SQLite WAL mode provides safe concurrent reads in development.**
`PRAGMA journal_mode=WAL` enables multiple simultaneous read connections while a write is in progress. This is sufficient for the development use case of a single writer (the pipeline) and multiple readers (the API, test queries).

**4. The migration path is explicit and tested.**
`db/migrate_sqlite_to_postgres.py` provides a one-time migration for users who begin on SQLite and need to move to PostgreSQL as their data volume grows. This preserves the zero-configuration entry point without stranding users.

---

## Consequences

- All tests run against SQLite in memory or against a temporary file. This means tests do not verify PostgreSQL-specific behavior (e.g., `TIMESTAMPTZ` semantics, connection pool behavior under concurrent load). This is an accepted limitation; PostgreSQL-specific integration testing is deferred to a CI environment with a live database.
- The `get_placeholder()` function uses a runtime type check (`isinstance(conn, psycopg2.extensions.connection)`) rather than a stored flag. This is correct: the connection object itself is the authoritative source of backend identity.
- `FOREIGN KEY` enforcement requires `PRAGMA foreign_keys=ON` in SQLite, where it is off by default. The connection factory sets this pragma on every SQLite connection, ensuring referential integrity is enforced consistently across backends.

---

## Alternatives Considered

**SQLAlchemy ORM**: Rejected. SQLAlchemy would abstract away the backend difference but adds a dependency, an ORM learning curve, and session management complexity. The query patterns in OpenYield are simple enough that raw SQL with a placeholder abstraction is maintainable and more transparent to contributors from a manufacturing-engineering background.

**SQLite only**: Rejected for production use. SQLite's write serialization would be a bottleneck for a fab running continuous batch ingestion from multiple inspection tools simultaneously.

**PostgreSQL only**: Rejected. Eliminates the zero-configuration development path that is essential for academic and research adoption — a primary beneficiary category under the CHIPS Act framing.

**DuckDB**: Considered as a SQLite alternative with better analytical query performance. Deferred to a future ADR. DuckDB's OLAP orientation is well-suited to the Pareto and SPC query patterns, but its transactional write behavior for the ingestion path needs evaluation.
