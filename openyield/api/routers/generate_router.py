"""
api/routers/generate_router.py
-------------------------------
Author: Yeonkuk Woo

Synthetic inspection data generation endpoint.

POST /generate — configures a substrate spec, generates synthetic panels
in-memory, persists them to the database, and optionally runs yield
calculation and DBSCAN clustering — all in a single request.
"""

from __future__ import annotations

import dataclasses
import io
import time
import logging
from typing import Any

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from openyield.api.dependencies import get_db
from openyield.db.connection import get_placeholder
from openyield.ingestion.ingest import upsert_panel, upsert_component, upsert_defect
from openyield.ingestion.adapters.klarf2_adapter import (
    encode_klarf2, DEFECT_TYPE_TO_CLASS,
    Klarf2FileInfo, Klarf2LotInfo, Klarf2SetupInfo,
    Klarf2Wafer, Klarf2Defect, Klarf2Summary,
)
from openyield.analysis.lot_tracker import auto_create_lot
from openyield.synthetic.generator import (
    generate_panel_id, generate_components,
    _generate_ground_truth, _simulate_system_a, _simulate_system_b,
    match_defects, SyntheticPanel,
)
from openyield.synthetic.substrate_profiles import get_profile
from openyield.yield_engine.calculator import calculate_panel_yield
from openyield.analysis.clustering import cluster_panel

router = APIRouter(prefix="/generate", tags=["generate"])
logger = logging.getLogger(__name__)
Connection = Any

_GRID_DEFAULTS = {
    "wafer":       (10, 10),
    "glass_panel": (6,  6),
}


# ---------------------------------------------------------------------------
# Request / response
# ---------------------------------------------------------------------------

class GenerateRequest(BaseModel):
    substrate_type:          str        = Field(..., description="'wafer' or 'glass_panel'")
    n_panels:                int        = Field(3,    ge=1,  le=20)
    rows:                    int | None = Field(None, ge=2,  le=30)
    cols:                    int | None = Field(None, ge=2,  le=30)
    product_type:            str | None = Field(None)
    mean_defect_count:       float | None = Field(None, ge=0.0, le=20.0,
                                 description="Poisson λ per die — overrides profile default")
    edge_exclusion_fraction: float      = Field(0.08, ge=0.0, le=0.30)
    seed:                    int | None = Field(None)
    run_yield:               bool       = Field(True)
    run_clustering:          bool       = Field(True)


class GeneratedPanelSummary(BaseModel):
    panel_id:         str
    lot_id:           str
    rows:             int
    cols:             int
    active_dies:      int
    total_defects:    int
    system_a_count:   int
    system_b_count:   int
    defect_density:   float | None
    yield_poisson:    float | None
    yield_negbinom:   float | None
    clustering_class: str | None


class GenerateResponse(BaseModel):
    substrate_type:    str
    n_panels:          int
    total_defects:     int
    mean_defect_count: float
    panels:            list[GeneratedPanelSummary]
    elapsed_ms:        float


# ---------------------------------------------------------------------------
# Internal panel builder (single code path, profile is already patched)
# ---------------------------------------------------------------------------

def _build_panel(
    profile,
    rows: int,
    cols: int,
    product_type: str | None,
    edge_exclusion_fraction: float,
    seed: int | None,
) -> SyntheticPanel:
    rng = np.random.default_rng(seed)
    panel_id     = generate_panel_id(profile.substrate_type)
    product_type = product_type or str(rng.choice(profile.product_types))

    components   = generate_components(
        panel_id=panel_id, rows=rows, cols=cols,
        profile=profile,
        edge_exclusion_fraction=edge_exclusion_fraction,
    )
    active_comps = [c for c in components if c.active]

    sys_a, sys_b = [], []
    for comp in active_comps:
        gt = _generate_ground_truth(comp, profile, rng)
        sys_a.extend(_simulate_system_a(gt, profile, rng))
        sys_b.extend(_simulate_system_b(gt, profile, rng))

    sys_a, sys_b = match_defects(sys_a, sys_b, profile.match_distance_threshold)

    return SyntheticPanel(
        panel_id=panel_id,
        product_type=product_type,
        substrate_type=profile.substrate_type.value,
        rows=rows, cols=cols,
        components=components,
        defects=sys_a + sys_b,
    )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("", response_model=GenerateResponse)
