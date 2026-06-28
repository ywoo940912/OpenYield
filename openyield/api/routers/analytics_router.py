"""
api/routers/analytics_router.py
---------------------------------
Author: Yeonkuk Woo

Pareto, SPC, correlation, and signature API endpoints.
"""

from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from openyield.api.dependencies import get_db
from openyield.db.connection import get_placeholder
from openyield.analysis.pareto import (
    calculate_pareto, calculate_zone_pareto,
    calculate_system_comparison, calculate_lot_trend,
)
from openyield.analysis.spc import calculate_spc
from openyield.analysis.correlation import calculate_correlation
from openyield.analysis.signatures import match_signatures, match_all_panels

router = APIRouter(tags=["analytics"])
Connection = Any


# ── Pareto schemas ─────────────────────────────────────────────────────────

class DefectTypeStatsResponse(BaseModel):
    defect_type:         str
    count:               int
    avg_size_mm:         float
    avg_confidence:      float
    impact_score:        float
    impact_fraction:     float
    cumulative_fraction: float
    yield_loss_estimate: float
    rank:                int

class ParetoResponse(BaseModel):
    panel_id:       str | None
    substrate_type: str | None
    source_system:  str
    calculated_at:  str
    total_defects:  int
    items:          list[DefectTypeStatsResponse]
    vital_few:      list[str]
    trivial_many:   list[str]



# ── Zone Pareto schemas ────────────────────────────────────────────────────

class ZoneParetoResponse(BaseModel):
    panel_id:       str | None
    substrate_type: str | None
    source_system:  str
    calculated_at:  str
    zones:          dict[str, ParetoResponse]


# ── System comparison schemas ──────────────────────────────────────────────

class SystemComparisonItemResponse(BaseModel):
    defect_type:       str
    count_a:           int
    count_b:           int
    impact_a:          float
    impact_b:          float
    match_rate:        float
    likely_real:       bool
    rank_a:            int
    rank_b:            int

class SystemComparisonResponse(BaseModel):
    panel_id:          str | None
    substrate_type:    str | None
    calculated_at:     str
    total_a:           int
    total_b:           int
    items:             list[SystemComparisonItemResponse]
    nuisance_suspects: list[str]
    confirmed_killers: list[str]


# ── Lot trend schemas ──────────────────────────────────────────────────────

class LotTrendPointResponse(BaseModel):
    lot_id:          str
    calculated_at:   str
    panel_count:     int
    defect_type:     str
    count:           int
    impact_score:    float
    impact_fraction: float
    yield_loss:      float

class LotTrendResponse(BaseModel):
    substrate_type: str | None
    defect_types:   list[str]
    trend:          list[LotTrendPointResponse]
    improving:      list[str]
    degrading:      list[str]


# ── SPC schemas ────────────────────────────────────────────────────────────

class SpcAlarmResponse(BaseModel):
    panel_id:      str
    sequence:      int
    chart_type:    str
    rule_fired:    str
    value:         float
    control_limit: float
    severity:      str


class CapabilityResponse(BaseModel):
    cp:             float | None
    cpk:            float | None
    usl:            float | None
    lsl:            float | None
    interpretation: str


class ControlPointResponse(BaseModel):
    panel_id:        str
    sequence:        int
    value:           float
    moving_range:    float
    ewma:            float
    cusum_pos:       float
    cusum_neg:       float
    ucl_shewhart:    float
    lcl_shewhart:    float
    ucl_ewma:        float
    lcl_ewma:        float
    ucl_cusum:       float
    ucl_imr:         float
    shewhart_signal: bool
    ewma_signal:     bool
    cusum_signal:    bool
    imr_signal:      bool
    we_rules:        list[str]

class SPCResponse(BaseModel):
    lot_id:           str | None
    substrate_type:   str | None
    calculated_at:    str
    n_points:         int
    centerline:       float
    sigma:            float
    lambda_ewma:      float
    L_ewma:           float
    cusum_k:          float
    cusum_h:          float
    points:           list[ControlPointResponse]
    alarms:           list[SpcAlarmResponse]
    shewhart_signals: list[str]
    ewma_signals:     list[str]
    cusum_signals:    list[str]
    imr_signals:      list[str]
    process_state:    str
    capability:       CapabilityResponse
    db_id:            int | None


# ── Correlation schemas ────────────────────────────────────────────────────

class RepeatLocationResponse(BaseModel):
    component_row:    int
    component_col:    int
    region_id:        str
    repeat_count:     int
    repeat_rate:      float
    dominant_type:    str
    type_consistency: float
    panel_ids:        list[str]

