"""
ai/classifier.py
-----------------
Author: Yeonkuk Woo
Module: AI defect type classifier (Phase 1)

Purpose
-------
Predicts defect_type from spatial and morphological features. Trained on
the synthetic labeled data produced by the OpenYield generator, which
provides ground-truth labels without proprietary data exposure.

This component provides the foundation for ML-driven defect triage in
domestic semiconductor inspection workflows. Beneficiary categories
include domestic glass substrate manufacturers, silicon wafer fabs,
national laboratories conducting inspection research, and academic
groups developing reproducible ML pipelines on open data.

Design choice — multinomial logistic regression
------------------------------------------------
Rationale documented in ADR-006. Logistic regression is preferred over
deep neural networks for this Phase 1 implementation because:

  1. Interpretable per-feature coefficients — a fab engineer can audit
     why a defect was assigned a label, which is required for any
     manufacturing process decision.
  2. Trainable with no GPU and no external ML framework dependency,
     consistent with the project's no-heavyweight-dependency principle.
  3. Convex loss with deterministic convergence — reproducible across
     environments, which is necessary for petition-evidence integrity.

Future phases (Phase 2: anomaly detection; Phase 3: yield prediction)
are scoped in the project roadmap.

Algorithm
---------
Multinomial logistic regression trained by batch gradient descent on
the softmax cross-entropy loss with L2 regularisation:

    P(y=k | x) = exp(w_k · x) / Σ_j exp(w_j · x)
    Loss        = -Σ log P(y_i | x_i) + λ Σ ||w_k||²

Features
--------
    size                   defect size in mm (continuous)
    confidence             system confidence score (continuous)
    x_normalized           x-coordinate / panel width (continuous)
    y_normalized           y-coordinate / panel height (continuous)
    component_row_norm     row index / total rows (continuous)
    component_col_norm     col index / total cols (continuous)
    zone_center            1 if region_id == 'zone_center' else 0
    zone_mid               1 if region_id == 'zone_mid'    else 0
    zone_edge              1 if region_id == 'zone_edge'   else 0
    region_NW/NE/SW/SE     glass-panel quadrant indicators
    is_wafer               1 if substrate_type == 'wafer' else 0

The classifier ignores defect_id, panel_id, and timestamps to remain
generalisable across panels and lots.
"""

from __future__ import annotations

import json
import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from openyield.db.connection import get_placeholder, is_postgres

logger = logging.getLogger(__name__)
Connection = Any


# ---------------------------------------------------------------------------
# Feature schema — order matters and must remain stable across versions
# ---------------------------------------------------------------------------

FEATURE_NAMES: list[str] = [
    "bias",                  # intercept
    "size",
    "confidence",
    "x_normalized",
    "y_normalized",
    "component_row_norm",
    "component_col_norm",
    "zone_center",
    "zone_mid",
    "zone_edge",
    "region_NW",
    "region_NE",
    "region_SW",
    "region_SE",
    "is_wafer",
]
N_FEATURES = len(FEATURE_NAMES)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class TrainingResult:
    model_version:     str
    classes:           list[str]
    n_training_samples: int
    n_features:        int
    accuracy:          float
    coefficients:      list[list[float]]   # [n_classes][n_features]
    feature_names:     list[str]
    trained_at:        str
    final_loss:        float
    iterations:        int


@dataclass
class Prediction:
    defect_id:       int
    panel_id:        str
    predicted_type:  str
    confidence:      float                  # softmax probability for predicted class
    class_probs:     dict[str, float]       # full distribution
    true_type:       str | None = None
    correct:         bool | None = None


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------

def _extract_features(row: dict, panel: dict) -> list[float]:
    """
    Build a feature vector from a defect row + its panel metadata.

    The row must include: size, confidence_score, x, y,
    component_row, component_col, region_id.
    The panel must include: substrate_type, rows, cols.
    """
    # Normalise spatial coordinates to [0, 1] using grid extent
    # (we don't have explicit panel width in DB, so use grid pitch implicitly
    # by normalising against the (rows × cols) bounding extent)
    row_idx = row["component_row"]
    col_idx = row["component_col"]
    rows_t  = max(panel["rows"], 1)
    cols_t  = max(panel["cols"], 1)

    row_norm = row_idx / rows_t
    col_norm = col_idx / cols_t

    # x, y are physical mm — normalise by an approximate panel extent
    x_extent = cols_t  # 1 unit per die — coarse but stable
    y_extent = rows_t
    x_norm   = (row["x"] / x_extent) if x_extent > 0 else 0.0
    y_norm   = (row["y"] / y_extent) if y_extent > 0 else 0.0
    # Clamp into a finite range
    x_norm   = max(-10.0, min(10.0, x_norm))
    y_norm   = max(-10.0, min(10.0, y_norm))

    region = row.get("region_id") or ""
    zone_center = 1.0 if region == "zone_center" else 0.0
    zone_mid    = 1.0 if region == "zone_mid"    else 0.0
    zone_edge   = 1.0 if region == "zone_edge"   else 0.0
    r_nw = 1.0 if region == "region_NW" else 0.0
    r_ne = 1.0 if region == "region_NE" else 0.0
    r_sw = 1.0 if region == "region_SW" else 0.0
    r_se = 1.0 if region == "region_SE" else 0.0
    is_wafer = 1.0 if panel["substrate_type"] == "wafer" else 0.0

    return [
        1.0,                          # bias
        float(row["size"]),
        float(row["confidence_score"]),
        x_norm,
        y_norm,
        row_norm,
        col_norm,
        zone_center,
        zone_mid,
        zone_edge,
        r_nw,
        r_ne,
        r_sw,
        r_se,
        is_wafer,
    ]


