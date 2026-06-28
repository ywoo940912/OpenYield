"""
tests/test_adapters.py
----------------------
Unit tests for ingestion/adapters — CsvAdapter, KlarfAdapter, NormalizedDefect.
"""

import csv
import pytest
from pathlib import Path
from datetime import datetime, timezone

from openyield.ingestion.adapters.base import BaseAdapter, NormalizedDefect
from openyield.ingestion.adapters.csv_adapter import CsvAdapter
from openyield.ingestion.adapters.klarf_adapter import KlarfAdapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def write_csv(path: Path, rows: list[dict], fieldnames: list[str] = None) -> Path:
    if fieldnames is None:
        fieldnames = [
            "panel_id", "component_row", "component_col", "source_system",
            "defect_type", "x", "y", "size", "confidence_score", "match_id", "created_at"
        ]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


def sample_row(**overrides):
    base = {
        "panel_id": "GP_TEST001", "component_row": 0, "component_col": 0,
        "source_system": "system_a", "defect_type": "particle",
        "x": 10.5, "y": 22.3, "size": 0.45, "confidence_score": 0.82,
        "match_id": "", "created_at": ""
    }
    base.update(overrides)
    return base


MINIMAL_KLARF = """\
FileVersion 1 2;
ResultTimestamp 2024 01 15 08 30 00;
WaferID WF_TESTWAFER01;
Units MICRON;
DefectRecordSpec 9 INSPECT_ID XREL YREL XINDEX YINDEX DIEROW DIECOL DEFECTSIZE CLASSNUMBER;
DefectList
1 150000 250000 0 0 2 3 800 1
2 300000 100000 0 0 4 5 1200 2
3 500000 450000 0 0 1 1 500 0
;
SummarySpec 1 NUMBEROFDEFECTS;
SummaryList
3;
EndOfFile;
"""

KLARF_MM_UNITS = """\
FileVersion 1 2;
WaferID WF_MM_TEST;
Units MM;
DefectRecordSpec 9 INSPECT_ID XREL YREL XINDEX YINDEX DIEROW DIECOL DEFECTSIZE CLASSNUMBER;
DefectList
1 15.0 25.0 0 0 0 0 0.8 1
;
EndOfFile;
"""

KLARF_MULTI_WAFER = """\
FileVersion 1 2;
WaferID WF_FIRST;
Units MICRON;
DefectRecordSpec 9 INSPECT_ID XREL YREL XINDEX YINDEX DIEROW DIECOL DEFECTSIZE CLASSNUMBER;
DefectList
1 100000 200000 0 0 1 1 500 1
;
SummarySpec 1 NUMBEROFDEFECTS;
SummaryList
1;
WaferID WF_SECOND;
DefectList
2 300000 400000 0 0 2 2 700 2
;
EndOfFile;
"""


# ---------------------------------------------------------------------------
# NormalizedDefect.validate()
# ---------------------------------------------------------------------------

def test_normalized_defect_valid():
    d = NormalizedDefect(
        panel_id="GP_001", component_row=0, component_col=0,
        source_system="system_a", defect_type="particle",
        x=10.0, y=20.0, size=0.5, confidence_score=0.8
    )
    assert d.validate() == []


def test_normalized_defect_empty_panel_id():
    d = NormalizedDefect(
        panel_id="", component_row=0, component_col=0,
        source_system="system_a", defect_type="particle",
        x=10.0, y=20.0, size=0.5, confidence_score=0.8
    )
    errors = d.validate()
    assert any("panel_id" in e for e in errors)


def test_normalized_defect_invalid_source_system():
    d = NormalizedDefect(
        panel_id="GP_001", component_row=0, component_col=0,
        source_system="system_c", defect_type="particle",
        x=10.0, y=20.0, size=0.5, confidence_score=0.8
    )
    errors = d.validate()
    assert any("source_system" in e for e in errors)


def test_normalized_defect_size_zero():
    d = NormalizedDefect(
        panel_id="GP_001", component_row=0, component_col=0,
        source_system="system_a", defect_type="particle",
        x=10.0, y=20.0, size=0.0, confidence_score=0.8
    )
    errors = d.validate()
    assert any("size" in e for e in errors)


