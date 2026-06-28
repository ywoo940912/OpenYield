#!/usr/bin/env python3
"""
run_pipeline.py
---------------
Author: Yeonkuk Woo

End-to-end pipeline for OpenYield — Semiconductor Inspection Data Platform.

Supports two substrate classes directly relevant to U.S. CHIPS Act manufacturing:
  - glass_panel  (AOI + confocal review) — OLED/LCD for defense and aerospace
  - wafer        (optical scanner + e-beam review, with edge exclusion)

Usage:
  pip install numpy
  python run_pipeline.py
  python run_pipeline.py --substrate wafer --rows 10 --cols 10 --panels 3
  python run_pipeline.py --substrate glass_panel --rows 6 --cols 6 --seed 99
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from openyield.db.schema import initialize_schema
from openyield.db.connection import get_connection
from openyield.ingestion.ingest import upsert_panel, upsert_component, ingest_csv
from openyield.synthetic.generator import generate_panel, write_defects_csv, write_components_csv
from openyield.synthetic.substrate_profiles import SubstrateType
from openyield.validation.checks import run_all_checks, print_validation_report
from openyield.yield_engine.calculator import calculate_all_yields, print_yield_report
from openyield.analysis.clustering import cluster_all_panels, print_cluster_report
from openyield.analysis.lot_tracker import summarise_all_lots, print_lot_report, auto_create_lot
from openyield.analysis.pareto import (
    calculate_pareto, print_pareto_report,
    calculate_zone_pareto, print_zone_pareto_report,
    calculate_system_comparison, print_system_comparison_report,
    calculate_lot_trend, print_lot_trend_report,
)
from openyield.analysis.spc import calculate_spc, print_spc_report
from openyield.analysis.spc import _capability
from openyield.analysis.correlation import calculate_correlation, print_correlation_report
from openyield.analysis.signatures import match_all_panels, print_signature_report
from openyield.ai.classifier import train_classifier, evaluate_classifier, print_classifier_report
from openyield.ingestion.ingest import upsert_lot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_pipeline")

SUBSTRATE_DEFAULTS = {
    "glass_panel": {"rows": 6,  "cols": 6},
    "wafer":       {"rows": 10, "cols": 10},
}


def parse_args():
    p = argparse.ArgumentParser(
        description="OpenYield — Semiconductor Inspection Data Platform"
    )
    p.add_argument("--substrate", default="all",
                   choices=["all", "glass_panel", "wafer"],
                   help="Substrate type to generate (default: all)")
    p.add_argument("--rows",   type=int, default=None)
    p.add_argument("--cols",   type=int, default=None)
    p.add_argument("--panels", type=int, default=2,
                   help="Panels per substrate type (default: 2)")
    p.add_argument("--db",     default="./inspection.db")
    p.add_argument("--outdir", default="./output")
    p.add_argument("--seed",   type=int, default=42)
    return p.parse_args()


def run_substrate(conn, substrate_type, rows, cols, n_panels, out_dir, base_seed):
    csv_files = []
    for i in range(n_panels):
        panel = generate_panel(
            rows=rows, cols=cols,
            substrate_type=substrate_type,
            seed=base_seed + i,
        )
        # Auto-assign to a lot
        lot_id = auto_create_lot(
            conn, panel.panel_id, substrate_type, panel.product_type
        )
        defect_csv = out_dir / f"{panel.panel_id}_defects.csv"
        write_defects_csv(panel, defect_csv)
        write_components_csv(panel, out_dir / f"{panel.panel_id}_components.csv")
        csv_files.append(defect_csv)

        with conn:
            upsert_panel(conn,
                panel_id=panel.panel_id,
                product_type=panel.product_type,
                substrate_type=panel.substrate_type,
                rows=panel.rows,
                cols=panel.cols,
                lot_id=lot_id,
            )
            for c in panel.components:
                upsert_component(conn,
                    panel_id=c.panel_id,
                    component_row=c.component_row,
                    component_col=c.component_col,
                    region_id=c.region_id,
                    center_x=c.center_x,
                    center_y=c.center_y,
                    active=c.active,
                )

    total = sum(ingest_csv(conn, f, skip_if_processed=True) for f in csv_files)
    logger.info("[%s] Ingested %d total defect records", substrate_type, total)

    # Idempotency check — re-running must return 0 new records
    re = sum(ingest_csv(conn, f, skip_if_processed=True) for f in csv_files)
    assert re == 0, "Idempotency violation!"
    logger.info("[%s] Idempotency confirmed.", substrate_type)


def main():
    args   = parse_args()
    db     = Path(args.db)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    conn = get_connection(path=db)
    initialize_schema(conn)

    substrates = (
        ["glass_panel", "wafer"]
        if args.substrate == "all"
        else [args.substrate]
    )

    for idx, st in enumerate(substrates):
        defaults = SUBSTRATE_DEFAULTS[st]
        rows = args.rows or defaults["rows"]
        cols = args.cols or defaults["cols"]
        logger.info(
            "=== Substrate: %s (%dx%d, %d panels) ===",
            st, rows, cols, args.panels
        )
        run_substrate(
            conn, st, rows, cols, args.panels, outdir, args.seed + idx * 100
        )

    logger.info("=== Running validation suite ===")
    results = run_all_checks(conn)
    print_validation_report(results)

    logger.info("=== Running yield engine ===")
    estimates = calculate_all_yields(conn, persist=True)
    print_yield_report(estimates)

    logger.info("=== Running clustering analysis ===")
    cluster_results = cluster_all_panels(conn, persist=True)
    print_cluster_report(cluster_results)

    logger.info("=== Running lot tracking ===")
    lot_summaries = summarise_all_lots(conn, persist=True)
    print_lot_report(lot_summaries)

    logger.info("=== Running defect Pareto analysis ===")
    for st in substrates:
        pareto = calculate_pareto(conn, substrate_type=st)
        print_pareto_report(pareto)
        zone_pareto = calculate_zone_pareto(conn, substrate_type=st)
        print_zone_pareto_report(zone_pareto)
        comparison = calculate_system_comparison(conn, substrate_type=st)
        print_system_comparison_report(comparison)
    lot_trend = calculate_lot_trend(conn)
    print_lot_trend_report(lot_trend)

    logger.info("=== Running SPC control charts ===")
    for st in substrates:
        spc = calculate_spc(
            conn, substrate_type=st,
            lambda_ewma=0.2, L_ewma=3.0,
            cusum_k=0.5, cusum_h=5.0,
            persist=True,
        )
        print_spc_report(spc)

    logger.info("=== Running wafer-to-wafer correlation ===")
    for st in substrates:
        corr = calculate_correlation(conn, substrate_type=st)
        print_correlation_report(corr)

    logger.info("=== Running defect signature matching ===")
    sig_results = match_all_panels(conn)
    print_signature_report(sig_results)

    logger.info("=== Training AI defect classifier (Phase 1) ===")
    try:
        train_result = train_classifier(
            conn, max_iterations=300, learning_rate=0.1, persist=True
        )
        logger.info(
            "Trained model %s | accuracy=%.3f",
            train_result.model_version, train_result.accuracy
        )
        eval_result = evaluate_classifier(conn)
        print_classifier_report(eval_result)
    except ValueError as exc:
        logger.warning("Classifier training skipped: %s", exc)

    conn.close()
    logger.info("Done. DB: %s", db.resolve())


if __name__ == "__main__":
    main()
