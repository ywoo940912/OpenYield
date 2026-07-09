"""
ingestion/adapters/klarf2_adapter.py
--------------------------------------
Author: Yeonkuk Woo

KLARF 2.0 binary file parser and OpenYield database ingestion adapter.

KLARF (KLA Results File) is the dominant interchange format for semiconductor
wafer inspection data, produced by KLA-Tencor inspection tools (Surfscan SP
series, PUMA, eDR series, etc.).  Version 1.x uses an ASCII record layout;
version 2.0 replaces it with a structured binary encoding that halves file
size and enables streaming ingest of multi-wafer lots.

This module implements:
  - Full binary parser for the KLARF 2.0 format (see block layout below)
  - Python dataclasses for every parsed record type
  - `ingest_klarf2_file()` — parse a .klf2 file and insert defects into the
    OpenYield `defects` and `panels` tables
  - `encode_klarf2()` — write in-memory KLARF 2.0 data back to bytes (used
    by the synthetic test generator)

KLARF 2.0 Binary Layout
-----------------------
All multi-byte integers are little-endian.

    ┌──────────────────────────────────────────────────┐
    │  File Header (12 bytes)                          │
    │    magic        :  8 bytes  "KLARF200"           │
    │    format_ver   :  uint16   2                    │
    │    endian_mark  :  uint16   0x4949 (little)      │
    └──────────────────────────────────────────────────┘
    ┌──────────────────────────────────────────────────┐
    │  Block (repeats until EOF block)                 │
    │    block_type   :  uint16                        │
    │    block_length :  uint32  (bytes after header)  │
    │    block_data   :  block_length bytes            │
    └──────────────────────────────────────────────────┘

    Block type IDs:
        0x0001  FILE_INFO
        0x0002  LOT_INFO
        0x0003  SETUP_INFO
        0x0004  WAFER_INFO
        0x0005  DEFECT_LIST   (parallel to WAFER_INFO, same wafer order)
        0x0006  SUMMARY
        0xFFFF  EOF

    DEFECT_LIST defect record (36 bytes each):
        defect_id     :  uint32
        x_mm          :  float32
        y_mm          :  float32
        x_size_mm     :  float32
        y_size_mm     :  float32
        class_number  :  uint16
        rough_bin     :  uint16
        fine_bin      :  uint16
        test_number   :  uint16
        cluster_number:  uint16
        _pad          :  uint16  (reserved, zero)
        confidence    :  float32

References
----------
[1] KLA-Tencor KLARF Specification Rev 2.0.1, Aug 2019 (internal).
[2] SEMI M1-0302 — Standard for 300 mm Wafer — Polished Single Crystal
    Silicon (referenced in KLARF lot genealogy fields).
[3] J. Kibarian and A. Khare, "Using spatial information to analyze
    correlations between test structure results," IEEE Trans. Semicond.
    Manuf., 4(3):219–225, 1991.
"""

from __future__ import annotations

import io
import logging
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openyield.db.connection import get_placeholder

logger = logging.getLogger(__name__)

Connection = Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAGIC           = b"KLARF200"
FORMAT_VERSION  = 2
ENDIAN_MARK     = 0x4949  # little-endian sentinel

BLOCK_FILE_INFO   = 0x0001
BLOCK_LOT_INFO    = 0x0002
BLOCK_SETUP_INFO  = 0x0003
BLOCK_WAFER_INFO  = 0x0004
BLOCK_DEFECT_LIST = 0x0005
BLOCK_SUMMARY     = 0x0006
BLOCK_EOF         = 0xFFFF

# Block header: type(2) + length(4)
_BLOCK_HEADER_STRUCT = struct.Struct("<HI")

# FILE_INFO block: station_id(32) + timestamp(4) + inspector_version(16)
_FILE_INFO_STRUCT = struct.Struct("<32sI16s")

# LOT_INFO block: lot_id(32) + step_id(32) + device_id(32) + process_step(32)
_LOT_INFO_STRUCT = struct.Struct("<32s32s32s32s")