def test_normalized_defect_confidence_out_of_range():
    d = NormalizedDefect(
        panel_id="GP_001", component_row=0, component_col=0,
        source_system="system_a", defect_type="particle",
        x=10.0, y=20.0, size=0.5, confidence_score=1.5
    )
    errors = d.validate()
    assert any("confidence_score" in e for e in errors)


def test_normalized_defect_match_id_must_be_none():
    d = NormalizedDefect(
        panel_id="GP_001", component_row=0, component_col=0,
        source_system="system_a", defect_type="particle",
        x=10.0, y=20.0, size=0.5, confidence_score=0.8,
        match_id="match_xyz"
    )
    errors = d.validate()
    assert any("match_id" in e for e in errors)


# ---------------------------------------------------------------------------
# CsvAdapter
# ---------------------------------------------------------------------------

def test_csv_adapter_parses_valid_file(tmp_dir):
    path = write_csv(tmp_dir / "defects.csv", [sample_row(), sample_row(x=20.0)])
    records = CsvAdapter().parse(path)
    assert len(records) == 2
    assert all(isinstance(r, NormalizedDefect) for r in records)


def test_csv_adapter_match_id_always_none(tmp_dir):
    path = write_csv(tmp_dir / "defects.csv", [sample_row(match_id="match_abc")])
    records = CsvAdapter().parse(path)
    assert records[0].match_id is None


def test_csv_adapter_missing_required_column(tmp_dir):
    path = write_csv(
        tmp_dir / "defects.csv",
        [{"panel_id": "GP_001", "x": 1.0}],
        fieldnames=["panel_id", "x"]
    )
    with pytest.raises(ValueError, match="missing required columns"):
        CsvAdapter().parse(path)


def test_csv_adapter_empty_file_raises(tmp_dir):
    path = tmp_dir / "empty.csv"
    path.write_text("")
    with pytest.raises(ValueError):
        CsvAdapter().parse(path)


def test_csv_adapter_invalid_row_raises(tmp_dir):
    path = write_csv(tmp_dir / "defects.csv", [sample_row(size="not_a_number")])
    with pytest.raises(ValueError):
        CsvAdapter().parse(path)


def test_csv_adapter_skip_invalid(tmp_dir):
    rows = [sample_row(), sample_row(size="bad"), sample_row(x=30.0)]
    path = write_csv(tmp_dir / "defects.csv", rows)
    records = CsvAdapter(skip_invalid=True).parse(path)
    assert len(records) == 2


def test_csv_adapter_file_not_found():
    with pytest.raises(FileNotFoundError):
        CsvAdapter().parse("/nonexistent/path/file.csv")


def test_csv_adapter_parses_created_at(tmp_dir):
    ts = "2024-03-15T08:30:00+00:00"
    path = write_csv(tmp_dir / "defects.csv", [sample_row(created_at=ts)])
    records = CsvAdapter().parse(path)
    assert records[0].created_at.year == 2024
    assert records[0].created_at.month == 3


def test_csv_adapter_extra_columns_ignored(tmp_dir):
    row = sample_row()
    row["extra_col"] = "ignored"
    fieldnames = list(row.keys())
    path = write_csv(tmp_dir / "defects.csv", [row], fieldnames=fieldnames)
    records = CsvAdapter().parse(path)
    assert len(records) == 1


def test_csv_adapter_correct_field_values(tmp_dir):
    path = write_csv(tmp_dir / "defects.csv", [
        sample_row(panel_id="GP_XYZ", component_row=2, component_col=3,
                   source_system="system_b", defect_type="scratch",
                   x=55.5, y=66.6, size=1.2, confidence_score=0.95)
    ])
    r = CsvAdapter().parse(path)[0]
    assert r.panel_id == "GP_XYZ"
    assert r.component_row == 2
    assert r.component_col == 3
    assert r.source_system == "system_b"
    assert r.defect_type == "scratch"
    assert r.x == pytest.approx(55.5)
    assert r.y == pytest.approx(66.6)
    assert r.size == pytest.approx(1.2)
    assert r.confidence_score == pytest.approx(0.95)


# ---------------------------------------------------------------------------
# KlarfAdapter
# ---------------------------------------------------------------------------

def test_klarf_adapter_parses_valid(tmp_dir):
    path = tmp_dir / "test.001"
    path.write_text(MINIMAL_KLARF)
    records = KlarfAdapter(source_system="system_a").parse(path)
    assert len(records) == 3


