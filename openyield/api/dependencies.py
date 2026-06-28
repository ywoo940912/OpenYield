"""
api/dependencies.py
-------------------
Author: Yeonkuk Woo

FastAPI dependency injection — database connection per request.
"""

from __future__ import annotations
import os
from typing import Generator, Any
from openyield.db.connection import get_connection
from openyield.db.schema import initialize_schema

Connection = Any

def get_db() -> Generator[Connection, None, None]:
    """
    Yield a database connection for the duration of a request,
    then close it. Used via FastAPI Depends().
    """
    db_path = os.getenv("DB_PATH", "./inspection.db")
    conn = get_connection(path=db_path)
    initialize_schema(conn)
    try:
        yield conn
    finally:
        conn.close()
