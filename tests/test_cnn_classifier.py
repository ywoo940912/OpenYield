"""
tests/test_cnn_classifier.py
-----------------------------
Tests for ai/cnn_classifier.py — pure NumPy CNN defect patch classifier.

Test organisation
-----------------
1. im2col / col2im utilities
2. Individual layer forward & backward shapes
3. CNN model (forward shape, n_params, predict)
4. Loss and metric helpers (softmax, cross-entropy, accuracy)
5. Database integration (image loading, training, persistence, comparison)
"""

from __future__ import annotations

import io
import math
import struct

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Module under test
# ---------------------------------------------------------------------------

from openyield.ai.cnn_classifier import (
    _im2col,
    _col2im,
    Conv2D,
    ReLU,
    MaxPool2D,
    GlobalAvgPool,
    Dense,
    CNN,
    _softmax,
    _cross_entropy_loss,
    _accuracy,
    _load_images_from_db,
    train_cnn,
    load_from_registry,
    compare_with_logistic,
    ClassifierComparison,
)


# ---------------------------------------------------------------------------
# PNG blob helper (avoids PIL in layer-level unit tests, but uses it in DB
# integration tests where it's a real dependency)
# ---------------------------------------------------------------------------

def _make_png_blob(seed: int = 0, h: int = 64, w: int = 64) -> bytes:
    """Create a minimal grayscale PNG blob for testing."""
    try:
        from PIL import Image
        rng = np.random.default_rng(seed)
        arr = rng.integers(0, 256, (h, w), dtype=np.uint8)
        buf = io.BytesIO()
        Image.fromarray(arr, mode="L").save(buf, format="PNG")
        return buf.getvalue()
    except ImportError:
        pytest.skip("Pillow not available")


# ---------------------------------------------------------------------------
# DB fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def mem_conn(tmp_path):
    from openyield.db.connection import get_connection
    from openyield.db.schema import initialize_schema
    conn = get_connection(tmp_path / "test.db")
    initialize_schema(conn)
    return conn


DEFECT_TYPES_WAFER = [
    "particle", "scratch", "pit", "crystal_defect",
    "metal_spike", "void", "bridging",
]


def _insert_images(conn, defect_types, n_per_type: int = 5):
    """Insert synthetic PNG blobs into defect_images for each defect type."""
    seed = 0
    for dtype in defect_types:
        for _ in range(n_per_type):
            blob = _make_png_blob(seed=seed)
            seed += 1
            conn.execute(
                "INSERT INTO defect_images (image_data, defect_type) VALUES (?, ?)",
                (blob, dtype),
            )
    conn.commit()


# ===========================================================================
# 1. im2col / col2im utilities
# ===========================================================================

class TestIm2Col:
    def test_output_shape(self):
        """im2col output: (N, out_H*out_W, C_in*kH*kW)."""
        x = np.random.randn(2, 1, 8, 8).astype(np.float32)
        col = _im2col(x, kH=3, kW=3)
        # out_H = 8-3+1 = 6, out_W = 6
        assert col.shape == (2, 6 * 6, 1 * 3 * 3)

    def test_output_shape_multichannel(self):
        x = np.random.randn(4, 3, 10, 10).astype(np.float32)
        col = _im2col(x, kH=3, kW=3)
        assert col.shape == (4, 8 * 8, 3 * 3 * 3)

    def test_patch_values_match_input(self):
        """Each row of col should match the corresponding receptive field."""
        x = np.arange(1 * 1 * 5 * 5, dtype=np.float32).reshape(1, 1, 5, 5)
        col = _im2col(x, kH=3, kW=3)
        # First patch (top-left): pixels [0:3, 0:3] = rows 0,1,2 of 5-wide input
        expected = np.array([0, 1, 2, 5, 6, 7, 10, 11, 12], dtype=np.float32)
        np.testing.assert_array_equal(col[0, 0, :], expected)

    def test_contiguous_output(self):
        """im2col must return a contiguous array (not a stride-tricks view)."""
        x = np.random.randn(1, 1, 6, 6).astype(np.float32)
        col = _im2col(x, kH=3, kW=3)
        assert col.flags["C_CONTIGUOUS"]