def test_klarf_adapter_micron_to_mm(tmp_dir):
    path = tmp_dir / "test.001"
    path.write_text(MINIMAL_KLARF)
    records = KlarfAdapter(source_system="system_a").parse(path)
    # First defect: XREL=150000 microns → 150.0 mm
    assert records[0].x == pytest.approx(150.0)
    assert records[0].y == pytest.approx(250.0)


def test_klarf_adapter_mm_units_no_conversion(tmp_dir):
    path = tmp_dir / "test_mm.001"
    path.write_text(KLARF_MM_UNITS)
    records = KlarfAdapter(source_system="system_b").parse(path)
    assert records[0].x == pytest.approx(15.0)
    assert records[0].y == pytest.approx(25.0)


def test_klarf_adapter_source_system_applied(tmp_dir):
    path = tmp_dir / "test.001"
    path.write_text(MINIMAL_KLARF)
    records = KlarfAdapter(source_system="system_b").parse(path)
    assert all(r.source_system == "system_b" for r in records)


def test_klarf_adapter_match_id_always_none(tmp_dir):
    path = tmp_dir / "test.001"
    path.write_text(MINIMAL_KLARF)
    records = KlarfAdapter(source_system="system_a").parse(path)
    assert all(r.match_id is None for r in records)


def test_klarf_adapter_class_map(tmp_dir):
    path = tmp_dir / "test.001"
    path.write_text(MINIMAL_KLARF)
    records = KlarfAdapter(source_system="system_a").parse(path)
    # CLASSNUMBER 1 → "particle", 2 → "scratch", 0 → "unclassified"
    types = {r.defect_type for r in records}
    assert "particle" in types
    assert "scratch" in types
    assert "unclassified" in types


def test_klarf_adapter_unknown_class_maps_to_unclassified(tmp_dir):
    klarf = MINIMAL_KLARF.replace(
        "1 150000 250000 0 0 2 3 800 1",
        "1 150000 250000 0 0 2 3 800 999"
    )
    path = tmp_dir / "test.001"
    path.write_text(klarf)
    records = KlarfAdapter(source_system="system_a").parse(path)
    assert records[0].defect_type == "unclassified"


def test_klarf_adapter_custom_class_map(tmp_dir):
    path = tmp_dir / "test.001"
    path.write_text(MINIMAL_KLARF)
    custom_map = {1: "my_particle", 2: "my_scratch", 0: "my_unknown"}
    records = KlarfAdapter(source_system="system_a", defect_class_map=custom_map).parse(path)
    assert records[0].defect_type == "my_particle"


def test_klarf_adapter_invalid_source_system():
    with pytest.raises(ValueError, match="source_system"):
        KlarfAdapter(source_system="system_c")


def test_klarf_adapter_wafer_id_used_as_panel_id(tmp_dir):
    path = tmp_dir / "test.001"
    path.write_text(MINIMAL_KLARF)
    records = KlarfAdapter(source_system="system_a").parse(path)
    assert all(r.panel_id == "WF_TESTWAFER01" for r in records)


def test_klarf_adapter_die_coordinates(tmp_dir):
    path = tmp_dir / "test.001"
    path.write_text(MINIMAL_KLARF)
    records = KlarfAdapter(source_system="system_a").parse(path)
    # First defect: DIEROW=2, DIECOL=3
    assert records[0].component_row == 2
    assert records[0].component_col == 3


def test_klarf_adapter_confidence_score_applied(tmp_dir):
    path = tmp_dir / "test.001"
    path.write_text(MINIMAL_KLARF)
    records = KlarfAdapter(source_system="system_a", confidence_score=0.88).parse(path)
    assert all(r.confidence_score == pytest.approx(0.88) for r in records)


def test_klarf_adapter_file_not_found():
    with pytest.raises(FileNotFoundError):
        KlarfAdapter(source_system="system_a").parse("/no/such/file.001")


def test_klarf_adapter_missing_record_spec(tmp_dir):
    klarf_no_spec = """\
FileVersion 1 2;
WaferID WF_NOSPEC;
Units MICRON;
DefectList
1 100 200 0 0 1 1 500 1
;
EndOfFile;
"""
    path = tmp_dir / "nospec.001"
    path.write_text(klarf_no_spec)
    # Should raise because col_index is empty when defect list is hit
    with pytest.raises((ValueError, KeyError)):
        KlarfAdapter(source_system="system_a").parse(path)