# SETUP_INFO block: recipe_id(32) + mode(1) + _pad(1) + pixel_size(f4) +
#                   die_width(f4) + die_height(f4) + num_classes(u2)
_SETUP_INFO_STRUCT = struct.Struct("<32sBBfffH")

# WAFER_INFO block: wafer_id(16) + slot(u1) + wafer_type(u1) +
#                   orientation(u2) + num_defects(u4)
_WAFER_INFO_STRUCT = struct.Struct("<16sBBHI")

# DEFECT record (36 bytes): id(u4) + x(f4) + y(f4) + xsz(f4) + ysz(f4) +
#   class(u2) + rough(u2) + fine(u2) + test(u2) + cluster(u2) + pad(u2) + conf(f4)
_DEFECT_STRUCT = struct.Struct("<IffffHHHHHHf")
assert _DEFECT_STRUCT.size == 36, f"Defect struct size mismatch: {_DEFECT_STRUCT.size}"

# SUMMARY block: total_wafers(u2) + total_defects(u4) + mean_def_per_wafer(f4)
_SUMMARY_STRUCT = struct.Struct("<HIf")

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Klarf2FileInfo:
    """Metadata from the FILE_INFO block."""
    station_id:        str
    file_timestamp:    int   # Unix epoch
    inspector_version: str


@dataclass
class Klarf2LotInfo:
    """Lot / process context from the LOT_INFO block."""
    lot_id:       str
    step_id:      str
    device_id:    str
    process_step: str


@dataclass
class Klarf2SetupInfo:
    """Inspection recipe and geometry from the SETUP_INFO block."""
    recipe_id:          str
    inspection_mode:    int    # 0=brightfield, 1=darkfield, 2=phase
    pixel_size_um:      float
    die_width_mm:       float
    die_height_mm:      float
    num_defect_classes: int


@dataclass
class Klarf2Defect:
    """Single defect record from a DEFECT_LIST block."""
    defect_id:      int
    x_mm:           float
    y_mm:           float
    x_size_mm:      float
    y_size_mm:      float
    class_number:   int
    rough_bin:      int
    fine_bin:       int
    test_number:    int
    cluster_number: int
    confidence:     float


@dataclass
class Klarf2Wafer:
    """Per-wafer data from WAFER_INFO + DEFECT_LIST block pair."""
    wafer_id:    str
    slot_number: int
    wafer_type:  int    # 0=product, 1=test, 2=monitor
    orientation: int    # degrees
    num_defects: int
    defects:     list[Klarf2Defect] = field(default_factory=list)


@dataclass
class Klarf2Summary:
    """Summary totals from the SUMMARY block."""
    total_wafers:         int
    total_defects:        int
    mean_defects_per_wafer: float


@dataclass
class Klarf2File:
    """Complete parsed KLARF 2.0 file."""
    file_info:  Klarf2FileInfo  | None
    lot_info:   Klarf2LotInfo   | None
    setup_info: Klarf2SetupInfo | None
    wafers:     list[Klarf2Wafer]
    summary:    Klarf2Summary   | None


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _read_exactly(buf: io.RawIOBase, n: int) -> bytes:
    """Read exactly n bytes; raise EOFError if stream ends early."""
    data = buf.read(n)
    if data is None or len(data) < n:
        raise EOFError(f"Expected {n} bytes, got {len(data) if data else 0}")
    return data


def _decode_fixed_str(b: bytes) -> str:
    """Decode a null-padded fixed-length byte string to str."""
    return b.rstrip(b"\x00").decode("ascii", errors="replace")


def _encode_fixed_str(s: str, length: int) -> bytes:
    """Encode str to a null-padded fixed-length bytes field."""
    encoded = s.encode("ascii", errors="replace")[:length]
    return encoded.ljust(length, b"\x00")


# ---------------------------------------------------------------------------
# Block parsers
# ---------------------------------------------------------------------------

def _parse_file_info(data: bytes) -> Klarf2FileInfo:
    if len(data) < _FILE_INFO_STRUCT.size:
        raise ValueError(f"FILE_INFO block too short: {len(data)}")
    station_b, ts, version_b = _FILE_INFO_STRUCT.unpack_from(data)
    return Klarf2FileInfo(
        station_id=_decode_fixed_str(station_b),
        file_timestamp=ts,
        inspector_version=_decode_fixed_str(version_b),
    )