def _load_training_set(
    conn: Connection,
    substrate_type: str | None,
    source_system: str = "system_a",
) -> tuple[list[list[float]], list[str], list[tuple[int, str]]]:
    """
    Pull labeled defects from the database and produce (X, y, id_refs).

    id_refs contains (defect_id, panel_id) for each row, used later if
    we want to persist per-row predictions on the training set.
    """
    ph = get_placeholder(conn)
    filters = [f"d.source_system = {ph}", "c.active = 1"]
    params: list[Any] = [source_system]

    if substrate_type:
        filters.append(f"p.substrate_type = {ph}")
        params.append(substrate_type)

    where = "WHERE " + " AND ".join(filters)
    sql = f"""
        SELECT d.defect_id, d.panel_id, d.component_row, d.component_col,
               d.size, d.confidence_score, d.x, d.y, d.defect_type,
               c.region_id,
               p.substrate_type, p.rows, p.cols
        FROM defects d
        JOIN panels p     ON p.panel_id = d.panel_id
        JOIN components c
          ON c.panel_id      = d.panel_id
         AND c.component_row = d.component_row
         AND c.component_col = d.component_col
        {where}
    """
    rows = conn.execute(sql, params).fetchall()

    X: list[list[float]] = []
    y: list[str] = []
    refs: list[tuple[int, str]] = []
    for r in rows:
        row_d = dict(r)
        X.append(_extract_features(row_d, row_d))
        y.append(row_d["defect_type"])
        refs.append((row_d["defect_id"], row_d["panel_id"]))
    return X, y, refs


# ---------------------------------------------------------------------------
# Math primitives (pure Python — no numpy required for the core)
# ---------------------------------------------------------------------------

def _softmax(logits: list[float]) -> list[float]:
    m = max(logits)
    exps = [math.exp(l - m) for l in logits]
    s = sum(exps)
    return [e / s for e in exps]


def _dot(w: list[float], x: list[float]) -> float:
    return sum(wi * xi for wi, xi in zip(w, x))


# ---------------------------------------------------------------------------
# Training — batch gradient descent on softmax cross-entropy
# ---------------------------------------------------------------------------

