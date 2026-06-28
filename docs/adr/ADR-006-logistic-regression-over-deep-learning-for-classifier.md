# ADR-006: Multinomial Logistic Regression Over Deep Learning for Phase 1 Defect Classifier

**Author:** Yeonkuk Woo
**Status:** Accepted
**Date:** 2025-01-05

---

## Context

Defect type classification — determining whether a detected defect is a particle, scratch, pit, crystal defect, void, or other type — is a core machine learning problem in semiconductor yield management. Commercial systems (KLA Klarity Defect Review, Hitachi DefectPro) use deep convolutional neural networks trained on large proprietary image datasets to achieve high classification accuracy.

OpenYield requires a defect classifier that:
1. Can be trained on the synthetic labeled data that OpenYield generates (no proprietary data)
2. Does not require a GPU or external ML framework
3. Produces interpretable per-feature coefficients that a fab engineer can audit
4. Converges deterministically to the same result on any hardware

The design space ranges from a simple logistic regression to a full convolutional neural network. A deep neural network would achieve higher classification accuracy on image data. The question is whether the Phase 1 classifier should optimize for accuracy or for the four requirements above.

---

## Decision

The Phase 1 classifier (`ai/classifier.py`) is a **multinomial logistic regression** trained by **batch gradient descent** on the **softmax cross-entropy loss** with **L2 regularization**. The implementation is written entirely in pure Python (approximately 80 lines for the training loop). scikit-learn, PyTorch, TensorFlow, and JAX are not dependencies.

The classifier operates on **tabular features** extracted from defect records (size, confidence score, spatial coordinates, zone indicators, substrate type) rather than on image data.

---

## Rationale

**1. Interpretability is a requirement for manufacturing process decisions.**
A fab engineer who receives a classifier output of "particle" must be able to understand why the system made that prediction. Multinomial logistic regression provides a weight matrix `W` of shape `[n_classes, n_features]`; the per-class contribution of each feature to the classification decision is directly readable. A convolutional neural network with millions of parameters does not provide this transparency. For Phase 1, which establishes the classifier architecture and integration with the data platform, interpretability takes precedence over accuracy.

**2. The no-GPU, no-framework requirement is essential for the beneficiary base.**
National laboratories operating air-gapped networks, academic groups without GPU allocations, and domestic fabs running older workstations cannot be expected to install PyTorch or TensorFlow. A classifier implemented in pure Python with no external dependencies can be run on any machine with a standard Python 3.11 installation. This is the correct baseline for a CHIPS Act open infrastructure project.

**3. Convex loss with deterministic convergence is required for petition evidence integrity.**
The gradient descent optimization on softmax cross-entropy loss is a convex problem with a unique global minimum. Given fixed hyperparameters and fixed data, the training algorithm converges to the same weight matrix on every run. This property is required for the classifier to serve as a reproducible artifact in technical evidence. A neural network with random weight initialization and non-convex loss would produce different coefficient values on different runs, making the classifier output non-reproducible without a fixed random seed — an insufficient guarantee for a formal technical exhibit.

**4. The feature set captures the defect attributes available in the OpenYield schema.**
The OpenYield database does not store raw image data for every defect; it stores spatial coordinates, size, confidence score, zone assignment, and substrate type. These are exactly the features available to the logistic regression classifier. A convolutional neural network trained on defect images would require the defect image generation pipeline to run before training — adding a dependency that makes the classifier less useful as a standalone module.

**5. The 15-feature design is extensible without retraining from scratch.**
Adding a new feature to the classifier requires adding one column to the feature vector, re-running training, and updating the stored coefficient matrix. This is a five-minute operation. Adding a new feature to a CNN requires redefining the network architecture and re-running a potentially hours-long training process.

---

## Feature Set

```
bias               (intercept)
size               defect size in mm
confidence         system confidence score
x_normalized       x-coordinate / panel width
y_normalized       y-coordinate / panel height
component_row_norm row index / total rows
component_col_norm col index / total cols
zone_center        1 if region_id == 'zone_center'
zone_mid           1 if region_id == 'zone_mid'
zone_edge          1 if region_id == 'zone_edge'
region_NW/NE/SW/SE glass-panel quadrant indicators
is_wafer           1 if substrate_type == 'wafer'
```

---

## Consequences

- The Phase 1 classifier does not use defect images. Defect images are generated separately by `synthetic/image_generator.py` and stored in `defect_images`. A convolutional image-based classifier is scoped as a Phase 2 deliverable (see roadmap and ADR-006 scope statement).
- Classification accuracy on the synthetic dataset is high (typically >90%) because the synthetic generator assigns defect types deterministically based on configurable probabilities, and the feature set captures the primary discriminating signals. Accuracy on real fab data will be lower until the classifier is fine-tuned on labeled production defects.
- The model is persisted to the `model_registry` table as a JSON-serialized coefficient matrix. Loading and applying the model at inference time requires only the coefficient matrix, the class list, and the feature extraction function — no framework serialization formats (pickle, ONNX, safetensors) are used.
- The model version identifier includes a UUID suffix (`v1-{uuid[:8]}`) to distinguish multiple training runs stored in the registry. The most recent model is used by default for prediction.

---

## Alternatives Considered

**scikit-learn `LogisticRegression`**: Rejected for the same reasons as sklearn in ADR-004. The dependency footprint and version sensitivity are incompatible with the no-framework requirement.

**Gradient boosted trees (e.g., XGBoost)**: Considered. Gradient boosted trees would achieve higher accuracy than logistic regression on tabular data. Rejected for Phase 1: the per-feature interpretability of logistic regression coefficients is more directly useful to a fab engineer than XGBoost's feature importances. XGBoost is a candidate for Phase 2.

**Convolutional neural network on defect images**: The correct Phase 2 architecture but not appropriate for Phase 1. The Phase 2 CNN will use defect images from `defect_images` as input and will be implemented using a minimal pure-Python or NumPy-based framework to maintain the no-external-ML-framework principle, or will introduce PyTorch as an optional dependency clearly scoped to the AI layer.

**Support Vector Machine (SVM)**: Considered. SVM with RBF kernel would achieve higher accuracy than logistic regression on non-linearly separable classes. Rejected: SVM training does not produce interpretable per-feature coefficients, which is the primary Phase 1 requirement.
