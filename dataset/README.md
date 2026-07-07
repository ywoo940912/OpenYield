---
language:
- en
license: apache-2.0
task_categories:
- image-classification
task_ids:
- multi-class-image-classification
tags:
- semiconductor
- defect-detection
- wafer-inspection
- glass-panel
- synthetic
- CHIPS-Act
- yield-engineering
- manufacturing
pretty_name: OpenYield Synthetic Semiconductor Defect Image Dataset
size_categories:
- 10K<n<100K
---

# OpenYield Synthetic Semiconductor Defect Image Dataset

## Dataset Summary

OpenYield-Defects is a synthetic, fully open-source dataset of **64×64 grayscale
image patches** representing semiconductor manufacturing defects on silicon wafers
and flat-panel glass substrates (TFT-LCD, OLED, AMOLED).

Every image is **procedurally generated** from a deterministic algorithm — no real
fab imagery, no proprietary data, no export-control restrictions. The dataset is
designed to train, benchmark, and evaluate defect classification models (CNNs,
vision transformers) for automated optical inspection (AOI) systems used in
domestic U.S. semiconductor manufacturing.

This dataset is a direct artifact of **OpenYield**, an open-source semiconductor
inspection data platform developed to support U.S. domestic chip manufacturing
under the [CHIPS and Science Act of 2022](https://www.congress.gov/bill/117th-congress/house-bill/4346).

---

## Defect Classes

| Class | Count | Description | Substrate |
|---|---|---|---|
| `particle` | ~2,348 | Gaussian blob — airborne contamination landing on substrate | Both |
| `scratch` | ~2,456 | Linear streak — mechanical contact during handling | Both |
| `line_defect` | ~1,807 | Oriented line artifact — lithography or etch issue | Both |
| `open_circuit` | ~1,724 | Missing conductive trace — etch over-exposure | Both |
| `short_circuit` | ~1,734 | Bridging conductive material — resist residue | Both |
| `mura` | ~1,766 | Soft low-frequency luminance gradient — TFT uniformity issue | Glass |
| `pinhole` | ~1,760 | Bright point on dark background — thin-film void | Glass |
| `bridging` | ~581 | Irregular multi-blob — resist bridging between features | Both |
| `metal_spike` | ~604 | Sharp protrusion — electromigration or hillock | Both |
| `crystal_defect` | ~583 | Faceted structure — epitaxial or polysilicon grain | Wafer |
| `pit` | ~596 | Hard-edged dark disk — etch pit or mechanical damage | Both |
| `void` | ~595 | Dark annulus with lighter center — delamination | Both |

**Total: ~16,554 images** (exact count depends on database seed)

---

## Image Format

- **Size**: 64 × 64 pixels
- **Color**: Grayscale (single channel, 8-bit)
- **Format**: PNG
- **Background**: Simulated sensor noise (Gaussian, σ=8 on 0–255 scale, mean luminance ≈ 200)
- **Defect rendering**: Procedural — Gaussian blobs, anti-aliased line segments, hard circles, soft gradients, bright points

Each patch simulates the appearance of a real defect as it would appear under
an automated optical inspection (AOI) sensor — dark defects on a light noisy
background, consistent with standard brightfield inspection.

---

## Reproducibility

Every image uses a **deterministic seed** derived from `hash(panel_id + defect_id)`.
Re-running `scripts/export_dataset.py` on the same database always produces
**byte-identical PNG files**. This property is required for benchmark integrity and
for any external study using OpenYield as a reference dataset.

---

## Dataset Splits

| Split | Count | Fraction |
|---|---|---|
| train | ~13,238 | 80% |
| val | ~1,650 | 10% |
| test | ~1,666 | 10% |

Splits are **stratified by defect type** to maintain class balance across all
three subsets. Split CSVs are in `split/train.csv`, `split/val.csv`, `split/test.csv`.

---

## Metadata Schema

`metadata.csv` contains one row per image:

| Column | Type | Description |
|---|---|---|
| `defect_id` | int | Unique defect record ID |
| `panel_id` | str | Parent panel (wafer or glass panel) |
| `substrate_type` | str | `wafer` or `glass_panel` |
| `lot_id` | str | Manufacturing lot ID |
| `defect_type` | str | One of the 12 defect classes above |
| `size_mm` | float | Physical defect size in mm |
| `confidence_score` | float | Simulated detection confidence [0–1] |
| `component_row` | int | Die row on panel grid |
| `component_col` | int | Die column on panel grid |
| `image_path` | str | Relative path: `images/<type>/<id>.png` |
| `generator_version` | str | `v1` (pinned for reproducibility) |
| `split` | str | `train`, `val`, or `test` |

---

## Intended Use

### Appropriate uses
- Training and benchmarking defect classification CNNs
- Evaluating data augmentation strategies for inspection AI
- Prototyping AOI pipelines without access to proprietary fab data
- Curriculum learning experiments (clean process → excursion)
- Few-shot learning research for rare defect classes

### Not intended for
- Direct deployment in production fab inspection without adaptation to real sensor data
- As a substitute for real inspection data when validating production models

---

## Generation

Dataset was generated with:

```bash
# 1. Seed synthetic fab data
python seed_demo.py

# 2. Export defect image patches
python scripts/export_dataset.py
```

Source code: [github.com/ywoo940912/OpenYield](https://github.com/ywoo940912/OpenYield)

---

## National Context

This dataset was created as part of OpenYield, a platform developed to
accelerate U.S. domestic semiconductor manufacturing capabilities. A core
bottleneck for domestic fabs is the shortage of open, shareable inspection data
for training AI-assisted defect classification systems — real fab data is
proprietary and subject to export controls.

OpenYield addresses this gap by generating synthetic inspection data that is
physically plausible, statistically calibrated to published semiconductor yield
models, and freely distributable under Apache 2.0.

This work is aligned with the goals of the **CHIPS and Science Act of 2022**
(Public Law 117-167), specifically the provisions supporting domestic
semiconductor manufacturing research infrastructure and workforce development.

---

## Citation

```bibtex
@software{openyield2024,
  author    = {Woo, Yeonkuk},
  title     = {OpenYield: Open-Source Semiconductor Inspection Data Platform},
  year      = {2024},
  url       = {https://github.com/ywoo940912/OpenYield},
  license   = {Apache-2.0},
  note      = {Developed under the CHIPS and Science Act of 2022}
}
```

---

## License

Apache 2.0 — see [LICENSE](https://github.com/ywoo940912/OpenYield/blob/main/LICENSE)
