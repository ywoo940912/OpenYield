"""
ai/cnn_classifier.py
---------------------
Author: Yeonkuk Woo

Convolutional Neural Network defect patch classifier.
Pure NumPy implementation — no PyTorch, TensorFlow, or scikit-learn.

Architecture
------------
    Input       : 64 × 64 × 1 grayscale defect patch
    Conv2D-1    : 8 filters, 3×3, valid padding → ReLU     [62 × 62 × 8]
    MaxPool2D   : 2×2, stride 2                             [31 × 31 × 8]
    Conv2D-2    : 16 filters, 3×3, valid padding → ReLU    [29 × 29 × 16]
    MaxPool2D   : 2×2, stride 2                             [14 × 14 × 16]
    GlobalAvgPool                                            [16]
    Dense       : 16 → n_classes                            [n_classes]
    Softmax (at loss)

Trainable parameters: ~1,360 for 7 defect classes.  Extremely lightweight —
no overfitting risk on the synthetic defect patch dataset.

im2col Convolution
------------------
Convolution is implemented via im2col (Chellapilla et al., 2006): the input
is rearranged into a column matrix of shape (N, out_H×out_W, C_in×kH×kW)
and the kernel reshaped to (C_out, C_in×kH×kW). A single batched matrix
multiply produces all output activations with no Python-level spatial loops.

Backward pass uses the matching col2im operation to propagate gradients
through the convolutional receptive fields.

Training
--------
    Loss      : Categorical cross-entropy
    Optimizer : Mini-batch SGD + momentum (μ = 0.9)
    Init      : He initialization for Conv layers; Xavier for Dense

Database Integration
--------------------
    Load images : SELECT image_data, defect_type FROM defect_images
    Save model  : INSERT OR REPLACE INTO model_registry (model_type='cnn', ...)
    Load model  : SELECT model_blob FROM model_registry WHERE model_type='cnn'

References
----------
[1] K. Chellapilla, S. Puri, P. Simard, "High Performance Convolutional Neural
    Networks for Document Processing," IWFHR, 2006.
[2] Y. LeCun et al., "Gradient-based learning applied to document recognition,"
    Proc. IEEE, 86(11):2278–2324, 1998.
"""

from __future__ import annotations

import io
import logging
import math
import pickle
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np

from openyield.db.connection import get_placeholder

logger = logging.getLogger(__name__)
Connection = Any

try:
    from PIL import Image as _PILImage
    _PIL_AVAILABLE = True
except ImportError:
    _PILImage = None       # type: ignore[assignment]
    _PIL_AVAILABLE = False


# ---------------------------------------------------------------------------
# Image decoding
# ---------------------------------------------------------------------------

def _decode_png(blob: bytes) -> np.ndarray:
    """Decode a PNG blob to a (H, W) float32 array normalised to [0, 1]."""
    if not _PIL_AVAILABLE:
        raise ImportError(
            "Pillow is required to decode defect image blobs. "
            "Install with: pip install pillow"
        )
    img = _PILImage.open(io.BytesIO(blob)).convert("L")
    return np.array(img, dtype=np.float32) / 255.0


# ---------------------------------------------------------------------------
# im2col / col2im
# ---------------------------------------------------------------------------

def _im2col(x: np.ndarray, kH: int, kW: int) -> np.ndarray:
    """
    Rearrange image patches into columns for batch convolution.

    Parameters
    ----------
    x   : (N, C_in, H, W) input feature map
    kH  : kernel height
    kW  : kernel width

    Returns
    -------
    col : (N, out_H × out_W, C_in × kH × kW) — each row is one receptive field.
          Last dimension ordering is (C_in, kH, kW) C-order, matching
          Conv weight reshaped as (C_out, C_in×kH×kW).
    """
    N, C, H, W = x.shape
    out_H = H - kH + 1
    out_W = W - kW + 1

    # stride_tricks extracts all (kH×kW) patches without copying data.
    shape = (N, C, out_H, out_W, kH, kW)
    strides = (
        x.strides[0],
        x.strides[1],
        x.strides[2],
        x.strides[3],
        x.strides[2],
        x.strides[3],
    )
    patches = np.lib.stride_tricks.as_strided(x, shape=shape, strides=strides)
    # (N, C, out_H, out_W, kH, kW) → (N, out_H, out_W, C, kH, kW) → flatten
    col = patches.transpose(0, 2, 3, 1, 4, 5).reshape(N, out_H * out_W, C * kH * kW)
    return col.copy()  # contiguous copy avoids stride-tricks aliasing in backward


