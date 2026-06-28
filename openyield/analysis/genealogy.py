"""
analysis/genealogy.py
----------------------
Author: Yeonkuk Woo

Lot genealogy tracking for semiconductor manufacturing process chains.

In semiconductor manufacturing, a *lot* is a batch of substrates (wafers or
glass panels) processed together through a sequence of steps.  Lots evolve
over time: they are split, merged, reworked, or converted into different
substrate types.  Tracing this lineage — *lot genealogy* — is mandatory for:

  - Root-cause analysis: a yield excursion in a child lot can be propagated
    back to its parent lots to isolate the originating process step.
  - Yield correlation: correlate yield at different steps to quantify the
    impact of each process operation.
  - Compliance: SEMI E40 (Process Management) and SEMI E30 (GEM) both require
    lot genealogy as part of a conformant MES interface.

This module implements:

  Database tables
  ---------------
  lot_nodes  : one row per lot — substrate type, process step, lot size,
               creation timestamp, arbitrary JSON metadata.
  lot_edges  : directed parent→child relationship with a relation_type and
               optional notes.

  Relation types
  --------------
  split   : one lot → several child lots (lot downsize, sampling split)
  merge   : several parent lots → one child lot (lot consolidation)
  rework  : lot re-processed; original becomes the parent
  convert : substrate conversion (wafer dice → chip lots, glass panel → die)
  inspect : inspection generates a derived analytical lot record

  Core algorithms
  ---------------
  get_ancestors()        : BFS up the DAG from a lot to its root(s)
  get_descendants()      : BFS down the DAG to all leaves
  get_lineage()          : Full LotLineage object (ancestors + descendants)
  build_adjacency()      : In-memory adjacency list of the full graph
  detect_cycles()        : Kahn's algorithm — returns lots forming a cycle
  compute_yield_correlation() : Pearson r between per-panel yields of two lots

References
----------
[1] SEMI E40-0308, "Standard for Processing Management" (lot genealogy §6).
[2] SEMI E30-0618, "Generic Model for Communications and Control of
    SEMI Equipment (GEM)" (lot attributes and genealogy events).
[3] B. T. Murphy, "Cost-size optima of monolithic integrated circuits,"
    Proc. IEEE, 52(12):1537–1545, 1964.
"""

from __future__ import annotations

import json
import logging
import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from openyield.db.connection import get_placeholder

logger = logging.getLogger(__name__)

Connection = Any

VALID_RELATION_TYPES = frozenset({"split", "merge", "rework", "convert", "inspect"})


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------

