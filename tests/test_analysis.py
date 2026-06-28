"""
tests/test_analysis.py
-----------------------
Unit and integration tests for clustering analysis and lot tracking.
"""

import pytest
from openyield.analysis.clustering import (
    cluster_panel, cluster_all_panels, _dbscan, _classify,
)
from openyield.analysis.lot_tracker import (
    summarise_lot, summarise_all_lots, auto_create_lot, _mean, _std,
)
from openyield.ingestion.ingest import (
    upsert_panel, upsert_component, upsert_defect, upsert_lot,
)
from openyield.yield_engine.calculator import calculate_panel_yield


# ---------------------------------------------------------------------------
# DBSCAN unit tests
# ---------------------------------------------------------------------------

def test_dbscan_two_clear_clusters():
    points = [
        (0.0, 0.0), (0.5, 0.0), (0.0, 0.5),       # cluster 0
        (10.0, 10.0), (10.5, 10.0), (10.0, 10.5),  # cluster 1
    ]
    labels = _dbscan(points, epsilon=1.5, min_samples=2)
    assert len(set(labels)) == 2
    assert -1 not in labels


def test_dbscan_all_noise():
    points = [(0.0, 0.0), (10.0, 10.0), (20.0, 5.0)]
    labels = _dbscan(points, epsilon=1.0, min_samples=2)
    assert all(l == -1 for l in labels)


def test_dbscan_single_cluster():
    points = [(i * 0.3, 0.0) for i in range(5)]
    labels = _dbscan(points, epsilon=0.5, min_samples=2)
    assert max(labels) == 0
    assert all(l == 0 for l in labels)


def test_dbscan_returns_correct_length():
    points = [(float(i), 0.0) for i in range(10)]
    labels = _dbscan(points, epsilon=1.5, min_samples=2)
    assert len(labels) == 10


# ---------------------------------------------------------------------------
# Classification unit tests
# ---------------------------------------------------------------------------

def test_classify_no_clusters_is_random():
    assert _classify(0, [], 50) == "random"


def test_classify_single_dominant_cluster_is_excursion():
    # One cluster with 80% of defects
    assert _classify(1, [40], 50) == "excursion"


def test_classify_multiple_equal_clusters_is_systematic():
    assert _classify(3, [10, 9, 11], 50) == "systematic"


def test_classify_one_small_cluster_random():
    # One cluster with only 10% of all defects
    assert _classify(1, [5], 50) == "random"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_panel_with_defects(
    conn, panel_id, substrate_type, rows, cols, pitch,
    defect_coords,  # list of (row, col, x, y)
    lot_id=None,
):
    with conn:
        upsert_panel(conn, panel_id, "TEST", substrate_type, rows, cols,
                     lot_id=lot_id)
        for r in range(rows):
            for c in range(cols):
                upsert_component(conn, panel_id, r, c, "zone_center",
                                 float(c * pitch), float(r * pitch))
        for row, col, x, y in defect_coords:
            upsert_defect(conn, panel_id, row, col, "system_a",
                          "particle", x, y, 0.05, 0.75)


# ---------------------------------------------------------------------------
# Clustering integration tests
# ---------------------------------------------------------------------------

def test_cluster_panel_random(mem_conn):
    """Scattered defects → random classification."""
    coords = [
        (0, 0, 1.0, 1.0), (1, 1, 30.0, 30.0), (2, 2, 60.0, 60.0),
        (0, 1, 15.0, 2.0), (1, 2, 45.0, 32.0),
    ]
    _setup_panel_with_defects(mem_conn, "WF_RAND", "wafer", 4, 4, 28.0, coords)
    result = cluster_panel(mem_conn, "WF_RAND", persist=False)
    assert result.panel_id == "WF_RAND"
    assert result.classification == "random"
    assert result.n_clusters == 0


def test_cluster_panel_excursion(mem_conn):
    """Tight cluster of defects → excursion classification."""
    # 8 defects tightly clustered in one spot
    coords = [
        (2, 2, 56.0 + i * 0.2, 56.0 + i * 0.2) for i in range(8)
    ] + [
        (0, 0, 1.0, 1.0),  # one isolated defect
    ]
    _setup_panel_with_defects(mem_conn, "WF_EXC", "wafer", 4, 4, 28.0, coords)
    result = cluster_panel(mem_conn, "WF_EXC", persist=False)
    assert result.classification == "excursion"
    assert result.n_clusters >= 1
    assert result.largest_cluster >= 6