def _parse_lot_info(data: bytes) -> Klarf2LotInfo:
    if len(data) < _LOT_INFO_STRUCT.size:
        raise ValueError(f"LOT_INFO block too short: {len(data)}")
    lot_b, step_b, dev_b, proc_b = _LOT_INFO_STRUCT.unpack_from(data)
    return Klarf2LotInfo(
        lot_id=_decode_fixed_str(lot_b),
        step_id=_decode_fixed_str(step_b),
        device_id=_decode_fixed_str(dev_b),
        process_step=_decode_fixed_str(proc_b),
    )


def _parse_setup_info(data: bytes) -> Klarf2SetupInfo:
    if len(data) < _SETUP_INFO_STRUCT.size:
        raise ValueError(f"SETUP_INFO block too short: {len(data)}")
    recipe_b, mode, _pad, pixel, die_w, die_h, n_cls = (
        _SETUP_INFO_STRUCT.unpack_from(data)
    )
    return Klarf2SetupInfo(
        recipe_id=_decode_fixed_str(recipe_b),
        inspection_mode=mode,
        pixel_size_um=float(pixel),
        die_width_mm=float(die_w),
        die_height_mm=float(die_h),
        num_defect_classes=n_cls,
    )


def _parse_wafer_info(data: bytes) -> Klarf2Wafer:
    if len(data) < _WAFER_INFO_STRUCT.size:
        raise ValueError(f"WAFER_INFO block too short: {len(data)}")
    wid_b, slot, wtype, orient, n_def = _WAFER_INFO_STRUCT.unpack_from(data)
    return Klarf2Wafer(
        wafer_id=_decode_fixed_str(wid_b),
        slot_number=slot,
        wafer_type=wtype,
        orientation=orient,
        num_defects=n_def,
    )


def _parse_defect_list(data: bytes, num_defects: int) -> list[Klarf2Defect]:
    """Parse a DEFECT_LIST block.  Ignores trailing bytes."""
    defects: list[Klarf2Defect] = []
    rec_size = _DEFECT_STRUCT.size
    offset = 0
    for i in range(num_defects):
        if offset + rec_size > len(data):
            logger.warning(
                "DEFECT_LIST truncated at record %d/%d", i, num_defects
            )
            break
        (
            did, x, y, xsz, ysz,
            cls, rough, fine, test, cluster, _pad, conf,
        ) = _DEFECT_STRUCT.unpack_from(data, offset)
        defects.append(Klarf2Defect(
            defect_id=did,
            x_mm=float(x),
            y_mm=float(y),
            x_size_mm=float(xsz),
            y_size_mm=float(ysz),
            class_number=cls,
            rough_bin=rough,
            fine_bin=fine,
            test_number=test,
            cluster_number=cluster,
            confidence=float(conf),
        ))
        offset += rec_size
    return defects


def _parse_summary(data: bytes) -> Klarf2Summary:
    if len(data) < _SUMMARY_STRUCT.size:
        raise ValueError(f"SUMMARY block too short: {len(data)}")
    total_w, total_d, mean = _SUMMARY_STRUCT.unpack_from(data)
    return Klarf2Summary(
        total_wafers=total_w,
        total_defects=total_d,
        mean_defects_per_wafer=float(mean),
    )


# ---------------------------------------------------------------------------
# Top-level parser
# ---------------------------------------------------------------------------

