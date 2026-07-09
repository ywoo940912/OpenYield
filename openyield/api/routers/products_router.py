"""api/routers/products_router.py — Product specification CRUD endpoints."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from openyield.api.dependencies import get_db

router = APIRouter(prefix="/products", tags=["products"])
Connection = Any


# ---------------------------------------------------------------------------
# Schema bootstrap
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS product_specs (
    spec_id                 TEXT PRIMARY KEY,
    product_name            TEXT NOT NULL,
    substrate_type          TEXT NOT NULL DEFAULT 'wafer',
    die_width_mm            REAL NOT NULL,
    die_height_mm           REAL NOT NULL,
    wafer_diameter_mm       REAL,
    critical_area_fraction  REAL NOT NULL DEFAULT 1.0,
    target_yield            REAL NOT NULL DEFAULT 0.80,
    alpha                   REAL NOT NULL DEFAULT 2.0,
    d0_target               REAL,
    process_node_nm         INTEGER,
    panel_width_mm          REAL,
    panel_height_mm         REAL,
    display_technology      TEXT,
    notes                   TEXT NOT NULL DEFAULT '',
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);
"""

# Added after initial release — run each startup to migrate existing DBs.
_MIGRATIONS = [
    "ALTER TABLE product_specs ADD COLUMN panel_width_mm REAL",
    "ALTER TABLE product_specs ADD COLUMN panel_height_mm REAL",
    "ALTER TABLE product_specs ADD COLUMN display_technology TEXT",
]


def _init(conn) -> None:
    with conn:
        conn.execute(_SCHEMA_SQL)
    for sql in _MIGRATIONS:
        try:
            with conn:
                conn.execute(sql)
        except Exception:
            pass  # column already exists


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ProductSpecCreate(BaseModel):
    spec_id:                str
    product_name:           str
    substrate_type:         str              = "wafer"
    die_width_mm:           float            = Field(gt=0)
    die_height_mm:          float            = Field(gt=0)
    # wafer-only
    wafer_diameter_mm:      float | None     = None
    process_node_nm:        int | None       = None
    # glass-panel-only
    panel_width_mm:         float | None     = None
    panel_height_mm:        float | None     = None
    display_technology:     str | None       = None
    # common
    critical_area_fraction: float            = Field(default=1.0, ge=0.0, le=1.0)
    target_yield:           float            = Field(default=0.80, ge=0.0, le=1.0)
    alpha:                  float            = Field(default=2.0, gt=0)
    d0_target:              float | None     = None
    notes:                  str              = ""


class ProductSpecUpdate(BaseModel):
    product_name:           str | None       = None
    substrate_type:         str | None       = None
    die_width_mm:           float | None     = None
    die_height_mm:          float | None     = None
    wafer_diameter_mm:      float | None     = None
    process_node_nm:        int | None       = None
    panel_width_mm:         float | None     = None
    panel_height_mm:        float | None     = None
    display_technology:     str | None       = None
    critical_area_fraction: float | None     = None
    target_yield:           float | None     = None
    alpha:                  float | None     = None
    d0_target:              float | None     = None
    notes:                  str | None       = None


class ProductSpec(BaseModel):
    spec_id:                str
    product_name:           str
    substrate_type:         str
    die_width_mm:           float
    die_height_mm:          float
    die_area_mm2:           float
    # wafer-only (None for glass panels)
    wafer_diameter_mm:      float | None
    process_node_nm:        int | None
    # glass-panel-only (None for wafers)
    panel_width_mm:         float | None
    panel_height_mm:        float | None
    display_technology:     str | None
    # common
    critical_area_fraction: float
    target_yield:           float
    alpha:                  float
    d0_target:              float | None
    notes:                  str
    created_at:             str
    updated_at:             str


