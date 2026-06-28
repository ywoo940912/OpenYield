# Getting Started with OpenYield

This guide walks you from a fresh clone to a running OpenYield instance with
synthetic inspection data loaded and the React dashboard open in your browser.

---

## Prerequisites

| Tool | Minimum version | Check |
|------|----------------|-------|
| Python | 3.11 | `python3 --version` |
| pip | 23 | `pip --version` |
| Node.js | 18 | `node --version` |
| npm | 9 | `npm --version` |
| Git | any | `git --version` |

A virtual environment is strongly recommended.

---

## 1 — Clone and install Python dependencies

```bash
git clone https://github.com/your-org/openyield.git
cd openyield

python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

pip install -e ".[dev]"           # installs openyield + pytest, pillow, fastapi, etc.
```

---

## 2 — Initialise the database

```bash
python3 - <<'EOF'
from openyield.db.connection import get_connection
from openyield.db.schema import initialize_schema

conn = get_connection("openyield.db")
initialize_schema(conn)
print("Database initialised at openyield.db")
EOF
```

The default backend is SQLite (`openyield.db` in the project root). To use
PostgreSQL instead, set the `DATABASE_URL` environment variable:

```bash
export DATABASE_URL="postgresql://user:pass@localhost:5432/openyield"
```

---

## 3 — Generate synthetic inspection data

OpenYield ships with a synthetic data generator that creates realistic wafer
and glass panel defect records without requiring real tool output.

```bash
python3 - <<'EOF'
from openyield.db.connection import get_connection
from openyield.synthetic.generator import generate_wafer_lot, generate_glass_panel_lot

conn = get_connection("openyield.db")

# Generate 3 wafers in lot LOT_DEMO_001
generate_wafer_lot(conn, lot_id="LOT_DEMO_001", n_panels=3)

# Generate 2 glass panels
generate_glass_panel_lot(conn, lot_id="LOT_DEMO_FPD", n_panels=2)

print("Synthetic data generated.")
EOF
```

---

## 4 — Start the backend API

```bash
# From the project root (with venv active)
uvicorn run:app --reload --port 8000
```

> **Why `run.py` and not `openyield/api/main.py` directly?**
> `run.py` layers CORS middleware and registers all feature routers on top of
> the base FastAPI app without modifying any existing file. Use it as the
> entry point whenever you need the full feature set including the React
> frontend.

Verify it's running:

```bash
curl http://localhost:8000/panels
# → {"panels": [...], "total": 5}

curl http://localhost:8000/docs
# → opens Swagger UI in browser
```

---

## 5 — Install and start the React frontend

```bash
cd frontend
cp package-full.json package.json   # if package.json is stale/missing deps
npm install
npm run dev
```

Open **http://localhost:5173** — you should see the OpenYield dashboard with
the synthetic panels listed.

Keep both the backend (port 8000) and frontend (port 5173) running in
separate terminal tabs.

---

## 6 — Quick tour

| URL | What to do |
|-----|-----------|
| `/dashboard` | See all panels. Click **Yield Map →** next to any panel. |
| `/yield-map` | Select a panel and model (Poisson / Murphy / NegBinom). The per-die heatmap and spatial vs global yield comparison render automatically. |
| `/genealogy` | Type a `lot_id` (e.g. `LOT_DEMO_001`) and press Enter to explore the lineage graph. |
| `/classifier` | Select a panel to see its defect type breakdown bar chart and the CNN model status. |
| `/upload` | Drag and drop a `.klf2` KLARF 2.0 file to ingest real tool data. |

---

## 7 — Run the test suite

```bash
# From the project root (venv active)
cd /Users/yeon/Documents/OpenYield
/Users/yeon/venv/bin/pytest tests/ -v
```

Expected: ~330 tests across 9 test files, all passing.

Individual suites:

```bash
pytest tests/test_critical_area.py      -v   # 38 tests — Maly CA extraction
pytest tests/test_spatial_predictor.py  -v   # 28 tests — Jensen's inequality
pytest tests/test_cnn_classifier.py     -v   # 66 tests — pure NumPy CNN
pytest tests/test_klarf2_adapter.py     -v   # 62 tests — KLARF 2.0 parser
pytest tests/test_genealogy.py          -v   # 62 tests — lot genealogy DAG
pytest tests/test_openmes_connector.py  -v   # 70 tests — MES connector
```

---

## 8 — Ingest a real KLARF 2.0 file

If you have a `.klf2` file from a KLA Surfscan or similar tool:

```bash
# Via the REST API (with backend running)
curl -X POST http://localhost:8000/ingest/klarf2 \
     -F "file=@/path/to/your/lot.klf2"

# Or via Python
from openyield.ingestion.adapters.klarf2_adapter import ingest_klarf2_file
from openyield.db.connection import get_connection

conn = get_connection("openyield.db")
result = ingest_klarf2_file(conn, "/path/to/your/lot.klf2")
print(result)
# → {"wafers_ingested": 25, "defects_inserted": 1842}
```

Or use the **Upload KLARF** page in the dashboard.

---

## Troubleshooting

**`ModuleNotFoundError: openyield`**
Run `pip install -e .` from the project root with the venv active.

**`uvicorn: command not found`**
Run `pip install uvicorn` or use `python -m uvicorn run:app --reload --port 8000`.

**Frontend shows blank page**
Check the browser console for CORS errors. Make sure both servers are running
and the backend is on port 8000.

**`PermissionError` on macOS (EPERM)**
Go to **System Settings → Privacy & Security → Full Disk Access** and grant
access to Terminal (or your IDE). This is a macOS TCC sandbox restriction.

**Pytest fails with `PermissionError: os.getcwd()`**
Same TCC issue. Run pytest from Terminal.app with its full path:
`/path/to/venv/bin/pytest tests/ -v`