def parse_klarf2(stream: io.RawIOBase | bytes) -> Klarf2File:
    """
    Parse a KLARF 2.0 binary stream or byte string.

    Parameters
    ----------
    stream : file-like object opened in binary mode, or raw bytes.

    Returns
    -------
    Klarf2File — fully parsed structure.

    Raises
    ------
    ValueError : Invalid magic, version mismatch, or structural error.
    EOFError   : Unexpected end of stream.
    """
    if isinstance(stream, (bytes, bytearray)):
        buf: io.RawIOBase = io.BytesIO(stream)
    else:
        buf = stream

    # File header
    header = _read_exactly(buf, len(MAGIC) + 4)
    magic      = header[:8]
    fmt_ver    = struct.unpack_from("<H", header, 8)[0]
    endian_mk  = struct.unpack_from("<H", header, 10)[0]

    if magic != MAGIC:
        raise ValueError(
            f"Invalid KLARF 2.0 magic: got {magic!r}, expected {MAGIC!r}"
        )
    if fmt_ver != FORMAT_VERSION:
        raise ValueError(
            f"Unsupported KLARF format version {fmt_ver}; expected {FORMAT_VERSION}"
        )
    if endian_mk != ENDIAN_MARK:
        raise ValueError(
            f"Unsupported endian mark 0x{endian_mk:04X}; "
            f"only little-endian (0x{ENDIAN_MARK:04X}) is supported"
        )

    file_info:  Klarf2FileInfo  | None = None
    lot_info:   Klarf2LotInfo   | None = None
    setup_info: Klarf2SetupInfo | None = None
    summary:    Klarf2Summary   | None = None
    wafers:     list[Klarf2Wafer] = []

    # WAFER_INFO blocks are collected first; DEFECT_LIST blocks fill them
    # in-order (WAFER_INFO[0] pairs with DEFECT_LIST[0], etc.).
    defect_list_index = 0

    while True:
        bh = buf.read(_BLOCK_HEADER_STRUCT.size)
        if not bh:
            logger.warning("Stream ended without EOF block")
            break
        if len(bh) < _BLOCK_HEADER_STRUCT.size:
            raise EOFError("Truncated block header")

        block_type, block_len = _BLOCK_HEADER_STRUCT.unpack(bh)

        if block_type == BLOCK_EOF:
            break

        data = _read_exactly(buf, block_len)

        if block_type == BLOCK_FILE_INFO:
            file_info = _parse_file_info(data)
        elif block_type == BLOCK_LOT_INFO:
            lot_info = _parse_lot_info(data)
        elif block_type == BLOCK_SETUP_INFO:
            setup_info = _parse_setup_info(data)
        elif block_type == BLOCK_WAFER_INFO:
            wafers.append(_parse_wafer_info(data))
        elif block_type == BLOCK_DEFECT_LIST:
            if defect_list_index >= len(wafers):
                logger.warning(
                    "DEFECT_LIST block #%d has no matching WAFER_INFO; skipping",
                    defect_list_index,
                )
            else:
                w = wafers[defect_list_index]
                w.defects = _parse_defect_list(data, w.num_defects)
            defect_list_index += 1
        elif block_type == BLOCK_SUMMARY:
            summary = _parse_summary(data)
        else:
            logger.debug("Unknown block type 0x%04X (%d bytes); skipping", block_type, block_len)

    logger.info(
        "Parsed KLARF 2.0 — lot=%s  wafers=%d  total_defects=%d",
        lot_info.lot_id if lot_info else "UNKNOWN",
        len(wafers),
        sum(len(w.defects) for w in wafers),
    )
    return Klarf2File(
        file_info=file_info,
        lot_info=lot_info,
        setup_info=setup_info,
        wafers=wafers,
        summary=summary,
    )


def parse_klarf2_file(path: str | Path) -> Klarf2File:
    """Parse a KLARF 2.0 file from disk."""
    with open(path, "rb") as fh:
        return parse_klarf2(fh)


# ---------------------------------------------------------------------------
# Encoder (for synthetic test data generation)
# ---------------------------------------------------------------------------

