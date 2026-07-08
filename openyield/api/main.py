"""
api/main.py
-----------
Author: Yeonkuk Woo

OpenYield REST API — FastAPI application entry point.

Run with:
    uvicorn openyield.api.main:app --reload --port 8000

Interactive docs available at:
    http://localhost:8000/docs      (Swagger UI)
    http://localhost:8000/redoc     (ReDoc)
"""

from __future__ import annotations
import os
from typing import Any
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware

from openyield.api.dependencies import get_db
from openyield.api.schemas import HealthResponse
from openyield.api.routers import (
    panels, defects, yield_router,
    ingest, validation_router,
    analysis_router, analytics_router, ai_router, claude_router, images_router,
    generate_router,
    spatial_router, genealogy_router, classify_router,
    products_router, simulator_router,
)
from openyield.db.connection import get_placeholder

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="OpenYield API",
    description=(
        "Open-source semiconductor inspection data platform. "
        "Funded under the National CHIPS and Science Act. "
        "Provides defect ingestion, yield calculation, and validation "
        "for silicon wafer and glass panel manufacturing."
    ),
    version="0.1.0",
    contact={
        "name": "OpenYield",
        "url": "https://github.com/openyield/openyield",
    },
    license_info={
        "name": "Apache 2.0",
        "url": "https://www.apache.org/licenses/LICENSE-2.0",
    },
)

_default_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
]
_extra = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_default_origins + _extra,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(panels.router)
app.include_router(defects.router)
app.include_router(yield_router.router)
app.include_router(ingest.router)
app.include_router(validation_router.router)
app.include_router(analysis_router.router)
app.include_router(analytics_router.router)
app.include_router(ai_router.router)
app.include_router(claude_router.router)
app.include_router(images_router.router)
app.include_router(generate_router.router)
app.include_router(spatial_router.router)
app.include_router(genealogy_router.router)
app.include_router(classify_router.router)
app.include_router(products_router.router)
app.include_router(simulator_router.router)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse, tags=["health"])
def health_check(conn: Any = Depends(get_db)):
    """Database connectivity and basic stats."""
    ph = get_placeholder(conn)
    try:
        panel_count  = conn.execute("SELECT COUNT(*) FROM panels").fetchone()[0]
        defect_count = conn.execute("SELECT COUNT(*) FROM defects").fetchone()[0]
        status = "ok"
    except Exception as exc:
        return HealthResponse(
            status="error",
            backend="sqlite",
            panel_count=0,
            defect_count=0,
            db_path=None,
        )

    db_path = os.getenv("DB_PATH", "./inspection.db")
    return HealthResponse(
        status=status,
        backend="sqlite",
        panel_count=panel_count,
        defect_count=defect_count,
        db_path=db_path,
    )


# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------

@app.get("/", tags=["health"])
def root():
    return {
        "name":    "OpenYield API",
        "version": "0.1.0",
        "docs":    "/docs",
        "health":  "/health",
    }
