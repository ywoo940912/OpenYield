# Manual wiring required in openyield/api/main.py

Add these imports and router registrations (EPERM prevents editing main.py directly):

```python
from openyield.api.routers import (
    spatial_router,
    panels_router,
    genealogy_router,
    ingest_router,
    classify_router,
)

app.include_router(spatial_router.router)
app.include_router(panels_router.router)
app.include_router(genealogy_router.router)
app.include_router(ingest_router.router)
app.include_router(classify_router.router)
```

Also add CORS so the React frontend (port 5173) can call FastAPI (port 8000):

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)
```

# Frontend setup

```bash
cd frontend
npm install
npm run dev          # starts on http://localhost:5173
```

Backend must be running on port 8000:
```bash
uvicorn openyield.api.main:app --reload --port 8000
```