def _col2im(
    d_col: np.ndarray,
    x_shape: tuple[int, int, int, int],
    kH: int,
    kW: int,
) -> np.ndarray:
    """
    Scatter column-matrix gradient back to input gradient (inverse of im2col).

    Parameters
    ----------
    d_col   : (N, out_H × out_W, C_in × kH × kW)
    x_shape : (N, C_in, H, W) — original input shape
    kH, kW  : kernel size

    Returns
    -------
    dx : (N, C_in, H, W)
    """
    N, C, H, W = x_shape
    out_H = H - kH + 1
    out_W = W - kW + 1

    d_col_4d = d_col.reshape(N, out_H, out_W, C, kH, kW)
    dx = np.zeros((N, C, H, W), dtype=d_col.dtype)
    for ki in range(kH):
        for kj in range(kW):
            # d_col_4d[:, :, :, :, ki, kj] → (N, out_H, out_W, C)
            # Add to dx[:, :, ki:ki+out_H, kj:kj+out_W] — (N, C, out_H, out_W)
            dx[:, :, ki:ki + out_H, kj:kj + out_W] += (
                d_col_4d[:, :, :, :, ki, kj].transpose(0, 3, 1, 2)
            )
    return dx


# ---------------------------------------------------------------------------
# Layer classes
# ---------------------------------------------------------------------------

class Conv2D:
    """
    2D convolution — valid padding (no padding), stride 1.

    Weight init: He (suitable for downstream ReLU activations).
    """

    def __init__(self, in_channels: int, out_channels: int, kernel_size: int = 3) -> None:
        self.C_in  = in_channels
        self.C_out = out_channels
        self.k     = kernel_size

        fan_in = in_channels * kernel_size * kernel_size
        std = math.sqrt(2.0 / fan_in) if fan_in > 0 else 0.01
        self.W: np.ndarray = (
            np.random.randn(out_channels, in_channels, kernel_size, kernel_size)
            .astype(np.float32) * std
        )
        self.b: np.ndarray = np.zeros(out_channels, dtype=np.float32)

        # SGD momentum buffers
        self.vW = np.zeros_like(self.W)
        self.vb = np.zeros_like(self.b)
        self.dW = np.zeros_like(self.W)
        self.db = np.zeros_like(self.b)

        self._col:     np.ndarray | None = None
        self._x_shape: tuple | None      = None

    def forward(self, x: np.ndarray) -> np.ndarray:
        """x: (N, C_in, H, W) → (N, C_out, out_H, out_W)"""
        N, C, H, W = x.shape
        k = self.k
        out_H = H - k + 1
        out_W = W - k + 1

        col = _im2col(x, k, k)                  # (N, out_H*out_W, C_in*k*k)
        self._col     = col
        self._x_shape = x.shape

        W_flat = self.W.reshape(self.C_out, -1)  # (C_out, C_in*k*k)
        out = col @ W_flat.T + self.b            # (N, out_H*out_W, C_out)
        return out.reshape(N, out_H, out_W, self.C_out).transpose(0, 3, 1, 2)

    def backward(self, d_out: np.ndarray) -> np.ndarray:
        """d_out: (N, C_out, out_H, out_W) → d_x: (N, C_in, H, W)"""
        N, C_out, out_H, out_W = d_out.shape
        d_flat = d_out.transpose(0, 2, 3, 1).reshape(N, out_H * out_W, C_out)

        self.db = d_flat.sum(axis=(0, 1))
        self.dW = (d_flat.transpose(0, 2, 1) @ self._col).sum(axis=0).reshape(self.W.shape)

        W_flat = self.W.reshape(self.C_out, -1)
        d_col  = d_flat @ W_flat                 # (N, out_H*out_W, C_in*k*k)
        return _col2im(d_col, self._x_shape, self.k, self.k)

    def update(self, lr: float, momentum: float = 0.9) -> None:
        self.vW = momentum * self.vW - lr * self.dW
        self.vb = momentum * self.vb - lr * self.db
        self.W += self.vW
        self.b += self.vb