def train_classifier(
    conn: Connection,
    *,
    substrate_type: str | None = None,
    source_system: str = "system_a",
    learning_rate: float = 0.05,
    l2_lambda: float = 0.01,
    max_iterations: int = 400,
    tolerance: float = 1e-5,
    persist: bool = True,
) -> TrainingResult:
    """
    Train a multinomial logistic regression classifier on labeled
    defects in the database.

    Returns a TrainingResult with model coefficients and training
    accuracy. When persist=True, the model is recorded in the
    model_registry table.
    """
    X, y, _refs = _load_training_set(conn, substrate_type, source_system)
    n = len(X)
    if n < 10:
        raise ValueError(
            f"Need at least 10 labeled defects to train; found {n}. "
            f"Run the synthetic generator first."
        )

    classes = sorted(set(y))
    if len(classes) < 2:
        raise ValueError(
            f"Need at least 2 distinct defect classes; found {classes}."
        )

    cls_idx = {c: i for i, c in enumerate(classes)}
    y_idx = [cls_idx[label] for label in y]
    k = len(classes)
    d = N_FEATURES

    # Initialise weights — small random would be fine; zeros work for convex loss
    W = [[0.0 for _ in range(d)] for _ in range(k)]

    prev_loss = float("inf")
    iterations_done = 0
    final_loss = 0.0

    for it in range(max_iterations):
        # Forward — compute softmax probabilities for every sample
        # and accumulate gradient and loss
        grad = [[0.0 for _ in range(d)] for _ in range(k)]
        loss = 0.0

        for i in range(n):
            xi = X[i]
            logits = [_dot(W[c], xi) for c in range(k)]
            probs  = _softmax(logits)
            true_c = y_idx[i]
            loss  -= math.log(max(probs[true_c], 1e-12))

            for c in range(k):
                err = probs[c] - (1.0 if c == true_c else 0.0)
                for j in range(d):
                    grad[c][j] += err * xi[j]

        # L2 regularisation contribution (skip bias index 0)
        for c in range(k):
            for j in range(1, d):
                loss += 0.5 * l2_lambda * W[c][j] * W[c][j]
                grad[c][j] += l2_lambda * W[c][j]

        loss /= n

        # Parameter update
        for c in range(k):
            for j in range(d):
                W[c][j] -= learning_rate * (grad[c][j] / n)

        iterations_done = it + 1
        final_loss = loss

        if abs(prev_loss - loss) < tolerance:
            logger.info(
                "Converged at iteration %d (Δloss=%.2e)",
                iterations_done, abs(prev_loss - loss)
            )
            break
        prev_loss = loss

    # Training-set accuracy
    correct = 0
    for i in range(n):
        logits = [_dot(W[c], X[i]) for c in range(k)]
        pred = max(range(k), key=lambda c: logits[c])
        if pred == y_idx[i]:
            correct += 1
    accuracy = correct / n

    model_version = f"v1-{uuid.uuid4().hex[:8]}"
    trained_at = datetime.now(timezone.utc).isoformat()

    logger.info(
        "Trained classifier %s: %d samples | %d classes | accuracy=%.3f | "
        "loss=%.4f | %d iters",
        model_version, n, k, accuracy, final_loss, iterations_done,
    )

    result = TrainingResult(
        model_version=model_version,
        classes=classes,
        n_training_samples=n,
        n_features=d,
        accuracy=round(accuracy, 6),
        coefficients=W,
        feature_names=FEATURE_NAMES,
        trained_at=trained_at,
        final_loss=round(final_loss, 6),
        iterations=iterations_done,
    )

    if persist:
        _save_model(conn, result, substrate_type)

    return result


# ---------------------------------------------------------------------------
# Prediction
# ---------------------------------------------------------------------------

def _predict_one(
    features: list[float],
    coefficients: list[list[float]],
    classes: list[str],
) -> tuple[str, float, dict[str, float]]:
    logits = [_dot(coefficients[c], features) for c in range(len(classes))]
    probs  = _softmax(logits)
    best   = max(range(len(classes)), key=lambda c: probs[c])
    return classes[best], probs[best], dict(zip(classes, probs))


def predict_panel(
    conn: Connection,
    panel_id: str,
    *,
    model_version: str | None = None,
    persist: bool = True,
) -> list[Prediction]:
    """
    Predict defect_type for every system_a defect on a panel using
    the specified trained model. When model_version is None the most
    recent registered model is used.
    """
    ph = get_placeholder(conn)
    model = _load_model(conn, model_version)
    if model is None:
        raise ValueError(
            "No trained model found. Call train_classifier first."
        )
    coefficients = model["coefficients"]
    classes      = model["classes"]
    mv           = model["model_version"]

    panel = conn.execute(
        f"SELECT * FROM panels WHERE panel_id = {ph}", (panel_id,)
    ).fetchone()
    if panel is None:
        raise ValueError(f"Panel not found: {panel_id!r}")
    panel = dict(panel)

    rows = conn.execute(
        f"""SELECT d.defect_id, d.panel_id, d.component_row, d.component_col,
                   d.size, d.confidence_score, d.x, d.y, d.defect_type,
                   c.region_id
            FROM defects d
            JOIN components c
              ON c.panel_id=d.panel_id
             AND c.component_row=d.component_row
             AND c.component_col=d.component_col
            WHERE d.panel_id={ph}
              AND d.source_system='system_a'
              AND c.active=1""",
        (panel_id,)
    ).fetchall()

    predictions: list[Prediction] = []
    for r in rows:
        rd = dict(r)
        feats = _extract_features(rd, panel)
        pred_type, conf, probs = _predict_one(feats, coefficients, classes)
        true_type = rd.get("defect_type")
        predictions.append(Prediction(
            defect_id=rd["defect_id"],
            panel_id=rd["panel_id"],
            predicted_type=pred_type,
            confidence=round(conf, 6),
            class_probs={c: round(p, 6) for c, p in probs.items()},
            true_type=true_type,
            correct=(true_type == pred_type) if true_type else None,
        ))

    if persist and predictions:
        _save_predictions(conn, predictions, mv)

    if predictions:
        corrects = sum(1 for p in predictions if p.correct)
        logger.info(
            "[%s] Predicted %d defects | model=%s | accuracy=%.3f",
            panel_id, len(predictions), mv, corrects / len(predictions),
        )
    return predictions


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _save_model(
    conn: Connection,
    result: TrainingResult,
    substrate_type: str | None,
) -> None:
    ph = get_placeholder(conn)
    with conn:
        conn.execute(
            f"INSERT INTO model_registry "
            f"(model_version, model_type, substrate_type, n_training_samples, "
            f"n_features, classes, accuracy, coefficients, feature_names, trained_at) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
            (
                result.model_version, "multinomial_logistic_regression",
                substrate_type, result.n_training_samples, result.n_features,
                json.dumps(result.classes),
                round(result.accuracy, 6),
                json.dumps(result.coefficients),
                json.dumps(result.feature_names),
                result.trained_at,
            )
        )


