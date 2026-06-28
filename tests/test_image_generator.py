"""
tests/test_image_generator.py
------------------------------
Tests for the synthetic defect image generator.
"""

import struct
import zlib
from pathlib import Path

import pytest

from openyield.synthetic.image_generator import (
    generate_images_for_panel,
    generate_images_for_all,
    _render_defect,
    _make_background,
    _write_grayscale_png,
    _seed_from,
    _DRNG,
    IMAGE_W, IMAGE_H, GENERATOR_VERSION,
)
from openyield.ingestion.ingest import (
    upsert_panel, upsert_component, upsert_defect
)


# ---------------------------------------------------------------------------
# Deterministic RNG
# ---------------------------------------------------------------------------

def test_drng_deterministic():
    a = _DRNG(42)
    b = _DRNG(42)
    for _ in range(10):
        assert a.next() == b.next()


def test_drng_different_seeds_diverge():
    a = _DRNG(1)
    b = _DRNG(2)
    diffs = sum(1 for _ in range(20) if a.next() != b.next())
    assert diffs > 0


def test_drng_uniform_range():
    rng = _DRNG(123)
    for _ in range(100):
        v = rng.next()
        assert 0.0 < v < 1.0


def test_seed_from_panel_defect_stable():
    s1 = _seed_from("WF_TEST", 5)
    s2 = _seed_from("WF_TEST", 5)
    assert s1 == s2

def test_seed_from_panel_defect_distinguishes():
    s1 = _seed_from("WF_TEST", 5)
    s2 = _seed_from("WF_TEST", 6)
    assert s1 != s2


# ---------------------------------------------------------------------------
# Background generation
# ---------------------------------------------------------------------------

def test_background_correct_size():
    pixels = _make_background(_DRNG(1))
    assert len(pixels) == IMAGE_W * IMAGE_H


def test_background_pixels_in_range():
    pixels = _make_background(_DRNG(1))
    assert all(0 <= p <= 255 for p in pixels)


def test_background_reproducible():
    a = _make_background(_DRNG(99))
    b = _make_background(_DRNG(99))
    assert a == b


# ---------------------------------------------------------------------------
# Defect rendering
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("defect_type", [
    "particle", "scratch", "void", "pit",
    "contamination", "mura", "pinhole",
])
def test_render_defect_returns_correct_pixel_count(defect_type):
    pixels = _render_defect(defect_type, 0.5, "WF_T", 1)
    assert len(pixels) == IMAGE_W * IMAGE_H


@pytest.mark.parametrize("defect_type", [
    "particle", "scratch", "void", "pit",
    "contamination", "mura", "pinhole",
])
def test_render_defect_pixels_in_range(defect_type):
    pixels = _render_defect(defect_type, 1.0, "WF_T", 7)
    assert all(0 <= p <= 255 for p in pixels)


def test_render_defect_is_reproducible():
    a = _render_defect("particle", 0.5, "WF_T", 12)
    b = _render_defect("particle", 0.5, "WF_T", 12)
    assert a == b


def test_render_defect_different_ids_differ():
    a = _render_defect("particle", 0.5, "WF_T", 1)
    b = _render_defect("particle", 0.5, "WF_T", 2)
    assert a != b


def test_render_defect_modifies_background():
    """Rendered patch must differ from a pure background."""
    rng_bg = _DRNG(_seed_from("WF_T", 1))
    bg = _make_background(rng_bg)
    rendered = _render_defect("particle", 1.0, "WF_T", 1)
    assert rendered != bg


def test_render_unknown_type_does_not_crash():
    pixels = _render_defect("mystery_type", 0.5, "WF_T", 99)
    assert len(pixels) == IMAGE_W * IMAGE_H


# ---------------------------------------------------------------------------
# PNG writing
# ---------------------------------------------------------------------------

def test_png_file_has_valid_signature(tmp_path):
    pixels = [128] * (IMAGE_W * IMAGE_H)
    out = tmp_path / "test.png"
    _write_grayscale_png(out, IMAGE_W, IMAGE_H, pixels)
    raw = out.read_bytes()
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")


def test_png_has_iend_chunk(tmp_path):
    pixels = [200] * (IMAGE_W * IMAGE_H)
    out = tmp_path / "test.png"
    _write_grayscale_png(out, IMAGE_W, IMAGE_H, pixels)
    raw = out.read_bytes()
    assert raw.endswith(b"IEND\xaeB`\x82")


def test_png_decodes_back_correctly(tmp_path):
    """Decode the PNG to confirm IHDR has correct width/height."""
    pixels = [100] * (IMAGE_W * IMAGE_H)
    out = tmp_path / "test.png"
    _write_grayscale_png(out, IMAGE_W, IMAGE_H, pixels)
    raw = out.read_bytes()
    # IHDR is the first chunk after the 8-byte signature
    # Chunk layout: 4-byte length, 4-byte type, data, 4-byte CRC
    ihdr_len = struct.unpack(">I", raw[8:12])[0]
    ihdr_type = raw[12:16]
    ihdr_data = raw[16:16+ihdr_len]
    assert ihdr_type == b"IHDR"
    width, height, depth, color = struct.unpack(">IIBB", ihdr_data[:10])
    assert width == IMAGE_W
    assert height == IMAGE_H
    assert depth == 8
    assert color == 0