class TestCol2Im:
    def test_output_shape(self):
        """col2im output shape matches x_shape."""
        x_shape = (2, 1, 8, 8)
        d_col = np.random.randn(2, 6 * 6, 1 * 3 * 3).astype(np.float32)
        dx = _col2im(d_col, x_shape, kH=3, kW=3)
        assert dx.shape == x_shape

    def test_gradient_accumulation(self):
        """
        col2im must accumulate contributions from overlapping receptive fields.
        For a 1×1×5×5 input with a 3×3 kernel, the centre pixel (2,2)
        appears in all 9 patches — its gradient must be the sum of 9 values.
        """
        x_shape = (1, 1, 5, 5)
        d_col = np.ones((1, 3 * 3, 1 * 3 * 3), dtype=np.float32)
        dx = _col2im(d_col, x_shape, kH=3, kW=3)
        # Centre pixel (2,2) is covered by all 9 (out_H=3, out_W=3) patches
        assert dx[0, 0, 2, 2] == pytest.approx(9.0)


# ===========================================================================
# 2. Individual layer forward / backward
# ===========================================================================

class TestConv2D:
    def _make(self):
        return Conv2D(in_channels=1, out_channels=8, kernel_size=3)

    def test_forward_shape(self):
        layer = self._make()
        x = np.random.randn(2, 1, 64, 64).astype(np.float32)
        out = layer.forward(x)
        assert out.shape == (2, 8, 62, 62)

    def test_backward_input_shape(self):
        layer = self._make()
        x = np.random.randn(2, 1, 64, 64).astype(np.float32)
        out = layer.forward(x)
        d_out = np.ones_like(out)
        dx = layer.backward(d_out)
        assert dx.shape == x.shape

    def test_backward_weight_gradient_shape(self):
        layer = self._make()
        x = np.random.randn(2, 1, 64, 64).astype(np.float32)
        out = layer.forward(x)
        layer.backward(np.ones_like(out))
        assert layer.dW.shape == layer.W.shape
        assert layer.db.shape == layer.b.shape

    def test_update_changes_weights(self):
        layer = self._make()
        x = np.random.randn(2, 1, 8, 8).astype(np.float32)
        out = layer.forward(x)
        layer.backward(np.ones_like(out))
        W_before = layer.W.copy()
        layer.update(lr=0.1)
        assert not np.allclose(layer.W, W_before)


class TestReLU:
    def test_forward_zeros_negatives(self):
        layer = ReLU()
        x = np.array([-1.0, 0.0, 1.0, -0.5, 2.0], dtype=np.float32)
        out = layer.forward(x)
        np.testing.assert_array_equal(out, np.array([0.0, 0.0, 1.0, 0.0, 2.0]))

    def test_backward_masks_gradient(self):
        layer = ReLU()
        x = np.array([-1.0, 0.5, -0.5, 2.0], dtype=np.float32)
        layer.forward(x)
        d_out = np.array([1.0, 1.0, 1.0, 1.0], dtype=np.float32)
        dx = layer.backward(d_out)
        np.testing.assert_array_equal(dx, np.array([0.0, 1.0, 0.0, 1.0]))

    def test_backward_shape_preserved(self):
        layer = ReLU()
        x = np.random.randn(2, 8, 30, 30).astype(np.float32)
        out = layer.forward(x)
        dx = layer.backward(np.ones_like(out))
        assert dx.shape == x.shape


class TestMaxPool2D:
    def test_forward_shape(self):
        layer = MaxPool2D()
        x = np.random.randn(2, 8, 62, 62).astype(np.float32)
        out = layer.forward(x)
        assert out.shape == (2, 8, 31, 31)

    def test_forward_selects_max(self):
        """2×2 pool should pick the largest value in each block."""
        layer = MaxPool2D()
        x = np.array([[[[1, 3], [2, 4]]]], dtype=np.float32)  # (1,1,2,2)
        out = layer.forward(x)
        assert out[0, 0, 0, 0] == pytest.approx(4.0)

    def test_backward_shape(self):
        layer = MaxPool2D()
        x = np.random.randn(2, 8, 30, 30).astype(np.float32)
        out = layer.forward(x)
        dx = layer.backward(np.ones_like(out))
        assert dx.shape == x.shape

    def test_backward_routes_to_max(self):
        """Gradient should go to the winning position only."""
        layer = MaxPool2D()
        x = np.array([[[[1.0, 3.0], [2.0, 0.5]]]], dtype=np.float32)
        layer.forward(x)
        d_out = np.array([[[[5.0]]]], dtype=np.float32)
        dx = layer.backward(d_out)
        # Max was at position (0,1) → dx[0,0,0,1] = 5.0, others 0
        assert dx[0, 0, 0, 1] == pytest.approx(5.0)
        assert dx[0, 0, 0, 0] == pytest.approx(0.0)
        assert dx[0, 0, 1, 0] == pytest.approx(0.0)


