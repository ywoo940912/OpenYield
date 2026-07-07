"""
scripts/export_dataset.py
--------------------------
Author: Yeonkuk Woo

Exports the OpenYield synthetic defect image dataset to a structured
directory ready for upload to Hugging Face Datasets or any ML training
pipeline.

WHY THIS EXISTS
---------------
OpenYield generates synthetic semiconductor defect inspection data —
both structured records (defect type, size, location, panel geometry)
and 64×64 grayscale image patches that visually represent each defect.

This dataset fills a critical gap: U.S. domestic semiconductor manufacturers
cannot share real inspection imagery due to trade secret and export control
concerns. OpenYield's procedurally generated dataset provides a fully open,
reproducible substitute that AI/ML researchers and inspection toolmakers can
use freely to train and benchmark defect classifiers.

OUTPUT STRUCTURE
----------------
    dataset/
    ├── images/
    │   ├── particle/       ← one subfolder per defect type
    │   │   ├── 00000001.png
    │   │   └── ...
    │   ├── scratch/
    │   ├── void/
    │   ├── pit/
    │   ├── contamination/
    │   ├── mura/           ← glass panel only
    │   └── pinhole/        ← glass panel only
    ├── metadata.csv        ← full record for every image
    └── split/
        ├── train.csv       ← 80% stratified by defect type
        ├── val.csv         ← 10%
        └── test.csv        ← 10%

metadata.csv columns
--------------------
    defect_id, panel_id, substrate_type, lot_id, defect_type,
    size_mm, confidence_score, component_row, component_col,
    image_path, generator_version

REPRODUCIBILITY
---------------
Every image is generated from a deterministic seed derived from
(panel_id, defect_id). Running this script twice on the same database
produces byte-identical output.

USAGE
-----
    python scripts/export_dataset.py
    python scripts/export_dataset.py --db path/to/other.db --out my_dataset/
    python scripts/export_dataset.py --limit 500   # quick sample
"""

import argparse
import csv
import math
import os
import random
import sys
from pathlib import Path

GENERATOR_VERSION = "v1"
DEFAULT_DB   = "inspection.db"
DEFAULT_OUT  = "dataset"
TRAIN_FRAC   = 0.80
VAL_FRAC     = 0.10
# test = 1 - TRAIN_FRAC - VAL_FRAC = 0.10


def parse_args():
    p = argparse.ArgumentParser(description="Export OpenYield defect image dataset")
    p.add_argument("--db",    default=DEFAULT_DB,  help="SQLite database path")
    p.add_argument("--out",   default=DEFAULT_OUT, help="Output directory")
    p.add_argument("--limit", type=int, default=None,
                   help="Max defects per panel (for quick samples)")
    p.add_argument("--seed",  type=int, default=42,
                   help="Shuffle seed for train/val/test split")
    return p.parse_args()


def connect(db_path: str):
    import sqlite3
    if not Path(db_path).exists():
        print(f"[ERROR] Database not found: {db_path}")
        print("  Seed the database first: python seed_demo.py")
        sys.exit(1)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_defects(conn, limit_per_panel=None):
    """Return all system_a defects joined with panel metadata."""
    sql = """
        SELECT
            d.defect_id,
            d.panel_id,
            p.substrate_type,
            p.lot_id,
            d.defect_type,
            d.size      AS size_mm,
            d.confidence_score,
            d.component_row,
            d.component_col
        FROM defects d
        JOIN panels p ON p.panel_id = d.panel_id
        WHERE d.source_system = 'system_a'
        ORDER BY d.panel_id, d.defect_id
    """
    rows = conn.execute(sql).fetchall()

    if limit_per_panel:
        from itertools import groupby
        limited = []
        for _, group in groupby(rows, key=lambda r: r["panel_id"]):
            limited.extend(list(group)[:limit_per_panel])
        return limited

    return rows


def render_and_save(defect_type: str, size_mm: float,
                    panel_id: str, defect_id: int,
                    out_path: Path) -> None:
    """Generate a 64×64 grayscale PNG patch and write it to out_path."""
    from openyield.synthetic.image_generator import _render_defect, _write_grayscale_png, IMAGE_W, IMAGE_H
    pixels = _render_defect(defect_type, size_mm, panel_id, defect_id)
    _write_grayscale_png(out_path, IMAGE_W, IMAGE_H, pixels)


def stratified_split(rows, train_frac, val_frac, seed):
    """
    Split rows into train/val/test maintaining defect_type distribution.
    Returns (train, val, test) lists of row dicts.
    """
    from collections import defaultdict
    rng = random.Random(seed)

    by_type = defaultdict(list)
    for r in rows:
        by_type[r["defect_type"]].append(r)

    train, val, test = [], [], []
    for defect_type, group in by_type.items():
        rng.shuffle(group)
        n = len(group)
        n_train = math.floor(n * train_frac)
        n_val   = math.floor(n * val_frac)
        train.extend(group[:n_train])
        val.extend(group[n_train:n_train + n_val])
        test.extend(group[n_train + n_val:])

    return train, val, test