class CorrelationResponse(BaseModel):
    lot_id:                  str | None
    substrate_type:          str | None
    total_panels:            int
    total_locations:         int
    repeat_threshold:        float
    systematic_locations:    list[RepeatLocationResponse]
    systematic_count:        int
    systematic_rate:         float
    calculated_at:           str
    classification:          str
    classification_reason:   str


# ── Signature schemas ──────────────────────────────────────────────────────

class SignatureMatchResponse(BaseModel):
    signature_name:     str
    confidence:         float
    description:        str
    root_cause:         str
    recommended_action: str
    evidence:           str

class SignatureResultResponse(BaseModel):
    panel_id:       str
    substrate_type: str
    calculated_at:  str
    defect_count:   int
    zone_fractions: dict[str, float]
    matches:        list[SignatureMatchResponse]
    top_match:      SignatureMatchResponse | None


# ── Pareto endpoints ───────────────────────────────────────────────────────

@router.get("/pareto", response_model=ParetoResponse)
def get_pareto(
    panel_id:       str | None = Query(None),
    substrate_type: str | None = Query(None),
    source_system:  str        = Query("system_a"),
    conn: Connection = Depends(get_db),
):
    """Yield-impact Pareto analysis across defect types."""
    result = calculate_pareto(
        conn,
        panel_id=panel_id,
        substrate_type=substrate_type,
        source_system=source_system,
    )
    return ParetoResponse(
        panel_id=result.panel_id,
        substrate_type=result.substrate_type,
        source_system=result.source_system,
        calculated_at=result.calculated_at,
        total_defects=result.total_defects,
        items=[DefectTypeStatsResponse(**i.__dict__) for i in result.items],
        vital_few=result.vital_few,
        trivial_many=result.trivial_many,
    )



@router.get("/pareto/zones", response_model=ZoneParetoResponse)
def get_zone_pareto(
    panel_id:       str | None = Query(None),
    substrate_type: str | None = Query(None),
    source_system:  str        = Query("system_a"),
    conn: Connection = Depends(get_db),
):
    """Yield-impact Pareto broken down by spatial zone/region."""
    result = calculate_zone_pareto(
        conn,
        panel_id=panel_id,
        substrate_type=substrate_type,
        source_system=source_system,
    )
    zones_resp = {}
    for zone, pr in result.zones.items():
        zones_resp[zone] = ParetoResponse(
            panel_id=pr.panel_id,
            substrate_type=pr.substrate_type,
            source_system=pr.source_system,
            calculated_at=pr.calculated_at,
            total_defects=pr.total_defects,
            items=[DefectTypeStatsResponse(**i.__dict__) for i in pr.items],
            vital_few=pr.vital_few,
            trivial_many=pr.trivial_many,
        )
    return ZoneParetoResponse(
        panel_id=result.panel_id,
        substrate_type=result.substrate_type,
        source_system=result.source_system,
        calculated_at=result.calculated_at,
        zones=zones_resp,
    )


@router.get("/pareto/systems", response_model=SystemComparisonResponse)
def get_system_comparison(
    panel_id:       str | None = Query(None),
    substrate_type: str | None = Query(None),
    conn: Connection = Depends(get_db),
):
    """System A vs B Pareto comparison — identifies nuisance vs real defects."""
    result = calculate_system_comparison(
        conn,
        panel_id=panel_id,
        substrate_type=substrate_type,
    )
    return SystemComparisonResponse(
        panel_id=result.panel_id,
        substrate_type=result.substrate_type,
        calculated_at=result.calculated_at,
        total_a=result.total_a,
        total_b=result.total_b,
        items=[SystemComparisonItemResponse(**i.__dict__) for i in result.items],
        nuisance_suspects=result.nuisance_suspects,
        confirmed_killers=result.confirmed_killers,
    )


@router.get("/pareto/trend", response_model=LotTrendResponse)
def get_lot_trend(
    substrate_type: str | None = Query(None),
    source_system:  str        = Query("system_a"),
    top_n_types:    int        = Query(5, ge=1, le=20),
    conn: Connection = Depends(get_db),
):
    """Lot-over-lot Pareto trend — shows improving/degrading defect types."""
    result = calculate_lot_trend(
        conn,
        substrate_type=substrate_type,
        source_system=source_system,
        top_n_types=top_n_types,
    )
    return LotTrendResponse(
        substrate_type=result.substrate_type,
        defect_types=result.defect_types,
        trend=[LotTrendPointResponse(**pt.__dict__) for pt in result.trend],
        improving=result.improving,
        degrading=result.degrading,
    )


# ── SPC endpoints ──────────────────────────────────────────────────────────