class TestGlobalAvgPool:
    def test_forward_shape(self):
        layer = GlobalAvgPool()
        x = np.random.randn(3, 16, 14, 14).astype(np.float32)
        out = layer.forward(x)
        assert out.shape == (3, 16)

    def test_forward_is_mean(self):
        layer = GlobalAvgPool()
        x = np.ones((1, 4, 3, 3), dtype=np.float32) * 7.0
        out = layer.forward(x)
        np.testing.assert_allclose(out, np.full((1, 4), 7.0))

    def test_backward_shape(self):
        layer = GlobalAvgPool()
        x = np.random.randn(2, 16, 14, 14).astype(np.float32)
        out = layer.forward(x)
        dx = layer.backward(np.ones_like(out))
        assert dx.shape == x.shape

    def test_backward_distributes_equally(self):
        """Each spatial position should receive gradient / (H*W)."""
        layer = GlobalAvgPool()
        x = np.ones((1, 1, 2, 2), dtype=np.float32)
        layer.forward(x)
        d_out = np.array([[8.0]], dtype=np.float32)
        dx = layer.backward(d_out)
        # 8.0 / 4 = 2.0 at each position
        np.testing.assert_allclose(dx, np.full((1, 1, 2, 2), 2.0))


class TestDense:
    def test_forward_shape(self):
        layer = Dense(16, 7)
        x = np.random.randn(4, 16).astype(np.float32)
        out = layer.forward(x)
        assert out.shape == (4, 7)

    def test_backward_input_shape(self):
        layer = Dense(16, 7)
        x = np.random.randn(4, 16).astype(np.float32)
        out = layer.forward(x)
        dx = layer.backward(np.ones_like(out))
        assert dx.shape == x.shape

    def test_backward_weight_gradient_shape(self):
        layer = Dense(16, 7)
        x = np.random.randn(4, 16).astype(np.float32)
        out = layer.forward(x)
        layer.backward(np.ones_like(out))
        assert layer.dW.shape == (16, 7)
        assert layer.db.shape == (7,)


# ===========================================================================
# 3. CNN model
# ===========================================================================

class TestCNN:
    def test_forward_shape(self):
        cnn = CNN(n_classes=7, seed=0)
        x = np.random.randn(4, 1, 64, 64).astype(np.float32)
        out = cnn.forward(x)
        assert out.shape == (4, 7)

    def test_n_params_7_classes(self):
        """
        Expected parameter count for 7 classes:
          Conv2D-1 : 8 × (1×3×3 + 1) =   80
          Conv2D-2 : 16 × (8×3×3 + 1) = 1168
          Dense    : 16×7 + 7          =  119
          Total                         = 1367
        """
        cnn = CNN(n_classes=7, seed=0)
        assert cnn.n_params() == 1367

    def test_predict_returns_class_indices(self):
        cnn = CNN(n_classes=5, seed=1)
        x = np.random.randn(8, 1, 64, 64).astype(np.float32)
        preds = cnn.predict(x)
        assert preds.shape == (8,)
        assert preds.min() >= 0
        assert preds.max() < 5

    def test_predict_proba_sums_to_one(self):
        cnn = CNN(n_classes=7, seed=2)
        x = np.random.randn(6, 1, 64, 64).astype(np.float32)
        probs = cnn.predict_proba(x)
        np.testing.assert_allclose(probs.sum(axis=1), np.ones(6), atol=1e-5)

    def test_predict_proba_nonnegative(self):
        cnn = CNN(n_classes=7, seed=3)
        x = np.random.randn(4, 1, 64, 64).astype(np.float32)
        probs = cnn.predict_proba(x)
        assert (probs >= 0).all()

    def test_seed_reproducibility(self):
        cnn_a = CNN(n_classes=7, seed=99)
        cnn_b = CNN(n_classes=7, seed=99)
        x = np.random.randn(2, 1, 64, 64).astype(np.float32)
        np.testing.assert_array_equal(cnn_a.forward(x), cnn_b.forward(x))


