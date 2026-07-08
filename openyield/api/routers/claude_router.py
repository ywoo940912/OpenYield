"""
api/routers/claude_router.py
-----------------------------
Author: Yeonkuk Woo

Claude-powered AI analysis endpoints for semiconductor defect intelligence.
Calls Anthropic's claude-opus-4-8 with adaptive thinking to produce
expert-level root cause analysis and yield reports over inspection data.

Set ANTHROPIC_API_KEY in your environment (or Railway variables) before use.
"""

from __future__ import annotations
import os
from typing import Any
import anthropic
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from openyield.api.dependencies import get_db
from openyield.db.connection import get_placeholder

router = APIRouter(prefix="/claude", tags=["claude-ai"])
Connection = Any

_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))


class DefectAnalysisResponse(BaseModel):
    defect_id:   int
    defect_type: str
    analysis:    str
    model:       str


class YieldReportResponse(BaseModel):
    lot_id:  str
    report:  str
    model:   str


@router.get("/analyze/{defect_id}", response_model=DefectAnalysisResponse)
def analyze_defect(defect_id: int, conn: Connection = Depends(get_db)):
    """
    Use Claude to generate a root-cause analysis for a specific defect.
    Returns a natural-language explanation suitable for yield engineers.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY not configured")

    ph = get_placeholder(conn)
    row = conn.execute(
        f"""SELECT d.defect_id, d.defect_type, d.size, d.confidence_score,
                   d.component_row, d.component_col,
                   p.substrate_type, p.lot_id
            FROM defects d
            JOIN panels p ON p.panel_id = d.panel_id
            WHERE d.defect_id = {ph}""",
        (defect_id,)
    ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail=f"Defect {defect_id} not found")

    prompt = f"""You are an expert semiconductor yield engineer analyzing AOI (automated optical inspection) data.

Defect record:
- Type: {row["defect_type"]}
- Physical size: {row["size"]:.4f} mm
- Detection confidence: {row["confidence_score"]:.2%}
- Location on panel: row {row["component_row"]}, column {row["component_col"]}
- Substrate: {row["substrate_type"]}
- Lot ID: {row["lot_id"] or "unknown"}

Provide a concise analysis (under 150 words) covering:
1. Most likely root cause in the manufacturing process
2. Whether this defect is systematic (process issue) or random (e.g. particle contamination)
3. Recommended corrective action for the fab team"""

    message = _client.messages.create(
        model="claude-opus-4-8",
        max_tokens=512,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": prompt}],
    )

    analysis_text = next(
        (b.text for b in message.content if b.type == "text"), ""
    )

    return DefectAnalysisResponse(
        defect_id=defect_id,
        defect_type=row["defect_type"],
        analysis=analysis_text,
        model=message.model,
    )


@router.get("/yield-report/{lot_id}", response_model=YieldReportResponse)
def yield_report(lot_id: str, conn: Connection = Depends(get_db)):
    """
    Use Claude to write a human-readable yield analysis report for a lot.
    Aggregates defect statistics from the database and sends them to Claude.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY not configured")

    ph = get_placeholder(conn)

    lot_row = conn.execute(
        f"""SELECT COUNT(DISTINCT panel_id) AS n_panels, substrate_type
            FROM panels WHERE lot_id = {ph}""",
        (lot_id,)
    ).fetchone()

    if lot_row is None or lot_row["n_panels"] == 0:
        raise HTTPException(status_code=404, detail=f"Lot {lot_id!r} not found")

    stats = conn.execute(
        f"""SELECT d.defect_type,
                   COUNT(*)               AS count,
                   AVG(d.size)            AS avg_size,
                   AVG(d.confidence_score) AS avg_conf
            FROM defects d
            JOIN panels p ON p.panel_id = d.panel_id
            WHERE p.lot_id = {ph} AND d.source_system = 'system_a'
            GROUP BY d.defect_type
            ORDER BY count DESC""",
        (lot_id,)
    ).fetchall()

    defect_summary = "\n".join(
        f"  - {r['defect_type']}: {r['count']} defects, avg size {r['avg_size']:.4f} mm"
        for r in stats
    ) if stats else "  No defects recorded."

    prompt = f"""You are a semiconductor yield analyst writing an engineering report.

Lot ID: {lot_id}
Substrate: {lot_row['substrate_type']}
Panels inspected: {lot_row['n_panels']}

Defect breakdown by type:
{defect_summary}

Write a concise yield engineering report (under 200 words) that:
1. Summarizes the overall defect picture for this lot
2. Identifies the dominant defect type and its most likely process origin
3. Flags any concerning patterns (e.g. multiple defect types suggesting a systemic issue)
4. Provides a clear recommendation for the process engineering team"""

    message = _client.messages.create(
        model="claude-opus-4-8",
        max_tokens=600,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": prompt}],
    )

    report_text = next(
        (b.text for b in message.content if b.type == "text"), ""
    )

    return YieldReportResponse(
        lot_id=lot_id,
        report=report_text,
        model=message.model,
    )
