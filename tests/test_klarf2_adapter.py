"""
tests/test_klarf2_adapter.py
------------------------------
Tests for ingestion/adapters/klarf2_adapter.py — KLARF 2.0 binary parser.

Test organisation
-----------------
1. encode_klarf2 / parse_klarf2 round-trip (no DB)
2. Block-level parsing unit tests
3. Error handling (bad magic, wrong version, truncation, etc.)
4. Defect record correctness
5. Summary and metadata fields
6. Multi-wafer files
7. Database ingestion integration tests
"""

from __future__ import annotations

import struct

import pytest

from openyield.ingestion.adapters.klarf2_adapter import (
    MAGIC, FORMAT_VERSION, ENDIAN_MARK,
    BLOCK_FILE_INFO, BLOCK_LOT_INFO, BLOCK_SETUP_INFO,
    BLOCK_WAFER_INFO, BLOCK_DEFECT_LIST, BLOCK_SUMMARY, BLOCK_EOF,
    _BLOCK_HEADER_STRUCT,
    Klarf2FileInfo,
    Klarf2LotInfo,
    Klarf2SetupInfo,
    Klarf2Wafer,
    Klarf2Defect,
    Klarf2Summary,
    Klarf2File,
    encode_klarf2,
    parse_klarf2,
    parse_klarf2_file,
    ingest_klarf2_bytes,
    _defect_type,
    _decode_fixed_str,
    _encode_fixed_str,
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

def _make_defect(
    defect_id: int = 1,
    x_mm: float = 10.5,
    y_mm: float = 20.3,
    x_size_mm: float = 0.005,
    y_size_mm: float = 0.004,
    class_number: int = 0,
    rough_bin: int = 1,
    fine_bin: int = 2,
    test_number: int = 0,
    cluster_number: int = 0,
    confidence: float = 0.92,
) -> Klarf2Defect:
    return Klarf2Defect(
        defect_id=defect_id, x_mm=x_mm, y_mm=y_mm,
        x_size_mm=x_size_mm, y_size_mm=y_size_mm,
        class_number=class_number, rough_bin=rough_bin, fine_bin=fine_bin,
        test_number=test_number, cluster_number=cluster_number,
        confidence=confidence,
    )


def _make_wafer(
    wafer_id: str = "W01",
    slot: int = 1,
    n_defects: int = 3,
) -> Klarf2Wafer:
    defects = [_make_defect(defect_id=i + 1) for i in range(n_defects)]
    return Klarf2Wafer(
        wafer_id=wafer_id,
        slot_number=slot,
        wafer_type=0,
        orientation=0,
        num_defects=len(defects),
        defects=defects,
    )


def _minimal_klf2(wafers=None, *, include_lot=True) -> bytes:
    """Produce the smallest valid KLARF 2.0 byte string."""
    return encode_klarf2(
        lot_info=Klarf2LotInfo(
            lot_id="LOT001", step_id="LI01", device_id="DEV_A", process_step="LITHO"
        ) if include_lot else None,
        wafers=wafers or [_make_wafer()],
    )


@pytest.fixture
def mem_conn(tmp_path):
    from openyield.db.connection import get_connection
    from openyield.db.schema import initialize_schema
    conn = get_connection(tmp_path / "test.db")
    initialize_schema(conn)
    return conn


# ===========================================================================
# 1. Fixed-string encoding helpers
# ===========================================================================

class TestFixedString:
    def test_encode_shorter_than_field(self):
        b = _encode_fixed_str("ABC", 8)
        assert len(b) == 8
        assert b[:3] == b"ABC"
        assert b[3:] == b"\x00" * 5

    def test_encode_exact_length(self):
        b = _encode_fixed_str("ABCD", 4)
        assert b == b"ABCD"

    def test_encode_truncates_long_string(self):
        b = _encode_fixed_str("ABCDEF", 4)
        assert b == b"ABCD"

    def test_decode_strips_nulls(self):
        b = b"LOT001\x00\x00\x00\x00"
        assert _decode_fixed_str(b) == "LOT001"

    def test_decode_empty_bytes(self):
        assert _decode_fixed_str(b"\x00\x00") == ""

    def test_roundtrip(self):
        s = "WF_ABC123"
        assert _decode_fixed_str(_encode_fixed_str(s, 16)) == s


# ===========================================================================
# 2. encode_klarf2 / parse_klarf2 round-trip
# ===========================================================================

class TestRoundTrip:
    def test_parse_minimal_no_error(self):
        data = _minimal_klf2()
        result = parse_klarf2(data)
        assert isinstance(result, Klarf2File)

    def test_lot_info_round_trips(self):
        lot = Klarf2LotInfo(
            lot_id="LOT_RT", step_id="LI02",
            device_id="CHIP_X", process_step="ETCH",
        )
        data = encode_klarf2(lot_info=lot, wafers=[_make_wafer()])
        result = parse_klarf2(data)
        assert result.lot_info is not None
        assert result.lot_info.lot_id   == "LOT_RT"
        assert result.lot_info.step_id  == "LI02"
        assert result.lot_info.device_id == "CHIP_X"
        assert result.lot_info.process_step == "ETCH"

    def test_setup_info_round_trips(self):
        setup = Klarf2SetupInfo(
            recipe_id="RECIPE_DF", inspection_mode=1,
            pixel_size_um=0.13, die_width_mm=15.0, die_height_mm=20.0,
            num_defect_classes=7,
        )
        data = encode_klarf2(setup_info=setup, wafers=[_make_wafer()])
        result = parse_klarf2(data)
        assert result.setup_info is not None
        assert result.setup_info.recipe_id          == "RECIPE_DF"
        assert result.setup_info.inspection_mode    == 1
        assert result.setup_info.pixel_size_um      == pytest.approx(0.13, rel=1e-5)
        assert result.setup_info.die_width_mm       == pytest.approx(15.0)
        assert result.setup_info.die_height_mm      == pytest.approx(20.0)
        assert result.setup_info.num_defect_classes == 7

    def test_file_info_round_trips(self):
        fi = Klarf2FileInfo(
            station_id="KLA_SURFSCAN_SP3", file_timestamp=1700000000,
            inspector_version="7.4.1",
        )
        data = encode_klarf2(file_info=fi, wafers=[_make_wafer()])
        result = parse_klarf2(data)
        assert result.file_info is not None
        assert result.file_info.station_id     == "KLA_SURFSCAN_SP3"
        assert result.file_info.file_timestamp == 1700000000
        assert result.file_info.inspector_version == "7.4.1"

    def test_summary_round_trips(self):
        s = Klarf2Summary(
            total_wafers=25, total_defects=312, mean_defects_per_wafer=12.48
        )
        data = encode_klarf2(summary=s, wafers=[_make_wafer()])
        result = parse_klarf2(data)
        assert result.summary is not None
        assert result.summary.total_wafers   == 25
        assert result.summary.total_defects  == 312
        assert result.summary.mean_defects_per_wafer == pytest.approx(12.48, rel=1e-4)

    def test_wafer_count(self):
        wafers = [_make_wafer(f"W{i:02d}", slot=i) for i in range(5)]
        data = encode_klarf2(wafers=wafers)
        result = parse_klarf2(data)
        assert len(result.wafers) == 5

    def test_wafer_id_preserved(self):
        wafers = [_make_wafer("ALICE"), _make_wafer("BOB")]
        data = encode_klarf2(wafers=wafers)
        result = parse_klarf2(data)
        ids = [w.wafer_id for w in result.wafers]
        assert ids == ["ALICE", "BOB"]

    def test_bytes_input_accepted(self):
        data = _minimal_klf2()
        result = parse_klarf2(bytes(data))
        assert len(result.wafers) == 1

    def test_no_wafers_is_valid(self):
        data = encode_klarf2(wafers=[])
        result = parse_klarf2(data)
        assert result.wafers == []

    def test_no_optional_blocks_is_valid(self):
        """Only wafer + EOF — no lot/setup/file/summary blocks."""
        data = encode_klarf2(wafers=[_make_wafer()])
        result = parse_klarf2(data)
        assert result.lot_info   is None
        assert result.setup_info is None
        assert result.file_info  is None
        assert result.summary    is None


# ===========================================================================
# 3. Defect record correctness
# ===========================================================================

class TestDefectRecords:
    def _parse_single_wafer(self, wafer: Klarf2Wafer) -> Klarf2Wafer:
        data = encode_klarf2(wafers=[wafer])
        result = parse_klarf2(data)
        return result.wafers[0]

    def test_defect_count_matches(self):
        wafer = _make_wafer(n_defects=10)
        parsed = self._parse_single_wafer(wafer)
        assert len(parsed.defects) == 10

    def test_defect_id_preserved(self):
        d = _make_defect(defect_id=42)
        parsed = self._parse_single_wafer(
            Klarf2Wafer("W1", 1, 0, 0, 1, [d])
        )
        assert parsed.defects[0].defect_id == 42

    def test_xy_coordinates_preserved(self):
        d = _make_defect(x_mm=73.125, y_mm=-12.875)
        parsed = self._parse_single_wafer(
            Klarf2Wafer("W1", 1, 0, 0, 1, [d])
        )
        assert parsed.defects[0].x_mm == pytest.approx(73.125, rel=1e-5)
        assert parsed.defects[0].y_mm == pytest.approx(-12.875, rel=1e-5)

    def test_size_fields_preserved(self):
        d = _make_defect(x_size_mm=0.007, y_size_mm=0.003)
        parsed = self._parse_single_wafer(
            Klarf2Wafer("W1", 1, 0, 0, 1, [d])
        )
        assert parsed.defects[0].x_size_mm == pytest.approx(0.007, rel=1e-4)
        assert parsed.defects[0].y_size_mm == pytest.approx(0.003, rel=1e-4)

    def test_class_number_preserved(self):
        for cls in range(12):
            d = _make_defect(class_number=cls)
            parsed = self._parse_single_wafer(
                Klarf2Wafer("W1", 1, 0, 0, 1, [d])
            )
            assert parsed.defects[0].class_number == cls

    def test_confidence_preserved(self):
        d = _make_defect(confidence=0.675)
        parsed = self._parse_single_wafer(
            Klarf2Wafer("W1", 1, 0, 0, 1, [d])
        )
        assert parsed.defects[0].confidence == pytest.approx(0.675, rel=1e-4)

    def test_bin_fields_preserved(self):
        d = _make_defect(rough_bin=3, fine_bin=7, test_number=5, cluster_number=2)
        parsed = self._parse_single_wafer(
            Klarf2Wafer("W1", 1, 0, 0, 1, [d])
        )
        p = parsed.defects[0]
        assert p.rough_bin      == 3
        assert p.fine_bin       == 7
        assert p.test_number    == 5
        assert p.cluster_number == 2

    def test_zero_defects_wafer(self):
        wafer = Klarf2Wafer("CLEAN", 1, 0, 0, 0, [])
        parsed = self._parse_single_wafer(wafer)
        assert parsed.defects == []

    def test_defect_order_preserved(self):
        defects = [_make_defect(defect_id=i, x_mm=float(i)) for i in range(20)]
        wafer = Klarf2Wafer("W1", 1, 0, 0, len(defects), defects)
        parsed = self._parse_single_wafer(wafer)
        ids = [d.defect_id for d in parsed.defects]
        assert ids == list(range(20))


# ===========================================================================
# 4. Defect type mapping
# ===========================================================================

class TestDefectTypeMapping:
    @pytest.mark.parametrize("cls, expected", [
        (0,  "particle"),
        (1,  "scratch"),
        (2,  "pit"),
        (3,  "crystal_defect"),
        (4,  "metal_spike"),
        (5,  "void"),
        (6,  "bridging"),
        (7,  "mura"),
        (8,  "pinhole"),
        (9,  "line_defect"),
        (10, "open_circuit"),
        (11, "short_circuit"),
    ])
    def test_known_class(self, cls, expected):
        assert _defect_type(cls) == expected

    def test_unknown_class_returns_class_prefix(self):
        assert _defect_type(99) == "class_99"


# ===========================================================================
# 5. Error handling
# ===========================================================================

class TestErrorHandling:
    def test_bad_magic_raises(self):
        data = b"BADMAGIC" + struct.pack("<HH", FORMAT_VERSION, ENDIAN_MARK)
        with pytest.raises(ValueError, match="magic"):
            parse_klarf2(data)

    def test_wrong_version_raises(self):
        data = MAGIC + struct.pack("<HH", 1, ENDIAN_MARK)
        with pytest.raises(ValueError, match="version"):
            parse_klarf2(data)

    def test_big_endian_mark_raises(self):
        data = MAGIC + struct.pack("<HH", FORMAT_VERSION, 0x4D4D)
        with pytest.raises(ValueError, match="endian"):
            parse_klarf2(data)

    def test_empty_bytes_raises(self):
        with pytest.raises((ValueError, EOFError)):
            parse_klarf2(b"")

    def test_truncated_header_raises(self):
        data = MAGIC[:4]  # only 4 of 12 header bytes
        with pytest.raises((ValueError, EOFError)):
            parse_klarf2(data)

    def test_truncated_block_body_raises(self):
        # Valid header but a block promising 100 bytes with only 5
        data = (
            MAGIC
            + struct.pack("<HH", FORMAT_VERSION, ENDIAN_MARK)
            + _BLOCK_HEADER_STRUCT.pack(BLOCK_LOT_INFO, 100)
            + b"\x00" * 5
        )
        with pytest.raises(EOFError):
            parse_klarf2(data)

    def test_unknown_block_type_is_skipped(self):
        """Unknown block types should be silently skipped, not raise."""
        unknown_data = b"\x00" * 8
        data = (
            MAGIC
            + struct.pack("<HH", FORMAT_VERSION, ENDIAN_MARK)
            + _BLOCK_HEADER_STRUCT.pack(0x00FF, len(unknown_data))
            + unknown_data
            + _BLOCK_HEADER_STRUCT.pack(BLOCK_EOF, 0)
        )
        result = parse_klarf2(data)
        assert result.wafers == []

    def test_stream_ends_without_eof_block(self):
        """Parser should not crash if stream ends without the EOF block."""
        data = encode_klarf2(wafers=[_make_wafer()])
        # Strip the final 6-byte EOF block
        truncated = data[:-_BLOCK_HEADER_STRUCT.size]
        result = parse_klarf2(truncated)
        assert len(result.wafers) >= 1


# ===========================================================================
# 6. Multi-wafer file
# ===========================================================================

class TestMultiWafer:
    def test_defect_counts_per_wafer(self):
        wafers = [
            _make_wafer("W01", n_defects=5),
            _make_wafer("W02", n_defects=12),
            _make_wafer("W03", n_defects=0),
        ]
        data = encode_klarf2(lot_info=Klarf2LotInfo(
            "L01", "LI", "DEV", "STEP"
        ), wafers=wafers)
        result = parse_klarf2(data)

        assert len(result.wafers) == 3
        assert len(result.wafers[0].defects) == 5
        assert len(result.wafers[1].defects) == 12
        assert len(result.wafers[2].defects) == 0

    def test_wafer_slot_numbers(self):
        wafers = [
            Klarf2Wafer(f"W{i:02d}", slot_number=i, wafer_type=0,
                        orientation=0, num_defects=0, defects=[])
            for i in range(1, 6)
        ]
        data = encode_klarf2(wafers=wafers)
        result = parse_klarf2(data)
        slots = [w.slot_number for w in result.wafers]
        assert slots == list(range(1, 6))

    def test_total_defects_across_wafers(self):
        counts = [3, 7, 2, 11]
        wafers = [_make_wafer(f"W{i:02d}", n_defects=c) for i, c in enumerate(counts)]
        data = encode_klarf2(wafers=wafers)
        result = parse_klarf2(data)
        total = sum(len(w.defects) for w in result.wafers)
        assert total == sum(counts)


# ===========================================================================
# 7. File round-trip (using tmp_path)
# ===========================================================================

class TestFileRoundTrip:
    def test_write_read_file(self, tmp_path):
        klf_path = tmp_path / "test.klf2"
        wafer = _make_wafer("W01", n_defects=4)
        lot = Klarf2LotInfo("LOT_FILE", "LI01", "CHIP", "DIFF")
        klf_path.write_bytes(encode_klarf2(lot_info=lot, wafers=[wafer]))

        result = parse_klarf2_file(klf_path)
        assert result.lot_info.lot_id == "LOT_FILE"
        assert len(result.wafers[0].defects) == 4

    def test_file_not_found_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            parse_klarf2_file(tmp_path / "nonexistent.klf2")


# ===========================================================================
# 8. Database ingestion
# ===========================================================================

class TestDBIngestion:
    def _klf2_bytes(self, lot_id="LOT_DB", wafer_id="WF001", n_defects=5):
        return encode_klarf2(
            lot_info=Klarf2LotInfo(lot_id, "LI01", "DEV_X", "ETCH"),
            wafers=[_make_wafer(wafer_id, n_defects=n_defects)],
        )

    def test_dry_run_does_not_write(self, mem_conn):
        data = self._klf2_bytes(n_defects=3)
        result = ingest_klarf2_bytes(mem_conn, data, dry_run=True)
        assert result["wafers_ingested"]  == 1
        assert result["defects_inserted"] == 3
        row = mem_conn.execute("SELECT COUNT(*) AS n FROM defects").fetchone()
        assert row["n"] == 0

    def test_defects_inserted_into_db(self, mem_conn):
        data = self._klf2_bytes(n_defects=7)
        result = ingest_klarf2_bytes(mem_conn, data)
        assert result["defects_inserted"] == 7
        row = mem_conn.execute("SELECT COUNT(*) AS n FROM defects").fetchone()
        assert row["n"] == 7

    def test_panel_created_in_db(self, mem_conn):
        data = self._klf2_bytes(lot_id="LOT_P", wafer_id="WA01")
        ingest_klarf2_bytes(mem_conn, data, panel_id_prefix="KLA")
        expected_id = "KLA_LOT_P_WA01"
        row = mem_conn.execute(
            "SELECT panel_id FROM panels WHERE panel_id = ?", (expected_id,)
        ).fetchone()
        assert row is not None

    def test_source_system_stored(self, mem_conn):
        data = self._klf2_bytes(n_defects=2)
        ingest_klarf2_bytes(mem_conn, data, source_system="kla_test")
        rows = mem_conn.execute(
            "SELECT DISTINCT source_system FROM defects"
        ).fetchall()
        systems = {r["source_system"] for r in rows}
        assert "kla_test" in systems

    def test_defect_type_mapped(self, mem_conn):
        """Class 0 = particle; class 1 = scratch."""
        d0 = _make_defect(defect_id=1, class_number=0)
        d1 = _make_defect(defect_id=2, class_number=1)
        wafer = Klarf2Wafer("W1", 1, 0, 0, 2, [d0, d1])
        data = encode_klarf2(
            lot_info=Klarf2LotInfo("L", "S", "D", "P"),
            wafers=[wafer],
        )
        ingest_klarf2_bytes(mem_conn, data)
        types = {
            r["defect_type"]
            for r in mem_conn.execute("SELECT defect_type FROM defects").fetchall()
        }
        assert "particle" in types
        assert "scratch"  in types

    def test_xy_stored_correctly(self, mem_conn):
        d = _make_defect(x_mm=55.0, y_mm=77.5)
        wafer = Klarf2Wafer("W1", 1, 0, 0, 1, [d])
        data = encode_klarf2(
            lot_info=Klarf2LotInfo("L", "S", "D", "P"),
            wafers=[wafer],
        )
        ingest_klarf2_bytes(mem_conn, data)
        row = mem_conn.execute("SELECT x_mm, y_mm FROM defects").fetchone()
        assert row["x_mm"] == pytest.approx(55.0, rel=1e-4)
        assert row["y_mm"] == pytest.approx(77.5, rel=1e-4)

    def test_size_is_max_of_x_y(self, mem_conn):
        """size_mm stored = max(x_size_mm, y_size_mm) per the adapter spec."""
        d = _make_defect(x_size_mm=0.003, y_size_mm=0.007)
        wafer = Klarf2Wafer("W1", 1, 0, 0, 1, [d])
        data = encode_klarf2(
            lot_info=Klarf2LotInfo("L", "S", "D", "P"),
            wafers=[wafer],
        )
        ingest_klarf2_bytes(mem_conn, data)
        row = mem_conn.execute("SELECT size_mm FROM defects").fetchone()
        assert row["size_mm"] == pytest.approx(0.007, rel=1e-4)

    def test_multi_wafer_ingest(self, mem_conn):
        wafers = [
            _make_wafer("W01", n_defects=4),
            _make_wafer("W02", n_defects=6),
        ]
        data = encode_klarf2(
            lot_info=Klarf2LotInfo("LOT_MW", "LI", "D", "P"),
            wafers=wafers,
        )
        result = ingest_klarf2_bytes(mem_conn, data, panel_id_prefix="KLA")
        assert result["wafers_ingested"]  == 2
        assert result["defects_inserted"] == 10
        total = mem_conn.execute("SELECT COUNT(*) AS n FROM defects").fetchone()["n"]
        assert total == 10

    def test_idempotent_panel_insert(self, mem_conn):
        """Re-ingesting the same lot should not duplicate the panel row."""
        data = self._klf2_bytes(n_defects=1)
        ingest_klarf2_bytes(mem_conn, data)
        ingest_klarf2_bytes(mem_conn, data)
        n = mem_conn.execute("SELECT COUNT(*) AS n FROM panels").fetchone()["n"]
        assert n == 1

    def test_return_counts_without_db(self, mem_conn):
        """Result dict keys and types are correct."""
        data = self._klf2_bytes(n_defects=5)
        result = ingest_klarf2_bytes(mem_conn, data, dry_run=True)
        assert set(result.keys()) == {"wafers_ingested", "defects_inserted"}
        assert isinstance(result["wafers_ingested"],  int)
        assert isinstance(result["defects_inserted"], int)
