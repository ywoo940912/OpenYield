"""
tests/test_ai_classifier.py
----------------------------
Tests for the AI defect classifier (Phase 1).
"""

import pytest
from openyield.ai.classifier import (
    train_classifier,
    predict_panel,
    evaluate_classifier,
    _softmax,
    _dot,
    _extract_features,
    FEATURE_NAMES,
    N_FEATURES,
)
from openyield.ingestion.ingest import (
    upsert_panel, upsert_component, upsert_defect
)


# ---------------------------------------------------------------------------
# Math primitives
# ---------------------------------------------------------------------------

def test_softmax_sums_to_one():
    probs = _softmax([1.0, 2.0, 3.0])
    assert sum(probs) == pytest.approx(1.0)
    assert all(p > 0 for p in probs)


def test_softmax_numerical_stability_large_logits():
    probs = _softmax([1000.0, 1001.0, 1002.0])
    assert sum(probs) == pytest.approx(1.0)
    assert all(0 <= p <= 1 for p in probs)


def test_softmax_uniform_when_equal():
    probs = _softmax([5.0, 5.0, 5.0])
    for p in probs:
        assert p == pytest.approx(1/3)


def test_dot_basic():
    assert _dot([1.0, 2.0, 3.0], [4.0, 5.0, 6.0]) == pytest.approx(32.0)


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------

def test_extract_features_returns_correct_length():
    row = {
        "size": 0.5, "confidence_score": 0.8,
        "x": 50.0, "y": 50.0,
        "component_row": 1, "component_col": 2,
        "region_id": "zone_center",
        "substrate_type": "wafer",
        "rows": 4, "cols": 4,
    }
    feats = _extract_features(row, row)
    assert len(feats) == N_FEATURES
    assert feats[0] == 1.0  # bias


def test_extract_features_zone_one_hot():
    row = {
        "size": 0.5, "confidence_score": 0.8,
        "x": 50.0, "y": 50.0,
        "component_row": 1, "component_col": 2,
        "region_id": "zone_edge",
        "substrate_type": "wafer",
        "rows": 4, "cols": 4,
    }
    feats = _extract_features(row, row)
    zone_edge_idx = FEATURE_NAMES.index("zone_edge")
    zone_center_idx = FEATURE_NAMES.index("zone_center")
    assert feats[zone_edge_idx] == 1.0
    assert feats[zone_center_idx] == 0.0


def test_extract_features_substrate_indicator():
    row = {
        "size": 0.5, "confidence_score": 0.8,
        "x": 50.0, "y": 50.0,
        "component_row": 1, "component_col": 2,
        "region_id": "region_NW",
        "substrate_type": "glass_panel",
        "rows": 3, "cols": 3,
    }
    feats = _extract_features(row, row)
    is_wafer_idx = FEATURE_NAMES.index("is_wafer")
    assert feats[is_wafer_idx] == 0.0


# ---------------------------------------------------------------------------
# Helpers for training data
# ---------------------------------------------------------------------------

def _make_labeled_dataset(conn):
    """
    Create a small labeled dataset where defect_type correlates
    strongly with size — the classifier should learn this perfectly.
    """
    with conn:
        upsert_panel(conn, "WF_AI1", "TEST", "wafer", 4, 4)
        for r in range(4):
            for c in range(4):
                upsert_component(
                    conn, "WF_AI1", r, c, "zone_center",
                    float(c * 28), float(r * 28)
                )
        # particles: small size 0.1
        for i in range(15):
            upsert_defect(
                conn, "WF_AI1", i % 4, i % 4, "system_a",
                "particle", float(i * 0.5), float(i * 0.3),
                0.1, 0.8
            )
        # scratches: large size 2.0
        for i in range(15):
            upsert_defect(
                conn, "WF_AI1", (i+1) % 4, (i+1) % 4, "system_a",
                "scratch", float(i * 0.5 + 100), float(i * 0.3 + 100),
                2.0, 0.8
            )


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def test_train_classifier_basic(mem_conn):
    _make_labeled_dataset(mem_conn)
    result = train_classifier(mem_conn, max_iterations=200, persist=False)
    assert result.n_training_samples == 30
    assert len(result.classes) == 2
    assert "particle" in result.classes
    assert "scratch"  in result.classes
    assert result.n_features == N_FEATURES
    assert len(result.coefficients) == 2
    assert all(len(row) == N_FEATURES for row in result.coefficients)


def test_train_classifier_learns_separable_data(mem_conn):
    """When particle is small and scratch is large, training acc → 1.0."""
    _make_labeled_dataset(mem_conn)
    result = train_classifier(mem_conn, max_iterations=400,
                              learning_rate=0.1, persist=False)
    assert result.accuracy >= 0.90


def test_train_classifier_persists(mem_conn):
    _make_labeled_dataset(mem_conn)
    result = train_classifier(mem_conn, max_iterations=100, persist=True)
    row = mem_conn.execute(
        "SELECT * FROM model_registry WHERE model_version=?",
        (result.model_version,)
    ).fetchone()
    assert row is not None
    assert row["n_training_samples"] == 30


