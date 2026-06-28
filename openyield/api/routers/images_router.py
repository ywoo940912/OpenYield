"""
api/routers/images_router.py
-----------------------------
Author: Yeonkuk Woo

Endpoints for synthetic defect image generation and retrieval.
"""

from __future__ import annotations
from pathlib import Path
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from openyield.api.dependencies import get_db
from openyield.db.connection import get_placeholder
from openyield.synthetic.image_generator import (
    generate_images_for_panel, generate_images_for_all,
)

router = APIRouter(prefix="/images", tags=["images"])
Connection = Any


class ImageGenerationResponse(BaseModel):
    panel_id:         str
    n_defects:        int
    n_images_written: int
    n_images_skipped: int
    output_dir:       str


class ImageRecordResponse(BaseModel):
    defect_id:         int
    panel_id:          str
    image_path:        str
    width:             int
    height:            int
    format:            str
    generator_version: str


@router.post("/generate/{panel_id}", response_model=ImageGenerationResponse)
def generate_for_panel(
    panel_id:    str,
    output_root: str  = Query("output/defect_images"),
    overwrite:   bool = Query(False),
    conn: Connection  = Depends(get_db),
):
    """Generate synthetic image patches for every system_a defect on a panel."""
    try:
        result = generate_images_for_panel(
            conn, panel_id,
            output_root=output_root,
            overwrite=overwrite,
            persist=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return ImageGenerationResponse(**result.__dict__)


@router.post("/generate", response_model=list[ImageGenerationResponse])
def generate_all(
    substrate_type: str | None = Query(None),
    output_root:    str        = Query("output/defect_images"),
    overwrite:      bool       = Query(False),
    conn: Connection = Depends(get_db),
):
    """Generate image patches for every panel (optionally filtered by substrate)."""
    results = generate_images_for_all(
        conn,
        output_root=output_root,
        substrate_type=substrate_type,
        overwrite=overwrite,
        persist=True,
    )
    return [ImageGenerationResponse(**r.__dict__) for r in results]


@router.get("/panel/{panel_id}", response_model=list[ImageRecordResponse])
def list_images_for_panel(
    panel_id: str,
    conn: Connection = Depends(get_db),
):
    """List all generated image records for a panel."""
    ph = get_placeholder(conn)
    rows = conn.execute(
        f"SELECT * FROM defect_images WHERE panel_id={ph} ORDER BY defect_id",
        (panel_id,)
    ).fetchall()
    return [
        ImageRecordResponse(
            defect_id=r["defect_id"], panel_id=r["panel_id"],
            image_path=r["image_path"], width=r["width"], height=r["height"],
            format=r["format"], generator_version=r["generator_version"],
        )
        for r in rows
    ]


@router.get("/file/{panel_id}/{defect_id}")
def get_image_file(
    panel_id:  str,
    defect_id: int,
    conn: Connection = Depends(get_db),
):
    """Return the PNG file for a specific defect."""
    ph = get_placeholder(conn)
    row = conn.execute(
        f"SELECT image_path FROM defect_images "
        f"WHERE panel_id={ph} AND defect_id={ph}",
        (panel_id, defect_id)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404,
                            detail="No image record for that defect")
    path = Path(row["image_path"])
    if not path.exists():
        raise HTTPException(
            status_code=410,
            detail=f"Image record exists but file is missing on disk: {path}"
        )
    return FileResponse(path, media_type="image/png")
