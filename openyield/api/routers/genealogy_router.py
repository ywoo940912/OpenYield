"""api/routers/genealogy_router.py — Lot genealogy endpoints."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from openyield.api.dependencies import get_db
from openyield.analysis.genealogy import (
    LotNode, GenealogyEdge,
    get_lineage, get_lot_node, detect_cycles,
    initialize_genealogy_schema,
    upsert_lot_node, add_genealogy_edge,
    VALID_RELATION_TYPES,
)

router = APIRouter(prefix="/genealogy", tags=["genealogy"])
Connection = Any


class LotNodeResponse(BaseModel):
    lot_id: str
    substrate_type: str
    process_step: str
    lot_size: int
    created_at: str


class EdgeResponse(BaseModel):
    parent_lot_id: str
    child_lot_id: str
    relation_type: str
    timestamp: str
    notes: str


class LineageResponse(BaseModel):
    lot_id: str
    ancestors: list[LotNodeResponse]
    descendants: list[LotNodeResponse]
    edges: list[EdgeResponse]
    depth: int


@router.get("/{lot_id}/lineage", response_model=LineageResponse)
def get_lot_lineage(lot_id: str, conn: Connection = Depends(get_db)):
    """Return full ancestor + descendant lineage for a lot."""
    initialize_genealogy_schema(conn)
    try:
        lineage = get_lineage(conn, lot_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    def _node(n) -> LotNodeResponse:
        return LotNodeResponse(
            lot_id=n.lot_id,
            substrate_type=n.substrate_type,
            process_step=n.process_step,
            lot_size=n.lot_size,
            created_at=n.created_at,
        )

    return LineageResponse(
        lot_id=lineage.lot_id,
        ancestors=[_node(n) for n in lineage.ancestors],
        descendants=[_node(n) for n in lineage.descendants],
        edges=[
            EdgeResponse(
                parent_lot_id=e.parent_lot_id,
                child_lot_id=e.child_lot_id,
                relation_type=e.relation_type,
                timestamp=e.timestamp,
                notes=e.notes,
            )
            for e in lineage.edges
        ],
        depth=lineage.depth,
    )


@router.get("/cycles", response_model=list[str])
def get_cycles(conn: Connection = Depends(get_db)):
    """Return lot IDs involved in genealogy cycles (empty = valid DAG)."""
    initialize_genealogy_schema(conn)
    return detect_cycles(conn)


# ---------------------------------------------------------------------------
# Write endpoints
# ---------------------------------------------------------------------------

class CreateLotRequest(BaseModel):
    lot_id: str
    substrate_type: str = "wafer"
    process_step: str = ""
    lot_size: int = 0
    metadata: dict = {}


class CreateEdgeRequest(BaseModel):
    parent_lot_id: str
    child_lot_id: str
    relation_type: str
    notes: str = ""


@router.post("/lots", response_model=LotNodeResponse, status_code=201)
def create_lot(body: CreateLotRequest, conn: Connection = Depends(get_db)):
    """
    Create or update a lot node in the genealogy graph.

    Idempotent — upserting the same lot_id overwrites metadata fields.
    """
    initialize_genealogy_schema(conn)
    node = LotNode(
        lot_id=body.lot_id,
        substrate_type=body.substrate_type,
        process_step=body.process_step,
        lot_size=body.lot_size,
        created_at=datetime.now(timezone.utc).isoformat(),
        metadata=body.metadata,
    )
    upsert_lot_node(conn, node)
    return LotNodeResponse(
        lot_id=node.lot_id,
        substrate_type=node.substrate_type,
        process_step=node.process_step,
        lot_size=node.lot_size,
        created_at=node.created_at,
    )


@router.post("/edges", status_code=201)
def create_edge(body: CreateEdgeRequest, conn: Connection = Depends(get_db)):
    """
    Add a directed parent → child relationship between two lot nodes.

    Both lots must already exist (call POST /genealogy/lots first).
    Valid relation_type values: split, merge, rework, convert, inspect.
    """
    initialize_genealogy_schema(conn)

    if body.relation_type not in VALID_RELATION_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid relation_type {body.relation_type!r}. "
                   f"Must be one of: {sorted(VALID_RELATION_TYPES)}",
        )

    for lot_id in (body.parent_lot_id, body.child_lot_id):
        if get_lot_node(conn, lot_id) is None:
            raise HTTPException(
                status_code=404,
                detail=f"Lot {lot_id!r} not found. "
                       "Create it with POST /genealogy/lots first.",
            )

    edge = GenealogyEdge(
        parent_lot_id=body.parent_lot_id,
        child_lot_id=body.child_lot_id,
        relation_type=body.relation_type,
        notes=body.notes,
    )
    try:
        add_genealogy_edge(conn, edge)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return {
        "parent_lot_id": body.parent_lot_id,
        "child_lot_id":  body.child_lot_id,
        "relation_type": body.relation_type,
        "notes":         body.notes,
    }
