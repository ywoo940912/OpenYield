# API Reference

Base URL: `http://localhost:8000`
Interactive docs: `http://localhost:8000/docs` (Swagger UI)

All requests and responses use JSON unless noted. Timestamps are ISO 8601 UTC.

---

## Panels

### `GET /panels`

List all inspection panels (wafers and glass substrates).

**Response 200**

```json
{
  "panels": [
    {
      "panel_id": "W001",
      "lot_id": "LOT_DEMO_001",
      "substrate": "wafer",
      "diameter_mm": 300.0,
      "defect_count": 42
    }
  ],
  "total": 1
}
```

---

### `GET /panels/{panel_id}`

Retrieve a single panel with full summary.

**Path parameters**

| Name | Type | Description |
|------|------|-------------|
| `panel_id` | string | Panel identifier |

**Response 200**

```json
{
  "panel_id": "W001",
  "lot_id": "LOT_DEMO_001",
  "substrate": "wafer",
  "diameter_mm": 300.0,
  "defect_count": 42,
  "created_at": "2025-01-15T09:30:00Z"
}
```

**Response 404**

```json
{ "detail": "Panel not found" }
```

---

## Yield

### `GET /yield/estimate`

Compute global yield for a panel using a statistical yield model.

**Query parameters**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `panel_id` | string | required | Panel to evaluate |
| `model` | string | `"poisson"` | `"poisson"` \| `"murphy"` \| `"negative_binomial"` |
| `die_area_mm2` | float | required | Die area in mm² |
| `d0` | float | optional | Defect density override (defects/cm²). Computed from DB if omitted. |

**Response 200**

```json
{
  "panel_id": "W001",
  "model": "poisson",
  "yield_fraction": 0.834,
  "d0": 0.12,
  "die_area_mm2": 40.0,
  "n_dies": 320
}
```

---

### `GET /yield/spatial`

Compute per-die spatial yield across the panel using Jensen's inequality
to convert spatial defect density variation into a yield uplift estimate.

**Query parameters**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `panel_id` | string | required | Panel to analyse |
| `n_bins_x` | int | `10` | Grid columns |
| `n_bins_y` | int | `10` | Grid rows |
| `die_area_mm2` | float | required | Die area in mm² |

**Response 200**

```json
{
  "panel_id": "W001",
  "spatial_yield": 0.871,
  "global_yield": 0.834,
  "yield_gain": 0.037,
  "cv_d0": 0.412,
  "die_yields": [
    { "x": 0, "y": 0, "yield": 0.95, "d0_local": 0.04 }
  ]
}
```

`spatial_yield` ≥ `global_yield` always (Jensen's inequality convexity bound).

---

### `GET /yield/critical-area`

Extract Maly critical area from defect images to calibrate the `d0` input.

**Query parameters**

| Name | Type | Description |
|------|------|-------------|
| `panel_id` | string | Panel whose defect images to sample |

**Response 200**

```json
{
  "panel_id": "W001",
  "critical_area_mm2": 12.4,
  "n_defects_sampled": 88
}
```

---

## Genealogy

### `GET /genealogy/{lot_id}/lineage`

Return the ancestor and descendant lot graph for a given lot, up to two
degrees in each direction.

**Path parameters**

| Name | Type | Description |
|------|------|-------------|
| `lot_id` | string | Starting lot |

**Response 200**

```json
{
  "lot_id": "LOT_DEMO_001",
  "ancestors": [
    { "lot_id": "LOT_INGOT_A", "relation": "split", "depth": 1 }
  ],
  "descendants": [
    { "lot_id": "LOT_DEMO_001_R", "relation": "rework", "depth": 1 }
  ],
  "edges": [
    {
      "parent_lot_id": "LOT_INGOT_A",
      "child_lot_id": "LOT_DEMO_001",
      "relation_type": "split",
      "created_at": "2025-01-10T00:00:00Z"
    }
  ]
}
```

Valid relation types: `split`, `merge`, `rework`, `convert`, `inspect`.

---

### `GET /genealogy/cycles`

Detect cycles in the lot genealogy DAG (should be empty in a valid dataset).

**Response 200**

```json
[]
```

If cycles are present:

```json
["LOT_BAD_A", "LOT_BAD_B"]
```

---

## Classification

### `GET /classify/{panel_id}/defects`

Return the defect type distribution for a panel.

**Path parameters**

| Name | Type | Description |
|------|------|-------------|
| `panel_id` | string | Panel to classify |

**Response 200**

```json
{
  "panel_id": "W001",
  "total_defects": 42,
  "distribution": {
    "particle": 18,
    "scratch": 9,
    "pit": 7,
    "void": 5,
    "bridge": 3
  }
}
```

---

### `GET /classify/cnn/status`

Return the status of the CNN defect classifier.

**Response 200 — model registered**

```json
{
  "status": "ok",
  "model_id": "cnn_v1",
  "n_parameters": 1367,
  "n_classes": 7,
  "architecture": "Conv2D(1→8)→ReLU→MaxPool→Conv2D(8→16)→ReLU→MaxPool→GAP→Dense",
  "accuracy": 0.912
}
```

**Response 200 — no model trained yet**

```json
{
  "status": "untrained",
  "message": "No CNN model in registry. POST /classify/cnn/train to create one."
}
```

---

## Ingestion

### `POST /ingest/klarf2`

Ingest a binary KLARF 2.0 (`.klf2`) file. The file is multipart/form-data.

**Request body** — `multipart/form-data`

| Field | Type | Description |
|-------|------|-------------|
| `file` | file | Binary `.klf2` file |

```bash
curl -X POST http://localhost:8000/ingest/klarf2 \
     -F "file=@wafer_lot_025.klf2"
```

**Response 200**

```json
{
  "lot_id": "LOT_025",
  "wafers_ingested": 25,
  "defects_inserted": 1842,
  "panel_ids": ["W001", "W002", "W003"]
}
```

**Response 400**

```json
{ "detail": "Invalid KLARF 2.0 magic bytes" }
```

---

## Python SDK quick-reference

All endpoints can also be called programmatically without HTTP:

```python
from openyield.db.connection import get_connection
from openyield.analysis.yield_calculator import estimate_yield
from openyield.analysis.spatial_predictor import compute_spatial_yield
from openyield.analysis.genealogy import get_ancestors, detect_cycles
from openyield.ai.cnn_classifier import CNN, train_cnn, compare_with_logistic
from openyield.ingestion.adapters.klarf2_adapter import ingest_klarf2_file
from openyield.integrations.openmes_connector import OpenMESConnector, HTTPTransport

conn = get_connection("openyield.db")

# Yield
result = estimate_yield(conn, panel_id="W001", model="poisson", die_area_mm2=40.0)

# Genealogy
ancestors = get_ancestors(conn, "LOT_DEMO_001", max_depth=3)
cycles    = detect_cycles(conn)

# Classify
from openyield.ai.cnn_classifier import load_from_registry
cnn = load_from_registry(conn)

# Ingest
ingest_klarf2_file(conn, "/path/to/file.klf2")

# MES sync
connector = OpenMESConnector(
    base_url="https://mes.example.com",
    api_key="tok_...",
    transport=HTTPTransport(),
)
report = connector.sync_lots_to_openyield(conn, ["LOT_025"])
```
