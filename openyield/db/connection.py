"""
db/connection.py
----------------
Author: Yeonkuk Woo

Backend-agnostic database connection factory.

Supports SQLite (development / testing) and PostgreSQL (production) through a
unified interface. All other modules import from here — swapping the backend
requires changing only the DB_BACKEND environment variable, not any query code.

Environment variables
---------------------
DB_BACKEND      "sqlite" (default) or "postgres"
DB_PATH         Path to SQLite file  (sqlite only, default: ./inspection.db)
DB_HOST         PostgreSQL host      (postgres only, default: localhost)
DB_PORT         PostgreSQL port      (postgres only, default: 5432)
DB_NAME         PostgreSQL database  (postgres only, default: inspection)
DB_USER         PostgreSQL user      (postgres only)
DB_PASSWORD     PostgreSQL password  (postgres only)
DB_POOL_MIN     Min pool connections (postgres only, default: 1)
DB_POOL_MAX     Max pool connections (postgres only, default: 5)

Usage
-----
    from db.connection import get_connection, Backend

    conn = get_connection()          # uses env vars
    conn = get_connection(backend=Backend.SQLITE, path="./dev.db")
    conn = get_connection(backend=Backend.POSTGRES)
"""

from __future__ import annotations

import os
import sqlite3
import logging
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class Backend(str, Enum):
    SQLITE   = "sqlite"
    POSTGRES = "postgres"


# ---------------------------------------------------------------------------
# Unified connection type hint (both backends share the DB-API 2.0 interface)
# ---------------------------------------------------------------------------

Connection = Any  # sqlite3.Connection | psycopg2.extensions.connection


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------

def _sqlite_connection(path: str | Path) -> sqlite3.Connection:
    """
    Return a configured SQLite connection.
    - WAL journal mode for safe concurrent reads
    - Foreign key enforcement (off by default in SQLite)
    - Row factory for dict-style column access
    """
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    logger.debug("SQLite connection opened: %s", path)
    return conn


# ---------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------

def _pg_connection(
    host: str,
    port: int,
    dbname: str,
    user: str,
    password: str,
) -> Any:
    """
    Return a psycopg2 connection with autocommit OFF (explicit transactions).
    psycopg2 enforces foreign keys by default — no extra config needed.

    RealDictCursor gives dict-style row access, matching sqlite3.Row behaviour.
    """
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError as exc:
        raise RuntimeError(
            "psycopg2 is not installed. Run: pip install psycopg2-binary"
        ) from exc

    conn = psycopg2.connect(
        host=host,
        port=port,
        dbname=dbname,
        user=user,
        password=password,
        cursor_factory=psycopg2.extras.RealDictCursor,
    )
    conn.autocommit = False
    logger.debug("PostgreSQL connection opened: %s@%s:%s/%s", user, host, port, dbname)
    return conn


def _pg_pool(
    host: str,
    port: int,
    dbname: str,
    user: str,
    password: str,
    min_conn: int = 1,
    max_conn: int = 5,
) -> Any:
    """
    Return a psycopg2 ThreadedConnectionPool for multi-threaded / server use.
    Call pool.getconn() / pool.putconn() around each request.
    """
    try:
        from psycopg2 import pool as pg_pool
        import psycopg2.extras
    except ImportError as exc:
        raise RuntimeError(
            "psycopg2 is not installed. Run: pip install psycopg2-binary"
        ) from exc

    return pg_pool.ThreadedConnectionPool(
        minconn=min_conn,
        maxconn=max_conn,
        host=host,
        port=port,
        dbname=dbname,
        user=user,
        password=password,
        cursor_factory=psycopg2.extras.RealDictCursor,
    )


# ---------------------------------------------------------------------------
# DATABASE_URL helper (Railway / Heroku / Render style connection strings)
# ---------------------------------------------------------------------------

def _pg_connection_from_url(url: str) -> Any:
    """Parse postgresql://user:pass@host:port/dbname and return a connection."""
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError as exc:
        raise RuntimeError(
            "psycopg2 is not installed. Run: pip install psycopg2-binary"
        ) from exc

    conn = psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)
    conn.autocommit = False
    logger.debug("PostgreSQL connection opened from DATABASE_URL")
    return conn


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------

def get_connection(
    backend: Backend | str | None = None,
    *,
    # SQLite
    path: str | Path | None = None,
    # PostgreSQL
    host: str | None = None,
    port: int | None = None,
    dbname: str | None = None,
    user: str | None = None,
    password: str | None = None,
) -> Connection:
    """
    Return a database connection for the configured backend.

    Parameters override environment variables when provided.
    """
    database_url = os.getenv("DATABASE_URL", "")
    if database_url and backend is None:
        backend = Backend.POSTGRES

    if backend is None:
        backend = os.getenv("DB_BACKEND", "sqlite")
    backend = Backend(backend)

    if backend == Backend.SQLITE:
        db_path = path or os.getenv("DB_PATH", "./inspection.db")
        return _sqlite_connection(db_path)

    if backend == Backend.POSTGRES:
        if database_url and not host:
            return _pg_connection_from_url(database_url)
        return _pg_connection(
            host     = host     or os.getenv("DB_HOST",     "localhost"),
            port     = int(port or os.getenv("DB_PORT",     "5432")),
            dbname   = dbname   or os.getenv("DB_NAME",     "inspection"),
            user     = user     or os.getenv("DB_USER",     ""),
            password = password or os.getenv("DB_PASSWORD", ""),
        )

    raise ValueError(f"Unknown backend: {backend}")


# ---------------------------------------------------------------------------
# Placeholder detector (used by schema.py and ingest.py)
# ---------------------------------------------------------------------------

def get_placeholder(conn: Connection) -> str:
    """
    Return the parameter placeholder for this backend.

    SQLite uses  ?   (qmark style)
    PostgreSQL uses  %s  (pyformat style)

    Usage in query modules:
        ph = get_placeholder(conn)
        conn.execute(f"SELECT * FROM panels WHERE panel_id = {ph}", (pid,))
    """
    try:
        import psycopg2.extensions
        if isinstance(conn, psycopg2.extensions.connection):
            return "%s"
    except ImportError:
        pass
    return "?"


def is_postgres(conn: Connection) -> bool:
    """Return True if the connection is a PostgreSQL connection."""
    try:
        import psycopg2.extensions
        return isinstance(conn, psycopg2.extensions.connection)
    except ImportError:
        return False
