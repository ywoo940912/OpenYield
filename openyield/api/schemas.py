"""
api/schemas.py
--------------
Author: Yeonkuk Woo

Pydantic response and request models for the OpenYield REST API.
"""

from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field


class PanelResponse(BaseModel):
    panel_id:       str
    product_type:   str
    substrate_type: str
    rows:           int
    cols:           int
    lot_id:         str | None = None
    created_at:     str

class PanelListResponse(BaseModel):
    total:   int
    page:    int
    limit:   int
    results: list[PanelResponse]

class ComponentResponse(BaseModel):
    panel_id:      str
    component_row: int
    component_col: int
    region_id:     str
    center_x:      float
    center_y:      float
    active:        bool

class DefectResponse(BaseModel):
    defect_id:        int
    panel_id:         str
    component_row:    int
    component_col:    int
    source_system:    str
    defect_type:      str
    x:                float
    y:                float
    size:             float
    confidence_score: float
    match_id:         str | None
    created_at:       str

class DefectListResponse(BaseModel):
    total:   int
    page:    int
    limit:   int
    results: list[DefectResponse]

class YieldResponse(BaseModel):
    panel_id:          str
    substrate_type:    str
    calculated_at:     str
    die_area_mm2:      float
    inspected_dies:    int
    defect_count:      int
    defect_density:    float = Field(description="Defects per mm²")
    yield_poisson:     float = Field(description="Poisson model [0-1]")
    yield_murphy:      float = Field(description="Murphy model [0-1]")
    yield_negbinom:    float = Field(description="Negative binomial [0-1]")
    clustering_alpha:  float
    alpha_method:      str
    recommended_model: str
    model_notes:       str

class CriticalAreaResponse(BaseModel):
    panel_id:             str
    ca_fraction:          float = Field(description="Mean critical area fraction in [0, 1]")
    layout_density:       float = Field(description="Fraction of die area with killable features")
    min_feature_mm:       float = Field(description="Minimum critical feature dimension (mm)")
    effective_area_mm2:   float = Field(description="CA-corrected die area used in yield models")
    full_die_area_mm2:    float = Field(description="Full die area (pitch²)")
    n_defects:            int   = Field(description="Defect sizes sampled from system_a")
    mean_defect_size_mm:  float = Field(description="Mean observed defect size (mm)")
    method:               str   = Field(description="CA model — 'maly_linear'")

class CheckResult(BaseModel):
    check_name: str
    passed:     bool
    metric:     float | int | None
    detail:     str

class ValidationResponse(BaseModel):
    passed:     int
    total:      int
    all_passed: bool
    results:    list[CheckResult]

class IngestResponse(BaseModel):
    file_name:        str
    records_ingested: int
    status:           str
    message:          str

class HealthResponse(BaseModel):
    status:       str
    backend:      str
    panel_count:  int
    defect_count: int
    db_path:      str | None
