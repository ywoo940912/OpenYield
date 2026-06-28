"""
demo.py — OpenYield end-to-end demonstration
=============================================
    python demo.py

Bootstrap note
--------------
The four core algorithm modules (klarf2_adapter, genealogy, openmes_connector,
cnn_classifier) are loaded directly from their source files.  Package-level
__init__.py files are stubbed in sys.modules so Python never tries to open
them — this is required when running inside macOS TCC-sandboxed shells.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Namespace stubs — MUST come before any openyield import
# ---------------------------------------------------------------------------
import sys
import types
import sqlite3 as _sqlite3

_ROOT = "/Users/yeon/Documents/OpenYield"

for _pkg, _rel in [
    ("openyield",                    "openyield"),
    ("openyield.db",                 "openyield/db"),
    ("openyield.ai",                 "openyield/ai"),
    ("openyield.ingestion",          "openyield/ingestion"),
    ("openyield.ingestion.adapters", "openyield/ingestion/adapters"),
    ("openyield.analysis",           "openyield/analysis"),
    ("openyield.integrations",       "openyield/integrations"),
    ("openyield.yield_engine",       "openyield/yield_engine"),
]:
    _m = types.ModuleType(_pkg)
    _m.__path__ = [f"{_ROOT}/{_rel}"]   # type: ignore[attr-defined]
    _m.__package__ = _pkg
    sys.modules[_pkg] = _m

# Shared in-memory DB — row_factory enables dict-style access in CNN loader
_CONN = _sqlite3.connect(":memory:")
_CONN.row_factory = _sqlite3.Row

# Stub openyield.db.connection
_db_conn_mod = types.ModuleType("openyield.db.connection")
_db_conn_mod.get_placeholder = lambda conn: "?"          # type: ignore[attr-defined]
_db_conn_mod.get_connection  = lambda path=None: _CONN   # type: ignore[attr-defined]
sys.modules["openyield.db.connection"] = _db_conn_mod

# Stub openyield.db.schema (we initialise tables manually below)
_db_schema_mod = types.ModuleType("openyield.db.schema")
_db_schema_mod.initialize_schema = lambda conn: None     # type: ignore[attr-defined]
sys.modules["openyield.db.schema"] = _db_schema_mod

# ---------------------------------------------------------------------------
# Preload the 4 algorithm modules we wrote this session.
# We use spec_from_file_location so Python opens files directly rather than
# scanning their parent directories (directory scan hits EPERM on macOS).
# ---------------------------------------------------------------------------
import importlib.util as _ilu


def _preload(pkg: str, rel: str) -> None:
    spec = _ilu.spec_from_file_location(pkg, f"{_ROOT}/{rel}")
    mod  = _ilu.module_from_spec(spec)
    mod.__package__ = pkg.rsplit(".", 1)[0]
    sys.modules[pkg] = mod
    spec.loader.exec_module(mod)   # type: ignore[union-attr]


_preload("openyield.analysis.genealogy",
         "openyield/analysis/genealogy.py")
_preload("openyield.ingestion.adapters.klarf2_adapter",
         "openyield/ingestion/adapters/klarf2_adapter.py")
_preload("openyield.integrations.openmes_connector",
         "openyield/integrations/openmes_connector.py")
_preload("openyield.ai.cnn_classifier",
         "openyield/ai/cnn_classifier.py")

# ---------------------------------------------------------------------------
# Remaining stdlib imports (after stubs are registered)
# ---------------------------------------------------------------------------
import io
import math
import time
import traceback

# ===========================================================================
# Terminal colour helpers
# ===========================================================================

_USE_COLOR = sys.stdout.isatty()


def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


def green(t: str)  -> str: return _c(t, "32")
def yellow(t: str) -> str: return _c(t, "33")
def cyan(t: str)   -> str: return _c(t, "36")
def bold(t: str)   -> str: return _c(t, "1")
def red(t: str)    -> str: return _c(t, "31")
def dim(t: str)    -> str: return _c(t, "2")


def _header(n: int, total: int, title: str) -> None:
    print(f"\n{bold(f'[{n}/{total}]')} {cyan(title)}")

def _ok(msg: str)   -> None: print(f"       {green('✓')}  {msg}")
def _info(msg: str) -> None: print(f"       {dim('·')}  {msg}")
def _warn(msg: str) -> None: print(f"       {yellow('!')}  {msg}")
def _fail(msg: str) -> None: print(f"       {red('✗')}  {msg}")


# ===========================================================================
# Section runner
# ===========================================================================

_failures: list[str] = []
_TOTAL = 7


def _run(n: int, title: str, fn) -> None:
    _header(n, _TOTAL, title)
    try:
        fn()
    except Exception as exc:
        _fail(f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
        _failures.append(title)


# ===========================================================================
# 1 — Database initialisation
# ===========================================================================

def _section_db() -> None:
    # Create all tables the algorithm modules expect
    _CONN.executescript("""
        CREATE TABLE IF NOT EXISTS panels (
            panel_id           TEXT PRIMARY KEY,
            substrate_type     TEXT NOT NULL,
            rows               INTEGER NOT NULL DEFAULT 1,
            cols               INTEGER NOT NULL DEFAULT 1,
            lot_id             TEXT,
            component_pitch_mm REAL DEFAULT 0.0,
            product_type       TEXT
        );

        CREATE TABLE IF NOT EXISTS components (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            panel_id      TEXT REFERENCES panels(panel_id),
            component_row INTEGER,
            component_col INTEGER,
            x_mm          REAL,
            y_mm          REAL,
            active        INTEGER DEFAULT 1,
            UNIQUE (panel_id, component_row, component_col)
        );

        CREATE TABLE IF NOT EXISTS defects (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            panel_id      TEXT REFERENCES panels(panel_id),
            component_row INTEGER,
            component_col INTEGER,
            source_system TEXT,
            defect_type   TEXT,
            x_mm          REAL,
            y_mm          REAL,
            size_mm       REAL,
            confidence    REAL
        );

        CREATE TABLE IF NOT EXISTS defect_images (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            image_data  BLOB,
            defect_type TEXT
        );

        CREATE TABLE IF NOT EXISTS model_registry (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            model_type  TEXT NOT NULL,
            trained_at  TEXT NOT NULL,
            model_blob  BLOB NOT NULL,
            notes       TEXT
        );
    """)
    _CONN.commit()

    # Genealogy tables are created by the real module
    from openyield.analysis.genealogy import initialize_genealogy_schema
    initialize_genealogy_schema(_CONN)

    _ok("SQLite :memory: database initialised")
    _ok("Schema: panels, components, defects, defect_images, model_registry")
    _ok("Schema: lot_nodes, lot_edges  (via genealogy module)")


# ===========================================================================
# 2 — Panel + defect ingestion
# ===========================================================================

_WAFER_PITCH = 28.0          # mm — realistic 300 mm wafer die pitch
_GLASS_PITCH = 370.0         # mm — Gen-8 glass substrate pitch

# (panel_id, substrate, lot_id, n_rows, n_cols, pitch,
#  defect_grid: {(row, col): n_defects})
_PANELS = [
    ("DEMO_W001", "wafer", "LOT_WAFER_01", 5, 5, _WAFER_PITCH,
     {(r, c): (8 if (r, c) == (0, 0) else 0)      # hot-spot in corner
      for r in range(5) for c in range(5)}),

    ("DEMO_W002", "wafer", "LOT_WAFER_01", 5, 5, _WAFER_PITCH,
     {(r, c): (5 if (r, c) == (2, 2) else 1)      # hot-spot in centre
      for r in range(5) for c in range(5)}),

    ("DEMO_G001", "glass_panel", "LOT_FPD_01", 3, 4, _GLASS_PITCH,
     {(r, c): (6 if (r, c) == (0, 3) else 2)      # hot-spot top-right
      for r in range(3) for c in range(4)}),
]


def _section_ingest() -> None:
    total_defects = 0
    DEFECT_TYPES = ["particle", "scratch", "pit", "void", "bridge"]

    for panel_id, sub, lot_id, rows, cols, pitch, grid in _PANELS:
        _CONN.execute(
            "INSERT OR IGNORE INTO panels "
            "(panel_id, substrate_type, rows, cols, lot_id, component_pitch_mm, product_type) "
            "VALUES (?,?,?,?,?,?,?)",
            (panel_id, sub, rows, cols, lot_id, pitch, "DEMO_CHIP"),
        )
        for (r, c), n in grid.items():
            x0, y0 = float(c * pitch), float(r * pitch)
            _CONN.execute(
                "INSERT OR IGNORE INTO components "
                "(panel_id, component_row, component_col, x_mm, y_mm, active) "
                "VALUES (?,?,?,?,?,1)",
                (panel_id, r, c, x0, y0),
            )
            for i in range(n):
                _CONN.execute(
                    "INSERT INTO defects "
                    "(panel_id, component_row, component_col, source_system, "
                    " defect_type, x_mm, y_mm, size_mm, confidence) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (panel_id, r, c, "KLA_SURFSCAN_SP3",
                     DEFECT_TYPES[i % len(DEFECT_TYPES)],
                     x0 + i * 0.3, y0 + i * 0.2, 0.05, 0.90),
                )
                total_defects += 1
    _CONN.commit()

    n_wafers = sum(1 for _, s, *_ in _PANELS if s == "wafer")
    n_glass  = sum(1 for _, s, *_ in _PANELS if s == "glass_panel")
    _ok(f"Inserted {len(_PANELS)} panels  ({n_wafers} wafer, {n_glass} glass)")
    _ok(f"Total defects: {total_defects}")
    _info(f"  Hotspot panel DEMO_W001: die (0,0) has 8 defects, all others 0")
    _info(f"  Mixed panel  DEMO_W002: die (2,2) has 5 defects, all others 1")


# ===========================================================================
# 3 — KLARF 2.0 binary parse & ingest
# ===========================================================================

def _section_klarf() -> None:
    from openyield.ingestion.adapters.klarf2_adapter import (
        encode_klarf2, parse_klarf2, ingest_klarf2_bytes,
        Klarf2LotInfo, Klarf2SetupInfo, Klarf2FileInfo,
        Klarf2Wafer, Klarf2Defect, Klarf2Summary,
    )

    # Build synthetic KLARF 2.0 binary for a 3-wafer lot
    wafers = []
    for slot in range(1, 4):
        defects = [
            Klarf2Defect(
                defect_id=i + 1,
                x_mm=float(i * 5), y_mm=float(i * 3),
                x_size_mm=0.004, y_size_mm=0.003,
                class_number=i % 6, rough_bin=1, fine_bin=2,
                test_number=0, cluster_number=0,
                confidence=0.85 + i * 0.01,
            )
            for i in range(4)
        ]
        wafers.append(Klarf2Wafer(
            wafer_id=f"KLF_W{slot:02d}", slot_number=slot,
            wafer_type=0, orientation=0,
            num_defects=len(defects), defects=defects,
        ))

    raw = encode_klarf2(
        lot_info=Klarf2LotInfo(
            lot_id="LOT_KLARF_01", step_id="LI05",
            device_id="CHIP_X", process_step="LITHO",
        ),
        setup_info=Klarf2SetupInfo(
            recipe_id="RECIPE_DF", inspection_mode=1,
            pixel_size_um=0.13, die_width_mm=15.0, die_height_mm=20.0,
            num_defect_classes=7,
        ),
        file_info=Klarf2FileInfo(
            station_id="KLA_SURFSCAN_SP3",
            file_timestamp=1_700_000_000,
            inspector_version="7.4.1",
        ),
        summary=Klarf2Summary(
            total_wafers=3, total_defects=12,
            mean_defects_per_wafer=4.0,
        ),
        wafers=wafers,
    )
    _ok(f"Encoded KLARF 2.0 binary: {len(raw):,} bytes  "
        f"(magic=KLARF200, little-endian TLV)")

    # Round-trip parse
    parsed = parse_klarf2(raw)
    total_d = sum(len(w.defects) for w in parsed.wafers)
    assert parsed.lot_info.lot_id == "LOT_KLARF_01"
    assert len(parsed.wafers) == 3 and total_d == 12
    _ok(f"Round-trip parse: lot={parsed.lot_info.lot_id}  "
        f"wafers={len(parsed.wafers)}  defects={total_d}")
    _info(f"  Tool: {parsed.file_info.station_id}  "
          f"recipe: {parsed.setup_info.recipe_id}")

    # Ingest into shared DB
    result = ingest_klarf2_bytes(_CONN, raw)
    _ok(f"Ingested into DB: {result['wafers_ingested']} wafers, "
        f"{result['defects_inserted']} defects")


# ===========================================================================
# 4 — Yield models  (inlined — spatial_predictor.py from prior session)
# ===========================================================================

def _poisson_yield(d0: float, area_cm2: float) -> float:
    return math.exp(-d0 * area_cm2)

def _murphy_yield(d0: float, area_cm2: float) -> float:
    x = d0 * area_cm2
    return 0.0 if x == 0 else ((1.0 - math.exp(-x)) / x) ** 2

def _negbinom_yield(d0: float, area_cm2: float, alpha: float = 2.0) -> float:
    return (1.0 + d0 * area_cm2 / alpha) ** (-alpha)


def _section_yield() -> None:
    panel_id   = "DEMO_W001"
    pitch      = _WAFER_PITCH
    die_area_mm2  = pitch * pitch          # 784 mm²
    die_area_cm2  = die_area_mm2 / 100.0  # 7.84 cm²

    n_dies = _CONN.execute(
        "SELECT COUNT(*) FROM components WHERE panel_id=? AND active=1",
        (panel_id,),
    ).fetchone()[0]
    n_defects = _CONN.execute(
        "SELECT COUNT(*) FROM defects WHERE panel_id=?",
        (panel_id,),
    ).fetchone()[0]

    total_area_cm2 = n_dies * die_area_cm2
    d0 = n_defects / total_area_cm2 if total_area_cm2 > 0 else 0.0

    yp = _poisson_yield(d0, die_area_cm2)
    ym = _murphy_yield(d0, die_area_cm2)
    yn = _negbinom_yield(d0, die_area_cm2)

    _ok(f"Panel: {panel_id}  |  dies={n_dies}  defects={n_defects}")
    _info(f"  D₀ = {d0:.4f} defects/cm²   die area = {die_area_cm2:.2f} cm²")
    _info(f"  Poisson        Y = exp(-D₀·A)              → {yp:.4f}  ({yp*100:.1f}%)")
    _info(f"  Murphy         Y = ((1-exp(-D₀A))/(D₀A))² → {ym:.4f}  ({ym*100:.1f}%)")
    _info(f"  Neg-Binomial   Y = (1+D₀A/α)^(-α), α=2    → {yn:.4f}  ({yn*100:.1f}%)")

    assert 0.0 < yp <= 1.0 and 0.0 < ym <= 1.0 and 0.0 < yn <= 1.0
    # Murphy ≥ Poisson for same D₀ (less pessimistic assumption)
    assert ym >= yp - 1e-9
    _ok("All yield estimates in (0, 1] and Murphy ≥ Poisson  ✓")


# ===========================================================================
# 5 — Spatial yield  (Jensen's inequality, inlined)
# ===========================================================================

def _section_spatial() -> None:
    panel_id      = "DEMO_W001"
    pitch         = _WAFER_PITCH
    die_area_cm2  = (pitch * pitch) / 100.0

    # Per-die defect counts
    rows = _CONN.execute(
        "SELECT component_row, component_col, COUNT(*) AS n "
        "FROM defects WHERE panel_id=? "
        "GROUP BY component_row, component_col",
        (panel_id,),
    ).fetchall()
    counts = {(r["component_row"], r["component_col"]): r["n"] for r in rows}

    all_dies = _CONN.execute(
        "SELECT component_row, component_col FROM components "
        "WHERE panel_id=? AND active=1",
        (panel_id,),
    ).fetchall()

    d0s = [counts.get((r["component_row"], r["component_col"]), 0) / die_area_cm2
           for r in all_dies]
    n = len(d0s)

    mean_d0       = sum(d0s) / n
    spatial_yield = sum(_poisson_yield(d, die_area_cm2) for d in d0s) / n
    global_yield  = _poisson_yield(mean_d0, die_area_cm2)
    yield_gain    = spatial_yield - global_yield

    # CV(D₀)
    var = sum((d - mean_d0) ** 2 for d in d0s) / n
    cv  = math.sqrt(var) / mean_d0 if mean_d0 > 0 else 0.0

    _ok(f"Panel: {panel_id}  |  {n} active dies")
    _info(f"  Spatial yield  (Poisson, per-die avg) : {spatial_yield:.4f}  ({spatial_yield*100:.1f}%)")
    _info(f"  Global  yield  (Poisson, mean D₀)     : {global_yield:.4f}  ({global_yield*100:.1f}%)")
    _info(f"  Jensen gain                            : {yield_gain*100:+.2f} pp")
    _info(f"  CV(D₀)                                 : {cv:.4f}")

    # Jensen's inequality: E[f(X)] ≥ f(E[X]) for convex f = exp(-)
    assert spatial_yield >= global_yield - 1e-9, "Jensen's inequality violated!"
    _ok("Jensen's inequality confirmed: spatial_yield ≥ global_yield  ✓")

    # Uniform panel (DEMO_W002) should have smaller gain
    rows2 = _CONN.execute(
        "SELECT component_row, component_col, COUNT(*) AS n "
        "FROM defects WHERE panel_id='DEMO_W002' "
        "GROUP BY component_row, component_col",
        (),
    ).fetchall()
    counts2 = {(r["component_row"], r["component_col"]): r["n"] for r in rows2}
    dies2 = _CONN.execute(
        "SELECT component_row, component_col FROM components "
        "WHERE panel_id='DEMO_W002' AND active=1",
    ).fetchall()
    d0s2   = [counts2.get((r["component_row"], r["component_col"]), 0) / die_area_cm2
              for r in dies2]
    sy2    = sum(_poisson_yield(d, die_area_cm2) for d in d0s2) / len(d0s2)
    gy2    = _poisson_yield(sum(d0s2) / len(d0s2), die_area_cm2)
    _info(f"  DEMO_W002 (mixed density): gain = {(sy2-gy2)*100:+.2f} pp")


# ===========================================================================
# 6 — Lot genealogy
# ===========================================================================

def _section_genealogy() -> None:
    from openyield.analysis.genealogy import (
        LotNode, GenealogyEdge,
        upsert_lot_node, add_genealogy_edge,
        get_ancestors, get_descendants, get_lineage,
        detect_cycles,
    )
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()

    #   INGOT_A ─┐
    #             ├─(merge)──► WAFER_LOT_01 ─(rework)──► WAFER_LOT_01_R
    #   INGOT_B ─┘              └──(convert)──► CHIP_LOT_01
    nodes = [
        LotNode("INGOT_A",        "wafer", "PULL",   1,   now, {}),
        LotNode("INGOT_B",        "wafer", "PULL",   1,   now, {}),
        LotNode("WAFER_LOT_01",   "wafer", "LITHO",  25,  now, {"fab": "F8"}),
        LotNode("WAFER_LOT_01_R", "wafer", "REWORK", 20,  now, {}),
        LotNode("CHIP_LOT_01",    "chip",  "DICE",   800, now, {}),
    ]
    for n in nodes:
        upsert_lot_node(_CONN, n)

    edges = [
        GenealogyEdge("INGOT_A",      "WAFER_LOT_01",   "merge",   ""),
        GenealogyEdge("INGOT_B",      "WAFER_LOT_01",   "merge",   ""),
        GenealogyEdge("WAFER_LOT_01", "WAFER_LOT_01_R", "rework",  "tool excursion"),
        GenealogyEdge("WAFER_LOT_01", "CHIP_LOT_01",    "convert", "dice step"),
    ]
    for e in edges:
        add_genealogy_edge(_CONN, e)

    _ok(f"Inserted {len(nodes)} lot nodes, {len(edges)} edges")

    ancestors = get_ancestors(_CONN, "CHIP_LOT_01")
    _ok(f"Ancestors of CHIP_LOT_01 : {[a.lot_id for a in ancestors]}")

    descendants = get_descendants(_CONN, "INGOT_A")
    _ok(f"Descendants of INGOT_A   : {[d.lot_id for d in descendants]}")

    lineage = get_lineage(_CONN, "WAFER_LOT_01")
    _info(f"  WAFER_LOT_01 lineage — "
          f"{len(lineage.ancestors)} ancestor(s), "
          f"{len(lineage.descendants)} descendant(s)")

    cycles = detect_cycles(_CONN)
    assert cycles == [], f"Unexpected cycles: {cycles}"
    _ok(f"Cycle detection (Kahn's algorithm): {len(cycles)} cycles  ✓")


# ===========================================================================
# 7 — OpenMES connector  (MockTransport)
# ===========================================================================

def _section_openmes() -> None:
    from openyield.integrations.openmes_connector import (
        OpenMESConnector, MockTransport,
        MESLot, MESProcessStep, MESWorkOrder, MESYieldResult,
    )

    transport = MockTransport()

    lots = [
        MESLot("MES_L001", "CHIP_X", "wafer", 25, "LITHO", "active",
               "2025-01-15T08:00:00Z", {"tool": "DUV_01"}),
        MESLot("MES_L002", "CHIP_X", "wafer", 25, "ETCH",  "active",
               "2025-01-15T08:00:00Z", {"tool": "DRY_03"}),
    ]
    for lot in lots:
        transport.register_lot(lot)
        transport.register_lot_history(lot.lot_id, [
            MESProcessStep(
                step_id=f"{lot.lot_id}_S1", lot_id=lot.lot_id,
                equipment_id=lot.attributes["tool"],
                recipe_id=f"REC_{lot.current_step}",
                start_time="2025-01-14T06:00:00Z",
                end_time="2025-01-14T08:00:00Z",
                status="completed",
                parameters={"power": 500},
            )
        ])

    transport.register_work_order(MESWorkOrder(
        work_order_id="WO_001", lot_id="MES_L001",
        product_id="CHIP_X", quantity=25,
        due_date="2025-02-01", priority=1, status="active",
    ))

    connector = OpenMESConnector(transport)

    pulled = connector.pull_lot("MES_L001")
    assert pulled.lot_id == "MES_L001"
    _ok(f"pull_lot: {pulled.lot_id}  step={pulled.current_step}  "
        f"status={pulled.status}")

    history = connector.pull_lot_history("MES_L001")
    _ok(f"pull_lot_history: {len(history)} step(s) for MES_L001")

    work_orders = connector.pull_work_orders()
    _ok(f"pull_work_orders: {len(work_orders)} order(s)")

    report = connector.sync_lots_to_openyield(_CONN, ["MES_L001", "MES_L002"])
    _ok(f"sync_lots_to_openyield: synced={report.lots_synced}  "
        f"panels_created={report.panels_created}  errors={len(report.errors)}")
    assert len(report.errors) == 0

    yr = MESYieldResult(
        panel_id="DEMO_W001", lot_id="MES_L001",
        yield_poisson=0.84, yield_murphy=0.87, yield_negbinom=0.85,
        defect_count=8,
    )
    ok = connector.push_yield_result(yr)
    assert ok is True
    _ok(f"push_yield_result: accepted  "
        f"(panel=DEMO_W001  Y_Poisson=84.0%  Y_Murphy=87.0%)")
    _info(f"  MockTransport recorded {len(transport.posted)} POST request(s)")


# ===========================================================================
# [+] CNN classifier  (optional — requires Pillow + NumPy)
# ===========================================================================

def _section_cnn() -> None:
    try:
        from PIL import Image as _PILImage
    except ImportError:
        _warn("Pillow not installed — skipping.  pip install pillow")
        return

    import numpy as np
    from openyield.ai.cnn_classifier import train_cnn, load_from_registry

    LABELS = ["particle", "scratch", "pit",
              "crystal_defect", "metal_spike", "void", "bridging"]
    rng = np.random.default_rng(42)

    _info(f"Generating {len(LABELS) * 6} synthetic 64×64 defect patches…")
    for label in LABELS:
        for seed in range(6):
            arr = rng.integers(0, 256, (64, 64), dtype=np.uint8)
            buf = io.BytesIO()
            _PILImage.fromarray(arr, mode="L").save(buf, format="PNG")
            _CONN.execute(
                "INSERT INTO defect_images (image_data, defect_type) VALUES (?,?)",
                (buf.getvalue(), label),
            )
    _CONN.commit()
    _ok(f"Inserted {len(LABELS) * 6} image blobs ({len(LABELS)} classes)")

    _info("Training CNN (pure NumPy, 1 367 params, 5 epochs)…")
    model, history = train_cnn(_CONN, epochs=5)
    final_val_acc = history.val_acc[-1] if history.val_acc else 0.0
    _ok(f"Training complete — params={model.n_params()}  "
        f"val_acc={final_val_acc:.3f}  epochs={history.epochs_run}")

    loaded_model, loaded_classes = load_from_registry(_CONN)
    assert loaded_model is not None and len(loaded_classes) == len(LABELS)
    _ok(f"Reloaded from model_registry — classes: {loaded_classes}")


# ===========================================================================
# Entry point
# ===========================================================================

def main() -> int:
    print(bold("\n══════════════════════════════════════════"))
    print(bold("  OpenYield  —  End-to-End Demo"))
    print(bold("══════════════════════════════════════════"))

    t0 = time.perf_counter()

    _run(1, "Database initialisation",            _section_db)
    _run(2, "Panel + defect ingestion",            _section_ingest)
    _run(3, "KLARF 2.0 binary parse & ingest",     _section_klarf)
    _run(4, "Yield models  (Poisson / Murphy / NB)",_section_yield)
    _run(5, "Spatial yield — Jensen's inequality", _section_spatial)
    _run(6, "Lot genealogy  (BFS + Kahn cycles)",  _section_genealogy)
    _run(7, "OpenMES connector  (MockTransport)",  _section_openmes)

    print(f"\n{bold('[+]')} {cyan('CNN defect classifier')} "
          f"{dim('(optional — requires Pillow)')}")
    _section_cnn()

    elapsed = time.perf_counter() - t0
    print(f"\n{bold('══════════════════════════════════════════')}")
    if _failures:
        print(red(f"  {len(_failures)}/{_TOTAL} section(s) FAILED: "
                  f"{', '.join(_failures)}"))
        print(bold("══════════════════════════════════════════\n"))
        return 1

    print(green(f"  All {_TOTAL} sections passed") + dim(f"  ({elapsed:.1f} s)"))
    print(bold("══════════════════════════════════════════\n"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