def test_cluster_panel_no_defects(mem_conn):
    """Panel with no defects → random with 0 clusters."""
    _setup_panel_with_defects(mem_conn, "WF_EMPTY", "wafer", 4, 4, 28.0, [])
    result = cluster_panel(mem_conn, "WF_EMPTY", persist=False)
    assert result.classification == "random"
    assert result.n_clusters == 0


def test_cluster_panel_persists(mem_conn):
    coords = [(0, 0, 1.0 + i*0.3, 1.0) for i in range(5)]
    _setup_panel_with_defects(mem_conn, "WF_PERS", "wafer", 4, 4, 28.0, coords)
    cluster_panel(mem_conn, "WF_PERS", persist=True)

    row = mem_conn.execute(
        "SELECT * FROM cluster_results WHERE panel_id='WF_PERS'"
    ).fetchone()
    assert row is not None
    assert row["panel_id"] == "WF_PERS"


def test_cluster_panel_defect_labels_saved(mem_conn):
    coords = [(0, 0, 1.0 + i*0.2, 1.0) for i in range(4)]
    _setup_panel_with_defects(mem_conn, "WF_LBL", "wafer", 4, 4, 28.0, coords)
    cluster_panel(mem_conn, "WF_LBL", persist=True)

    rows = mem_conn.execute(
        "SELECT * FROM defect_clusters WHERE panel_id='WF_LBL'"
    ).fetchall()
    assert len(rows) == 4


def test_cluster_all_panels(mem_conn):
    for i, pid in enumerate(["WF_A01", "WF_A02"]):
        coords = [(0, 0, float(j + i*5), 1.0) for j in range(3)]
        _setup_panel_with_defects(mem_conn, pid, "wafer", 4, 4, 28.0, coords)

    results = cluster_all_panels(mem_conn, persist=False)
    assert len(results) == 2


def test_cluster_panel_not_found(mem_conn):
    with pytest.raises(ValueError, match="not found"):
        cluster_panel(mem_conn, "NONEXISTENT", persist=False)


def test_cluster_custom_epsilon(mem_conn):
    """Custom epsilon changes cluster detection."""
    coords = [(0, 0, 0.0, 0.0), (0, 0, 3.0, 0.0), (0, 0, 6.0, 0.0)]
    _setup_panel_with_defects(mem_conn, "WF_EPS", "wafer", 4, 4, 28.0, coords)

    # Large epsilon — all in one cluster
    r1 = cluster_panel(mem_conn, "WF_EPS", epsilon_mm=5.0, persist=False)
    # Small epsilon — all noise
    r2 = cluster_panel(mem_conn, "WF_EPS", epsilon_mm=0.5, persist=False)

    assert r1.n_clusters >= 1
    assert r2.n_clusters == 0


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------

def test_mean_basic():
    assert _mean([1.0, 2.0, 3.0]) == pytest.approx(2.0)


def test_mean_empty():
    assert _mean([]) == 0.0


def test_std_basic():
    assert _std([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0], 5.0) == pytest.approx(2.138, rel=1e-3)


def test_std_single():
    assert _std([5.0], 5.0) == 0.0


# ---------------------------------------------------------------------------
# Lot tracking integration tests
# ---------------------------------------------------------------------------

