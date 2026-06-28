# Architecture

OpenYield is a monorepo with a Python backend (FastAPI + SQLite/PostgreSQL)
and a React/TypeScript frontend. All core algorithms are pure Python — no
scikit-learn, PyTorch, or pandas in the production code paths.

---

## Repository layout

```
openyield/                  ← Python package root
│
├── ai/
│   └── cnn_classifier.py   ← pure-NumPy 2-layer CNN (Phase 2.1)
│
├── analysis/
│   ├── yield_calculator.py ← Poisson / Murphy / NegBinom models
│   ├── spatial_predictor.py← Jensen's inequality spatial yield
│   ├── critical_area.py    ← Maly critical area extraction
│   └── genealogy.py        ← lot DAG (BFS + Kahn cycle detection)
│
├── ingestion/
│   └── adapters/
│       ├── klarf_adapter.py ← KLARF 1.x text parser
│       └── klarf2_adapter.py← KLARF 2.0 binary TLV parser/encoder (Phase 3.2)
│
├── integrations/
│   └── openmes_connector.py ← MES sync via Transport protocol (Phase 3.4)
│
├── db/
│   ├── connection.py        ← SQLite / PostgreSQL connection factory
│   └── schema.py            ← initialize_schema() DDL
│
├── synthetic/
│   └── generator.py         ← synthetic wafer/glass panel data
│
└── api/
    ├── main.py              ← base FastAPI app (original routers)
    └── routers/
        ├── spatial_router.py
        ├── panels_router.py
        ├── genealogy_router.py
        ├── ingest_router.py
        └── classify_router.py

frontend/                    ← React + Vite + Tailwind SPA
│
├── src/
│   ├── api.ts               ← typed fetch wrappers for all endpoints
│   ├── types.ts             ← TypeScript interfaces
│   ├── App.tsx              ← BrowserRouter + routes
│   ├── main.tsx             ← ReactDOM.createRoot entry
│   │
│   ├── components/
│   │   ├── Layout.tsx       ← dark sidebar, nav, Outlet
│   │   ├── StatCard.tsx     ← metric card (green/amber/red/blue)
│   │   ├── DieHeatmap.tsx   ← SVG die grid with hover tooltip
│   │   ├── YieldChart.tsx   ← Recharts bar chart + reference line
│   │   └── LotTree.tsx      ← DAG visualiser (flex, no SVG lib)
│   │
│   └── pages/
│       ├── Dashboard.tsx    ← panel list + stat cards
│       ├── YieldMap.tsx     ← heatmap + yield chart + spatial stats
│       ├── Genealogy.tsx    ← lot lineage search
│       ├── Classifier.tsx   ← defect distribution + CNN status
│       └── KlarfUpload.tsx  ← drag-and-drop file ingestion
│
├── vite.config.ts           ← dev proxy: /panels /yield /genealogy → :8000
├── tailwind.config.js
├── postcss.config.js
├── tsconfig.json
└── package-full.json        ← canonical deps (cp to package.json)

run.py                       ← entry point: imports app + wires CORS + routers
docs/
├── getting-started.md
├── api-reference.md
├── architecture.md          ← this file
├── development.md
├── semi-compliance.md
└── comparison-vs-commercial.md
tests/
├── test_critical_area.py    ← 38 tests
├── test_spatial_predictor.py← 28 tests
├── test_cnn_classifier.py   ← 66 tests
├── test_klarf2_adapter.py   ← 62 tests
├── test_genealogy.py        ← 62 tests
└── test_openmes_connector.py← 70 tests
```

---

## Request flow

```
Browser (port 5173)
  │  GET /panels
  ↓
Vite dev proxy  (vite.config.ts)
  │  forwards to http://localhost:8000/panels
  ↓
FastAPI  (run.py → uvicorn port 8000)
  │  panels_router.GET /panels
  ↓
openyield.db.connection.get_connection()
  │  SQLite  (or PostgreSQL via DATABASE_URL)
  ↓
SQL query → JSON response → browser
```

For file upload (`POST /ingest/klarf2`):

```
Browser drag-and-drop
  ↓
KlarfUpload.tsx  → FormData POST
  ↓
Vite proxy → FastAPI ingest_router
  ↓
klarf2_adapter.parse_klarf2(bytes)   ← TLV binary decode
  ↓
ingest_klarf2_bytes(conn, ...)       ← INSERT panels + defects
  ↓
{"wafers_ingested": N, ...}          → browser success card
```

---

## Database schema (simplified)

```sql
panels (
  panel_id    TEXT PRIMARY KEY,
  lot_id      TEXT,
  substrate   TEXT,          -- 'wafer' | 'glass'
  diameter_mm REAL,
  created_at  TIMESTAMP
)

defects (
  id          INTEGER PRIMARY KEY,
  panel_id    TEXT REFERENCES panels,
  x_mm        REAL,
  y_mm        REAL,
  defect_type TEXT,
  size_um     REAL
)

defect_images (
  id          INTEGER PRIMARY KEY,
  panel_id    TEXT,
  label       TEXT,
  image_data  BLOB
)

model_registry (
  id          INTEGER PRIMARY KEY,
  model_id    TEXT UNIQUE,
  weights     BLOB,          -- JSON-serialised NumPy arrays
  metadata    TEXT           -- JSON: n_params, accuracy, etc.
)

lot_nodes (
  lot_id      TEXT PRIMARY KEY,
  created_at  TIMESTAMP
)

lot_edges (
  parent_lot_id TEXT,
  child_lot_id  TEXT,
  relation_type TEXT,
  PRIMARY KEY (parent_lot_id, child_lot_id)
)
```