class ReLU:
    """Rectified linear unit — elementwise max(0, x)."""

    def __init__(self) -> None:
        self._mask: np.ndarray | None = None

    def forward(self, x: np.ndarray) -> np.ndarray:
        self._mask = (x > 0).astype(np.float32)
        return x * self._mask

    def backward(self, d_out: np.ndarray) -> np.ndarray:
        return d_out * self._mask

    def update(self, lr: float, momentum: float = 0.9) -> None:
        pass


class MaxPool2D:
    """2×2 max pooling with stride 2 (non-overlapping blocks)."""

    def __init__(self) -> None:
        self._x_trim: np.ndarray | None = None

    def forward(self, x: np.ndarray) -> np.ndarray:
        """x: (N, C, H, W) → (N, C, H//2, W//2)"""
        N, C, H, W = x.shape
        H2, W2 = H // 2, W // 2
        x_trim = x[:, :, :H2 * 2, :W2 * 2]
        self._x_trim = x_trim
        return x_trim.reshape(N, C, H2, 2, W2, 2).max(axis=(3, 5))

    def backward(self, d_out: np.ndarray) -> np.ndarray:
        """Route gradient to the winning (max) element in each 2×2 block."""
        N, C, H2, W2 = d_out.shape
        x_r     = self._x_trim.reshape(N, C, H2, 2, W2, 2)
        max_vals = x_r.max(axis=(3, 5), keepdims=True)
        mask     = (x_r == max_vals).astype(np.float32)
        # Distribute equally on ties
        mask    /= mask.sum(axis=(3, 5), keepdims=True).clip(min=1)
        d_x_r   = mask * d_out[:, :, :, np.newaxis, :, np.newaxis]
        return d_x_r.reshape(N, C, H2 * 2, W2 * 2)

    def update(self, lr: float, momentum: float = 0.9) -> None:
        pass


class GlobalAvgPool:
    """Global average pooling — spatial mean over H and W, keeping C."""

    def __init__(self) -> None:
        self._x_shape: tuple | None = None

    def forward(self, x: np.ndarray) -> np.ndarray:
        """x: (N, C, H, W) → (N, C)"""
        self._x_shape = x.shape
        return x.mean(axis=(2, 3))

    def backward(self, d_out: np.ndarray) -> np.ndarray:
        """Broadcast gradient back uniformly across spatial positions."""
        N, C, H, W = self._x_shape
        return np.broadcast_to(
            d_out[:, :, np.newaxis, np.newaxis] / (H * W),
            (N, C, H, W),
        ).copy()

    def update(self, lr: float, momentum: float = 0.9) -> None:
        pass


class Dense:
    """Fully connected layer: y = x @ W + b."""

    def __init__(self, in_features: int, out_features: int) -> None:
        # Xavier initialisation
        std = math.sqrt(2.0 / (in_features + out_features))
        self.W: np.ndarray = (
            np.random.randn(in_features, out_features).astype(np.float32) * std
        )
        self.b: np.ndarray = np.zeros(out_features, dtype=np.float32)
        self.vW = np.zeros_like(self.W)
        self.vb = np.zeros_like(self.b)
        self.dW = np.zeros_like(self.W)
        self.db = np.zeros_like(self.b)
        self._x: np.ndarray | None = None

    def forward(self, x: np.ndarray) -> np.ndarray:
        self._x = x
        return x @ self.W + self.b

    def backward(self, d_out: np.ndarray) -> np.ndarray:
        self.dW = self._x.T @ d_out
        self.db = d_out.sum(axis=0)
        return d_out @ self.W.T

    def update(self, lr: float, momentum: float = 0.9) -> None:
        self.vW = momentum * self.vW - lr * self.dW
        self.vb = momentum * self.vb - lr * self.db
        self.W += self.vW
        self.b += self.vb


