"""
api/routers/images_router.py
-----------------------------
Author: Yeonkuk Woo

Endpoints for synthetic defect image generation and retrieval.
"""

from __future__ import annotations
import io
import struct
import zlib
from pathlib import Path
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from openyield.api.dependencies import get_db
from openyield.db.connection import get_placeholder
from openyield.synthetic.image_generator import (
    generate_images_for_panel, generate_images_for_all,
    _render_defect, IMAGE_W, IMAGE_H,
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

def _pixels_to_png_bytes(pixels: list[int]) -> bytes:
    """Encode flat pixel list to a minimal grayscale PNG in memory."""
    sig = b"\x89PNG\r\n\x1a\n"
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", IMAGE_W, IMAGE_H, 8, 0, 0, 0, 0)
    raw = bytearray()
    for y in range(IMAGE_H):
        raw.append(0)
        for x in range(IMAGE_W):
            v = pixels[y * IMAGE_W + x]
            raw.append(max(0, min(255, v)))
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b"")


@router.get("/render/{defect_id}")
def render_defect_image(
    defect_id: int,
    conn: Connection = Depends(get_db),
):
    """
    Stream a synthetic defect image PNG on-the-fly without touching disk.
    Works on ephemeral filesystems (Railway, containers).
    """
    ph = get_placeholder(conn)
    row = conn.execute(
        f"SELECT panel_id, defect_type, size FROM defects WHERE defect_id={ph}",
        (defect_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Defect {defect_id} not found")

    pixels = _render_defect(row["defect_type"], float(row["size"]), row["panel_id"], defect_id)
    png_bytes = _pixels_to_png_bytes(pixels)
    return StreamingResponse(
        io.BytesIO(png_bytes),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/render/panel/{panel_id}")
def render_panel_gallery(
    panel_id: str,
    limit: int = Query(50, le=200),
    conn: Connection = Depends(get_db),
):
    """
    Return JSON metadata for all defects on a panel so the frontend
    can render a gallery using /images/render/{defect_id} per thumbnail.
    """
    ph = get_placeholder(conn)
    if conn.execute(f"SELECT 1 FROM panels WHERE panel_id={ph}", (panel_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail=f"Panel {panel_id!r} not found")

    rows = conn.execute(
        f"""SELECT defect_id, defect_type, size, confidence_score,
                   component_row, component_col, x, y
            FROM defects
            WHERE panel_id={ph} AND source_system='system_a'
            ORDER BY defect_id
            LIMIT {limit}""",
        (panel_id,)
    ).fetchall()

    return {
        "panel_id": panel_id,
        "total": len(rows),
        "defects": [
            {
                "defect_id":       r["defect_id"],
                "defect_type":     r["defect_type"],
                "size":            r["size"],
                "confidence_score": r["confidence_score"],
                "component_row":   r["component_row"],
                "component_col":   r["component_col"],
                "render_url":      f"/images/render/{r['defect_id']}",
            }
            for r in rows
        ],
    }
