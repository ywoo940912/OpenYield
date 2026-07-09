"""
ingestion/adapters/flex_csv_adapter.py
---------------------------------------
Author: Yeonkuk Woo

Flexible CSV adapter for OpenYield — designed for small fabs and third-party
inspection equipment that produce their own CSV formats.

A JSON mapping config describes how to translate any CSV into the OpenYield
NormalizedDefect schema.  No Python programming is required for the common case.

MAPPING CONFIG FORMAT
---------------------
The config is a JSON object with one key per OpenYield field.
Each field spec is one of four forms:

    Direct column (with optional type coercion and scaling):
        {"column": "X_POSITION", "type": "float", "scale": 0.001}

    Fixed literal value (doesn't read from the CSV):
        {"value": "system_a"}

    Discrete value map (translate class codes → defect type strings):
        {"column": "CLASS_CODE", "map": {"1": "particle", "2": "scratch"}, "default": "unclassified"}

    Template (build the value from multiple columns):
        {"template": "{LOT_ID}_{WAFER_ID}"}

FIELD SPECS
-----------
Required OpenYield fields and their accepted types:

    panel_id         str    — wafer / panel identifier
    component_row    int    — die row on the panel grid
    component_col    int    — die column on the panel grid
    source_system    str    — "system_a" or "system_b"
    defect_type      str    — particle, scratch, void, pit, … (see DEFECT_TYPES)
    x                float  — x coordinate in mm
    y                float  — y coordinate in mm
    size             float  — defect size in mm (must be > 0)
    confidence_score float  — detection confidence [0.0–1.0]

Optional top-level config keys:
    encoding         str    — CSV file encoding (default: "utf-8-sig")
    delimiter        str    — CSV delimiter (default: ",")
    skip_rows        int    — number of header rows to skip before the column row (default: 0)
    substrate_type   str    — "wafer" or "glass_panel" (default: "wafer")

EXAMPLE CONFIG
--------------
    {
        "encoding":       "utf-8-sig",
        "delimiter":      ",",
        "substrate_type": "wafer",
        "panel_id":         {"template": "{LOT_ID}_{WAFER_ID}"},
        "component_row":    {"column": "DIE_ROW",   "type": "int"},
        "component_col":    {"column": "DIE_COL",   "type": "int"},
        "source_system":    {"value":  "system_a"},
        "defect_type":      {"column": "CLASS_NUM",
                             "map":    {"0": "particle", "1": "scratch",
                                        "2": "pit",      "3": "void"},
                             "default": "unclassified"},
        "x":                {"column": "X_UM", "scale": 0.001},
        "y":                {"column": "Y_UM", "scale": 0.001},
        "size":             {"column": "SIZE_UM", "scale": 0.001},
        "confidence_score": {"value": 0.75}
    }

USAGE
-----
    from openyield.ingestion.adapters.flex_csv_adapter import FlexCsvAdapter

    adapter = FlexCsvAdapter.from_json_file("my_tool_mapping.json")
    defects = adapter.parse("inspection_run_2024.csv")

    # Or supply config as a dict:
    adapter = FlexCsvAdapter(config={...})
    defects = adapter.parse("data.csv")
"""

from __future__ import annotations

import csv
import json
import logging
import re
from pathlib import Path
from typing import Any

from .base import BaseAdapter, NormalizedDefect

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = (
    "panel_id", "component_row", "component_col", "source_system",
    "defect_type", "x", "y", "size", "confidence_score",
)

KNOWN_DEFECT_TYPES = {
    "particle", "scratch", "void", "pit", "contamination", "mura",
    "pinhole", "line_defect", "open_circuit", "short_circuit",
    "metal_spike", "bridging", "crystal_defect", "unclassified",
}

_TEMPLATE_RE = re.compile(r"\{(\w+)\}")


class ConfigError(ValueError):
    """Raised when a mapping config is invalid."""