def initialize_genealogy_schema(conn: Connection) -> None:
    """
    Create lot_nodes and lot_edges tables if they do not exist.

    Called automatically by every public function.  Safe to call multiple
    times (uses IF NOT EXISTS).
    """
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lot_nodes (
                lot_id          TEXT PRIMARY KEY,
                substrate_type  TEXT NOT NULL,
                process_step    TEXT NOT NULL DEFAULT '',
                lot_size        INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT NOT NULL,
                metadata_json   TEXT NOT NULL DEFAULT '{}'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lot_edges (
                parent_lot_id   TEXT NOT NULL,
                child_lot_id    TEXT NOT NULL,
                relation_type   TEXT NOT NULL,
                timestamp       TEXT NOT NULL,
                notes           TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (parent_lot_id, child_lot_id)
            )
        """)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class LotNode:
    """
    A single lot in the genealogy graph.

    Attributes
    ----------
    lot_id         : Unique lot identifier (matches panels.lot_id).
    substrate_type : "wafer", "glass_panel", "ingot", "chip", etc.
    process_step   : Last completed process step (e.g. "LITHO", "CMP").
    lot_size       : Number of substrates in the lot.
    created_at     : ISO-8601 timestamp when the lot was created.
    metadata       : Arbitrary key-value pairs (equipment ID, recipe, etc.).
    """
    lot_id:         str
    substrate_type: str
    process_step:   str  = ""
    lot_size:       int  = 0
    created_at:     str  = ""
    metadata:       dict = field(default_factory=dict)


@dataclass
class GenealogyEdge:
    """
    A directed parent → child relationship between two lots.

    Attributes
    ----------
    parent_lot_id : Source lot.
    child_lot_id  : Derived lot.
    relation_type : One of VALID_RELATION_TYPES.
    timestamp     : ISO-8601 when the relationship was established.
    notes         : Free-text annotation (equipment, operator, etc.).
    """
    parent_lot_id: str
    child_lot_id:  str
    relation_type: str
    timestamp:     str  = ""
    notes:         str  = ""


@dataclass
class LotLineage:
    """
    Full genealogy context for a single lot.

    Attributes
    ----------
    lot_id      : Focal lot.
    ancestors   : Ordered list from root(s) to the focal lot's direct parents.
    descendants : Ordered list from the focal lot's direct children to leaves.
    edges       : All edges traversed in both directions.
    depth       : Longest ancestral path length (0 = root lot).
    """
    lot_id:      str
    ancestors:   list[LotNode]
    descendants: list[LotNode]
    edges:       list[GenealogyEdge]
    depth:       int


@dataclass
class YieldCorrelation:
    """
    Pearson correlation between panel yields of two lots.

    Attributes
    ----------
    ancestor_lot_id    : The upstream lot.
    descendant_lot_id  : The downstream lot.
    model              : Yield model used ("poisson", "murphy", "negbinom").
    pearson_r          : Correlation coefficient ∈ [−1, 1].
    n_panels           : Number of panel pairs used.
    mean_ancestor_yield  : Mean yield in ancestor lot.
    mean_descendant_yield: Mean yield in descendant lot.
    """
    ancestor_lot_id:       str
    descendant_lot_id:     str
    model:                 str
    pearson_r:             float
    n_panels:              int
    mean_ancestor_yield:   float
    mean_descendant_yield: float


# ---------------------------------------------------------------------------
# Node / edge write operations
# ---------------------------------------------------------------------------

def upsert_lot_node(conn: Connection, node: LotNode) -> None:
    """
    Insert or replace a lot node in lot_nodes.

    Parameters
    ----------
    conn : Database connection.
    node : LotNode to persist.
    """
    initialize_genealogy_schema(conn)
    if not node.lot_id:
        raise ValueError("lot_id must not be empty")
    created_at = node.created_at or datetime.now(timezone.utc).isoformat()
    ph = get_placeholder(conn)
    with conn:
        conn.execute(
            f"INSERT OR REPLACE INTO lot_nodes "
            f"(lot_id, substrate_type, process_step, lot_size, created_at, metadata_json) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph})",
            (
                node.lot_id,
                node.substrate_type,
                node.process_step,
                node.lot_size,
                created_at,
                json.dumps(node.metadata),
            ),
        )


def add_genealogy_edge(conn: Connection, edge: GenealogyEdge) -> None:
    """
    Record a parent → child relationship in lot_edges.

    Parameters
    ----------
    conn : Database connection.
    edge : GenealogyEdge to persist.

    Raises
    ------
    ValueError : Invalid relation_type or self-loop.
    """
    initialize_genealogy_schema(conn)
    if edge.relation_type not in VALID_RELATION_TYPES:
        raise ValueError(
            f"Invalid relation_type {edge.relation_type!r}. "
            f"Must be one of {sorted(VALID_RELATION_TYPES)}"
        )
    if edge.parent_lot_id == edge.child_lot_id:
        raise ValueError(
            f"Self-loop not allowed: parent and child both = {edge.parent_lot_id!r}"
        )
    ts = edge.timestamp or datetime.now(timezone.utc).isoformat()
    ph = get_placeholder(conn)
    with conn:
        conn.execute(
            f"INSERT OR REPLACE INTO lot_edges "
            f"(parent_lot_id, child_lot_id, relation_type, timestamp, notes) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph})",
            (
                edge.parent_lot_id,
                edge.child_lot_id,
                edge.relation_type,
                ts,
                edge.notes,
            ),
        )


# ---------------------------------------------------------------------------
# Node read operations
# ---------------------------------------------------------------------------

def get_lot_node(conn: Connection, lot_id: str) -> LotNode | None:
    """Return the LotNode for a given lot_id, or None if not found."""
    initialize_genealogy_schema(conn)
    ph = get_placeholder(conn)
    row = conn.execute(
        f"SELECT * FROM lot_nodes WHERE lot_id = {ph}", (lot_id,)
    ).fetchone()
    if row is None:
        return None
    return LotNode(
        lot_id=row["lot_id"],
        substrate_type=row["substrate_type"],
        process_step=row["process_step"],
        lot_size=row["lot_size"],
        created_at=row["created_at"],
        metadata=json.loads(row["metadata_json"]),
    )


def _get_parents(conn: Connection, lot_id: str) -> list[str]:
    """Return parent lot IDs for a given lot."""
    ph = get_placeholder(conn)
    rows = conn.execute(
        f"SELECT parent_lot_id FROM lot_edges WHERE child_lot_id = {ph}",
        (lot_id,),
    ).fetchall()
    return [r["parent_lot_id"] for r in rows]


def _get_children(conn: Connection, lot_id: str) -> list[str]:
    """Return child lot IDs for a given lot."""
    ph = get_placeholder(conn)
    rows = conn.execute(
        f"SELECT child_lot_id FROM lot_edges WHERE parent_lot_id = {ph}",
        (lot_id,),
    ).fetchall()
    return [r["child_lot_id"] for r in rows]


def _get_all_edges(conn: Connection) -> list[GenealogyEdge]:
    """Load every edge from lot_edges."""
    rows = conn.execute(
        "SELECT parent_lot_id, child_lot_id, relation_type, timestamp, notes "
        "FROM lot_edges"
    ).fetchall()
    return [
        GenealogyEdge(
            parent_lot_id=r["parent_lot_id"],
            child_lot_id=r["child_lot_id"],
            relation_type=r["relation_type"],
            timestamp=r["timestamp"],
            notes=r["notes"],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Traversal algorithms
# ---------------------------------------------------------------------------

def get_ancestors(
    conn: Connection,
    lot_id: str,
    *,
    max_depth: int | None = None,
) -> list[LotNode]:
    """
    Return all ancestor lots in breadth-first order (closest first).

    BFS up the parent links.  The focal lot itself is not included.

    Parameters
    ----------
    conn      : Database connection.
    lot_id    : Starting lot.
    max_depth : Maximum number of hops to traverse (None = unlimited).

    Returns
    -------
    list[LotNode] — unique ancestors, BFS order (closest first).
    """
    initialize_genealogy_schema(conn)
    visited: set[str] = {lot_id}
    queue: deque[tuple[str, int]] = deque([(lot_id, 0)])
    result: list[LotNode] = []

    while queue:
        current, depth = queue.popleft()
        if max_depth is not None and depth >= max_depth:
            continue
        for parent_id in _get_parents(conn, current):
            if parent_id not in visited:
                visited.add(parent_id)
                node = get_lot_node(conn, parent_id)
                if node is not None:
                    result.append(node)
                queue.append((parent_id, depth + 1))

    return result


def get_descendants(
    conn: Connection,
    lot_id: str,
    *,
    max_depth: int | None = None,
) -> list[LotNode]:
    """
    Return all descendant lots in breadth-first order (closest first).

    BFS down the child links.  The focal lot itself is not included.

    Parameters
    ----------
    conn      : Database connection.
    lot_id    : Starting lot.
    max_depth : Maximum number of hops to traverse (None = unlimited).

    Returns
    -------
    list[LotNode] — unique descendants, BFS order (closest first).
    """
    initialize_genealogy_schema(conn)
    visited: set[str] = {lot_id}
    queue: deque[tuple[str, int]] = deque([(lot_id, 0)])
    result: list[LotNode] = []

    while queue:
        current, depth = queue.popleft()
        if max_depth is not None and depth >= max_depth:
            continue
        for child_id in _get_children(conn, current):
            if child_id not in visited:
                visited.add(child_id)
                node = get_lot_node(conn, child_id)
                if node is not None:
                    result.append(node)
                queue.append((child_id, depth + 1))

    return result


def _compute_depth(conn: Connection, lot_id: str) -> int:
    """Longest ancestral path length (0 for root lots)."""
    parents = _get_parents(conn, lot_id)
    if not parents:
        return 0
    return 1 + max(_compute_depth(conn, p) for p in parents)


def get_lineage(conn: Connection, lot_id: str) -> LotLineage:
    """
    Build the complete lineage for a lot.

    Returns a LotLineage containing ancestor nodes, descendant nodes,
    all traversed edges, and the depth from the root.

    Raises
    ------
    ValueError : If lot_id is not found in lot_nodes.
    """
    initialize_genealogy_schema(conn)
    if get_lot_node(conn, lot_id) is None:
        raise ValueError(f"Lot not found: {lot_id!r}")

    ancestors   = get_ancestors(conn, lot_id)
    descendants = get_descendants(conn, lot_id)

    ancestor_ids   = {n.lot_id for n in ancestors}   | {lot_id}
    descendant_ids = {n.lot_id for n in descendants} | {lot_id}
    all_ids = ancestor_ids | descendant_ids

    all_edges = _get_all_edges(conn)
    relevant_edges = [
        e for e in all_edges
        if e.parent_lot_id in all_ids and e.child_lot_id in all_ids
    ]
    depth = _compute_depth(conn, lot_id)

    return LotLineage(
        lot_id=lot_id,
        ancestors=ancestors,
        descendants=descendants,
        edges=relevant_edges,
        depth=depth,
    )


# ---------------------------------------------------------------------------
# Graph-level operations
# ---------------------------------------------------------------------------

def build_adjacency(conn: Connection) -> dict[str, list[str]]:
    """
    Return the full parent → children adjacency list for all lots.

    Returns
    -------
    dict[str, list[str]] — maps each lot_id to its list of child lot_ids.
    Lots with no children appear as empty lists.
    """
    initialize_genealogy_schema(conn)
    rows = conn.execute("SELECT lot_id FROM lot_nodes").fetchall()
    adj: dict[str, list[str]] = {r["lot_id"]: [] for r in rows}
    for edge in _get_all_edges(conn):
        if edge.parent_lot_id in adj:
            adj[edge.parent_lot_id].append(edge.child_lot_id)
    return adj


def detect_cycles(conn: Connection) -> list[str]:
    """
    Detect cycles in the lot genealogy DAG using Kahn's topological sort.

    A valid genealogy is a DAG (directed acyclic graph).  If any lot forms
    part of a cycle — which would indicate a data integrity error — it is
    returned in the result list.

    Returns
    -------
    list[str] — lot IDs participating in at least one cycle.
                Empty list = no cycles (DAG is valid).
    """
    initialize_genealogy_schema(conn)
    all_edges = _get_all_edges(conn)
    rows      = conn.execute("SELECT lot_id FROM lot_nodes").fetchall()
    all_lots  = {r["lot_id"] for r in rows}

    in_degree: dict[str, int] = {lid: 0 for lid in all_lots}
    children:  dict[str, list[str]] = {lid: [] for lid in all_lots}

    for e in all_edges:
        if e.parent_lot_id in all_lots and e.child_lot_id in all_lots:
            in_degree[e.child_lot_id] += 1
            children[e.parent_lot_id].append(e.child_lot_id)

    # Kahn: start with all zero-in-degree nodes
    queue: deque[str] = deque(
        lid for lid, deg in in_degree.items() if deg == 0
    )
    processed = 0
    while queue:
        node = queue.popleft()
        processed += 1
        for child in children[node]:
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)

    if processed == len(all_lots):
        return []   # No cycles

    return [lid for lid, deg in in_degree.items() if deg > 0]


# ---------------------------------------------------------------------------
# Yield correlation
# ---------------------------------------------------------------------------

def _pearson_r(xs: list[float], ys: list[float]) -> float:
    """
    Compute Pearson correlation coefficient between two equal-length lists.

    Returns 0.0 if std of either series is zero (constant yield = undefined r).
    """
    n = len(xs)
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / n
    sx  = math.sqrt(sum((x - mx) ** 2 for x in xs) / n)
    sy  = math.sqrt(sum((y - my) ** 2 for y in ys) / n)
    if sx < 1e-12 or sy < 1e-12:
        return 0.0
    return cov / (sx * sy)


def _lot_panel_yields(
    conn: Connection,
    lot_id: str,
    model: str,
) -> dict[str, float]:
    """
    Return {panel_id: yield_value} for all panels in a lot.

    Reads pre-computed yield estimates from the yield_estimates table.
    Only panels whose lot_id matches are included.

    Parameters
    ----------
    model : "poisson", "murphy", or "negbinom"
    """
    col_map = {
        "poisson":  "yield_poisson",
        "murphy":   "yield_murphy",
        "negbinom": "yield_negbinom",
    }
    if model not in col_map:
        raise ValueError(
            f"Unknown yield model {model!r}. "
            f"Choose from {list(col_map.keys())}"
        )
    col = col_map[model]
    ph = get_placeholder(conn)

    rows = conn.execute(
        f"SELECT ye.panel_id, ye.{col} AS y "
        f"FROM yield_estimates ye "
        f"JOIN panels p ON ye.panel_id = p.panel_id "
        f"WHERE p.lot_id = {ph}",
        (lot_id,),
    ).fetchall()
    return {r["panel_id"]: float(r["y"]) for r in rows}


def compute_yield_correlation(
    conn: Connection,
    ancestor_lot_id: str,
    descendant_lot_id: str,
    *,
    model: str = "negbinom",
) -> YieldCorrelation:
    """
    Compute Pearson correlation between per-panel yields of two lots.

    Correlation is computed over panels that appear in the yield_estimates
    table for both lots.  A high positive correlation indicates that
    within-lot spatial yield patterns propagate from ancestor to descendant
    — a common signature of upstream process contamination.

    Parameters
    ----------
    conn               : Database connection.
    ancestor_lot_id    : Upstream lot (e.g. a wafer fabrication lot).
    descendant_lot_id  : Downstream lot (e.g. an assembly lot).
    model              : Yield model to use for correlation.

    Returns
    -------
    YieldCorrelation

    Raises
    ------
    ValueError : If either lot has no yield data, or no common panels.
    """
    initialize_genealogy_schema(conn)

    anc_yields = _lot_panel_yields(conn, ancestor_lot_id, model)
    desc_yields = _lot_panel_yields(conn, descendant_lot_id, model)

    common = sorted(set(anc_yields) & set(desc_yields))
    if not common:
        raise ValueError(
            f"No common panels with yield data between "
            f"{ancestor_lot_id!r} and {descendant_lot_id!r}."
        )

    anc_vals  = [anc_yields[pid]  for pid in common]
    desc_vals = [desc_yields[pid] for pid in common]

    r = _pearson_r(anc_vals, desc_vals)
    n = len(common)
    mean_anc  = sum(anc_vals)  / n
    mean_desc = sum(desc_vals) / n

    logger.info(
        "Yield correlation [%s] %s → %s  r=%.3f  n=%d  "
        "mean_anc=%.1f%%  mean_desc=%.1f%%",
        model, ancestor_lot_id, descendant_lot_id,
        r, n, mean_anc * 100, mean_desc * 100,
    )
    return YieldCorrelation(
        ancestor_lot_id=ancestor_lot_id,
        descendant_lot_id=descendant_lot_id,
        model=model,
        pearson_r=round(r, 6),
        n_panels=n,
        mean_ancestor_yield=round(mean_anc, 6),
        mean_descendant_yield=round(mean_desc, 6),
    )
