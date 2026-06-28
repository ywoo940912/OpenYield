"""
ingestion/adapters/csv_adapter.py
----------------------------------
Author: Yeonkuk Woo

Generic CSV adapter for OpenYield defect ingestion.

Expected CSV columns (order-independent, header row required):
    panel_id, component_row, component_col, source_system,
    defect_type, x, y, size, confidence_score

Optional columns:
    match_id    — ignored on ingest (always set to None)
    created_at  — parsed as ISO-8601 if present, otherwise UTC now

Any extra columns are silently ignored, making this adapter forward-compatible
with richer CSV exports.

Usage
-----
    from openyield.ingestion.adapters.csv_adapter import CsvAdapter

    adapter = CsvAdapter()
    defects = adapter.parse("output/GP_1A2B3C4D_defects.csv")
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime, timezone
from pathlib import Path

from .base import BaseAdapter, NormalizedDefect

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = {
    "panel_id", "component_row", "component_col",
    "source_system", "defect_type",
    "x", "y", "size", "confidence_score",
}


class CsvAdapter(BaseAdapter):
    """
    Parses OpenYield-format CSV defect files into NormalizedDefect records.

    Parameters
    ----------
    encoding : str
        File encoding (default: utf-8).
    skip_invalid : bool
        If True, log and skip invalid rows instead of raising.
        If False (default), raise ValueError on the first invalid row.
    """

    def __init__(self, encoding: str = "utf-8", skip_invalid: bool = False) -> None:
        self.encoding    = encoding
        self.skip_invalid = skip_invalid

    def parse(self, file_path: str | Path) -> list[NormalizedDefect]:
        path = self._require_path(file_path)
        records: list[NormalizedDefect] = []
        skipped = 0

        with open(path, newline="", encoding=self.encoding) as fh:
            reader = csv.DictReader(fh)

            # Validate header
            if reader.fieldnames is None:
                raise ValueError(f"CsvAdapter: empty file: {path}")
            actual = set(reader.fieldnames)
            missing = REQUIRED_COLUMNS - actual
            if missing:
                raise ValueError(
                    f"CsvAdapter: missing required columns {sorted(missing)} in {path}"
                )

            for line_num, row in enumerate(reader, start=2):  # 1-indexed, row 1 = header
                try:
                    record = self._parse_row(row)
                    errors = record.validate()
                    if errors:
                        raise ValueError(f"row {line_num}: {'; '.join(errors)}")
                    records.append(record)
                except (ValueError, KeyError) as exc:
                    if self.skip_invalid:
                        logger.warning("CsvAdapter skipping row %d in %s: %s",
                                       line_num, path.name, exc)
                        skipped += 1
                    else:
                        raise ValueError(f"CsvAdapter parse error in {path}: {exc}") from exc

        logger.info(
            "CsvAdapter: parsed %d records from %s (%d skipped)",
            len(records), path.name, skipped,
        )
        return records

    @staticmethod
    def _parse_row(row: dict) -> NormalizedDefect:
        # Parse optional created_at
        raw_ts = row.get("created_at", "").strip()
        if raw_ts:
            try:
                created_at = datetime.fromisoformat(raw_ts)
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
            except ValueError:
                created_at = datetime.now(timezone.utc)
        else:
            created_at = datetime.now(timezone.utc)

        return NormalizedDefect(
            panel_id=row["panel_id"].strip(),
            component_row=int(row["component_row"]),
            component_col=int(row["component_col"]),
            source_system=row["source_system"].strip(),
            defect_type=row["defect_type"].strip(),
            x=float(row["x"]),
            y=float(row["y"]),
            size=float(row["size"]),
            confidence_score=float(row["confidence_score"]),
            match_id=None,  # always None at ingest
            created_at=created_at,
        )
