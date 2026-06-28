"""
api/routers/ai_router.py
-------------------------
Author: Yeonkuk Woo

AI defect classifier endpoints.
"""

from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from openyield.api.dependencies import get_db
from openyield.ai.classifier import (
    train_classifier, predict_panel, evaluate_classifier,
)

router = APIRouter(prefix="/ai", tags=["ai"])
Connection = Any


# ── Schemas ────────────────────────────────────────────────────────────────

class TrainingResponse(BaseModel):
    model_version:      str
    classes:            list[str]
    n_training_samples: int
    n_features:         int
    accuracy:           float
    feature_names:      list[str]
    trained_at:         str
    final_loss:         float
    iterations:         int


class PredictionResponse(BaseModel):
    defect_id:      int
    panel_id:       str
    predicted_type: str
    confidence:     float
    class_probs:    dict[str, float]
    true_type:      str | None
    correct:        bool | None


class ClassMetricsResponse(BaseModel):
    precision: float
    recall:    float
    support:   int


class EvaluationResponse(BaseModel):
    model_version: str
    n_samples:     int
    accuracy:      float
    per_class:     dict[str, ClassMetricsResponse]


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.post("/train", response_model=TrainingResponse)
def train(
    substrate_type: str | None = Query(None),
    learning_rate:  float      = Query(0.05, gt=0, le=1),
    l2_lambda:      float      = Query(0.01, ge=0),
    max_iterations: int        = Query(400, ge=1, le=5000),
    conn: Connection = Depends(get_db),
):
    """Train the Phase 1 defect classifier on labeled defects in the database."""
    try:
        result = train_classifier(
            conn,
            substrate_type=substrate_type,
            learning_rate=learning_rate,
            l2_lambda=l2_lambda,
            max_iterations=max_iterations,
            persist=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return TrainingResponse(
        model_version=result.model_version,
        classes=result.classes,
        n_training_samples=result.n_training_samples,
        n_features=result.n_features,
        accuracy=result.accuracy,
        feature_names=result.feature_names,
        trained_at=result.trained_at,
        final_loss=result.final_loss,
        iterations=result.iterations,
    )


@router.post("/predict/{panel_id}", response_model=list[PredictionResponse])
def predict(
    panel_id:      str,
    model_version: str | None = Query(None, description="Use latest if omitted"),
    conn: Connection = Depends(get_db),
):
    """Predict defect_type for every system_a defect on a panel."""
    try:
        preds = predict_panel(
            conn, panel_id,
            model_version=model_version, persist=True
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return [
        PredictionResponse(
            defect_id=p.defect_id, panel_id=p.panel_id,
            predicted_type=p.predicted_type, confidence=p.confidence,
            class_probs=p.class_probs, true_type=p.true_type,
            correct=p.correct,
        )
        for p in preds
    ]


@router.get("/evaluate", response_model=EvaluationResponse)
def evaluate(
    model_version:  str | None = Query(None),
    substrate_type: str | None = Query(None),
    conn: Connection = Depends(get_db),
):
    """Evaluate the trained classifier against ground-truth labels."""
    try:
        result = evaluate_classifier(
            conn,
            model_version=model_version,
            substrate_type=substrate_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return EvaluationResponse(
        model_version=result["model_version"],
        n_samples=result["n_samples"],
        accuracy=result["accuracy"],
        per_class={
            cls: ClassMetricsResponse(**m)
            for cls, m in result["per_class"].items()
        },
    )