def generate_synthetic_panels(
    req:  GenerateRequest,
    conn: Connection = Depends(get_db),
):
    """
    Generate synthetic inspection data and persist to the database.

    Configures a substrate profile, generates N panels with realistic
    defect distributions (Poisson arrival, Gaussian clustering, dual-system
    detection simulation), persists panels + components + defects, and
    optionally calculates yield and DBSCAN clustering.

    `mean_defect_count` overrides the profile's Poisson λ — use values below
    the profile default to simulate a clean process, or above to simulate
    a process excursion.
    """
    t0 = time.perf_counter()

    try:
        profile = get_profile(req.substrate_type)
    except (ValueError, KeyError):
        raise HTTPException(
            status_code=422,
            detail=f"Unknown substrate_type: {req.substrate_type!r}. Use 'wafer' or 'glass_panel'.",
        )

    # Patch profile when override is requested
    eff_mean = req.mean_defect_count if req.mean_defect_count is not None else profile.mean_defect_count
    if req.mean_defect_count is not None:
        profile = dataclasses.replace(profile, mean_defect_count=req.mean_defect_count)

    default_rows, default_cols = _GRID_DEFAULTS.get(req.substrate_type, (6, 6))
    rows = req.rows if req.rows is not None else default_rows
    cols = req.cols if req.cols is not None else default_cols

    summaries:     list[GeneratedPanelSummary] = []
    total_defects: int = 0

    for i in range(req.n_panels):
        seed_i = None if req.seed is None else req.seed + i

        panel = _build_panel(
            profile=profile,
            rows=rows, cols=cols,
            product_type=req.product_type,
            edge_exclusion_fraction=req.edge_exclusion_fraction,
            seed=seed_i,
        )

        with conn:
            lot_id = auto_create_lot(conn, panel.panel_id, panel.substrate_type, panel.product_type)
            upsert_panel(
                conn,
                panel_id=panel.panel_id,
                product_type=panel.product_type,
                substrate_type=panel.substrate_type,
                rows=panel.rows, cols=panel.cols,
                lot_id=lot_id,
            )
            for c in panel.components:
                upsert_component(
                    conn,
                    panel_id=c.panel_id,
                    component_row=c.component_row,
                    component_col=c.component_col,
                    region_id=c.region_id,
                    center_x=c.center_x,
                    center_y=c.center_y,
                    active=c.active,
                )
            for d in panel.defects:
                upsert_defect(
                    conn,
                    panel_id=d.panel_id,
                    component_row=d.component_row,
                    component_col=d.component_col,
                    source_system=d.source_system,
                    defect_type=d.defect_type,
                    x=d.x, y=d.y,
                    size=d.size,
                    confidence_score=d.confidence_score,
                    match_id=d.match_id,
                    created_at=d.created_at,
                )

        sys_a_count = sum(1 for d in panel.defects if d.source_system == "system_a")
        sys_b_count = sum(1 for d in panel.defects if d.source_system == "system_b")
        active_dies = sum(1 for c in panel.components if c.active)
        total_defects += len(panel.defects)

        ye = None
        if req.run_yield:
            try:
                ye = calculate_panel_yield(conn, panel.panel_id, persist=True)
            except Exception as exc:
                logger.warning("Yield failed for %s: %s", panel.panel_id, exc)

        cr = None
        if req.run_clustering:
            try:
                cr = cluster_panel(conn, panel.panel_id, persist=True)
            except Exception as exc:
                logger.warning("Clustering failed for %s: %s", panel.panel_id, exc)

        summaries.append(GeneratedPanelSummary(
            panel_id=panel.panel_id,
            lot_id=lot_id,
            rows=panel.rows,
            cols=panel.cols,
            active_dies=active_dies,
            total_defects=len(panel.defects),
            system_a_count=sys_a_count,
            system_b_count=sys_b_count,
            defect_density=ye.defect_density   if ye else None,
            yield_poisson=ye.yield_poisson     if ye else None,
            yield_negbinom=ye.yield_negbinom   if ye else None,
            clustering_class=cr.classification if cr else None,
        ))

        logger.info(
            "[generate] %s %s | %d defects | yield_nb=%.3f | cluster=%s",
            panel.substrate_type, panel.panel_id,
            len(panel.defects),
            ye.yield_negbinom if ye else 0.0,
            cr.classification if cr else "—",
        )

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)

    return GenerateResponse(
        substrate_type=req.substrate_type,
        n_panels=req.n_panels,
        total_defects=total_defects,
        mean_defect_count=eff_mean,
        panels=summaries,
        elapsed_ms=elapsed_ms,
    )