@router.get("/spc", response_model=SPCResponse)
def get_spc(
    lot_id:         str | None = Query(None),
    substrate_type: str | None = Query(None),
    lambda_ewma:    float      = Query(0.2, gt=0, le=1),
    L_ewma:         float      = Query(3.0, gt=0),
    cusum_k:        float      = Query(0.5, gt=0),
    cusum_h:        float      = Query(5.0, gt=0),
    usl:            float | None = Query(None),
    lsl:            float | None = Query(None),
    conn: Connection = Depends(get_db),
):
    """Shewhart, EWMA, CUSUM, and IMR control charts for defect density."""
    result = calculate_spc(
        conn,
        lot_id=lot_id,
        substrate_type=substrate_type,
        lambda_ewma=lambda_ewma,
        L_ewma=L_ewma,
        cusum_k=cusum_k,
        cusum_h=cusum_h,
        usl=usl,
        lsl=lsl,
        persist=True,
    )
    return SPCResponse(
        lot_id=result.lot_id,
        substrate_type=result.substrate_type,
        calculated_at=result.calculated_at,
        n_points=result.n_points,
        centerline=result.centerline,
        sigma=result.sigma,
        lambda_ewma=result.lambda_ewma,
        L_ewma=result.L_ewma,
        cusum_k=result.cusum_k,
        cusum_h=result.cusum_h,
        points=[ControlPointResponse(**p.__dict__) for p in result.points],
        alarms=[SpcAlarmResponse(**a.__dict__) for a in result.alarms],
        shewhart_signals=result.shewhart_signals,
        ewma_signals=result.ewma_signals,
        cusum_signals=result.cusum_signals,
        imr_signals=result.imr_signals,
        process_state=result.process_state,
        capability=CapabilityResponse(**result.capability.__dict__),
        db_id=result.db_id,
    )


# ── Correlation endpoints ──────────────────────────────────────────────────

@router.get("/correlation", response_model=CorrelationResponse)
def get_correlation(
    lot_id:           str | None = Query(None),
    substrate_type:   str | None = Query(None),
    source_system:    str        = Query("system_a"),
    repeat_threshold: float      = Query(0.5, gt=0, le=1),
    conn: Connection = Depends(get_db),
):
    """Wafer-to-wafer defect correlation — finds systematic repeating locations."""
    result = calculate_correlation(
        conn,
        lot_id=lot_id,
        substrate_type=substrate_type,
        source_system=source_system,
        repeat_threshold=repeat_threshold,
    )
    return CorrelationResponse(
        lot_id=result.lot_id,
        substrate_type=result.substrate_type,
        total_panels=result.total_panels,
        total_locations=result.total_locations,
        repeat_threshold=result.repeat_threshold,
        systematic_locations=[
            RepeatLocationResponse(**loc.__dict__)
            for loc in result.systematic_locations
        ],
        systematic_count=result.systematic_count,
        systematic_rate=result.systematic_rate,
        calculated_at=result.calculated_at,
        classification=result.classification,
        classification_reason=result.classification_reason,
    )


# ── Signature endpoints ────────────────────────────────────────────────────

@router.get("/signatures/{panel_id}", response_model=SignatureResultResponse)
def get_signatures(
    panel_id:      str,
    source_system: str = Query("system_a"),
    conn: Connection = Depends(get_db),
):
    """Match defect spatial pattern against signature library."""
    ph = get_placeholder(conn)
    if not conn.execute(
        f"SELECT 1 FROM panels WHERE panel_id={ph}", (panel_id,)
    ).fetchone():
        raise HTTPException(status_code=404, detail=f"Panel '{panel_id}' not found")
    try:
        result = match_signatures(conn, panel_id, source_system=source_system)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return _sig_to_response(result)


@router.get("/signatures", response_model=list[SignatureResultResponse])
def get_all_signatures(
    substrate_type: str | None = Query(None),
    source_system:  str        = Query("system_a"),
    conn: Connection = Depends(get_db),
):
    """Run signature matching on all panels."""
    results = match_all_panels(
        conn,
        substrate_type=substrate_type,
        source_system=source_system,
    )
    return [_sig_to_response(r) for r in results]


def _sig_to_response(r) -> SignatureResultResponse:
    return SignatureResultResponse(
        panel_id=r.panel_id,
        substrate_type=r.substrate_type,
        calculated_at=r.calculated_at,
        defect_count=r.defect_count,
        zone_fractions=r.zone_fractions,
        matches=[SignatureMatchResponse(**m.__dict__) for m in r.matches],
        top_match=SignatureMatchResponse(**r.top_match.__dict__) if r.top_match else None,
    )
