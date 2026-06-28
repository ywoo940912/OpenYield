"""
ingestion/adapters/base.py
--------------------------
Author: Yeonkuk Woo

Adapter interface for the OpenYield ingestion layer.

All format-specific adapters (CSV, KLARF, future vendor formats) must subclass
BaseAdapter and implement parse().  The ingestion pipeline only depends on this
interface — it never imports a concrete adapter directly.

NormalizedDefect
----------------
The canonical record type returned by every adapter.  Fields map 1-to-1 to the
upsert_defect() signature in ingestion/ingest.py.

match_id is always None on ingest — it is assigned later by the spatial
matching step in the pipeline.  Adapters must not populate it.

Usage
-----
    from openyield.ingestion.adapters.csv_adapter import CsvAdapter

    adapter = CsvAdapter()
    defects = adapter.parse("path/to/file.csv")
    for d in defects:
        upsert_defect(conn, **d.__dict__)
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Canonical record type
# ---------------------------------------------------------------------------

@dataclass
class NormalizedDefect:
    """
    A single defect record in the OpenYield canonical schema.

    All coordinates are in millimeters.
    match_id is always None at ingestion time.
    """
    panel_id:         str
    component_row:    int
    component_col:    int
    source_system:    str          # 'system_a' or 'system_b'
    defect_type:      str
    x:                float        # mm
    y:                float        # mm
    size:             float        # mm, must be > 0
    confidence_score: float        # [0.0, 1.0]
    match_id:         str | None = None
    created_at:       datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def validate(self) -> list[str]:
        """
        Return a list of validation error strings.
        Empty list means the record is valid.
        """
        errors: list[str] = []
        if not self.panel_id:
            errors.append("panel_id is empty")
        if self.source_system not in ("system_a", "system_b"):
            errors.append(f"source_system must be 'system_a' or 'system_b', got {self.source_system!r}")
        if self.size <= 0:
            errors.append(f"size must be > 0, got {self.size}")
        if not (0.0 <= self.confidence_score <= 1.0):
            errors.append(f"confidence_score must be in [0.0, 1.0], got {self.confidence_score}")
        if self.match_id is not None:
            errors.append("match_id must be None at ingestion time (assigned by matching step)")
        return errors


# ---------------------------------------------------------------------------
# Abstract adapter base class
# ---------------------------------------------------------------------------

class BaseAdapter(abc.ABC):
    """
    Abstract base class for all OpenYield ingestion adapters.

    Subclasses implement parse() for a specific file format.
    The pipeline calls parse() and receives a list of NormalizedDefect objects
    ready for upsert_defect().

    Contract
    --------
    - parse() must not write to the database.
    - parse() must not populate match_id (always None).
    - parse() must return an empty list for empty or header-only input files.
    - parse() should raise ValueError for malformed files, not silently skip rows.
    - All coordinates must be in millimeters on return.
    """

    @abc.abstractmethod
    def parse(self, file_path: str | Path) -> list[NormalizedDefect]:
        """
        Parse a file and return a list of NormalizedDefect records.

        Parameters
        ----------
        file_path : str | Path
            Path to the file to parse.

        Returns
        -------
        list[NormalizedDefect]
            Parsed and normalized defect records.  Never None.

        Raises
        ------
        FileNotFoundError
            If file_path does not exist.
        ValueError
            If the file is malformed or missing required fields.
        """

    @property
    def name(self) -> str:
        """Human-readable adapter name for logging."""
        return self.__class__.__name__

    def _require_path(self, file_path: str | Path) -> Path:
        """Resolve and validate that the file exists."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"{self.name}: file not found: {path}")
        return path