PostgreSQL uses `$1/$2` placeholders; SQLite uses `?/??`. The
`get_placeholder(conn)` helper in `db/connection.py` returns the right
marker for whichever backend is active.

---

## Key algorithms

### Yield models (`analysis/yield_calculator.py`)

| Model | Formula |
|-------|---------|
| Poisson | `Y = exp(-D₀ · A)` |
| Murphy | `Y = ((1 − exp(-D₀·A)) / (D₀·A))²` |
| Negative Binomial | `Y = (1 + D₀·A/α)^(−α)`, α=2 |

`D₀` = defect density (defects/cm²), `A` = die area (cm²).

### Spatial yield (`analysis/spatial_predictor.py`)

The panel is binned into an `n_bins_x × n_bins_y` grid.  Each cell gets a
local defect density `d0_i`.  Spatial yield is:

```
Y_spatial = (1/N) · Σ exp(-d0_i · A)
```

Because exp is convex, Jensen's inequality guarantees:

```
Y_spatial ≥ exp(-mean(d0_i) · A) = Y_global
```

The `yield_gain = Y_spatial − Y_global` quantifies the value of knowing
where hot-spots are.

### CNN classifier (`ai/cnn_classifier.py`)

Pure NumPy 2-layer convolutional network for 64×64 greyscale defect patches.

```
Input 1×64×64
  → Conv2D(1→8, 3×3)   bias → 8×62×62
  → ReLU
  → MaxPool2D(2×2)      → 8×31×31
  → Conv2D(8→16, 3×3)  bias → 16×29×29
  → ReLU
  → MaxPool2D(2×2)      → 16×14×14
  → GlobalAvgPool       → 16
  → Dense(16→7)         bias → 7
  → Softmax
```

Total trainable parameters: **1,367**
(Conv1: 8×(9+1)=80, Conv2: 16×(8×9+1)=1168, Dense: 7×(16+1)=119)

Convolution uses `im2col` via `np.lib.stride_tricks.as_strided` for
memory-efficient matrix multiplication.  Training: SGD + momentum (μ=0.9),
cross-entropy loss.

### KLARF 2.0 (`ingestion/adapters/klarf2_adapter.py`)

Binary format with 8-byte magic `KLARF200` followed by:

```
Endian marker  (u16, 0x4949 = little-endian)
TLV blocks:
  block_type  u16
  length      u32
  data        <length bytes>
```

Block types: 0x01 Header, 0x02 Wafer, 0x03 DefectList, 0x04 DefectRecord.

Each defect record is 36 bytes: `struct "<IffffHHHHHHf"`.

### Lot genealogy (`analysis/genealogy.py`)

The lot graph is a DAG stored in `lot_nodes` + `lot_edges`.

- **Ancestor BFS**: queue starts at `lot_id`, follows parent edges upward.
- **Descendant BFS**: follows child edges downward.
- **Cycle detection**: Kahn's topological sort — nodes that never reach
  in-degree 0 are part of a cycle.
- **Yield correlation**: Pearson r between `lot_nodes.yield_fraction` values
  across connected lots; returns 0.0 for constant or empty series.

### OpenMES connector (`integrations/openmes_connector.py`)

Uses a structural `Transport` Protocol (PEP 544) so the same connector
works against the real MES (`HTTPTransport`) or an in-memory stub
(`MockTransport`) in tests — no monkeypatching required.

`HTTPTransport` implements exponential back-off: wait 2^attempt seconds,
up to 3 retries, on any non-2xx response or connection error.

---

## run.py wiring

`openyield/api/main.py` is the original FastAPI app created in Phase 1. To
avoid modifying existing files, `run.py` (project root) imports the app and
layers on top:

1. Imports `app` from `openyield.api.main`
2. Adds `CORSMiddleware` (allows `localhost:5173`)
3. Registers the five new routers (`spatial`, `panels`, `genealogy`,
   `ingest`, `classify`)

Always start the server with:

```bash
uvicorn run:app --reload --port 8000
```

---

## Frontend ↔ Backend communication

The Vite dev server (`npm run dev`, port 5173) proxies all API paths to
`http://localhost:8000` (see `frontend/vite.config.ts`).  No absolute URLs
appear anywhere in `src/api.ts` — every fetch is a relative path like
`/panels`.  This means the same `api.ts` bundle works in both dev and
production (where you'd point Nginx at the same FastAPI server).

In production, build with `npm run build` and serve `frontend/dist/` from
a static file server or CDN.  Point the backend requests from the CDN
origin to `https://api.yourdomain.com`.