def _setup_lot_with_panels(conn, lot_id, substrate_type, n_panels,
                            densities=None):
    """Create a lot with n_panels, each having a given defect density."""
    with conn:
        upsert_lot(conn, lot_id, substrate_type, "TEST-PRODUCT", lot_size=25)

    pitch = 28.0 if substrate_type == "wafer" else 370.0
    rows_cols = (4, 4) if substrate_type == "wafer" else (3, 3)

    for i in range(n_panels):
        pid = f"{lot_id}_P{i:02d}"
        n_defects = int((densities[i] if densities else 0.002) * pitch**2 * rows_cols[0] * rows_cols[1])
        coords = [
            (r, c, float(c * pitch + j * 0.5), float(r * pitch + j * 0.3))
            for r in range(rows_cols[0])
            for c in range(rows_cols[1])
            for j in range(max(1, n_defects // (rows_cols[0] * rows_cols[1])))
        ][:max(n_defects, 1)]

        _setup_panel_with_defects(
            conn, pid, substrate_type,
            rows_cols[0], rows_cols[1], pitch, coords,
            lot_id=lot_id
        )
        calculate_panel_yield(conn, pid, persist=True)


def test_summarise_lot_clean(mem_conn):
    _setup_lot_with_panels(
        mem_conn, "LOT_CLEAN", "wafer", 4,
        densities=[0.002, 0.0021, 0.0019, 0.002]
    )
    summary = summarise_lot(mem_conn, "LOT_CLEAN", persist=False)
    assert summary.lot_id == "LOT_CLEAN"
    assert summary.panel_count == 4
    assert summary.lot_status in ("clean", "watch")
    assert summary.avg_defect_density > 0


def test_summarise_lot_excursion(mem_conn):
    """One panel with dramatically higher density → excursion."""
    _setup_lot_with_panels(
        mem_conn, "LOT_EXC", "wafer", 4,
        densities=[0.001, 0.001, 0.001, 0.05]  # last panel is 50x higher
    )
    summary = summarise_lot(mem_conn, "LOT_EXC", persist=False)
    assert summary.lot_status == "excursion"
    assert summary.excursion_count >= 1


def test_summarise_lot_not_found(mem_conn):
    with pytest.raises(ValueError, match="not found"):
        summarise_lot(mem_conn, "NONEXISTENT_LOT", persist=False)


def test_summarise_lot_no_panels(mem_conn):
    with mem_conn:
        upsert_lot(mem_conn, "LOT_EMPTY", "wafer", "TEST", lot_size=25)
    with pytest.raises(ValueError):
        summarise_lot(mem_conn, "LOT_EMPTY", persist=False)


def test_summarise_lot_persists(mem_conn):
    _setup_lot_with_panels(mem_conn, "LOT_PERS", "wafer", 2)
    summarise_lot(mem_conn, "LOT_PERS", persist=True)
    row = mem_conn.execute(
        "SELECT * FROM lot_summaries WHERE lot_id='LOT_PERS'"
    ).fetchone()
    assert row is not None


def test_summarise_all_lots(mem_conn):
    _setup_lot_with_panels(mem_conn, "LOT_ALL1", "wafer",       2)
    _setup_lot_with_panels(mem_conn, "LOT_ALL2", "glass_panel", 2)
    summaries = summarise_all_lots(mem_conn, persist=False)
    assert len(summaries) == 2


def test_summarise_all_lots_filtered(mem_conn):
    _setup_lot_with_panels(mem_conn, "LOT_FLT1", "wafer",       2)
    _setup_lot_with_panels(mem_conn, "LOT_FLT2", "glass_panel", 2)
    summaries = summarise_all_lots(
        mem_conn, substrate_type="wafer", persist=False
    )
    assert len(summaries) == 1
    assert summaries[0].substrate_type == "wafer"


def test_auto_create_lot_creates_new(mem_conn):
    lot_id = auto_create_lot(
        mem_conn, "WF_AUTO01", "wafer", "LOGIC-7NM"
    )
    assert lot_id.startswith("WL_")
    row = mem_conn.execute(
        f"SELECT * FROM lots WHERE lot_id=?", (lot_id,)
    ).fetchone()
    assert row is not None


def test_auto_create_lot_reuses_existing(mem_conn):
    """Two panels with same substrate/product → same lot."""
    lot1 = auto_create_lot(mem_conn, "WF_B01", "wafer", "LOGIC-7NM")
    lot2 = auto_create_lot(mem_conn, "WF_B02", "wafer", "LOGIC-7NM")
    assert lot1 == lot2


def test_lot_fallback_small_lot(mem_conn):
    """Lot with < 3 panels uses fixed threshold, should not crash."""
    _setup_lot_with_panels(mem_conn, "LOT_SMALL", "wafer", 2)
    summary = summarise_lot(mem_conn, "LOT_SMALL", persist=False)
    assert summary.lot_status in ("clean", "watch", "excursion")
