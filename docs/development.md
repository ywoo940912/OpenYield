# Development Guide

This guide covers how to extend OpenYield: adding yield models, defect types,
API endpoints, and new frontend pages.

---

## Development setup

```bash
git clone https://github.com/your-org/openyield.git
cd openyield
python3 -m venv venv && source venv/bin/activate
pip install -e ".[dev]"

# Frontend
cd frontend && npm install && cd ..
```

Run both servers in separate terminals:

```bash
# Terminal 1 — backend
uvicorn run:app --reload --port 8000

# Terminal 2 — frontend
cd frontend && npm run dev
```

---

## Running tests

```bash
# All tests (~330)
pytest tests/ -v

# Single suite
pytest tests/test_cnn_classifier.py -v

# With coverage
pytest tests/ --cov=openyield --cov-report=term-missing
```

On macOS, if pytest fails with `PermissionError: os.getcwd()`, run it with
the full venv path from Terminal.app (not a sandboxed shell):

```bash
/Users/yourname/venv/bin/pytest tests/ -v
```

### Test file map

| File | Subject | Count |
|------|---------|-------|
| `tests/test_critical_area.py` | Maly CA extraction | 38 |
| `tests/test_spatial_predictor.py` | Jensen spatial yield | 28 |
| `tests/test_cnn_classifier.py` | Pure NumPy CNN | 66 |
| `tests/test_klarf2_adapter.py` | KLARF 2.0 parser | 62 |
| `tests/test_genealogy.py` | Lot DAG, Kahn | 62 |
| `tests/test_openmes_connector.py` | MES connector | 70 |

All tests use in-memory SQLite (`:memory:`), so they're fast and leave no
files on disk.

---

## Adding a new yield model

1. Open `openyield/analysis/yield_calculator.py`.

2. Add a branch inside `estimate_yield()`:

```python
elif model == "bose_einstein":
    # Example: Bose–Einstein model
    y = 1.0 / (1.0 + d0 * area) ** n_defect_types
```

3. Add the model name to the Swagger `Literal` type in
   `openyield/api/routers/spatial_router.py` (or whichever router
   accepts the `model` parameter).

4. Write a test in `tests/test_yield_calculator.py`:

```python
def test_bose_einstein_zero_density():
    assert estimate_yield(conn, ..., model="bose_einstein") == pytest.approx(1.0)
```

5. Add the model to the frontend `<select>` in
   `frontend/src/pages/YieldMap.tsx`:

```tsx
<option value="bose_einstein">Bose–Einstein</option>
```

---

## Adding a new defect type

1. Update the `_defect_type()` mapping in
   `openyield/ingestion/adapters/klarf2_adapter.py`.

2. If training the CNN classifier, add the new class to the label list in
   `openyield/ai/cnn_classifier.py` and retrain.

3. Update the colour map in `frontend/src/components/LotTree.tsx` and the
   chart legend in `frontend/src/pages/Classifier.tsx` if you want a
   distinct colour for the new type.

---

## Adding a new API endpoint

1. Create or edit a router file in `openyield/api/routers/`:

```python
# openyield/api/routers/my_router.py
from fastapi import APIRouter
from openyield.db.connection import get_connection

router = APIRouter(prefix="/my-feature", tags=["my-feature"])

@router.get("/")
def my_endpoint():
    conn = get_connection()
    ...
    return {"result": ...}
```

2. Register it in `run.py`:

```python
from openyield.api.routers import my_router
app.include_router(my_router.router)
```

3. Add a proxy rule in `frontend/vite.config.ts`:

```typescript
"/my-feature": { target: "http://localhost:8000", changeOrigin: true },
```

4. Add a typed fetch wrapper in `frontend/src/api.ts`:

```typescript
myFeature: {
  list: () => get<MyFeatureResponse>("/my-feature/"),
},
```

5. Add a TypeScript interface in `frontend/src/types.ts`.

---

## Adding a new frontend page

1. Create `frontend/src/pages/MyPage.tsx`.

2. Register the route in `frontend/src/App.tsx`:

```tsx
<Route path="/my-page" element={<MyPage />} />
```

3. Add a nav link in `frontend/src/components/Layout.tsx`:

```tsx
{ to: "/my-page", label: "My Feature" },
```

---

## Extending the CNN classifier

The CNN in `openyield/ai/cnn_classifier.py` is intentionally minimal (1,367
parameters, 7 classes, 64×64 input). To scale it:

- **More filters**: increase the `out_channels` argument of `Conv2D`.
- **More classes**: pass `n_classes=N` to `CNN(...)`.
- **Different input size**: update `input_size` in `GlobalAvgPool` (the GAP
  layer is size-agnostic, but the Dense input dim changes).
- **Batch training**: `train_cnn()` accepts a `batch_size` kwarg.
- **Persistence**: `CNN.save(conn, model_id)` / `load_from_registry(conn)`
  serialise weights as JSON blobs in the `model_registry` table.

---

## Database migration

OpenYield has no migration framework. Schema changes go in
`openyield/db/schema.py` inside `initialize_schema()`.  Use `IF NOT EXISTS`
for new tables/columns so the function stays idempotent.

For the genealogy tables specifically, call:

```python
from openyield.analysis.genealogy import initialize_genealogy_schema
initialize_genealogy_schema(conn)
```

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `""` (SQLite) | `postgresql://user:pass@host/db` |
| `OPENYIELD_DB_PATH` | `openyield.db` | SQLite file path |
| `MES_BASE_URL` | — | OpenMES server base URL |
| `MES_API_KEY` | — | OpenMES API key |

---

## Code style

- Python: PEP 8, type hints on all public functions, no f-string in SQL
  (use parameterised queries).
- TypeScript: strict mode, no `any`, React function components only.
- No comments unless the *why* is non-obvious (hidden constraint, subtle
  invariant, library bug workaround).
- No docstrings except one-line module descriptions.

---

## Project phases (completed)

| Phase | Module | Description |
|-------|--------|-------------|
| 1.0 | `db/`, `api/main.py` | Core schema, base FastAPI app |
| 1.1 | `analysis/yield_calculator.py` | Poisson / Murphy / NegBinom |
| 1.2 | `ingestion/adapters/klarf_adapter.py` | KLARF 1.x text parser |
| 2.0 | `analysis/critical_area.py` | Maly critical area |
| 2.1 | `ai/cnn_classifier.py` | Pure NumPy CNN |
| 3.1 | `analysis/spatial_predictor.py` | Jensen's inequality spatial yield |
| 3.2 | `ingestion/adapters/klarf2_adapter.py` | KLARF 2.0 binary parser |
| 3.3 | `analysis/genealogy.py` | Lot genealogy DAG |
| 3.4 | `integrations/openmes_connector.py` | OpenMES connector |
| 3.5 | `docs/semi-compliance.md` | SEMI standards compliance doc |
| 4.0 | `frontend/` | React + Vite + Tailwind dashboard |
