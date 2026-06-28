"""
tests/test_genealogy.py
------------------------
Tests for analysis/genealogy.py — lot genealogy tracking.

Test organisation
-----------------
1. Schema initialisation (idempotent)
2. LotNode upsert / retrieval
3. GenealogyEdge insert / validation
4. Ancestor traversal
5. Descendant traversal
6. LotLineage assembly
7. Adjacency list / build_adjacency
8. Cycle detection (Kahn's algorithm)
9. Pearson correlation helper
10. Yield correlation (integration)
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone

import pytest

from openyield.analysis.genealogy import (
    LotNode,
    GenealogyEdge,
    LotLineage,
    YieldCorrelation,
    initialize_genealogy_schema,
    upsert_lot_node,
    add_genealogy_edge,
    get_lot_node,
    get_ancestors,
    get_descendants,
    get_lineage,
    build_adjacency,
    detect_cycles,
    compute_yield_correlation,
    _pearson_r,
    VALID_RELATION_TYPES,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def conn(tmp_path):
    from openyield.db.connection import get_connection
    from openyield.db.schema import initialize_schema
    c = get_connection(tmp_path / "test.db")
    initialize_schema(c)
    initialize_genealogy_schema(c)
    return c


def _node(lot_id: str, substrate="wafer", step="LI01", size=25) -> LotNode:
    return LotNode(
        lot_id=lot_id,
        substrate_type=substrate,
        process_step=step,
        lot_size=size,
        created_at=datetime.now(timezone.utc).isoformat(),
        metadata={},
    )


def _edge(
    parent: str, child: str,
    relation: str = "split",
    notes: str = "",
) -> GenealogyEdge:
    return GenealogyEdge(
        parent_lot_id=parent,
        child_lot_id=child,
        relation_type=relation,
        timestamp=datetime.now(timezone.utc).isoformat(),
        notes=notes,
    )


# ---------------------------------------------------------------------------
# 1. Schema initialisation
# ---------------------------------------------------------------------------

class TestSchemaInit:
    def test_tables_created(self, conn):
        tables = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "lot_nodes" in tables
        assert "lot_edges" in tables

    def test_idempotent_double_init(self, conn):
        """Calling initialize_genealogy_schema twice must not raise."""
        initialize_genealogy_schema(conn)
        initialize_genealogy_schema(conn)


# ---------------------------------------------------------------------------
# 2. LotNode upsert / retrieval
# ---------------------------------------------------------------------------

class TestLotNode:
    def test_insert_and_get(self, conn):
        upsert_lot_node(conn, _node("L01"))
        node = get_lot_node(conn, "L01")
        assert node is not None
        assert node.lot_id == "L01"

    def test_substrate_type_preserved(self, conn):
        upsert_lot_node(conn, _node("L02", substrate="glass_panel"))
        assert get_lot_node(conn, "L02").substrate_type == "glass_panel"

    def test_process_step_preserved(self, conn):
        upsert_lot_node(conn, _node("L03", step="CMP"))
        assert get_lot_node(conn, "L03").process_step == "CMP"

    def test_lot_size_preserved(self, conn):
        upsert_lot_node(conn, _node("L04", size=50))
        assert get_lot_node(conn, "L04").lot_size == 50

    def test_metadata_round_trips(self, conn):
        node = LotNode(
            lot_id="L05", substrate_type="wafer",
            metadata={"recipe": "ETCH_V2", "tool": "ET500"},
        )
        upsert_lot_node(conn, node)
        loaded = get_lot_node(conn, "L05")
        assert loaded.metadata["recipe"] == "ETCH_V2"
        assert loaded.metadata["tool"]   == "ET500"

    def test_get_nonexistent_returns_none(self, conn):
        assert get_lot_node(conn, "DOES_NOT_EXIST") is None

    def test_upsert_updates_existing(self, conn):
        upsert_lot_node(conn, _node("L06", step="LI01"))
        upsert_lot_node(conn, _node("L06", step="ETCH"))
        assert get_lot_node(conn, "L06").process_step == "ETCH"

    def test_empty_lot_id_raises(self, conn):
        with pytest.raises(ValueError, match="lot_id"):
            upsert_lot_node(conn, LotNode(lot_id="", substrate_type="wafer"))

    def test_created_at_auto_filled(self, conn):
        """If created_at is empty, the module fills it with current UTC time."""
        node = LotNode(lot_id="L07", substrate_type="wafer", created_at="")
        upsert_lot_node(conn, node)
        loaded = get_lot_node(conn, "L07")
        assert loaded.created_at != ""


# ---------------------------------------------------------------------------
# 3. GenealogyEdge insert / validation
# ---------------------------------------------------------------------------

class TestEdge:
    def test_insert_and_query(self, conn):
        for lid in ("PA", "CA"):
            upsert_lot_node(conn, _node(lid))
        add_genealogy_edge(conn, _edge("PA", "CA", relation="split"))
        row = conn.execute(
            "SELECT * FROM lot_edges WHERE parent_lot_id='PA' AND child_lot_id='CA'"
        ).fetchone()
        assert row is not None

    def test_all_relation_types_valid(self, conn):
        for i, rel in enumerate(sorted(VALID_RELATION_TYPES)):
            p, c = f"P_{rel}", f"C_{rel}"
            upsert_lot_node(conn, _node(p))
            upsert_lot_node(conn, _node(c))
            add_genealogy_edge(conn, _edge(p, c, relation=rel))

    def test_invalid_relation_raises(self, conn):
        with pytest.raises(ValueError, match="relation_type"):
            add_genealogy_edge(conn, _edge("X", "Y", relation="teleport"))

    def test_self_loop_raises(self, conn):
        upsert_lot_node(conn, _node("SELF"))
        with pytest.raises(ValueError, match="[Ss]elf"):
            add_genealogy_edge(conn, _edge("SELF", "SELF"))

    def test_notes_preserved(self, conn):
        upsert_lot_node(conn, _node("PN"))
        upsert_lot_node(conn, _node("CN"))
        add_genealogy_edge(conn, _edge("PN", "CN", notes="Quarantine split"))
        row = conn.execute(
            "SELECT notes FROM lot_edges WHERE parent_lot_id='PN'"
        ).fetchone()
        assert row["notes"] == "Quarantine split"

    def test_duplicate_edge_is_idempotent(self, conn):
        upsert_lot_node(conn, _node("P2"))
        upsert_lot_node(conn, _node("C2"))
        add_genealogy_edge(conn, _edge("P2", "C2"))
        add_genealogy_edge(conn, _edge("P2", "C2"))
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM lot_edges WHERE parent_lot_id='P2'"
        ).fetchone()["n"]
        assert count == 1


# ---------------------------------------------------------------------------
# 4. Ancestor traversal
# ---------------------------------------------------------------------------

class TestAncestors:
    def _make_chain(self, conn, ids: list[str]) -> None:
        """L0 → L1 → L2 → ... linear chain."""
        for lid in ids:
            upsert_lot_node(conn, _node(lid))
        for parent, child in zip(ids[:-1], ids[1:]):
            add_genealogy_edge(conn, _edge(parent, child))

    def test_root_has_no_ancestors(self, conn):
        upsert_lot_node(conn, _node("ROOT"))
        assert get_ancestors(conn, "ROOT") == []

    def test_single_parent(self, conn):
        self._make_chain(conn, ["A", "B"])
        ancestors = get_ancestors(conn, "B")
        assert len(ancestors) == 1
        assert ancestors[0].lot_id == "A"

    def test_chain_of_three(self, conn):
        self._make_chain(conn, ["G0", "G1", "G2"])
        ancestors = get_ancestors(conn, "G2")
        ids = {n.lot_id for n in ancestors}
        assert ids == {"G0", "G1"}

    def test_max_depth_limits_traversal(self, conn):
        self._make_chain(conn, ["D0", "D1", "D2", "D3"])
        ancestors = get_ancestors(conn, "D3", max_depth=1)
        assert len(ancestors) == 1
        assert ancestors[0].lot_id == "D2"

    def test_diamond_ancestry(self, conn):
        """ROOT → A, ROOT → B; A → LEAF, B → LEAF (diamond graph)."""
        for lid in ("DROOT", "DA", "DB", "DLEAF"):
            upsert_lot_node(conn, _node(lid))
        add_genealogy_edge(conn, _edge("DROOT", "DA"))
        add_genealogy_edge(conn, _edge("DROOT", "DB"))
        add_genealogy_edge(conn, _edge("DA",    "DLEAF"))
        add_genealogy_edge(conn, _edge("DB",    "DLEAF"))
        ancestors = get_ancestors(conn, "DLEAF")
        ids = {n.lot_id for n in ancestors}
        # Should include DA, DB, DROOT exactly once each
        assert ids == {"DROOT", "DA", "DB"}
        assert len(ancestors) == 3  # no duplicates


# ---------------------------------------------------------------------------
# 5. Descendant traversal
# ---------------------------------------------------------------------------

class TestDescendants:
    def test_leaf_has_no_descendants(self, conn):
        upsert_lot_node(conn, _node("LEAF"))
        assert get_descendants(conn, "LEAF") == []

    def test_single_child(self, conn):
        for lid in ("PAR", "KID"):
            upsert_lot_node(conn, _node(lid))
        add_genealogy_edge(conn, _edge("PAR", "KID"))
        desc = get_descendants(conn, "PAR")
        assert len(desc) == 1
        assert desc[0].lot_id == "KID"

    def test_branching_tree(self, conn):
        """ROOT → (A, B); A → (C, D); B → E."""
        for lid in ("TR", "TA", "TB", "TC", "TD", "TE"):
            upsert_lot_node(conn, _node(lid))
        add_genealogy_edge(conn, _edge("TR", "TA"))
        add_genealogy_edge(conn, _edge("TR", "TB"))
        add_genealogy_edge(conn, _edge("TA", "TC"))
        add_genealogy_edge(conn, _edge("TA", "TD"))
        add_genealogy_edge(conn, _edge("TB", "TE"))
        desc = get_descendants(conn, "TR")
        ids = {n.lot_id for n in desc}
        assert ids == {"TA", "TB", "TC", "TD", "TE"}

    def test_max_depth_limits_descendants(self, conn):
        for lid in ("R", "M", "L"):
            upsert_lot_node(conn, _node(lid))
        add_genealogy_edge(conn, _edge("R", "M"))
        add_genealogy_edge(conn, _edge("M", "L"))
        desc = get_descendants(conn, "R", max_depth=1)
        assert len(desc) == 1
        assert desc[0].lot_id == "M"


# ---------------------------------------------------------------------------
# 6. LotLineage
# ---------------------------------------------------------------------------

class TestLotLineage:
    def _make_tree(self, conn):
        """ROOT → MID → LEAF."""
        for lid in ("ROOT", "MID", "LEAF"):
            upsert_lot_node(conn, _node(lid))
        add_genealogy_edge(conn, _edge("ROOT", "MID"))
        add_genealogy_edge(conn, _edge("MID",  "LEAF"))

    def test_get_lineage_returns_lineage(self, conn):
        self._make_tree(conn)
        lin = get_lineage(conn, "MID")
        assert isinstance(lin, LotLineage)

    def test_lineage_lot_id(self, conn):
        self._make_tree(conn)
        lin = get_lineage(conn, "MID")
        assert lin.lot_id == "MID"

    def test_lineage_ancestors(self, conn):
        self._make_tree(conn)
        lin = get_lineage(conn, "LEAF")
        ancestor_ids = {n.lot_id for n in lin.ancestors}
        assert "ROOT" in ancestor_ids
        assert "MID"  in ancestor_ids

    def test_lineage_descendants(self, conn):
        self._make_tree(conn)
        lin = get_lineage(conn, "ROOT")
        desc_ids = {n.lot_id for n in lin.descendants}
        assert "MID"  in desc_ids
        assert "LEAF" in desc_ids

    def test_lineage_depth(self, conn):
        self._make_tree(conn)
        assert get_lineage(conn, "ROOT").depth == 0
        assert get_lineage(conn, "MID").depth  == 1
        assert get_lineage(conn, "LEAF").depth == 2

    def test_lineage_edges_included(self, conn):
        self._make_tree(conn)
        lin = get_lineage(conn, "MID")
        edge_pairs = {(e.parent_lot_id, e.child_lot_id) for e in lin.edges}
        assert ("ROOT", "MID")  in edge_pairs
        assert ("MID",  "LEAF") in edge_pairs

    def test_nonexistent_lot_raises(self, conn):
        with pytest.raises(ValueError, match="not found"):
            get_lineage(conn, "GHOST")


# ---------------------------------------------------------------------------
# 7. Adjacency list
# ---------------------------------------------------------------------------

class TestAdjacency:
    def test_empty_graph(self, conn):
        adj = build_adjacency(conn)
        assert adj == {}

    def test_single_node_no_edges(self, conn):
        upsert_lot_node(conn, _node("ALONE"))
        adj = build_adjacency(conn)
        assert "ALONE" in adj
        assert adj["ALONE"] == []

    def test_edge_appears_in_adjacency(self, conn):
        upsert_lot_node(conn, _node("PA"))
        upsert_lot_node(conn, _node("CH"))
        add_genealogy_edge(conn, _edge("PA", "CH"))
        adj = build_adjacency(conn)
        assert "CH" in adj["PA"]

    def test_all_nodes_present(self, conn):
        for lid in ("N1", "N2", "N3"):
            upsert_lot_node(conn, _node(lid))
        add_genealogy_edge(conn, _edge("N1", "N2"))
        add_genealogy_edge(conn, _edge("N2", "N3"))
        adj = build_adjacency(conn)
        assert set(adj.keys()) == {"N1", "N2", "N3"}


# ---------------------------------------------------------------------------
# 8. Cycle detection
# ---------------------------------------------------------------------------

class TestCycleDetection:
    def test_empty_graph_no_cycles(self, conn):
        assert detect_cycles(conn) == []

    def test_linear_chain_no_cycles(self, conn):
        for lid in ("C1", "C2", "C3"):
            upsert_lot_node(conn, _node(lid))
        add_genealogy_edge(conn, _edge("C1", "C2"))
        add_genealogy_edge(conn, _edge("C2", "C3"))
        assert detect_cycles(conn) == []

    def test_diamond_no_cycles(self, conn):
        for lid in ("CR", "CA", "CB", "CL"):
            upsert_lot_node(conn, _node(lid))
        add_genealogy_edge(conn, _edge("CR", "CA"))
        add_genealogy_edge(conn, _edge("CR", "CB"))
        add_genealogy_edge(conn, _edge("CA", "CL"))
        add_genealogy_edge(conn, _edge("CB", "CL"))
        assert detect_cycles(conn) == []

    def test_simple_cycle_detected(self, conn):
        """A → B → A forms a 2-node cycle."""
        for lid in ("XA", "XB"):
            upsert_lot_node(conn, _node(lid))
        # Insert edges directly (bypassing self-loop guard) to test cycle detection
        conn.execute(
            "INSERT OR REPLACE INTO lot_edges "
            "(parent_lot_id, child_lot_id, relation_type, timestamp, notes) "
            "VALUES (?,?,?,?,?)",
            ("XA", "XB", "rework", "2025-01-01T00:00:00", ""),
        )
        conn.execute(
            "INSERT OR REPLACE INTO lot_edges "
            "(parent_lot_id, child_lot_id, relation_type, timestamp, notes) "
            "VALUES (?,?,?,?,?)",
            ("XB", "XA", "rework", "2025-01-01T00:00:01", ""),
        )
        conn.commit()
        cycled = detect_cycles(conn)
        assert set(cycled) == {"XA", "XB"}

    def test_three_node_cycle_detected(self, conn):
        """A → B → C → A forms a 3-node cycle."""
        for lid in ("YA", "YB", "YC"):
            upsert_lot_node(conn, _node(lid))
        for parent, child in [("YA", "YB"), ("YB", "YC"), ("YC", "YA")]:
            conn.execute(
                "INSERT OR REPLACE INTO lot_edges "
                "(parent_lot_id, child_lot_id, relation_type, timestamp, notes) "
                "VALUES (?,?,?,?,?)",
                (parent, child, "rework", "2025-01-01T00:00:00", ""),
            )
        conn.commit()
        cycled = detect_cycles(conn)
        assert set(cycled) == {"YA", "YB", "YC"}

    def test_partial_cycle(self, conn):
        """ROOT → A (no cycle); B → C → B (cycle). ROOT should NOT be in result."""
        for lid in ("GROOT", "GA", "GB", "GC"):
            upsert_lot_node(conn, _node(lid))
        add_genealogy_edge(conn, _edge("GROOT", "GA"))
        for parent, child in [("GB", "GC"), ("GC", "GB")]:
            conn.execute(
                "INSERT OR REPLACE INTO lot_edges "
                "(parent_lot_id, child_lot_id, relation_type, timestamp, notes) "
                "VALUES (?,?,?,?,?)",
                (parent, child, "rework", "2025-01-01T00:00:00", ""),
            )
        conn.commit()
        cycled = set(detect_cycles(conn))
        assert "GROOT" not in cycled
        assert "GA"    not in cycled
        assert cycled == {"GB", "GC"}


# ---------------------------------------------------------------------------
# 9. Pearson r helper
# ---------------------------------------------------------------------------

class TestPearsonR:
    def test_perfect_positive(self):
        xs = [1.0, 2.0, 3.0, 4.0]
        ys = [2.0, 4.0, 6.0, 8.0]
        assert _pearson_r(xs, ys) == pytest.approx(1.0, abs=1e-9)

    def test_perfect_negative(self):
        xs = [1.0, 2.0, 3.0, 4.0]
        ys = [4.0, 3.0, 2.0, 1.0]
        assert _pearson_r(xs, ys) == pytest.approx(-1.0, abs=1e-9)

    def test_zero_correlation(self):
        xs = [1.0, 2.0, 3.0, 4.0]
        ys = [1.0, 3.0, 1.0, 3.0]
        r = _pearson_r(xs, ys)
        assert abs(r) < 0.1

    def test_constant_y_returns_zero(self):
        """Undefined correlation → 0.0 by convention."""
        xs = [0.1, 0.2, 0.3]
        ys = [0.5, 0.5, 0.5]
        assert _pearson_r(xs, ys) == pytest.approx(0.0)

    def test_single_point_returns_zero(self):
        assert _pearson_r([1.0], [2.0]) == pytest.approx(0.0)

    def test_empty_returns_zero(self):
        assert _pearson_r([], []) == pytest.approx(0.0)

    def test_result_in_minus_one_to_one(self):
        import random
        rng = random.Random(42)
        xs = [rng.random() for _ in range(20)]
        ys = [rng.random() for _ in range(20)]
        r = _pearson_r(xs, ys)
        assert -1.0 <= r <= 1.0

    def test_known_value(self):
        """[0, 1, 2], [2, 4, 6] → r = 1.0."""
        assert _pearson_r([0.0, 1.0, 2.0], [2.0, 4.0, 6.0]) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 10. Yield correlation (integration)
# ---------------------------------------------------------------------------

class TestYieldCorrelation:
    def _insert_yield(self, conn, panel_id: str, lot_id: str, yield_val: float):
        """Insert a panel and a fake yield_estimate row."""
        conn.execute(
            "INSERT OR IGNORE INTO panels "
            "(panel_id, substrate_type, rows, cols, lot_id, "
            " component_pitch_mm, product_type) "
            "VALUES (?,?,?,?,?,?,?)",
            (panel_id, "wafer", 1, 1, lot_id, 28.0, "TEST"),
        )
        # yield_estimates may or may not exist in the schema; create it if not
        conn.execute("""
            CREATE TABLE IF NOT EXISTS yield_estimates (
                panel_id       TEXT PRIMARY KEY,
                yield_poisson  REAL,
                yield_murphy   REAL,
                yield_negbinom REAL
            )
        """)
        conn.execute(
            "INSERT OR REPLACE INTO yield_estimates "
            "(panel_id, yield_poisson, yield_murphy, yield_negbinom) "
            "VALUES (?,?,?,?)",
            (panel_id, yield_val, yield_val * 1.01, yield_val * 0.99),
        )
        conn.commit()

    def test_perfect_correlation(self, conn):
        for i in range(5):
            y = 0.5 + i * 0.1
            self._insert_yield(conn, f"ANC_{i}", "LOT_ANC", y)
            self._insert_yield(conn, f"DSC_{i}", "LOT_DSC", y)
        result = compute_yield_correlation(conn, "LOT_ANC", "LOT_DSC")
        assert isinstance(result, YieldCorrelation)

    def test_returns_yield_correlation_type(self, conn):
        for i in range(3):
            self._insert_yield(conn, f"P_A{i}", "L_ANC", 0.8 - i * 0.1)
            self._insert_yield(conn, f"P_D{i}", "L_DSC", 0.7 - i * 0.1)
        result = compute_yield_correlation(conn, "L_ANC", "L_DSC")
        assert isinstance(result, YieldCorrelation)

    def test_no_common_panels_raises(self, conn):
        self._insert_yield(conn, "P_ONLY_ANC", "L_X", 0.9)
        self._insert_yield(conn, "P_ONLY_DSC", "L_Y", 0.8)
        with pytest.raises(ValueError, match="[Nn]o common"):
            compute_yield_correlation(conn, "L_X", "L_Y")

    def test_invalid_model_raises(self, conn):
        self._insert_yield(conn, "P_M", "L_M", 0.9)
        with pytest.raises(ValueError, match="model"):
            compute_yield_correlation(conn, "L_M", "L_M", model="quadratic")

    def test_n_panels_count(self, conn):
        for i in range(4):
            self._insert_yield(conn, f"SHARED_{i}", "L_SH_A", 0.8)
            self._insert_yield(conn, f"SHARED_{i}", "L_SH_B", 0.75)
        result = compute_yield_correlation(conn, "L_SH_A", "L_SH_B")
        assert result.n_panels == 4

    def test_mean_yields_correct(self, conn):
        yields_a = [0.8, 0.9, 0.7]
        for i, y in enumerate(yields_a):
            self._insert_yield(conn, f"PANEL_{i}", "L_MA", y)
            self._insert_yield(conn, f"PANEL_{i}", "L_MB", y)
        result = compute_yield_correlation(conn, "L_MA", "L_MB", model="poisson")
        assert result.mean_ancestor_yield == pytest.approx(sum(yields_a) / 3, rel=1e-4)

    def test_pearson_r_in_valid_range(self, conn):
        for i in range(6):
            self._insert_yield(conn, f"R_PANEL_{i}", "L_RA", i * 0.1)
            self._insert_yield(conn, f"R_PANEL_{i}", "L_RB", (5 - i) * 0.1)
        result = compute_yield_correlation(conn, "L_RA", "L_RB")
        assert -1.0 <= result.pearson_r <= 1.0