# ---------------------------------------------------------------------------
# DB-driven image generation
# ---------------------------------------------------------------------------

def _setup_defects(conn, panel_id):
    with conn:
        upsert_panel(conn, panel_id, "TEST", "wafer", 4, 4)
        for r in range(4):
            for c in range(4):
                upsert_component(conn, panel_id, r, c, "zone_center",
                                 float(c*28), float(r*28))
        types = ["particle", "scratch", "void", "pit",
                 "contamination", "mura", "pinhole"]
        for i, t in enumerate(types):
            upsert_defect(conn, panel_id, i % 4, i % 4, "system_a",
                          t, float(i), float(i), 0.5, 0.8)


def test_generate_images_for_panel_writes_files(mem_conn, tmp_path):
    _setup_defects(mem_conn, "WF_IMG1")
    result = generate_images_for_panel(
        mem_conn, "WF_IMG1",
        output_root=tmp_path, persist=True
    )
    assert result.n_images_written == 7
    out_dir = Path(result.output_dir)
    assert len(list(out_dir.glob("*.png"))) == 7


def test_generate_images_persists_records(mem_conn, tmp_path):
    _setup_defects(mem_conn, "WF_IMG2")
    generate_images_for_panel(
        mem_conn, "WF_IMG2",
        output_root=tmp_path, persist=True
    )
    rows = mem_conn.execute(
        "SELECT * FROM defect_images WHERE panel_id='WF_IMG2'"
    ).fetchall()
    assert len(rows) == 7
    for row in rows:
        assert row["width"] == IMAGE_W
        assert row["height"] == IMAGE_H
        assert row["generator_version"] == GENERATOR_VERSION


def test_generate_images_no_persist(mem_conn, tmp_path):
    _setup_defects(mem_conn, "WF_IMG3")
    generate_images_for_panel(
        mem_conn, "WF_IMG3",
        output_root=tmp_path, persist=False
    )
    rows = mem_conn.execute(
        "SELECT COUNT(*) FROM defect_images WHERE panel_id='WF_IMG3'"
    ).fetchone()[0]
    assert rows == 0


def test_generate_images_skips_existing(mem_conn, tmp_path):
    _setup_defects(mem_conn, "WF_IMG4")
    r1 = generate_images_for_panel(
        mem_conn, "WF_IMG4", output_root=tmp_path, persist=True
    )
    assert r1.n_images_written == 7
    r2 = generate_images_for_panel(
        mem_conn, "WF_IMG4", output_root=tmp_path, persist=True
    )
    assert r2.n_images_skipped == 7
    assert r2.n_images_written == 0


def test_generate_images_overwrite_flag(mem_conn, tmp_path):
    _setup_defects(mem_conn, "WF_IMG5")
    generate_images_for_panel(
        mem_conn, "WF_IMG5", output_root=tmp_path, persist=True
    )
    r2 = generate_images_for_panel(
        mem_conn, "WF_IMG5",
        output_root=tmp_path, overwrite=True, persist=True
    )
    assert r2.n_images_written == 7
    assert r2.n_images_skipped == 0


def test_generate_images_panel_not_found(mem_conn, tmp_path):
    with pytest.raises(ValueError, match="not found"):
        generate_images_for_panel(
            mem_conn, "NONEXISTENT", output_root=tmp_path, persist=False
        )


def test_generate_images_for_all_multiple_panels(mem_conn, tmp_path):
    _setup_defects(mem_conn, "WF_IMG6")
    _setup_defects(mem_conn, "WF_IMG7")
    results = generate_images_for_all(
        mem_conn, output_root=tmp_path, persist=True
    )
    assert len(results) == 2
    total = sum(r.n_images_written for r in results)
    assert total == 14


def test_generate_images_substrate_filter(mem_conn, tmp_path):
    _setup_defects(mem_conn, "WF_IMG_W")
    with mem_conn:
        upsert_panel(mem_conn, "GP_IMG", "TEST", "glass_panel", 3, 3)
        for r in range(3):
            for c in range(3):
                upsert_component(mem_conn, "GP_IMG", r, c, "region_NW",
                                 float(c*370), float(r*370))
        upsert_defect(mem_conn, "GP_IMG", 0, 0, "system_a",
                      "mura", 10.0, 10.0, 1.5, 0.8)

    results = generate_images_for_all(
        mem_conn, output_root=tmp_path,
        substrate_type="wafer", persist=True
    )
    assert len(results) == 1
    assert results[0].panel_id == "WF_IMG_W"


def test_image_files_byte_identical_across_runs(mem_conn, tmp_path):
    """Reproducibility — re-running generation produces the same PNG bytes."""
    _setup_defects(mem_conn, "WF_REPRO")
    out1 = tmp_path / "run1"
    out2 = tmp_path / "run2"
    generate_images_for_panel(
        mem_conn, "WF_REPRO", output_root=out1, persist=False
    )
    generate_images_for_panel(
        mem_conn, "WF_REPRO", output_root=out2, persist=False
    )
    files1 = sorted((out1 / "WF_REPRO").glob("*.png"))
    files2 = sorted((out2 / "WF_REPRO").glob("*.png"))
    assert len(files1) == len(files2)
    for f1, f2 in zip(files1, files2):
        assert f1.read_bytes() == f2.read_bytes()
