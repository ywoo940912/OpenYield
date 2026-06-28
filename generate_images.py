#!/usr/bin/env python3
"""
generate_images.py
------------------
Author: Yeonkuk Woo

Standalone command for procedural synthetic defect image generation.

Generates 64x64 grayscale PNG image patches for every system_a defect
recorded in the OpenYield database. Run this AFTER the main pipeline
has produced defect records:

    python run_pipeline.py        # produces defect records
    python generate_images.py     # produces PNG patches

Output layout:
    output/defect_images/<panel_id>/<defect_id>.png

A row in the defect_images table records each generated image with
its on-disk path, dimensions, and generator version, enabling external
classifiers and visual review tools to retrieve patches by defect_id.

Usage
-----
    python generate_images.py                    # all panels
    python generate_images.py --substrate wafer  # filter by substrate
    python generate_images.py --overwrite        # regenerate existing
    python generate_images.py --out path/to/dir  # custom output directory
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from openyield.db.connection import get_connection
from openyield.db.schema import initialize_schema
from openyield.synthetic.image_generator import (
    generate_images_for_all,
    print_image_report,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate synthetic defect image patches."
    )
    parser.add_argument(
        "--db", default=os.getenv("DB_PATH", "./inspection.db"),
        help="SQLite database path (default: ./inspection.db)"
    )
    parser.add_argument(
        "--out", default="output/defect_images",
        help="Output root directory (default: output/defect_images)"
    )
    parser.add_argument(
        "--substrate", choices=["wafer", "glass_panel"], default=None,
        help="Restrict to one substrate type"
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="Regenerate images that already exist on disk"
    )
    parser.add_argument(
        "--no-persist", action="store_true",
        help="Do not write defect_images table rows"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Verbose logging"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    conn = get_connection(path=args.db)
    initialize_schema(conn)

    results = generate_images_for_all(
        conn,
        output_root=args.out,
        substrate_type=args.substrate,
        overwrite=args.overwrite,
        persist=not args.no_persist,
    )
    print_image_report(results)

    conn.close()


if __name__ == "__main__":
    main()