META_FIELDS = [
    "defect_id", "panel_id", "substrate_type", "lot_id",
    "defect_type", "size_mm", "confidence_score",
    "component_row", "component_col",
    "image_path", "generator_version", "split",
]


def write_csv(path: Path, records: list[dict], split_name: str):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=META_FIELDS)
        w.writeheader()
        for r in records:
            r["split"] = split_name
            w.writerow(r)


def main():
    args = parse_args()

    try:
        import openyield  # noqa: F401
    except ImportError:
        print("[ERROR] OpenYield not importable. Set PYTHONPATH or install with pip install -e .")
        sys.exit(1)

    print("=" * 64)
    print("  OpenYield — Synthetic Defect Dataset Export")
    print("=" * 64)
    print(f"  Database : {args.db}")
    print(f"  Output   : {args.out}/")
    print()

    conn = connect(args.db)
    rows = fetch_defects(conn, limit_per_panel=args.limit)

    if not rows:
        print("[ERROR] No defects found. Run seed_demo.py first.")
        sys.exit(1)

    print(f"  Found {len(rows):,} defect records")

    out_root   = Path(args.out)
    images_dir = out_root / "images"
    split_dir  = out_root / "split"
    split_dir.mkdir(parents=True, exist_ok=True)

    # ── Generate images ────────────────────────────────────────────────────────
    all_meta: list[dict] = []
    skipped = 0
    written = 0
    types_seen: set[str] = set()

    for i, row in enumerate(rows):
        defect_id   = row["defect_id"]
        defect_type = row["defect_type"]
        size_mm     = float(row["size_mm"])
        panel_id    = row["panel_id"]

        type_dir  = images_dir / defect_type
        type_dir.mkdir(parents=True, exist_ok=True)
        img_path  = type_dir / f"{defect_id:08d}.png"

        if not img_path.exists():
            render_and_save(defect_type, size_mm, panel_id, defect_id, img_path)
            written += 1
        else:
            skipped += 1

        types_seen.add(defect_type)

        all_meta.append({
            "defect_id":        defect_id,
            "panel_id":         panel_id,
            "substrate_type":   row["substrate_type"],
            "lot_id":           row["lot_id"] or "",
            "defect_type":      defect_type,
            "size_mm":          f"{size_mm:.6f}",
            "confidence_score": f"{float(row['confidence_score']):.4f}",
            "component_row":    row["component_row"],
            "component_col":    row["component_col"],
            "image_path":       str(img_path.relative_to(out_root)),
            "generator_version": GENERATOR_VERSION,
            "split":            "",
        })

        if (i + 1) % 500 == 0 or (i + 1) == len(rows):
            print(f"  Progress: {i+1:,}/{len(rows):,}  written={written}  skipped={skipped}", end="\r")

    print()

    # ── Train/val/test split ───────────────────────────────────────────────────
    train_rows, val_rows, test_rows = stratified_split(
        all_meta, TRAIN_FRAC, VAL_FRAC, args.seed
    )

    write_csv(split_dir / "train.csv", train_rows, "train")
    write_csv(split_dir / "val.csv",   val_rows,   "val")
    write_csv(split_dir / "test.csv",  test_rows,  "test")

    # ── Full metadata CSV ──────────────────────────────────────────────────────
    all_meta_with_split = train_rows + val_rows + test_rows
    all_meta_with_split.sort(key=lambda r: int(r["defect_id"]))
    write_csv(out_root / "metadata.csv", all_meta_with_split, "")

    # ── Summary ────────────────────────────────────────────────────────────────
    print()
    print("=" * 64)
    print("  EXPORT COMPLETE")
    print("=" * 64)
    print(f"  Total images : {len(all_meta):,}")
    print(f"  Written      : {written:,}  (new)")
    print(f"  Skipped      : {skipped:,}  (already existed)")
    print()
    print("  Defect type breakdown:")
    for t in sorted(types_seen):
        count = sum(1 for r in all_meta if r["defect_type"] == t)
        bar = "█" * min(30, round(count / max(len(all_meta), 1) * 100))
        print(f"    {t:<16} {count:>6,}  {bar}")
    print()
    print("  Train / Val / Test split:")
    print(f"    train : {len(train_rows):,}  ({len(train_rows)/len(all_meta)*100:.1f}%)")
    print(f"    val   : {len(val_rows):,}  ({len(val_rows)/len(all_meta)*100:.1f}%)")
    print(f"    test  : {len(test_rows):,}  ({len(test_rows)/len(all_meta)*100:.1f}%)")
    print()
    print("  Output files:")
    print(f"    {out_root}/metadata.csv")
    print(f"    {out_root}/split/train.csv  val.csv  test.csv")
    print(f"    {out_root}/images/<defect_type>/<defect_id>.png")
    print()
    print("  Next step: upload to Hugging Face")
    print("    huggingface-cli login")
    print("    python -c \"from datasets import Dataset; ...")
    print("=" * 64)


if __name__ == "__main__":
    main()