def _load_model(conn: Connection, model_version: str | None) -> dict | None:
    ph = get_placeholder(conn)
    if model_version:
        row = conn.execute(
            f"SELECT * FROM model_registry WHERE model_version={ph}",
            (model_version,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM model_registry ORDER BY trained_at DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return None
    return {
        "model_version": row["model_version"],
        "classes":       json.loads(row["classes"]),
        "coefficients":  json.loads(row["coefficients"]),
        "feature_names": json.loads(row["feature_names"]),
        "accuracy":      row["accuracy"],
    }


def _save_predictions(
    conn: Connection,
    predictions: list[Prediction],
    model_version: str,
) -> None:
    ph = get_placeholder(conn)
    now = datetime.now(timezone.utc).isoformat()
    rows = [
        (p.defect_id, p.panel_id, model_version,
         p.predicted_type, p.confidence,
         p.true_type, int(p.correct) if p.correct is not None else None,
         now)
        for p in predictions
    ]
    with conn:
        conn.executemany(
            f"INSERT INTO defect_predictions "
            f"(defect_id, panel_id, model_version, predicted_type, confidence, "
            f"true_type, correct, calculated_at) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
            rows
        )


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def evaluate_classifier(
    conn: Connection,
    *,
    model_version: str | None = None,
    substrate_type: str | None = None,
    source_system: str = "system_a",
) -> dict:
    """
    Evaluate the classifier against ground-truth defect_type labels.
    Returns overall accuracy and per-class precision and recall.
    """
    model = _load_model(conn, model_version)
    if model is None:
        raise ValueError("No trained model found.")

    coefficients = model["coefficients"]
    classes      = model["classes"]

    X, y, _refs = _load_training_set(conn, substrate_type, source_system)
    if not X:
        return {
            "model_version": model["model_version"],
            "n_samples": 0,
            "accuracy": 0.0,
            "per_class": {},
        }

    correct = 0
    tp = {c: 0 for c in classes}
    fp = {c: 0 for c in classes}
    fn = {c: 0 for c in classes}

    for xi, true in zip(X, y):
        pred, _, _ = _predict_one(xi, coefficients, classes)
        if pred == true:
            correct += 1
            tp[pred] += 1
        else:
            fp[pred] += 1
            if true in fn:
                fn[true] += 1

    per_class = {}
    for c in classes:
        precision = tp[c] / (tp[c] + fp[c]) if (tp[c] + fp[c]) > 0 else 0.0
        recall    = tp[c] / (tp[c] + fn[c]) if (tp[c] + fn[c]) > 0 else 0.0
        per_class[c] = {
            "precision": round(precision, 4),
            "recall":    round(recall, 4),
            "support":   tp[c] + fn[c],
        }

    return {
        "model_version": model["model_version"],
        "n_samples": len(X),
        "accuracy":  round(correct / len(X), 6),
        "per_class": per_class,
    }


def print_classifier_report(eval_result: dict) -> None:
    print(f"\n{'='*64}")
    print(f"  DEFECT CLASSIFIER EVALUATION")
    print(f"  Model: {eval_result['model_version']}")
    print(f"  Samples: {eval_result['n_samples']} | "
          f"Accuracy: {eval_result['accuracy']*100:.2f}%")
    print(f"{'='*64}")
    print(f"  {'Class':<20} {'Precision':>10} {'Recall':>10} {'Support':>10}")
    print(f"  {'-'*20} {'-'*10} {'-'*10} {'-'*10}")
    for cls, m in eval_result["per_class"].items():
        print(
            f"  {cls:<20} {m['precision']*100:>9.2f}% "
            f"{m['recall']*100:>9.2f}% {m['support']:>10}"
        )
    print()
