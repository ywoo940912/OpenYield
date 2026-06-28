"""api/routers/classify_router.py — Defect classification endpoints."""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from openyield.api.dependencies import get_db

router = APIRouter(prefix="/classify", tags=["classify"])
Connection = Any


class DefectDistribution(BaseModel):
    panel_id: str
    defect_counts: dict[str, int]
    total_defects: int
    top_class: str
    top_class_fraction: float
    source_system: str


class CNNStatus(BaseModel):
    model_available: bool
    trained_at: str | None
    val_accuracy: float | None
    n_classes: int | None
    notes: str | None


@router.get("/{panel_id}/defects", response_model=DefectDistribution)
def get_defect_distribution(panel_id: str, conn: Connection = Depends(get_db)):
    """
    Return defect type distribution for a panel from the defects table.

    Counts defects by type for system_a source, providing a classification
    summary without requiring CNN inference.
    """
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM panels WHERE panel_id = ?", (panel_id,)
    ).fetchone()
    if not row or row["n"] == 0:
        raise HTTPException(status_code=404, detail=f"Panel {panel_id!r} not found")

    rows = conn.execute("""
        SELECT defect_type, COUNT(*) AS cnt
        FROM defects
        WHERE panel_id = ?
        GROUP BY defect_type
        ORDER BY cnt DESC
    """, (panel_id,)).fetchall()

    defect_counts = {r["defect_type"]: r["cnt"] for r in rows}
    total = sum(defect_counts.values())
    top_class = rows[0]["defect_type"] if rows else "none"
    top_fraction = (defect_counts[top_class] / total) if total > 0 else 0.0

    return DefectDistribution(
        panel_id=panel_id,
        defect_counts=defect_counts,
        total_defects=total,
        top_class=top_class,
        top_class_fraction=round(top_fraction, 4),
        source_system="system_a",
    )


@router.get("/cnn/status", response_model=CNNStatus)
def get_cnn_status(conn: Connection = Depends(get_db)):
    """Return CNN model status from model_registry."""
    try:
        row = conn.execute(
            "SELECT trained_at, notes FROM model_registry "
            "WHERE model_type = 'cnn' ORDER BY trained_at DESC LIMIT 1"
        ).fetchone()
    except Exception:
        return CNNStatus(
            model_available=False, trained_at=None,
            val_accuracy=None, n_classes=None, notes=None,
        )

    if row is None:
        return CNNStatus(
            model_available=False, trained_at=None,
            val_accuracy=None, n_classes=None, notes=None,
        )

    import re
    notes = row["notes"] or ""
    val_acc = None
    n_cls   = None
    m = re.search(r"val_acc=(\d+\.?\d*)", notes)
    if m:
        val_acc = float(m.group(1))
    m2 = re.search(r"classes=(\d+)", notes)
    if m2:
        n_cls = int(m2.group(1))

    return CNNStatus(
        model_available=True,
        trained_at=row["trained_at"],
        val_accuracy=val_acc,
        n_classes=n_cls,
        notes=notes,
    )


class TrainRequest(BaseModel):
    epochs: int = 10
    batch_size: int = 16
    lr: float = 0.01
    momentum: float = 0.9
    val_split: float = 0.2
    seed: int = 42


class TrainResponse(BaseModel):
    model_type: str
    n_params: int
    n_classes: int
    epochs_run: int
    final_val_accuracy: float | None
    trained_at: str


@router.post("/cnn/train", response_model=TrainResponse)
def train_cnn_endpoint(
    body: TrainRequest = TrainRequest(),
    conn: Connection = Depends(get_db),
):
    """
    Train the CNN defect classifier on images in the defect_images table.

    Requires at least one row in defect_images (panel_id, image_data, defect_type).
    Training runs synchronously — for large datasets consider a background task.
    The trained model is persisted to model_registry and returned by GET /classify/cnn/status.
    """
    from datetime import datetime, timezone

    try:
        from openyield.ai.cnn_classifier import train_cnn
    except ImportError as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=f"CNN module unavailable: {exc}")

    try:
        model, history = train_cnn(
            conn,
            epochs=body.epochs,
            batch_size=body.batch_size,
            lr=body.lr,
            momentum=body.momentum,
            val_split=body.val_split,
            seed=body.seed,
            persist=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    final_val_acc = history.val_acc[-1] if history.val_acc else None

    return TrainResponse(
        model_type="cnn",
        n_params=model.n_params(),
        n_classes=model.n_classes,
        epochs_run=history.epochs_run,
        final_val_accuracy=final_val_acc,
        trained_at=datetime.now(timezone.utc).isoformat(),
    )
