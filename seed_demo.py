#!/usr/bin/env python3
"""
seed_demo.py — Populate OpenYield with realistic synthetic data for full demo testing.

Usage
-----
1. Start the API server in one terminal:
       uvicorn run:app --reload --port 8000

2. Run this script in another terminal:
       python seed_demo.py

3. Open the frontend:
       cd frontend && npm run dev

To reset and re-seed from scratch:
       rm inspection.db && python seed_demo.py

Scenario
--------
Glass panels  — 4 lots, 17 panels total
  LOT 1  TFT-LCD-G8    Early production   mean_defect=4.5  (rough start)
  LOT 2  OLED-G8.5     Ramp phase         mean_defect=3.5  (improving)
  LOT 3  TFT-LCD-G10   EXCURSION          mean_defect=9.0  (lamination event)
  LOT 4  AMOLED-G6     Recovery/mature    mean_defect=2.0  (process stabilized)

Wafers — 4 lots, 20 panels total
  LOT 5  LOGIC-7NM     Advanced node      mean_defect=2.0
  LOT 6  DRAM-1ALPHA   Memory             mean_defect=1.8
  LOT 7  FLASH-3D-128L 3D NAND            mean_defect=1.5
  LOT 8  ANALOG-180NM  Mature node        mean_defect=1.2  (cleanest process)
"""

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://localhost:8000"


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _post(path: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req  = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"  HTTP {e.code} from {path}: {body[:200]}")
        raise


def _check_server() -> bool:
    try:
        with urllib.request.urlopen(f"{BASE}/health", timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


# ── Core generate call ────────────────────────────────────────────────────────

def generate(
    substrate_type:    str,
    product_type:      str,
    n_panels:          int,
    mean_defect_count: float,
    seed:              int,
) -> dict:
    return _post("/generate", {
        "substrate_type":    substrate_type,
        "product_type":      product_type,
        "n_panels":          n_panels,
        "mean_defect_count": mean_defect_count,
        "run_yield":         True,
        "run_clustering":    True,
        "seed":              seed,
    })


# ── Pretty print helpers ──────────────────────────────────────────────────────

def _bar(fraction: float, width: int = 20) -> str:
    filled = round(fraction * width)
    return "█" * filled + "░" * (width - filled)


def _print_result(label: str, result: dict) -> None:
    panels   = result["panels"]
    lot_id   = panels[0]["lot_id"] if panels else "—"
    defects  = result["total_defects"]
    n        = result["n_panels"]
    yields   = [p["yield_negbinom"] for p in panels if p["yield_negbinom"] is not None]
    avg_y    = sum(yields) / len(yields) if yields else 0.0
    clusters = [p["clustering_class"] for p in panels if p["clustering_class"]]

    excursion_count = sum(1 for p in panels if p["clustering_class"] == "excursion")

    print(f"  {label}")
    print(f"    Lot: {lot_id}  |  Panels: {n}  |  Defects: {defects}")
    print(f"    Avg yield: {_bar(avg_y)} {avg_y*100:.1f}%")
    if excursion_count:
        print(f"    ⚠ {excursion_count} excursion panel(s) detected by clustering")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print()
    print("  OpenYield Demo Seed")
    print("  " + "─" * 46)
    print()

    if not _check_server():
        print("  ERROR: API server not responding at http://localhost:8000")
        print("  Start it first:")
        print("      uvicorn run:app --reload --port 8000")
        print()
        sys.exit(1)

    print("  Server OK\n")

    total_panels  = 0
    total_defects = 0

    # ── Glass panels ─────────────────────────────────────────────────────────
    print("  Glass Panel lots  (Gen-8 / Gen-10 TFT + OLED)")
    print("  " + "─" * 46)

    glass_lots = [
        # product_type       n   λ/die  label                              seed
        ("TFT-LCD-G8",       4,  4.5,  "LOT 1 — Early production",          42),
        ("OLED-G8.5",        4,  3.5,  "LOT 2 — Ramp phase",                43),
        ("TFT-LCD-G10",      4,  9.0,  "LOT 3 — EXCURSION (lamination PM)", 44),
        ("AMOLED-G6",        5,  2.0,  "LOT 4 — Recovery / mature",         45),
    ]

    for product_type, n, lam, label, seed in glass_lots:
        result = generate("glass_panel", product_type, n, lam, seed)
        total_panels  += result["n_panels"]
        total_defects += result["total_defects"]
        _print_result(label, result)
        time.sleep(0.05)   # preserve created_at ordering for trend charts

    # ── Wafers ───────────────────────────────────────────────────────────────
    print("  Wafer lots  (300mm — logic, memory, flash, analog)")
    print("  " + "─" * 46)

    wafer_lots = [
        # product_type       n   λ/die  label               seed
        ("LOGIC-7NM",        5,  2.0,  "LOT 5 — Logic 7nm",    10),
        ("DRAM-1ALPHA",      5,  1.8,  "LOT 6 — DRAM 1-alpha", 11),
        ("FLASH-3D-128L",    5,  1.5,  "LOT 7 — 3D NAND",      12),
        ("ANALOG-180NM",     5,  1.2,  "LOT 8 — Analog 180nm", 13),
    ]

    for product_type, n, lam, label, seed in wafer_lots:
        result = generate("wafer", product_type, n, lam, seed)
        total_panels  += result["n_panels"]
        total_defects += result["total_defects"]
        _print_result(label, result)
        time.sleep(0.05)

    # ── Summary ───────────────────────────────────────────────────────────────
    print("  " + "─" * 46)
    print(f"  Done.  {total_panels} panels  |  {total_defects:,} defects ingested")
    print()
    print("  Pages to test:")
    print("    Dashboard     →  http://localhost:5173/dashboard")
    print("    Yield Map     →  http://localhost:5173/yield-map")
    print("    Analytics     →  http://localhost:5173/analytics")
    print("      Pareto tab  →  all panels, glass_panel filter")
    print("      SPC tab     →  all panels (excursion in LOT 3 should alarm)")
    print("      Lot Trend   →  glass_panel filter shows excursion + recovery")
    print("      Scatter tab →  pick any GP_* panel for defect spatial map")
    print("    Genealogy     →  http://localhost:5173/genealogy")
    print("    Classifier    →  http://localhost:5173/classifier")
    print("    Simulator     →  http://localhost:5173/simulator")
    print()


if __name__ == "__main__":
    main()