def encode_klarf2(
    *,
    lot_info:   Klarf2LotInfo   | None = None,
    setup_info: Klarf2SetupInfo | None = None,
    file_info:  Klarf2FileInfo  | None = None,
    wafers:     list[Klarf2Wafer] | None = None,
    summary:    Klarf2Summary   | None = None,
) -> bytes:
    """
    Serialise KLARF 2.0 data to bytes.

    Used by the test suite to produce valid .klf2 byte strings without
    requiring real KLA tool output.
    """
    buf = io.BytesIO()

    # File header
    buf.write(MAGIC)
    buf.write(struct.pack("<HH", FORMAT_VERSION, ENDIAN_MARK))

    def _write_block(btype: int, data: bytes) -> None:
        buf.write(_BLOCK_HEADER_STRUCT.pack(btype, len(data)))
        buf.write(data)

    if file_info is not None:
        _write_block(
            BLOCK_FILE_INFO,
            _FILE_INFO_STRUCT.pack(
                _encode_fixed_str(file_info.station_id, 32),
                file_info.file_timestamp,
                _encode_fixed_str(file_info.inspector_version, 16),
            ),
        )

    if lot_info is not None:
        _write_block(
            BLOCK_LOT_INFO,
            _LOT_INFO_STRUCT.pack(
                _encode_fixed_str(lot_info.lot_id,       32),
                _encode_fixed_str(lot_info.step_id,      32),
                _encode_fixed_str(lot_info.device_id,    32),
                _encode_fixed_str(lot_info.process_step, 32),
            ),
        )

    if setup_info is not None:
        _write_block(
            BLOCK_SETUP_INFO,
            _SETUP_INFO_STRUCT.pack(
                _encode_fixed_str(setup_info.recipe_id, 32),
                setup_info.inspection_mode,
                0,  # pad
                setup_info.pixel_size_um,
                setup_info.die_width_mm,
                setup_info.die_height_mm,
                setup_info.num_defect_classes,
            ),
        )

    for wafer in (wafers or []):
        _write_block(
            BLOCK_WAFER_INFO,
            _WAFER_INFO_STRUCT.pack(
                _encode_fixed_str(wafer.wafer_id, 16),
                wafer.slot_number,
                wafer.wafer_type,
                wafer.orientation,
                len(wafer.defects),
            ),
        )
        defect_bytes = b"".join(
            _DEFECT_STRUCT.pack(
                d.defect_id,
                d.x_mm, d.y_mm,
                d.x_size_mm, d.y_size_mm,
                d.class_number,
                d.rough_bin, d.fine_bin,
                d.test_number, d.cluster_number,
                0,  # pad
                d.confidence,
            )
            for d in wafer.defects
        )
        _write_block(BLOCK_DEFECT_LIST, defect_bytes)

    if summary is not None:
        _write_block(
            BLOCK_SUMMARY,
            _SUMMARY_STRUCT.pack(
                summary.total_wafers,
                summary.total_defects,
                summary.mean_defects_per_wafer,
            ),
        )

    # EOF block (zero-length)
    buf.write(_BLOCK_HEADER_STRUCT.pack(BLOCK_EOF, 0))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Database ingestion
# ---------------------------------------------------------------------------

# Map KLARF class numbers to OpenYield defect_type strings
_CLASS_TO_TYPE: dict[int, str] = {
    0:  "particle",
    1:  "scratch",
    2:  "pit",
    3:  "crystal_defect",
    4:  "metal_spike",
    5:  "void",
    6:  "bridging",
    7:  "mura",
    8:  "pinhole",
    9:  "line_defect",
    10: "open_circuit",
    11: "short_circuit",
}

# Public aliases — used by the KLARF export endpoint
CLASS_TO_DEFECT_TYPE: dict[int, str] = _CLASS_TO_TYPE
DEFECT_TYPE_TO_CLASS: dict[str, int] = {v: k for k, v in _CLASS_TO_TYPE.items()}


def _defect_type(class_number: int) -> str:
    return _CLASS_TO_TYPE.get(class_number, f"class_{class_number}")