# ===========================================================================
# 4. Loss and metric helpers
# ===========================================================================

class TestSoftmax:
    def test_sums_to_one(self):
        logits = np.random.randn(8, 7).astype(np.float32)
        probs = _softmax(logits)
        np.testing.assert_allclose(probs.sum(axis=1), np.ones(8), atol=1e-5)

    def test_nonnegative(self):
        logits = np.random.randn(4, 5).astype(np.float32)
        assert (_softmax(logits) >= 0).all()

    def test_numerical_stability(self):
        """Very large logits should not produce NaN/inf."""
        logits = np.array([[1e9, 0.0, 0.0]], dtype=np.float32)
        probs = _softmax(logits)
        assert not np.any(np.isnan(probs))
        assert not np.any(np.isinf(probs))

    def test_argmax_preserved(self):
        logits = np.array([[0.1, 3.5, 0.2]], dtype=np.float32)
        assert np.argmax(_softmax(logits)) == 1


class TestCrossEntropy:
    def test_loss_shape_and_gradient_shape(self):
        logits = np.random.randn(4, 7).astype(np.float32)
        y = np.array([0, 2, 5, 1], dtype=np.int32)
        loss, grad = _cross_entropy_loss(logits, y)
        assert isinstance(loss, float)
        assert grad.shape == logits.shape

    def test_loss_nonnegative(self):
        logits = np.random.randn(4, 7).astype(np.float32)
        y = np.array([0, 1, 2, 3], dtype=np.int32)
        loss, _ = _cross_entropy_loss(logits, y)
        assert loss >= 0.0

    def test_gradient_sums_to_zero(self):
        """
        Cross-entropy + softmax gradient: (probs - one_hot) / N.
        Per-sample sum = (1 - 1) / N = 0, so total gradient sum ≈ 0.
        """
        logits = np.random.randn(4, 7).astype(np.float32)
        y = np.array([0, 1, 2, 3], dtype=np.int32)
        _, grad = _cross_entropy_loss(logits, y)
        np.testing.assert_allclose(grad.sum(), 0.0, atol=1e-5)

    def test_gradient_correct_sign(self):
        """
        Gradient for correct class should be negative (pull prediction up),
        gradient for incorrect classes should be positive (push prediction down).
        Here we use logits that strongly predict the wrong class to make the
        gradient sign unambiguous.
        """
        logits = np.array([[0.0, 5.0, 0.0]], dtype=np.float32)   # network says class 1
        y = np.array([0], dtype=np.int32)                          # true class is 0
        _, grad = _cross_entropy_loss(logits, y)
        assert grad[0, 0] < 0   # true class: pull up
        assert grad[0, 1] > 0   # wrong class: push down


class TestAccuracy:
    def test_all_correct(self):
        logits = np.zeros((3, 4), dtype=np.float32)
        logits[0, 0] = 10.0
        logits[1, 2] = 10.0
        logits[2, 3] = 10.0
        y = np.array([0, 2, 3], dtype=np.int32)
        assert _accuracy(logits, y) == pytest.approx(1.0)

    def test_none_correct(self):
        logits = np.zeros((3, 4), dtype=np.float32)
        logits[:, 1] = 10.0   # always predicts class 1
        y = np.array([0, 2, 3], dtype=np.int32)
        assert _accuracy(logits, y) == pytest.approx(0.0)

    def test_half_correct(self):
        logits = np.zeros((4, 2), dtype=np.float32)
        logits[:, 0] = 10.0   # always predicts class 0
        y = np.array([0, 1, 0, 1], dtype=np.int32)
        assert _accuracy(logits, y) == pytest.approx(0.5)


# ===========================================================================
# 5. Database integration
# ===========================================================================