# ---------------------------------------------------------------------------
# Loss and metrics
# ---------------------------------------------------------------------------

def _softmax(logits: np.ndarray) -> np.ndarray:
    """Numerically stable softmax — subtracts row max before exp."""
    shifted = logits - logits.max(axis=1, keepdims=True)
    e = np.exp(shifted)
    return e / e.sum(axis=1, keepdims=True)


def _cross_entropy_loss(
    logits: np.ndarray,
    y: np.ndarray,
) -> tuple[float, np.ndarray]:
    """
    Categorical cross-entropy loss and gradient w.r.t. logits.

    Parameters
    ----------
    logits : (N, C) raw network output
    y      : (N,) integer class labels in [0, C)

    Returns
    -------
    loss     : scalar (mean across batch)
    d_logits : (N, C) gradient of loss w.r.t. logits
    """
    N = logits.shape[0]
    probs = _softmax(logits)
    log_likelihood = np.log(probs[np.arange(N), y] + 1e-12)
    loss = -log_likelihood.mean()

    # Combined softmax + CE gradient: (probs − one_hot) / N
    d = probs.copy()
    d[np.arange(N), y] -= 1.0
    d /= N
    return float(loss), d


def _accuracy(logits: np.ndarray, y: np.ndarray) -> float:
    """Fraction of correctly classified samples."""
    return float((np.argmax(logits, axis=1) == y).mean())


# ---------------------------------------------------------------------------
# CNN model
# ---------------------------------------------------------------------------