def ingest_klarf2_file(
    conn: Connection,
    path: str | Path,
    *,
    panel_id_prefix: str = "KLA",
    substrate_type:  str = "wafer",
    source_system:   str = "kla_surfscan",
    dry_run:         bool = False,
) -> dict[str, int]:
    """
    Parse a KLARF 2.0 file and ingest its defects into the OpenYield database.

    Each wafer in the file becomes a panel in the `panels` table (keyed by
    ``<panel_id_prefix>_<lot_id>_<wafer_id>``).  Defect records from the
    DEFECT_LIST blocks are inserted into the `defects` table.

    Parameters
    ----------
    conn             : Database connection (SQLite or PostgreSQL).
    path             : Path to the .klf2 file.
    panel_id_prefix  : Prefix for generated panel IDs (default "KLA").
    substrate_type   : "wafer" or "glass_panel" (default "wafer").
    source_system    : Value stored in defects.source_system (default
                       "kla_surfscan").
    dry_run          : If True, parse only — do not write to DB.

    Returns
    -------
    dict with keys "wafers_ingested", "defects_inserted".
    """
    klf = parse_klarf2_file(path)
    lot_id = klf.lot_info.lot_id if klf.lot_info else "UNKNOWN"

    wafers_ingested = 0
    defects_inserted = 0

    ph = get_placeholder(conn)

    for wafer in klf.wafers:
        panel_id = f"{panel_id_prefix}_{lot_id}_{wafer.wafer_id}"

        if not dry_run:
            with conn:
                conn.execute(
                    f"INSERT OR IGNORE INTO panels "
                    f"(panel_id, substrate_type, rows, cols, lot_id, "
                    f" component_pitch_mm, product_type) "
                    f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph})",
                    (
                        panel_id, substrate_type,
                        1, 1,        # die layout unknown from KLARF alone
                        lot_id,
                        0.0,         # pitch not encoded in KLARF; updated by caller
                        "KLARF_IMPORT",
                    ),
                )

            with conn:
                conn.executemany(
                    f"INSERT INTO defects "
                    f"(panel_id, component_row, component_col, "
                    f" source_system, defect_type, x, y, "
                    f" size, confidence_score) "
                    f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
                    [
                        (
                            panel_id, 0, 0,
                            source_system,
                            _defect_type(d.class_number),
                            d.x_mm, d.y_mm,
                            max(d.x_size_mm, d.y_size_mm),
                            d.confidence,
                        )
                        for d in wafer.defects
                    ],
                )

        wafers_ingested += 1
        defects_inserted += len(wafer.defects)

    logger.info(
        "KLARF 2.0 ingest (%s) — lot=%s  wafers=%d  defects=%d%s",
        path, lot_id, wafers_ingested, defects_inserted,
        " [DRY RUN]" if dry_run else "",
    )
    return {
        "wafers_ingested":  wafers_ingested,
        "defects_inserted": defects_inserted,
    }


def ingest_klarf2_bytes(
    conn: Connection,
    data: bytes,
    *,
    panel_id_prefix: str = "KLA",
    substrate_type:  str = "wafer",
    source_system:   str = "kla_surfscan",
    dry_run:         bool = False,
) -> dict[str, int]:
    """
    Same as ``ingest_klarf2_file`` but accepts raw bytes instead of a path.
    Useful when data arrives over a network socket or from an S3 stream.
    """
    klf = parse_klarf2(data)
    lot_id = klf.lot_info.lot_id if klf.lot_info else "UNKNOWN"
    ph = get_placeholder(conn)

    wafers_ingested = 0
    defects_inserted = 0

    for wafer in klf.wafers:
        panel_id = f"{panel_id_prefix}_{lot_id}_{wafer.wafer_id}"

        if not dry_run:
            with conn:
                conn.execute(
                    f"INSERT OR IGNORE INTO panels "
                    f"(panel_id, substrate_type, rows, cols, lot_id, "
                    f" component_pitch_mm, product_type) "
                    f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph})",
                    (panel_id, substrate_type, 1, 1, lot_id, 0.0, "KLARF_IMPORT"),
                )
            with conn:
                conn.executemany(
                    f"INSERT INTO defects "
                    f"(panel_id, component_row, component_col, "
                    f" source_system, defect_type, x, y, "
                    f" size, confidence_score) "
                    f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
                    [
                        (
                            panel_id, 0, 0, source_system,
                            _defect_type(d.class_number),
                            d.x_mm, d.y_mm,
                            max(d.x_size_mm, d.y_size_mm),
                            d.confidence,
                        )
                        for d in wafer.defects
                    ],
                )

        wafers_ingested += 1
        defects_inserted += len(wafer.defects)

    return {
        "wafers_ingested":  wafers_ingested,
        "defects_inserted": defects_inserted,
    }
