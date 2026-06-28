"""
api/routers/analysis_router.py
--------------------------------
Author: Yeonkuk Woo

Clustering analysis and lot tracking endpoints.
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from openyield.api.dependencies import get_db
from openyield.db.connection import get_placeholder
from openyield.analysis.clustering import cluster_panel, cluster_all_panels
from openyield.analysis.lot_tracker import summarise_lot, summarise_all_lots
from openyield.analysis.bin_analysis import build_panel_map
from openyield.analysis.trend import compute_trend

router = APIRouter(tags=["analysis"])
Connection = Any


# ── Schemas ────────────────────────────────────────────────────────────────

class ClusterResponse(BaseModel):
    panel_id:        str
    n_clusters:      int
    n_noise:         int
    classification:  str
    largest_cluster: int
    epsilon_mm:      float
    min_samples:     int
    cluster_summary: dict
    calculated_at:   str

class PanelLotStatsResponse(BaseModel):
    panel_id:        str
    defect_density:  float
    yield_negbinom:  float | None
    cluster_class:   str | None

class LotSummaryResponse(BaseModel):
    lot_id:              str
    substrate_type:      str
    panel_count:         int
    panels:              list[PanelLotStatsResponse]
    avg_defect_density:  float
    std_defect_density:  float
    avg_yield_negbinom:  float | None
    std_yield_negbinom:  float | None
    excursion_count:     int
    lot_status:          str
    status_reason:       str


# ── Clustering endpoints ───────────────────────────────────────────────────

@router.post("/panels/{panel_id}/cluster", response_model=ClusterResponse)
def run_clustering(
    panel_id:    str,
    epsilon_mm:  float | None = Query(None, description="Override DBSCAN ε (mm)"),
    min_samples: int          = Query(3, ge=2),
    conn:        Connection   = Depends(get_db),
):
    """Run DBSCAN spatial clustering on a panel's system_a defects."""
    ph = get_placeholder(conn)
    if not conn.execute(
        f"SELECT 1 FROM panels WHERE panel_id={ph}", (panel_id,)
    ).fetchone():
        raise HTTPException(status_code=404, detail=f"Panel '{panel_id}' not found")
    try:
        result = cluster_panel(
            conn, panel_id,
            epsilon_mm=epsilon_mm,
            min_samples=min_samples,
            persist=True,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return ClusterResponse(
        panel_id=result.panel_id,
        n_clusters=result.n_clusters,
        n_noise=result.n_noise,
        classification=result.classification,
        largest_cluster=result.largest_cluster,
        epsilon_mm=result.epsilon_mm,
        min_samples=result.min_samples,
        cluster_summary=result.cluster_summary,
        calculated_at=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/panels/{panel_id}/cluster", response_model=ClusterResponse)
def get_clustering(panel_id: str, conn: Connection = Depends(get_db)):
    """Return the most recent clustering result for a panel."""
    ph = get_placeholder(conn)
    row = conn.execute(
        f"SELECT * FROM cluster_results WHERE panel_id={ph} "
        f"ORDER BY calculated_at DESC LIMIT 1",
        (panel_id,)
    ).fetchone()
    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"No clustering result for '{panel_id}'. "
                   f"POST /panels/{panel_id}/cluster to compute it."
        )
    import json
    return ClusterResponse(
        panel_id=row["panel_id"],
        n_clusters=row["n_clusters"],
        n_noise=row["n_noise"],
        classification=row["classification"],
        largest_cluster=row["largest_cluster"],
        epsilon_mm=row["epsilon_mm"],
        min_samples=row["min_samples"],
        cluster_summary=json.loads(row["cluster_summary"] or "{}"),
        calculated_at=str(row["calculated_at"]),
    )


@router.post("/cluster/all", response_model=list[ClusterResponse])
def run_clustering_all(
    substrate_type: str | None = Query(None),
    conn: Connection = Depends(get_db),
):
    """Run clustering on all panels."""
    results = cluster_all_panels(conn, substrate_type=substrate_type, persist=True)
    return [
        ClusterResponse(
            panel_id=r.panel_id,
            n_clusters=r.n_clusters,
            n_noise=r.n_noise,
            classification=r.classification,
            largest_cluster=r.largest_cluster,
            epsilon_mm=r.epsilon_mm,
            min_samples=r.min_samples,
            cluster_summary=r.cluster_summary,
            calculated_at=datetime.now(timezone.utc).isoformat(),
        )
        for r in results
    ]


# ── Lot endpoints ──────────────────────────────────────────────────────────

@router.get("/lots", response_model=list[LotSummaryResponse])
def list_lot_summaries(
    substrate_type: str | None = Query(None),
    conn: Connection = Depends(get_db),
):
    """Return lot summaries for all lots."""
    summaries = summarise_all_lots(
        conn, substrate_type=substrate_type, persist=False
    )
    return [_lot_to_response(s) for s in summaries]


@router.get("/lots/{lot_id}", response_model=LotSummaryResponse)
def get_lot_summary(lot_id: str, conn: Connection = Depends(get_db)):
    """Return summary for a specific lot."""
    try:
        summary = summarise_lot(conn, lot_id, persist=False)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _lot_to_response(summary)


@router.post("/lots/{lot_id}/summarise", response_model=LotSummaryResponse)
def compute_lot_summary(lot_id: str, conn: Connection = Depends(get_db)):
    """Recompute and persist lot summary."""
    try:
        summary = summarise_lot(conn, lot_id, persist=True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _lot_to_response(summary)


# ── Panel map endpoint ─────────────────────────────────────────────────────

class MapCellResponse(BaseModel):
    row:           int
    col:           int
    active:        bool
    region_id:     str
    defect_count:  int
    defect_types:  dict
    cluster_label: int | None
    yield_poisson: float | None

class PanelMapResponse(BaseModel):
    panel_id:           str
    substrate_type:     str
    rows:               int
    cols:               int
    cells:              list[MapCellResponse]
    total_defects:      int
    active_dies:        int
    defect_density:     float | None
    yield_poisson:      float | None
    yield_murphy:       float | None
    yield_negbinom:     float | None
    clustering_class:   str | None


@router.get("/panels/{panel_id}/map", response_model=PanelMapResponse)
def get_panel_map(panel_id: str, conn: Connection = Depends(get_db)):
    """Spatial bin map for a panel — powers the frontend heatmap."""
    try:
        pm = build_panel_map(conn, panel_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return PanelMapResponse(
        panel_id=pm.panel_id,
        substrate_type=pm.substrate_type,
        rows=pm.rows, cols=pm.cols,
        cells=[
            MapCellResponse(
                row=c.row, col=c.col,
                active=c.active, region_id=c.region_id,
                defect_count=c.defect_count, defect_types=c.defect_types,
                cluster_label=c.cluster_label, yield_poisson=c.yield_poisson,
            )
            for c in pm.cells
        ],
        total_defects=pm.total_defects,
        active_dies=pm.active_dies,
        defect_density=pm.defect_density,
        yield_poisson=pm.yield_poisson,
        yield_murphy=pm.yield_murphy,
        yield_negbinom=pm.yield_negbinom,
        clustering_class=pm.clustering_class,
    )


# ── Trend endpoints ────────────────────────────────────────────────────────

class TrendPointResponse(BaseModel):
    lot_id:             str
    sequence:           int
    created_at:         str
    substrate_type:     str
    avg_defect_density: float
    avg_yield_negbinom: float | None
    excursion_count:    int
    lot_status:         str

class TrendResultResponse(BaseModel):
    substrate_type: str
    n_lots:         int
    data_points:    list[TrendPointResponse]
    slope:          float
    intercept:      float
    r_squared:      float
    direction:      str
    mean_density:   float
    mean_yield:     float | None
    first_lot_id:   str | None
    last_lot_id:    str | None


@router.get("/trends", response_model=TrendResultResponse)
def get_trend(
    substrate_type: str | None = Query(None, description="'glass_panel' or 'wafer'"),
    limit: int = Query(50, ge=2, le=200),
    conn: Connection = Depends(get_db),
):
    """Defect density and yield trend across lots ordered chronologically."""
    result = compute_trend(conn, substrate_type=substrate_type, limit=limit)
    return TrendResultResponse(
        substrate_type=result.substrate_type,
        n_lots=result.n_lots,
        data_points=[
            TrendPointResponse(
                lot_id=p.lot_id, sequence=p.sequence, created_at=p.created_at,
                substrate_type=p.substrate_type,
                avg_defect_density=p.avg_defect_density,
                avg_yield_negbinom=p.avg_yield_negbinom,
                excursion_count=p.excursion_count, lot_status=p.lot_status,
            )
            for p in result.data_points
        ],
        slope=result.slope, intercept=result.intercept,
        r_squared=result.r_squared, direction=result.direction,
        mean_density=result.mean_density, mean_yield=result.mean_yield,
        first_lot_id=result.first_lot_id, last_lot_id=result.last_lot_id,
    )


def _lot_to_response(s) -> LotSummaryResponse:
    return LotSummaryResponse(
        lot_id=s.lot_id,
        substrate_type=s.substrate_type,
        panel_count=s.panel_count,
        panels=[
            PanelLotStatsResponse(
                panel_id=p.panel_id,
                defect_density=p.defect_density,
                yield_negbinom=p.yield_negbinom,
                cluster_class=p.cluster_class,
            )
            for p in s.panels
        ],
        avg_defect_density=s.avg_defect_density,
        std_defect_density=s.std_defect_density,
        avg_yield_negbinom=s.avg_yield_negbinom,
        std_yield_negbinom=s.std_yield_negbinom,
        excursion_count=s.excursion_count,
        lot_status=s.lot_status,
        status_reason=s.status_reason,
    )
