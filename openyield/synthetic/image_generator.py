"""
synthetic/image_generator.py
-----------------------------
Author: Yeonkuk Woo
Module: Synthetic defect image generator

Purpose
-------
Produces 64x64 grayscale PNG image patches for every defect record in the
OpenYield database. Images are generated procedurally — no external image
data, no proprietary references — so the resulting dataset is fully open
and distributable.

Beneficiary categories include domestic inspection toolmakers prototyping
classifier pipelines, academic groups training defect-image CNNs on
reproducible data, and national laboratory researchers benchmarking
inspection systems without proprietary data access.

Procedural signatures per defect type
--------------------------------------
    particle      Gaussian blob, dark on lighter background.
                  Radius and contrast scale with defect.size.
    scratch       Short oriented linear streak. Length scales with
                  defect.size; orientation seeded by defect_id.
    void          Ring shape — dark annulus with lighter centre.
    pit           Sharp-edged dark circle.
    contamination Irregular noisy blob (gaussian + per-pixel noise).
    mura          (glass only) soft low-frequency gradient over patch.
    pinhole       (glass only) single bright point on dark patch.

Background
----------
Every patch starts with a base luminance plus uniform Gaussian noise to
imitate the speckle of a real inspection sensor. Noise standard deviation
is fixed at 8 (out of 255) — perceptually realistic, deterministic, and
documented for reproducibility.

Reproducibility
----------------
Every image uses a deterministic seed derived from (panel_id, defect_id).
Re-running the generator yields byte-identical PNGs unless the underlying
defect record changes. This property is required for petition evidence
integrity (re-runs must be reproducible) and for any external benchmark
using OpenYield as a reference dataset.

PNG output without dependencies
--------------------------------
We emit minimal grayscale PNGs by hand to avoid Pillow/numpy dependencies
in the core image generation path. The PNG writer here supports only
8-bit grayscale (color type 0), which is exactly what fab defect patches
need. Implemented per RFC 2083 §11.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import struct
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openyield.db.connection import get_placeholder, is_postgres

logger = logging.getLogger(__name__)
Connection = Any

# Image parameters — fixed for v1 to guarantee reproducibility
IMAGE_W = 64
IMAGE_H = 64
BG_LUMINANCE = 200      # light-gray background
BG_NOISE_STD = 8        # standard deviation of sensor noise (in 0-255 units)
GENERATOR_VERSION = "v1"


# ---------------------------------------------------------------------------
# Pure-Python deterministic RNG (Park-Miller LCG)
# ---------------------------------------------------------------------------
# We avoid python's `random` module because we want byte-identical output
# across Python versions; Park-Miller is specified and stable.

_LCG_MOD = 2147483647   # 2^31 - 1
_LCG_MUL = 48271


class _DRNG:
    """Deterministic Park-Miller LCG. Output in (0, 1)."""

    __slots__ = ("state",)

    def __init__(self, seed: int):
        s = seed % _LCG_MOD
        self.state = s if s != 0 else 1

    def next(self) -> float:
        self.state = (self.state * _LCG_MUL) % _LCG_MOD
        return self.state / _LCG_MOD

    def randint(self, lo: int, hi_inclusive: int) -> int:
        return lo + int(self.next() * (hi_inclusive - lo + 1))

    def normal(self, mean: float = 0.0, std: float = 1.0) -> float:
        # Box-Muller using two uniforms; we discard the second sample
        u1 = max(self.next(), 1e-12)
        u2 = self.next()
        z  = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
        return mean + std * z


def _seed_from(panel_id: str, defect_id: int) -> int:
    h = hashlib.sha1(f"{panel_id}:{defect_id}".encode()).digest()
    return int.from_bytes(h[:4], "big")


# ---------------------------------------------------------------------------
# 8-bit grayscale PNG writer (no external deps)
# ---------------------------------------------------------------------------

def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data)) +
        tag + data +
        struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def _write_grayscale_png(path: Path, width: int, height: int,
                         pixels: list[int]) -> None:
    """
    Write an 8-bit grayscale PNG.

    pixels : flat list of length width*height containing 0-255 values.
    """
    # PNG signature
    sig = b"\x89PNG\r\n\x1a\n"

    # IHDR: width, height, bit_depth=8, color_type=0 (grayscale),
    # compression=0, filter=0, interlace=0
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)

    # IDAT: filtered scanlines (filter type 0 = None) compressed with zlib
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type
        row_start = y * width
        for x in range(width):
            v = pixels[row_start + x]
            v = 0 if v < 0 else (255 if v > 255 else v)
            raw.append(v)
    idat = zlib.compress(bytes(raw), 9)

    with open(path, "wb") as f:
        f.write(sig)
        f.write(_png_chunk(b"IHDR", ihdr))
        f.write(_png_chunk(b"IDAT", idat))
        f.write(_png_chunk(b"IEND", b""))


# ---------------------------------------------------------------------------
# Patch background
# ---------------------------------------------------------------------------

def _make_background(rng: _DRNG) -> list[int]:
    """Return a flat W*H pixel list initialised to BG_LUMINANCE + noise."""
    pixels = [0] * (IMAGE_W * IMAGE_H)
    for i in range(IMAGE_W * IMAGE_H):
        n = rng.normal(0.0, BG_NOISE_STD)
        pixels[i] = int(round(BG_LUMINANCE + n))
    return pixels


def _clip(v: float) -> int:
    return 0 if v < 0 else (255 if v > 255 else int(round(v)))


# ---------------------------------------------------------------------------
# Defect-type specific procedural drawing
# ---------------------------------------------------------------------------

def _draw_gaussian_blob(pixels: list[int], cx: float, cy: float,
                        radius: float, contrast: int) -> None:
    """Dark gaussian blob — used for particles and contamination centres."""
    sigma = max(radius / 2.5, 0.5)
    r = int(radius * 3) + 1
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            x = int(cx + dx)
            y = int(cy + dy)
            if 0 <= x < IMAGE_W and 0 <= y < IMAGE_H:
                d2 = dx * dx + dy * dy
                amp = math.exp(-d2 / (2.0 * sigma * sigma))
                v   = pixels[y * IMAGE_W + x] - contrast * amp
                pixels[y * IMAGE_W + x] = _clip(v)


def _draw_linear_streak(pixels: list[int], cx: float, cy: float,
                        length: float, angle: float, contrast: int) -> None:
    """Short oriented linear streak — used for scratches."""
    half = length / 2.0
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    steps = max(int(length * 4), 8)
    for s in range(steps):
        t = -half + (s / max(steps - 1, 1)) * length
        x_centre = cx + cos_a * t
        y_centre = cy + sin_a * t
        # 2-pixel-wide brush perpendicular to streak
        for dperp in (-1, 0, 1):
            x = int(x_centre - sin_a * dperp)
            y = int(y_centre + cos_a * dperp)
            if 0 <= x < IMAGE_W and 0 <= y < IMAGE_H:
                falloff = 1.0 - abs(dperp) * 0.3
                v = pixels[y * IMAGE_W + x] - contrast * falloff
                pixels[y * IMAGE_W + x] = _clip(v)


def _draw_ring(pixels: list[int], cx: float, cy: float,
               outer_r: float, inner_r: float, contrast: int) -> None:
    """Dark ring / annulus — used for voids."""
    r = int(outer_r) + 2
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            x = int(cx + dx)
            y = int(cy + dy)
            if 0 <= x < IMAGE_W and 0 <= y < IMAGE_H:
                d = math.sqrt(dx * dx + dy * dy)
                if inner_r <= d <= outer_r:
                    edge = 1.0 - min(
                        abs(d - inner_r), abs(d - outer_r)
                    ) / max(outer_r - inner_r, 0.5)
                    v = pixels[y * IMAGE_W + x] - contrast * (0.6 + 0.4 * edge)
                    pixels[y * IMAGE_W + x] = _clip(v)


def _draw_hard_circle(pixels: list[int], cx: float, cy: float,
                      radius: float, contrast: int) -> None:
    """Sharp-edged dark circle — used for pits."""
    r = int(radius) + 1
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            x = int(cx + dx)
            y = int(cy + dy)
            if 0 <= x < IMAGE_W and 0 <= y < IMAGE_H:
                if dx * dx + dy * dy <= radius * radius:
                    v = pixels[y * IMAGE_W + x] - contrast
                    pixels[y * IMAGE_W + x] = _clip(v)


def _draw_soft_gradient(pixels: list[int], angle: float, amplitude: int) -> None:
    """Low-frequency luminance gradient — used for mura on glass panels."""
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    for y in range(IMAGE_H):
        for x in range(IMAGE_W):
            # Project (x, y) onto the gradient direction, normalise to [0, 1]
            proj = (x * cos_a + y * sin_a)
            extent = abs(cos_a) * IMAGE_W + abs(sin_a) * IMAGE_H
            t = (proj - 0.0) / max(extent, 1.0)
            shift = (t - 0.5) * 2.0 * amplitude
            v = pixels[y * IMAGE_W + x] - shift
            pixels[y * IMAGE_W + x] = _clip(v)


def _draw_bright_point(pixels: list[int], cx: float, cy: float,
                       intensity: int) -> None:
    """Single bright point — used for pinholes on glass."""
    x, y = int(cx), int(cy)
    if 0 <= x < IMAGE_W and 0 <= y < IMAGE_H:
        v = pixels[y * IMAGE_W + x] + intensity
        pixels[y * IMAGE_W + x] = _clip(v)
        # Slight bloom into 4-neighbours
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < IMAGE_W and 0 <= ny < IMAGE_H:
                v = pixels[ny * IMAGE_W + nx] + intensity // 2
                pixels[ny * IMAGE_W + nx] = _clip(v)


# ---------------------------------------------------------------------------
# Single-defect renderer
# ---------------------------------------------------------------------------

def _render_defect(defect_type: str, size_mm: float,
                   panel_id: str, defect_id: int) -> list[int]:
    """
    Render one 64x64 grayscale patch for the given defect.

    size_mm scales the on-image feature size — large defects produce
    larger features, but every patch fits within 64x64 with margin.
    """
    rng = _DRNG(_seed_from(panel_id, defect_id))
    pixels = _make_background(rng)

    cx = IMAGE_W / 2.0 + rng.normal(0.0, 2.0)
    cy = IMAGE_H / 2.0 + rng.normal(0.0, 2.0)

    # Map physical size (mm) to image-pixel feature size with mild saturation
    # so very large defects don't overflow the patch.
    pixel_size = 4.0 + 18.0 * (1.0 - math.exp(-size_mm * 2.0))

    if defect_type == "particle":
        _draw_gaussian_blob(pixels, cx, cy,
                            radius=pixel_size * 0.6,
                            contrast=110 + rng.randint(0, 40))

    elif defect_type == "scratch":
        angle = rng.next() * 2.0 * math.pi
        _draw_linear_streak(pixels, cx, cy,
                            length=pixel_size * 2.2,
                            angle=angle,
                            contrast=90 + rng.randint(0, 50))

    elif defect_type == "void":
        outer = pixel_size * 0.8
        inner = outer * 0.55
        _draw_ring(pixels, cx, cy, outer, inner,
                   contrast=100 + rng.randint(0, 40))

    elif defect_type == "pit":
        _draw_hard_circle(pixels, cx, cy,
                          radius=max(pixel_size * 0.45, 2.0),
                          contrast=130 + rng.randint(0, 30))

    elif defect_type == "contamination":
        # Three small overlapping blobs for irregular shape
        for _ in range(3):
            ox = rng.normal(0.0, pixel_size * 0.4)
            oy = rng.normal(0.0, pixel_size * 0.4)
            _draw_gaussian_blob(pixels, cx + ox, cy + oy,
                                radius=pixel_size * 0.45,
                                contrast=70 + rng.randint(0, 50))

    elif defect_type == "mura":
        angle = rng.next() * 2.0 * math.pi
        _draw_soft_gradient(pixels, angle,
                            amplitude=30 + rng.randint(0, 20))

    elif defect_type == "pinhole":
        # Darken background slightly then add bright point
        for i in range(len(pixels)):
            pixels[i] = _clip(pixels[i] - 40)
        _draw_bright_point(pixels, cx, cy,
                           intensity=120 + rng.randint(0, 50))

    else:
        # Unknown type — render as a faint particle so the patch is never empty
        _draw_gaussian_blob(pixels, cx, cy,
                            radius=pixel_size * 0.4,
                            contrast=60)

    return pixels


# ---------------------------------------------------------------------------
# Public API — generate images for defects in the database
# ---------------------------------------------------------------------------

@dataclass
class ImageGenerationResult:
    panel_id:        str
    n_defects:       int
    n_images_written: int
    n_images_skipped: int       # already existed
    output_dir:      str


def generate_images_for_panel(
    conn: Connection,
    panel_id: str,
    *,
    output_root: str | Path = "output/defect_images",
    source_system: str = "system_a",
    overwrite: bool = False,
    persist: bool = True,
) -> ImageGenerationResult:
    """
    Generate one PNG patch per system_a defect on the given panel.

    Files are written to:
        <output_root>/<panel_id>/<defect_id>.png

    A row in defect_images is inserted for each new image when
    persist=True. Existing rows are left untouched unless overwrite=True.
    """
    ph = get_placeholder(conn)

    if conn.execute(
        f"SELECT 1 FROM panels WHERE panel_id={ph}", (panel_id,)
    ).fetchone() is None:
        raise ValueError(f"Panel not found: {panel_id!r}")

    out_dir = Path(output_root) / panel_id
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = conn.execute(
        f"""SELECT defect_id, defect_type, size
            FROM defects
            WHERE panel_id={ph} AND source_system={ph}""",
        (panel_id, source_system)
    ).fetchall()

    n_written = 0
    n_skipped = 0
    new_records: list[tuple] = []

    for row in rows:
        defect_id   = row["defect_id"]
        defect_type = row["defect_type"]
        size_mm     = float(row["size"])
        img_path    = out_dir / f"{defect_id}.png"

        if img_path.exists() and not overwrite:
            n_skipped += 1
            continue

        pixels = _render_defect(defect_type, size_mm, panel_id, defect_id)
        _write_grayscale_png(img_path, IMAGE_W, IMAGE_H, pixels)
        n_written += 1
        new_records.append((
            defect_id, panel_id, str(img_path),
            IMAGE_W, IMAGE_H, "png", GENERATOR_VERSION
        ))

    if persist and new_records:
        with conn:
            if is_postgres(conn):
                conn.executemany(
                    f"INSERT INTO defect_images "
                    f"(defect_id, panel_id, image_path, width, height, "
                    f"format, generator_version) "
                    f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph}) "
                    f"ON CONFLICT (defect_id, panel_id) DO UPDATE SET "
                    f"image_path=EXCLUDED.image_path, "
                    f"generator_version=EXCLUDED.generator_version",
                    new_records
                )
            else:
                conn.executemany(
                    f"INSERT OR REPLACE INTO defect_images "
                    f"(defect_id, panel_id, image_path, width, height, "
                    f"format, generator_version) "
                    f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph})",
                    new_records
                )

    logger.info(
        "[%s] images: %d written | %d skipped | dir=%s",
        panel_id, n_written, n_skipped, out_dir,
    )

    return ImageGenerationResult(
        panel_id=panel_id,
        n_defects=len(rows),
        n_images_written=n_written,
        n_images_skipped=n_skipped,
        output_dir=str(out_dir),
    )


def generate_images_for_all(
    conn: Connection,
    *,
    output_root: str | Path = "output/defect_images",
    substrate_type: str | None = None,
    source_system: str = "system_a",
    overwrite: bool = False,
    persist: bool = True,
) -> list[ImageGenerationResult]:
    """Generate images for every panel (optionally one substrate type)."""
    ph = get_placeholder(conn)
    if substrate_type:
        rows = conn.execute(
            f"SELECT panel_id FROM panels WHERE substrate_type={ph}",
            (substrate_type,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT panel_id FROM panels").fetchall()

    results = []
    for r in rows:
        try:
            results.append(generate_images_for_panel(
                conn, r["panel_id"],
                output_root=output_root,
                source_system=source_system,
                overwrite=overwrite,
                persist=persist,
            ))
        except Exception as exc:
            logger.error("Image generation failed for %s: %s",
                         r["panel_id"], exc)
    return results


def print_image_report(results: list[ImageGenerationResult]) -> None:
    if not results:
        print("No image generation results.")
        return
    total_written = sum(r.n_images_written for r in results)
    total_skipped = sum(r.n_images_skipped for r in results)
    print(f"\n{'='*64}")
    print(f"  IMAGE GENERATION REPORT ({len(results)} panel(s))")
    print(f"  Written: {total_written}   Skipped: {total_skipped}")
    print(f"{'='*64}")
    for r in results:
        print(
            f"  {r.panel_id:<22} written={r.n_images_written:>4} "
            f"skipped={r.n_images_skipped:>4}   {r.output_dir}"
        )
    print()