class TestLoadImages:
    def test_empty_table_raises(self, mem_conn):
        with pytest.raises(ValueError, match="empty"):
            _load_images_from_db(mem_conn)

    def test_returns_correct_shape(self, mem_conn):
        _insert_images(mem_conn, DEFECT_TYPES_WAFER, n_per_type=3)
        X, y, classes = _load_images_from_db(mem_conn)
        n = 7 * 3
        assert X.shape == (n, 1, 64, 64)
        assert y.shape == (n,)

    def test_pixel_values_in_unit_range(self, mem_conn):
        _insert_images(mem_conn, DEFECT_TYPES_WAFER, n_per_type=2)
        X, _, _ = _load_images_from_db(mem_conn)
        assert X.min() >= 0.0
        assert X.max() <= 1.0

    def test_labels_in_range(self, mem_conn):
        _insert_images(mem_conn, DEFECT_TYPES_WAFER, n_per_type=3)
        X, y, classes = _load_images_from_db(mem_conn)
        assert y.min() >= 0
        assert y.max() < len(classes)

    def test_class_list_sorted(self, mem_conn):
        _insert_images(mem_conn, ["void", "particle", "scratch"], n_per_type=2)
        _, _, classes = _load_images_from_db(mem_conn)
        assert classes == sorted(classes)

    def test_limit_parameter(self, mem_conn):
        _insert_images(mem_conn, DEFECT_TYPES_WAFER, n_per_type=4)
        X, _, _ = _load_images_from_db(mem_conn, limit=10)
        assert len(X) == 10

    def test_n_classes_matches_unique_types(self, mem_conn):
        _insert_images(mem_conn, ["particle", "scratch", "void"], n_per_type=3)
        _, _, classes = _load_images_from_db(mem_conn)
        assert len(classes) == 3


class TestTrainCNN:
    def test_train_runs_and_returns(self, mem_conn):
        _insert_images(mem_conn, DEFECT_TYPES_WAFER, n_per_type=4)
        cnn, history = train_cnn(
            mem_conn, epochs=2, batch_size=8, lr=0.01, persist=False, seed=0,
        )
        assert isinstance(cnn, CNN)
        assert history.epochs_run == 2

    def test_history_length_matches_epochs(self, mem_conn):
        _insert_images(mem_conn, DEFECT_TYPES_WAFER, n_per_type=4)
        _, history = train_cnn(
            mem_conn, epochs=3, batch_size=8, persist=False, seed=0,
        )
        assert len(history.train_loss) == 3
        assert len(history.val_loss)   == 3
        assert len(history.train_acc)  == 3
        assert len(history.val_acc)    == 3

    def test_train_loss_is_positive(self, mem_conn):
        _insert_images(mem_conn, DEFECT_TYPES_WAFER, n_per_type=4)
        _, history = train_cnn(
            mem_conn, epochs=2, batch_size=8, persist=False, seed=0,
        )
        for loss in history.train_loss:
            assert loss > 0.0

    def test_accuracy_in_unit_interval(self, mem_conn):
        _insert_images(mem_conn, DEFECT_TYPES_WAFER, n_per_type=4)
        _, history = train_cnn(
            mem_conn, epochs=2, batch_size=8, persist=False, seed=0,
        )
        for acc in history.val_acc:
            assert 0.0 <= acc <= 1.0

    def test_persist_saves_to_registry(self, mem_conn):
        _insert_images(mem_conn, DEFECT_TYPES_WAFER, n_per_type=4)
        train_cnn(mem_conn, epochs=2, batch_size=8, lr=0.01, persist=True, seed=0)
        row = mem_conn.execute(
            "SELECT model_type FROM model_registry WHERE model_type = 'cnn'"
        ).fetchone()
        assert row is not None

    def test_n_classes_propagates(self, mem_conn):
        _insert_images(mem_conn, ["particle", "scratch", "pit"], n_per_type=4)
        cnn, _ = train_cnn(
            mem_conn, epochs=1, batch_size=4, persist=False, seed=0,
        )
        assert cnn.n_classes == 3