class CNN:
    """
    2-layer convolutional neural network for defect patch classification.

    Architecture
    ------------
    Conv2D(1→8, 3×3) → ReLU → MaxPool(2×2)
    Conv2D(8→16, 3×3) → ReLU → MaxPool(2×2)
    GlobalAvgPool → Dense(16→n_classes)

    ~1,360 trainable parameters (7-class problem).
    """

    def __init__(self, n_classes: int, *, seed: int | None = None) -> None:
        if seed is not None:
            np.random.seed(seed)
        self.n_classes = n_classes
        self.layers: list = [
            Conv2D(1, 8, 3),       # 0
            ReLU(),                 # 1
            MaxPool2D(),            # 2
            Conv2D(8, 16, 3),      # 3
            ReLU(),                 # 4
            MaxPool2D(),            # 5
            GlobalAvgPool(),        # 6
            Dense(16, n_classes),  # 7
        ]

    def forward(self, x: np.ndarray) -> np.ndarray:
        """x: (N, 1, 64, 64) → logits: (N, n_classes)"""
        out = x
        for layer in self.layers:
            out = layer.forward(out)
        return out

    def backward(self, d_loss: np.ndarray) -> None:
        grad = d_loss
        for layer in reversed(self.layers):
            grad = layer.backward(grad)

    def update(self, lr: float, momentum: float = 0.9) -> None:
        for layer in self.layers:
            layer.update(lr, momentum)

    def predict(self, x: np.ndarray) -> np.ndarray:
        """Return predicted class indices (N,)."""
        return np.argmax(self.forward(x), axis=1)

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        """Return softmax class probabilities (N, n_classes)."""
        return _softmax(self.forward(x))

    def n_params(self) -> int:
        """Total trainable parameter count."""
        return sum(
            layer.W.size + layer.b.size
            for layer in self.layers
            if hasattr(layer, "W")
        )


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_images_from_db(
    conn: Connection,
    limit: int | None = None,
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """
    Load defect image patches and type labels from the defect_images table.

    Returns
    -------
    X       : (N, 1, 64, 64) float32 images in [0, 1]
    y       : (N,) int32 class indices
    classes : list[str] — index-to-name mapping (alphabetical order)
    """
    sql = "SELECT image_data, defect_type FROM defect_images ORDER BY ROWID"
    if limit is not None:
        sql += f" LIMIT {limit}"
    rows = conn.execute(sql).fetchall()
    if not rows:
        raise ValueError(
            "defect_images table is empty. "
            "Generate images first with generate_images.py."
        )

    classes   = sorted({r["defect_type"] for r in rows})
    class_idx = {c: i for i, c in enumerate(classes)}

    images, labels = [], []
    for row in rows:
        try:
            arr = _decode_png(bytes(row["image_data"]))  # (64, 64)
        except Exception as exc:
            logger.warning("Skipping unreadable image: %s", exc)
            continue
        images.append(arr[np.newaxis, :, :])          # (1, 64, 64)
        labels.append(class_idx[row["defect_type"]])

    if not images:
        raise ValueError("No valid images decoded from defect_images.")

    X = np.stack(images).astype(np.float32)           # (N, 1, 64, 64)
    y = np.array(labels, dtype=np.int32)
    logger.info("Loaded %d images — %d classes: %s", len(X), len(classes), classes)
    return X, y, classes


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

@dataclass
class TrainingHistory:
    """Per-epoch training and validation metrics."""
    train_loss: list[float] = field(default_factory=list)
    train_acc:  list[float] = field(default_factory=list)
    val_loss:   list[float] = field(default_factory=list)
    val_acc:    list[float] = field(default_factory=list)
    epochs_run: int = 0


def train_cnn(
    conn: Connection,
    *,
    epochs:     int   = 20,
    batch_size: int   = 16,
    lr:         float = 0.01,
    momentum:   float = 0.9,
    val_split:  float = 0.2,
    seed:       int   = 42,
    persist:    bool  = True,
    image_limit: int | None = None,
) -> tuple[CNN, TrainingHistory]:
    """
    Load images from the database, train the CNN, and optionally persist.

    Parameters
    ----------
    conn         : Database connection (SQLite or PostgreSQL).
    epochs       : Training epochs (default 20).
    batch_size   : Mini-batch size (default 16).
    lr           : SGD learning rate (default 0.01).
    momentum     : SGD momentum (default 0.9).
    val_split    : Fraction held out for validation (default 0.2).
    seed         : RNG seed for reproducibility.
    persist      : Save model to model_registry when True (default).
    image_limit  : Cap on images loaded (None = all).

    Returns
    -------
    (CNN, TrainingHistory)
    """
    rng = random.Random(seed)
    np.random.seed(seed)

    X, y, classes = _load_images_from_db(conn, limit=image_limit)
    n_classes = len(classes)
    N = len(X)

    # Shuffle and split
    idx = list(range(N))
    rng.shuffle(idx)
    n_val = max(1, int(N * val_split))
    val_idx   = idx[:n_val]
    train_idx = idx[n_val:]

    X_val, y_val = X[val_idx], y[val_idx]

    cnn     = CNN(n_classes=n_classes, seed=seed)
    history = TrainingHistory()

    logger.info(
        "CNN training — %d train / %d val | %d classes | "
        "epochs=%d batch=%d lr=%g momentum=%g",
        len(train_idx), len(val_idx), n_classes,
        epochs, batch_size, lr, momentum,
    )

    for epoch in range(1, epochs + 1):
        rng.shuffle(train_idx)
        X_tr = X[train_idx]
        y_tr = y[train_idx]

        for start in range(0, len(X_tr), batch_size):
            xb = X_tr[start:start + batch_size]
            yb = y_tr[start:start + batch_size]
            if len(xb) == 0:
                continue
            logits            = cnn.forward(xb)
            loss, d_logits    = _cross_entropy_loss(logits, yb)
            cnn.backward(d_logits)
            cnn.update(lr, momentum)

        # Evaluate on full train and val sets
        t_logits = cnn.forward(X_tr)
        v_logits = cnn.forward(X_val)
        t_loss, _ = _cross_entropy_loss(t_logits, y_tr)
        v_loss, _ = _cross_entropy_loss(v_logits, y_val)
        t_acc     = _accuracy(t_logits, y_tr)
        v_acc     = _accuracy(v_logits, y_val)

        history.train_loss.append(round(float(t_loss), 4))
        history.train_acc.append(round(float(t_acc),   4))
        history.val_loss.append(round(float(v_loss),   4))
        history.val_acc.append(round(float(v_acc),     4))
        history.epochs_run = epoch

        logger.info(
            "Epoch %2d/%d  loss=%.4f acc=%.1f%%  val_loss=%.4f val_acc=%.1f%%",
            epoch, epochs,
            t_loss, t_acc * 100,
            v_loss, v_acc * 100,
        )

    if persist:
        _save_to_registry(conn, cnn, classes, history)

    return cnn, history


# ---------------------------------------------------------------------------
# Model persistence
# ---------------------------------------------------------------------------

def _save_to_registry(
    conn: Connection,
    cnn: CNN,
    classes: list[str],
    history: TrainingHistory,
) -> None:
    """Persist CNN weights and metadata to model_registry."""
    ph = get_placeholder(conn)
    payload = {
        "n_classes": cnn.n_classes,
        "classes":   classes,
        "weights": {
            "conv1_W": cnn.layers[0].W.tolist(),
            "conv1_b": cnn.layers[0].b.tolist(),
            "conv2_W": cnn.layers[3].W.tolist(),
            "conv2_b": cnn.layers[3].b.tolist(),
            "dense_W": cnn.layers[7].W.tolist(),
            "dense_b": cnn.layers[7].b.tolist(),
        },
        "history": {
            "train_acc":  history.train_acc,
            "val_acc":    history.val_acc,
            "train_loss": history.train_loss,
            "val_loss":   history.val_loss,
        },
    }
    blob = pickle.dumps(payload)
    now  = datetime.now(timezone.utc).isoformat()
    val_acc = history.val_acc[-1] if history.val_acc else 0.0

    with conn:
        conn.execute(
            f"INSERT OR REPLACE INTO model_registry "
            f"(model_type, trained_at, model_blob, notes) "
            f"VALUES ({ph},{ph},{ph},{ph})",
            (
                "cnn", now, blob,
                f"CNN val_acc={val_acc:.3f} "
                f"epochs={history.epochs_run} "
                f"classes={len(classes)}",
            ),
        )
    logger.info(
        "CNN saved to model_registry — val_acc=%.1f%%  params=%d",
        val_acc * 100, cnn.n_params(),
    )


def load_from_registry(conn: Connection) -> tuple[CNN, list[str]]:
    """
    Load the most recently trained CNN from model_registry.

    Returns
    -------
    (CNN, classes) — restored model and class-index list
    """
    row = conn.execute(
        "SELECT model_blob FROM model_registry "
        "WHERE model_type = 'cnn' ORDER BY trained_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise ValueError(
            "No CNN found in model_registry. Run train_cnn() first."
        )

    payload   = pickle.loads(bytes(row["model_blob"]))
    classes   = payload["classes"]
    n_classes = payload["n_classes"]

    cnn = CNN(n_classes=n_classes)
    w   = payload["weights"]
    cnn.layers[0].W = np.array(w["conv1_W"], dtype=np.float32)
    cnn.layers[0].b = np.array(w["conv1_b"], dtype=np.float32)
    cnn.layers[3].W = np.array(w["conv2_W"], dtype=np.float32)
    cnn.layers[3].b = np.array(w["conv2_b"], dtype=np.float32)
    cnn.layers[7].W = np.array(w["dense_W"], dtype=np.float32)
    cnn.layers[7].b = np.array(w["dense_b"], dtype=np.float32)

    return cnn, classes


# ---------------------------------------------------------------------------
# Evaluation and comparison
# ---------------------------------------------------------------------------

@dataclass
class ClassifierComparison:
    """
    Comparison of CNN vs. logistic regression classifier.

    The models use different inputs — CNN operates on raw 64×64 pixel patches
    while the logistic regression classifier uses 15 handcrafted tabular
    features per defect record. Both solve the same defect type classification
    task.
    """
    cnn_val_accuracy: float
    cnn_n_params:     int
    cnn_epochs:       int
    cnn_architecture: str = "Conv2D(8)→ReLU→MaxPool→Conv2D(16)→ReLU→MaxPool→GAP→Dense"
    lr_accuracy:      float | None = None
    lr_n_features:    int   = 15
    n_classes:        int   = 0
    n_images_train:   int   = 0
    n_images_val:     int   = 0
    notes:            str   = ""


def compare_with_logistic(
    conn: Connection,
    *,
    epochs:      int   = 20,
    batch_size:  int   = 16,
    lr:          float = 0.01,
    seed:        int   = 42,
    image_limit: int | None = None,
) -> ClassifierComparison:
    """
    Train the CNN and compare its validation accuracy against the logistic
    regression baseline stored in model_registry.

    The two classifiers complement each other:
        CNN  — learns directly from pixel statistics; no feature engineering;
                useful when morphological image features matter.
        LR   — uses curated spatial/statistical tabular features; highly
                interpretable; fast to train and predict.

    Parameters
    ----------
    conn         : Database connection.
    epochs       : Epochs to train CNN (default 20).
    batch_size   : Mini-batch size (default 16).
    lr           : Learning rate (default 0.01).
    seed         : RNG seed.
    image_limit  : Cap on images loaded.

    Returns
    -------
    ClassifierComparison
    """
    cnn, history = train_cnn(
        conn,
        epochs=epochs, batch_size=batch_size, lr=lr,
        seed=seed, persist=True, image_limit=image_limit,
    )
    cnn_val_acc = history.val_acc[-1] if history.val_acc else 0.0

    # Retrieve LR accuracy from model_registry notes (if available)
    lr_acc: float | None = None
    try:
        row = conn.execute(
            "SELECT notes FROM model_registry "
            "WHERE model_type = 'logistic_regression' "
            "ORDER BY trained_at DESC LIMIT 1"
        ).fetchone()
        if row and row["notes"]:
            m = re.search(r"acc[=:](\d+\.?\d*)", str(row["notes"]))
            if m:
                lr_acc = float(m.group(1))
    except Exception:
        pass

    # Reconstruct dataset sizes (val split 20%)
    X, _, _ = _load_images_from_db(conn, limit=image_limit)
    n_val   = max(1, int(len(X) * 0.2))
    n_train = len(X) - n_val

    notes = (
        f"CNN uses raw 64×64 pixel input ({64*64} features); "
        f"LR uses {15} handcrafted tabular features. "
        f"CNN val_acc={cnn_val_acc:.1%}"
    )
    if lr_acc is not None:
        notes += f", LR acc={lr_acc:.1%}"
        if cnn_val_acc > lr_acc:
            notes += f" (CNN +{(cnn_val_acc - lr_acc):.1%})"
        else:
            notes += f" (LR +{(lr_acc - cnn_val_acc):.1%})"
    else:
        notes += " (LR not in model_registry)"

    report = ClassifierComparison(
        cnn_val_accuracy=round(cnn_val_acc, 4),
        cnn_n_params=cnn.n_params(),
        cnn_epochs=history.epochs_run,
        lr_accuracy=lr_acc,
        n_classes=cnn.n_classes,
        n_images_train=n_train,
        n_images_val=n_val,
        notes=notes,
    )

    logger.info(
        "Classifier comparison — CNN val_acc=%.1f%% (%d params) | "
        "LR=%s (%d features) | %d classes",
        cnn_val_acc * 100, cnn.n_params(),
        f"{lr_acc:.1%}" if lr_acc is not None else "N/A",
        15, cnn.n_classes,
    )
    return report
