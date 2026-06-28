"""
ingestion/adapters/klarf_adapter.py
-------------------------------------
Author: Yeonkuk Woo

KLARF 1.x ASCII format adapter for OpenYield defect ingestion.

KLARF (KLA Results File) is the semiconductor industry standard for
transferring defect inspection results between tools and yield management
systems.  This adapter parses the subset of KLARF 1.x fields used by
OpenYield without requiring any proprietary libraries.

Field mapping (KLARF → OpenYield schema)
-----------------------------------------
KLARF field            OpenYield field        Notes
---------------------  ---------------------  --------------------------------
WaferID / LotID        panel_id               WaferID preferred; LotID fallback
InspectionTest         source_system          Mapped via source_system_map
XREL / YREL            x / y (mm)             Converted from µm if needed
DIEROW / DIECOL        component_row/col      Zero-based die grid coordinates
DEFECTSIZE             size (mm)              Converted from µm if needed
CLASSNUMBER / label    defect_type            Mapped via defect_class_map
confidence_score       confidence_score       Fixed value (KLARF has no score)

Unit handling
-------------
KLARF files specify units in the FileVersion/Units keyword.  This adapter
supports MICRON (default) and MM.  All output coordinates are in mm.

Limitations
-----------
- Parses KLARF 1.x (ASCII keyword-value format).  KLARF 2.0 (binary) is not
  supported.
- DefectList parsing assumes the column order declared in the DefectRecordSpec
  keyword.
- Multi-wafer KLARF files (multiple WaferID sections) are parsed as separate
  panels using each WaferID.

Usage
-----
    from openyield.ingestion.adapters.klarf_adapter import KlarfAdapter

    adapter = KlarfAdapter(source_system="system_a")
    defects = adapter.parse("wafer_inspection.001")
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Iterator

from .base import BaseAdapter, NormalizedDefect

logger = logging.getLogger(__name__)

# Default KLARF class number → defect type string
DEFAULT_CLASS_MAP: dict[int, str] = {
    0:  "unclassified",
    1:  "particle",
    2:  "scratch",
    3:  "pit",
    4:  "crystal_defect",
    5:  "void",
    6:  "bridging",
    7:  "metal_spike",
    8:  "edge_defect",
    9:  "foreign_material",
}

# Columns required in the DefectRecordSpec for this adapter to function
REQUIRED_KLARF_COLS = {"XREL", "YREL", "DEFECTSIZE", "CLASSNUMBER", "DIEROW", "DIECOL"}


class KlarfAdapter(BaseAdapter):
    """
    Parses KLARF 1.x ASCII defect files into NormalizedDefect records.

    Parameters
    ----------
    source_system : str
        Overrides the source_system field for all parsed defects.
        KLARF InspectionTest IDs are not guaranteed to match 'system_a'/'system_b';
        the caller must supply the correct mapping.
        Must be 'system_a' or 'system_b'.
    confidence_score : float
        Fixed confidence score assigned to all KLARF defects.
        KLARF 1.x has no native confidence field.
        Default: 0.75 (conservative mid-high confidence for scanner output).
    defect_class_map : dict[int, str] | None
        Override mapping from CLASSNUMBER integers to defect_type strings.
        Unrecognized class numbers fall back to 'unclassified'.
    units : str | None
        Force unit interpretation: 'MICRON' or 'MM'.
        If None (default), read from the KLARF Units keyword.
        Falls back to 'MICRON' if the keyword is absent.
    encoding : str
        File encoding (default: latin-1, which is safe for KLARF ASCII).
    """

    def __init__(
        self,
        source_system: str = "system_a",
        confidence_score: float = 0.75,
        defect_class_map: dict[int, str] | None = None,
        units: str | None = None,
        encoding: str = "latin-1",
    ) -> None:
        if source_system not in ("system_a", "system_b"):
            raise ValueError(
                f"source_system must be 'system_a' or 'system_b', got {source_system!r}"
            )
        self.source_system    = source_system
        self.confidence_score = confidence_score
        self.class_map        = defect_class_map or DEFAULT_CLASS_MAP
        self.forced_units     = units
        self.encoding         = encoding

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def parse(self, file_path: str | Path) -> list[NormalizedDefect]:
        path = self._require_path(file_path)
        raw  = path.read_text(encoding=self.encoding)
        return self._parse_klarf(raw, source_file=path.name)

    # ------------------------------------------------------------------
    # Internal parser
    # ------------------------------------------------------------------

    def _parse_klarf(self, text: str, source_file: str = "") -> list[NormalizedDefect]:
        """Parse raw KLARF text and return NormalizedDefect records."""
        lines   = iter(text.splitlines())
        records: list[NormalizedDefect] = []

        # State
        wafer_id      = ""
        units         = self.forced_units or "MICRON"
        col_index:    dict[str, int] = {}
        in_defect_list = False
        defect_lines:  list[str] = []

        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            upper = line.upper()

            # ---- WaferID ----
            if upper.startswith("WAFERID"):
                wafer_id = self._extract_value(line)

            # ---- Units ----
            elif upper.startswith("UNITS") and not self.forced_units:
                val = self._extract_value(line).upper()
                if val in ("MICRON", "MM"):
                    units = val

            # ---- DefectRecordSpec — defines column order ----
            elif upper.startswith("DEFECTRECORDSPEC"):
                col_index = self._parse_record_spec(line)
                missing = REQUIRED_KLARF_COLS - set(col_index)
                if missing:
                    raise ValueError(
                        f"KlarfAdapter: DefectRecordSpec missing required fields "
                        f"{sorted(missing)} in {source_file}"
                    )

            # ---- DefectList — collect raw defect rows ----
            elif upper.startswith("DEFECTLIST"):
                in_defect_list = True

            elif in_defect_list:
                if upper.startswith("SUMMARYSPEC") or upper.startswith("ENDOFFILE"):
                    in_defect_list = False
                    # Parse all collected defect lines
                    if not wafer_id:
                        wafer_id = f"UNKNOWN_{source_file}"
                    recs = self._parse_defect_list(
                        defect_lines, col_index, wafer_id, units
                    )
                    records.extend(recs)
                    defect_lines = []
                elif line:
                    defect_lines.append(line)

        # Handle files that end without SummarySpec
        if in_defect_list and defect_lines and col_index:
            if not wafer_id:
                wafer_id = f"UNKNOWN_{source_file}"
            records.extend(
                self._parse_defect_list(defect_lines, col_index, wafer_id, units)
            )

        logger.info(
            "KlarfAdapter: parsed %d defects from %s (wafer: %s, units: %s)",
            len(records), source_file, wafer_id, units,
        )
        return records

    def _parse_defect_list(
        self,
        lines: list[str],
        col_index: dict[str, int],
        wafer_id: str,
        units: str,
    ) -> list[NormalizedDefect]:
        """Convert raw defect list lines to NormalizedDefect records."""
        scale = 0.001 if units == "MICRON" else 1.0  # µm → mm
        records: list[NormalizedDefect] = []

        for line_num, line in enumerate(lines, start=1):
            # Remove trailing semicolons (KLARF terminates lists with ;)
            line = line.rstrip(";").strip()
            if not line:
                continue

            parts = line.split()
            try:
                x    = float(parts[col_index["XREL"]]) * scale
                y    = float(parts[col_index["YREL"]]) * scale
                size = float(parts[col_index["DEFECTSIZE"]]) * scale
                cls  = int(float(parts[col_index["CLASSNUMBER"]]))
                row  = int(float(parts[col_index["DIEROW"]]))
                col  = int(float(parts[col_index["DIECOL"]]))
            except (IndexError, ValueError) as exc:
                raise ValueError(
                    f"KlarfAdapter: malformed defect record at line {line_num}: "
                    f"{line!r} — {exc}"
                ) from exc

            if size <= 0:
                logger.warning(
                    "KlarfAdapter: skipping defect with size <= 0 at line %d", line_num
                )
                continue

            defect_type = self.class_map.get(cls, "unclassified")

            record = NormalizedDefect(
                panel_id=wafer_id,
                component_row=row,
                component_col=col,
                source_system=self.source_system,
                defect_type=defect_type,
                x=round(x, 4),
                y=round(y, 4),
                size=round(size, 4),
                confidence_score=self.confidence_score,
                match_id=None,
            )
            records.append(record)

        return records

    # ------------------------------------------------------------------
    # KLARF keyword helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_value(line: str) -> str:
        """
        Extract the value from a KLARF keyword line.
        Handles both 'KEYWORD Value;' and 'KEYWORD "Value";' forms.
        """
        # Strip keyword name and semicolon
        parts = line.split(None, 1)
        if len(parts) < 2:
            return ""
        val = parts[1].rstrip(";").strip().strip('"')
        return val

    @staticmethod
    def _parse_record_spec(line: str) -> dict[str, int]:
        """
        Parse DefectRecordSpec into a {COLUMN_NAME: index} map.

        KLARF format:
            DefectRecordSpec 17 INSPECT_ID XREL YREL ... CLASSNUMBER;
        The first token after the keyword is the column count, which we skip.
        """
        parts = line.split()
        # Find the keyword, skip it and the count
        try:
            kw_idx = next(
                i for i, p in enumerate(parts)
                if p.upper() == "DEFECTRECORDSPEC"
            )
        except StopIteration:
            return {}

        col_names = parts[kw_idx + 2:]  # skip keyword + count
        # Strip trailing semicolons from last token
        col_names = [c.rstrip(";").upper() for c in col_names if c.rstrip(";")]
        return {name: idx for idx, name in enumerate(col_names)}