@router.get("/klarf2/{lot_id}")
def export_lot_as_klarf2(lot_id: str, conn: Connection = Depends(get_db)):
    """
    Export a generated lot as a KLARF 2.0 binary (.klf2) file.

    Each panel in the lot becomes a WAFER_INFO + DEFECT_LIST block pair.
    Only system_a defects are exported (primary inspection system).
    The file is valid KLARF 2.0 and can be re-ingested via POST /ingest/klarf2.
    """
    ph = get_placeholder(conn)

    panels = conn.execute(
        f"SELECT panel_id, substrate_type FROM panels WHERE lot_id = {ph} ORDER BY panel_id",
        (lot_id,)
    ).fetchall()

    if not panels:
        raise HTTPException(status_code=404, detail=f"Lot {lot_id!r} not found")

    wafers: list[Klarf2Wafer] = []
    for slot, panel in enumerate(panels, start=1):
        rows = conn.execute(
            f"SELECT defect_type, x, y, size, confidence_score FROM defects "
            f"WHERE panel_id = {ph} AND source_system = 'system_a' ORDER BY defect_id",
            (panel["panel_id"],)
        ).fetchall()

        klarf_defects = [
            Klarf2Defect(
                defect_id=i + 1,
                x_mm=float(d["x"]),
                y_mm=float(d["y"]),
                x_size_mm=float(d["size"]),
                y_size_mm=float(d["size"]),
                class_number=DEFECT_TYPE_TO_CLASS.get(d["defect_type"], 0),
                rough_bin=0,
                fine_bin=0,
                test_number=1,
                cluster_number=0,
                confidence=float(d["confidence_score"]),
            )
            for i, d in enumerate(rows)
        ]

        wafers.append(Klarf2Wafer(
            wafer_id=panel["panel_id"][:16],
            slot_number=slot,
            wafer_type=0,
            orientation=0,
            num_defects=len(klarf_defects),
            defects=klarf_defects,
        ))

    total_defects = sum(len(w.defects) for w in wafers)

    data = encode_klarf2(
        file_info=Klarf2FileInfo(
            station_id="OpenYield",
            file_timestamp=int(time.time()),
            inspector_version="1.0.0",
        ),
        lot_info=Klarf2LotInfo(
            lot_id=lot_id[:32],
            step_id="AOI",
            device_id="SYNTHETIC",
            process_step="FINAL_INSPECTION",
        ),
        setup_info=Klarf2SetupInfo(
            recipe_id="OPENYIELD_SYNTHETIC",
            inspection_mode=0,
            pixel_size_um=1.0,
            die_width_mm=1.0,
            die_height_mm=1.0,
            num_defect_classes=len(DEFECT_TYPE_TO_CLASS),
        ),
        wafers=wafers,
        summary=Klarf2Summary(
            total_wafers=len(wafers),
            total_defects=total_defects,
            mean_defects_per_wafer=total_defects / len(wafers) if wafers else 0.0,
        ),
    )

    filename = f"{lot_id}.klf2"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