def _row_to_spec(row) -> ProductSpec:
    return ProductSpec(
        spec_id=row["spec_id"],
        product_name=row["product_name"],
        substrate_type=row["substrate_type"],
        die_width_mm=row["die_width_mm"],
        die_height_mm=row["die_height_mm"],
        die_area_mm2=row["die_width_mm"] * row["die_height_mm"],
        wafer_diameter_mm=row["wafer_diameter_mm"],
        process_node_nm=row["process_node_nm"],
        panel_width_mm=row["panel_width_mm"],
        panel_height_mm=row["panel_height_mm"],
        display_technology=row["display_technology"],
        critical_area_fraction=row["critical_area_fraction"],
        target_yield=row["target_yield"],
        alpha=row["alpha"],
        d0_target=row["d0_target"],
        notes=row["notes"] or "",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/specs", response_model=ProductSpec, status_code=201)
def create_spec(body: ProductSpecCreate, conn: Connection = Depends(get_db)):
    """Create a new product specification."""
    _init(conn)
    now = datetime.now(timezone.utc).isoformat()
    try:
        with conn:
            conn.execute(
                "INSERT INTO product_specs "
                "(spec_id, product_name, substrate_type, die_width_mm, die_height_mm, "
                " wafer_diameter_mm, process_node_nm, "
                " panel_width_mm, panel_height_mm, display_technology, "
                " critical_area_fraction, target_yield, alpha, "
                " d0_target, notes, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    body.spec_id, body.product_name, body.substrate_type,
                    body.die_width_mm, body.die_height_mm,
                    body.wafer_diameter_mm, body.process_node_nm,
                    body.panel_width_mm, body.panel_height_mm, body.display_technology,
                    body.critical_area_fraction, body.target_yield, body.alpha,
                    body.d0_target, body.notes, now, now,
                ),
            )
    except Exception as exc:
        if "UNIQUE" in str(exc).upper():
            raise HTTPException(status_code=409,
                                detail=f"spec_id {body.spec_id!r} already exists")
        raise HTTPException(status_code=500, detail=str(exc))

    row = conn.execute(
        "SELECT * FROM product_specs WHERE spec_id = ?", (body.spec_id,)
    ).fetchone()
    return _row_to_spec(row)


@router.get("/specs", response_model=list[ProductSpec])
def list_specs(conn: Connection = Depends(get_db)):
    """List all product specifications."""
    _init(conn)
    rows = conn.execute(
        "SELECT * FROM product_specs ORDER BY created_at DESC"
    ).fetchall()
    return [_row_to_spec(r) for r in rows]


@router.get("/specs/{spec_id}", response_model=ProductSpec)
def get_spec(spec_id: str, conn: Connection = Depends(get_db)):
    """Retrieve a single product specification."""
    _init(conn)
    row = conn.execute(
        "SELECT * FROM product_specs WHERE spec_id = ?", (spec_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"spec {spec_id!r} not found")
    return _row_to_spec(row)


@router.patch("/specs/{spec_id}", response_model=ProductSpec)
def update_spec(
    spec_id: str,
    body: ProductSpecUpdate,
    conn: Connection = Depends(get_db),
):
    """Partially update a product specification."""
    _init(conn)
    row = conn.execute(
        "SELECT * FROM product_specs WHERE spec_id = ?", (spec_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"spec {spec_id!r} not found")

    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        return _row_to_spec(row)

    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values     = list(updates.values()) + [spec_id]
    with conn:
        conn.execute(
            f"UPDATE product_specs SET {set_clause} WHERE spec_id = ?", values
        )
    row = conn.execute(
        "SELECT * FROM product_specs WHERE spec_id = ?", (spec_id,)
    ).fetchone()
    return _row_to_spec(row)


@router.delete("/specs/{spec_id}", status_code=204)
def delete_spec(spec_id: str, conn: Connection = Depends(get_db)):
    """Delete a product specification."""
    _init(conn)
    result = conn.execute(
        "SELECT COUNT(*) FROM product_specs WHERE spec_id = ?", (spec_id,)
    ).fetchone()[0]
    if result == 0:
        raise HTTPException(status_code=404, detail=f"spec {spec_id!r} not found")
    with conn:
        conn.execute("DELETE FROM product_specs WHERE spec_id = ?", (spec_id,))
