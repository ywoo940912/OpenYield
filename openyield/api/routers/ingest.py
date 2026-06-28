"""
api/routers/ingest.py
----------------------
Author: Yeonkuk Woo

Ingestion endpoints for the OpenYield REST API.

POST /ingest/csv   — upload a CSV defect file and ingest it
GET  /ingest/files — list tracked files and their status
"""

from __future__ import annotations

import tempfile
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query

from openyield.api.dependencies import get_db
from openyield.api.schemas import IngestResponse
from openyield.db.connection import get_placeholder
from openyield.ingestion.ingest import ingest_csv, is_file_processed

router = APIRouter(prefix="/ingest", tags=["ingest"])


# ---------------------------------------------------------------------------
# Upload and ingest CSV
# ---------------------------------------------------------------------------

@router.post("/csv", response_model=IngestResponse)
async def ingest_csv_upload(
    file: UploadFile = File(..., description="Defect CSV file to ingest"),
    skip_if_processed: bool = Query(
        True, description="Skip file if already processed"
    ),
    conn=Depends(get_db),
):
    """
    Upload a CSV defect file and ingest it into the database.

    Required CSV columns:
        panel_id, component_row, component_col, source_system,
        defect_type, x, y, size, confidence_score

    Optional columns:
        match_id, created_at

    The file is tracked in the files table. Re-uploading the same
    filename with skip_if_processed=true returns immediately.
    """
    if not file.filename or not file.filename.endswith(".csv"):
        raise HTTPException(
            status_code=400,
            detail="File must be a .csv file"
        )

    # Write upload to a temp file
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=".csv", prefix=f"openyield_{file.filename}_"
        ) as tmp:
            shutil.copyfileobj(file.file, tmp)
            tmp_path = Path(tmp.name)

        # Check if already processed by original filename
        if skip_if_processed and is_file_processed(conn, file.filename):
            return IngestResponse(
                file_name=file.filename,
                records_ingested=0,
                status="skipped",
                message=f"File already processed: {file.filename}",
            )

        # Ingest using the temp path but track under original filename
        try:
            n = ingest_csv(conn, tmp_path, skip_if_processed=False)
            # Re-track under original filename
            with conn:
                ph = get_placeholder(conn)
                conn.execute(
                    f"INSERT INTO files (file_path, status, processed_at) "
                    f"VALUES ({ph}, 'processed', CURRENT_TIMESTAMP) "
                    f"ON CONFLICT(file_path) DO UPDATE SET "
                    f"status='processed', processed_at=CURRENT_TIMESTAMP",
                    (file.filename,)
                )
            return IngestResponse(
                file_name=file.filename,
                records_ingested=n,
                status="ingested",
                message=f"Successfully ingested {n} records from {file.filename}",
            )
        except Exception as exc:
            return IngestResponse(
                file_name=file.filename,
                records_ingested=0,
                status="failed",
                message=str(exc),
            )

    finally:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink()


# ---------------------------------------------------------------------------
# List tracked files
# ---------------------------------------------------------------------------

@router.get("/files")
def list_files(
    status: str | None = Query(None, description="Filter: pending, processed, failed"),
    conn=Depends(get_db),
):
    """List all tracked files and their ingestion status."""
    ph = get_placeholder(conn)

    if status:
        rows = conn.execute(
            f"SELECT * FROM files WHERE status = {ph} ORDER BY processed_at DESC",
            (status,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM files ORDER BY processed_at DESC"
        ).fetchall()

    return {
        "total": len(rows),
        "files": [dict(r) for r in rows],
    }