def test_train_classifier_no_persist(mem_conn):
    _make_labeled_dataset(mem_conn)
    train_classifier(mem_conn, max_iterations=50, persist=False)
    count = mem_conn.execute(
        "SELECT COUNT(*) FROM model_registry"
    ).fetchone()[0]
    assert count == 0


def test_train_classifier_insufficient_data(mem_conn):
    with mem_conn:
        upsert_panel(mem_conn, "WF_S", "TEST", "wafer", 2, 2)
        for r in range(2):
            for c in range(2):
                upsert_component(mem_conn, "WF_S", r, c, "zone_center",
                                 float(c*28), float(r*28))
        upsert_defect(mem_conn, "WF_S", 0, 0, "system_a",
                      "particle", 1.0, 1.0, 0.1, 0.8)
    with pytest.raises(ValueError, match="at least 10"):
        train_classifier(mem_conn, max_iterations=50, persist=False)


def test_train_classifier_single_class_error(mem_conn):
    with mem_conn:
        upsert_panel(mem_conn, "WF_1", "TEST", "wafer", 4, 4)
        for r in range(4):
            for c in range(4):
                upsert_component(mem_conn, "WF_1", r, c, "zone_center",
                                 float(c*28), float(r*28))
        for i in range(15):
            upsert_defect(mem_conn, "WF_1", i % 4, i % 4, "system_a",
                          "particle", float(i), 1.0, 0.1, 0.8)
    with pytest.raises(ValueError, match="2 distinct"):
        train_classifier(mem_conn, max_iterations=50, persist=False)


def test_training_loss_decreases(mem_conn):
    """Final loss must be strictly less than initial loss."""
    _make_labeled_dataset(mem_conn)
    # Initial uniform softmax → loss = ln(K) per sample = ln(2) ≈ 0.693
    result = train_classifier(mem_conn, max_iterations=200,
                              learning_rate=0.1, persist=False)
    import math
    assert result.final_loss < math.log(len(result.classes))


# ---------------------------------------------------------------------------
# Prediction
# ---------------------------------------------------------------------------

def test_predict_panel_returns_predictions(mem_conn):
    _make_labeled_dataset(mem_conn)
    train_classifier(mem_conn, max_iterations=200, persist=True)
    preds = predict_panel(mem_conn, "WF_AI1", persist=False)
    assert len(preds) == 30
    for p in preds:
        assert p.predicted_type in ("particle", "scratch")
        assert 0.0 <= p.confidence <= 1.0
        assert sum(p.class_probs.values()) == pytest.approx(1.0, abs=1e-4)


def test_predict_panel_persists(mem_conn):
    _make_labeled_dataset(mem_conn)
    train_classifier(mem_conn, max_iterations=200, persist=True)
    preds = predict_panel(mem_conn, "WF_AI1", persist=True)
    rows = mem_conn.execute(
        "SELECT COUNT(*) FROM defect_predictions WHERE panel_id='WF_AI1'"
    ).fetchone()[0]
    assert rows == len(preds)


def test_predict_panel_no_persist(mem_conn):
    _make_labeled_dataset(mem_conn)
    train_classifier(mem_conn, max_iterations=200, persist=True)
    predict_panel(mem_conn, "WF_AI1", persist=False)
    rows = mem_conn.execute(
        "SELECT COUNT(*) FROM defect_predictions"
    ).fetchone()[0]
    assert rows == 0


def test_predict_panel_not_found(mem_conn):
    _make_labeled_dataset(mem_conn)
    train_classifier(mem_conn, max_iterations=100, persist=True)
    with pytest.raises(ValueError, match="not found"):
        predict_panel(mem_conn, "NONEXISTENT", persist=False)


def test_predict_no_model_trained(mem_conn):
    _make_labeled_dataset(mem_conn)
    with pytest.raises(ValueError, match="No trained model"):
        predict_panel(mem_conn, "WF_AI1", persist=False)


def test_predict_records_correctness(mem_conn):
    _make_labeled_dataset(mem_conn)
    train_classifier(mem_conn, max_iterations=400,
                     learning_rate=0.1, persist=True)
    preds = predict_panel(mem_conn, "WF_AI1", persist=False)
    correct = sum(1 for p in preds if p.correct)
    assert correct >= int(0.8 * len(preds))


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def test_evaluate_classifier_metrics(mem_conn):
    _make_labeled_dataset(mem_conn)
    train_classifier(mem_conn, max_iterations=400, learning_rate=0.1,
                     persist=True)
    metrics = evaluate_classifier(mem_conn)
    assert metrics["n_samples"] == 30
    assert 0.0 <= metrics["accuracy"] <= 1.0
    for cls, m in metrics["per_class"].items():
        assert 0.0 <= m["precision"] <= 1.0
        assert 0.0 <= m["recall"]    <= 1.0
        assert m["support"] >= 0


def test_evaluate_no_model(mem_conn):
    with pytest.raises(ValueError, match="No trained model"):
        evaluate_classifier(mem_conn)


def test_evaluate_returns_known_classes(mem_conn):
    _make_labeled_dataset(mem_conn)
    train_classifier(mem_conn, max_iterations=100, persist=True)
    metrics = evaluate_classifier(mem_conn)
    assert "particle" in metrics["per_class"]
    assert "scratch"  in metrics["per_class"]