class FlexCsvAdapter(BaseAdapter):
    """
    Parses any CSV defect file using a declarative JSON column mapping config.

    Parameters
    ----------
    config : dict
        The mapping configuration (see module docstring for format).
    warn_unknown_types : bool
        Log a warning when defect_type is not in KNOWN_DEFECT_TYPES (default True).
        Unknown types are still ingested — the config's map/default controls them.
    """

    def __init__(
        self,
        config: dict[str, Any],
        *,
        warn_unknown_types: bool = True,
    ) -> None:
        self.config            = config
        self.warn_unknown_types = warn_unknown_types
        self._validate_config()

    @classmethod
    def from_json_file(cls, path: str | Path, **kwargs) -> "FlexCsvAdapter":
        """Load a FlexCsvAdapter from a JSON mapping file on disk."""
        text = Path(path).read_text(encoding="utf-8")
        return cls(json.loads(text), **kwargs)

    @classmethod
    def from_json_string(cls, json_str: str, **kwargs) -> "FlexCsvAdapter":
        """Load a FlexCsvAdapter from a JSON string."""
        return cls(json.loads(json_str), **kwargs)

    # ── Public interface ───────────────────────────────────────────────────────

    def parse(self, file_path: str | Path) -> list[NormalizedDefect]:
        path = self._require_path(file_path)
        encoding  = self.config.get("encoding",  "utf-8-sig")
        delimiter = self.config.get("delimiter", ",")
        skip_rows = int(self.config.get("skip_rows", 0))

        records: list[NormalizedDefect] = []
        errors:  list[str]              = []

        with open(path, newline="", encoding=encoding, errors="replace") as fh:
            for _ in range(skip_rows):
                next(fh, None)
            reader = csv.DictReader(fh, delimiter=delimiter)
            for line_num, row in enumerate(reader, start=skip_rows + 2):
                try:
                    record = self._map_row(row, line_num)
                    errs   = record.validate()
                    if errs:
                        errors.append(f"Line {line_num}: {'; '.join(errs)}")
                        continue
                    records.append(record)
                except (KeyError, ValueError, TypeError) as exc:
                    errors.append(f"Line {line_num}: {exc}")

        if errors:
            sample = errors[:5]
            suffix = f" … ({len(errors) - 5} more)" if len(errors) > 5 else ""
            raise ValueError(
                f"FlexCsvAdapter: {len(errors)} row(s) failed in {path.name}:\n"
                + "\n".join(f"  {e}" for e in sample) + suffix
            )

        logger.info("FlexCsvAdapter: parsed %d records from %s", len(records), path.name)
        return records

    # ── Internal row mapper ────────────────────────────────────────────────────

    def _map_row(self, row: dict[str, str], line_num: int) -> NormalizedDefect:
        """Resolve all required fields from a single CSV row."""
        def resolve(field: str) -> Any:
            spec = self.config[field]
            return self._resolve_spec(spec, row, field)

        defect_type = str(resolve("defect_type"))
        if self.warn_unknown_types and defect_type not in KNOWN_DEFECT_TYPES:
            logger.warning(
                "Line %d: unrecognised defect_type %r — ingesting anyway",
                line_num, defect_type,
            )

        return NormalizedDefect(
            panel_id=str(resolve("panel_id")),
            component_row=int(resolve("component_row")),
            component_col=int(resolve("component_col")),
            source_system=str(resolve("source_system")),
            defect_type=defect_type,
            x=float(resolve("x")),
            y=float(resolve("y")),
            size=float(resolve("size")),
            confidence_score=float(resolve("confidence_score")),
        )

    def _resolve_spec(self, spec: Any, row: dict[str, str], field: str) -> Any:
        """Resolve a single field spec against a CSV row."""
        # Shorthand: bare string means {"column": "<string>"}
        if isinstance(spec, str):
            return row[spec]
        if isinstance(spec, (int, float, bool)):
            return spec

        if not isinstance(spec, dict):
            raise ConfigError(f"Field {field!r}: spec must be a dict, str, or number")

        # ── {"value": ...} — fixed literal ────────────────────────────────────
        if "value" in spec:
            return spec["value"]

        # ── {"template": "..."} — multi-column f-string ───────────────────────
        if "template" in spec:
            tmpl = spec["template"]
            col_names = _TEMPLATE_RE.findall(tmpl)
            result = tmpl
            for col in col_names:
                val = row.get(col, "")
                result = result.replace(f"{{{col}}}", val.strip())
            return result

        # ── {"column": "..."} — read from CSV column ──────────────────────────
        if "column" not in spec:
            raise ConfigError(f"Field {field!r}: spec must have 'column', 'value', or 'template'")

        col  = spec["column"]
        raw  = row.get(col)

        if raw is None:
            # Column not present in this CSV at all
            if "default" in spec:
                return spec["default"]
            raise KeyError(f"Column {col!r} not found in CSV (available: {list(row)[:8]}…)")

        raw = raw.strip()
        if raw == "" and "default" in spec:
            return spec["default"]

        # ── {"map": {...}} — discrete value translation ───────────────────────
        if "map" in spec:
            mapping = spec["map"]
            if raw in mapping:
                return mapping[raw]
            if "default" in spec:
                return spec["default"]
            raise ValueError(
                f"Column {col!r}: value {raw!r} not in map and no default set. "
                f"Known keys: {list(mapping)[:8]}"
            )

        # ── scale + type coercion ─────────────────────────────────────────────
        value: Any = raw
        typ = spec.get("type", "").lower()
        if typ in ("float", "f"):
            value = float(value)
        elif typ in ("int", "i"):
            value = int(float(value))  # int(float()) handles "3.0" → 3

        if "scale" in spec:
            value = float(value) * float(spec["scale"])

        return value

    # ── Config validator ───────────────────────────────────────────────────────

    def _validate_config(self) -> None:
        """Raise ConfigError if required fields are missing from the config."""
        missing = [f for f in REQUIRED_FIELDS if f not in self.config]
        if missing:
            raise ConfigError(
                f"FlexCsvAdapter: mapping config is missing required fields: {missing}\n"
                f"Required: {list(REQUIRED_FIELDS)}"
            )

    # ── Schema export ──────────────────────────────────────────────────────────

    @staticmethod
    def example_config() -> dict[str, Any]:
        """Return a fully-commented example config dict (rename columns as needed)."""
        return {
            "_comment":       "OpenYield FlexCsvAdapter mapping — rename columns to match your CSV",
            "encoding":       "utf-8-sig",
            "delimiter":      ",",
            "skip_rows":      0,
            "substrate_type": "wafer",
            "panel_id":         {"template": "{LOT_ID}_{WAFER_ID}"},
            "component_row":    {"column": "DIE_ROW",  "type": "int"},
            "component_col":    {"column": "DIE_COL",  "type": "int"},
            "source_system":    {"value":  "system_a"},
            "defect_type":      {
                "column":  "CLASS_CODE",
                "map":     {
                    "0": "particle",   "1": "scratch",
                    "2": "pit",        "3": "void",
                    "4": "metal_spike","5": "bridging",
                },
                "default": "unclassified",
            },
            "x":                {"column": "X_UM",    "scale": 0.001},
            "y":                {"column": "Y_UM",    "scale": 0.001},
            "size":             {"column": "SIZE_UM", "scale": 0.001},
            "confidence_score": {"value":  0.75},
        }