class TestLoadFromRegistry:
    def test_roundtrip_weights(self, mem_conn):
        """Saved and loaded CNN must produce identical predictions."""
        _insert_images(mem_conn, DEFECT_TYPES_WAFER, n_per_type=4)
        cnn_orig, _ = train_cnn(
            mem_conn, epochs=2, batch_size=8, persist=True, seed=42,
        )
        cnn_loaded, classes_loaded = load_from_registry(mem_conn)

        x = np.random.default_rng(0).random((3, 1, 64, 64)).astype(np.float32)
        out_orig   = cnn_orig.forward(x)
        out_loaded = cnn_loaded.forward(x)
        np.testing.assert_allclose(out_orig, out_loaded, atol=1e-5)

    def test_classes_preserved(self, mem_conn):
        _insert_images(mem_conn, DEFECT_TYPES_WAFER, n_per_type=4)
        train_cnn(mem_conn, epochs=1, batch_size=8, persist=True, seed=0)
        _, classes = load_from_registry(mem_conn)
        assert sorted(classes) == sorted(DEFECT_TYPES_WAFER)

    def test_no_model_raises(self, mem_conn):
        with pytest.raises(ValueError, match="No CNN found"):
            load_from_registry(mem_conn)

    def test_n_classes_matches(self, mem_conn):
        _insert_images(mem_conn, ["a", "b", "c"], n_per_type=4)
        train_cnn(mem_conn, epochs=1, batch_size=4, persist=True, seed=0)
        cnn, _ = load_from_registry(mem_conn)
        assert cnn.n_classes == 3


class TestCompareWithLogistic:
    def test_returns_comparison_type(self, mem_conn):
        _insert_images(mem_conn, DEFECT_TYPES_WAFER, n_per_type=4)
        report = compare_with_logistic(
            mem_conn, epochs=2, batch_size=8, lr=0.01, seed=0,
        )
        assert isinstance(report, ClassifierComparison)

    def test_cnn_accuracy_in_unit_interval(self, mem_conn):
        _insert_images(mem_conn, DEFECT_TYPES_WAFER, n_per_type=4)
        report = compare_with_logistic(
            mem_conn, epochs=2, batch_size=8, seed=0,
        )
        assert 0.0 <= report.cnn_val_accuracy <= 1.0

    def test_lr_accuracy_none_when_not_in_registry(self, mem_conn):
        """If logistic regression has never been saved, lr_accuracy should be None."""
        _insert_images(mem_conn, DEFECT_TYPES_WAFER, n_per_type=4)
        report = compare_with_logistic(
            mem_conn, epochs=2, batch_size=8, seed=0,
        )
        assert report.lr_accuracy is None

    def test_lr_accuracy_parsed_when_present(self, mem_conn):
        """If model_registry has a logistic regression row, lr_accuracy is parsed."""
        _insert_images(mem_conn, DEFECT_TYPES_WAFER, n_per_type=4)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        mem_conn.execute(
            "INSERT OR REPLACE INTO model_registry "
            "(model_type, trained_at, model_blob, notes) VALUES (?,?,?,?)",
            ("logistic_regression", now, b"", "acc=0.872 classes=7"),
        )
        mem_conn.commit()

        report = compare_with_logistic(
            mem_conn, epochs=2, batch_size=8, seed=0,
        )
        assert report.lr_accuracy == pytest.approx(0.872, rel=1e-3)

    def test_n_classes_matches_data(self, mem_conn):
        _insert_images(mem_conn, DEFECT_TYPES_WAFER, n_per_type=4)
        report = compare_with_logistic(
            mem_conn, epochs=2, batch_size=8, seed=0,
        )
        assert report.n_classes == 7

    def test_cnn_n_params_positive(self, mem_conn):
        _insert_images(mem_conn, DEFECT_TYPES_WAFER, n_per_type=4)
        report = compare_with_logistic(
            mem_conn, epochs=1, batch_size=8, seed=0,
        )
        assert report.cnn_n_params > 0

    def test_train_images_plus_val_equals_total(self, mem_conn):
        n_per_type = 5
        total = 7 * n_per_type
        _insert_images(mem_conn, DEFECT_TYPES_WAFER, n_per_type=n_per_type)
        report = compare_with_logistic(
            mem_conn, epochs=1, batch_size=8, seed=0,
        )
        assert report.n_images_train + report.n_images_val == total

    def test_notes_contains_accuracy(self, mem_conn):
        _insert_images(mem_conn, DEFECT_TYPES_WAFER, n_per_type=4)
        report = compare_with_logistic(
            mem_conn, epochs=1, batch_size=8, seed=0,
        )
        assert "CNN val_acc=" in report.notes
