"""
analysis/clustering.py
-----------------------
Author: Yeonkuk Woo

Spatial defect clustering analysis for OpenYield.

Uses DBSCAN (Density-Based Spatial Clustering of Applications with Noise)
to detect whether defects on a panel are randomly distributed (contamination)
or spatially clustered (process excursion or systematic tool issue).

Classification logic
--------------------
After running DBSCAN on system_a defect coordinates:

  random      — No significant clusters found. Defects follow a
                near-Poisson spatial distribution. Consistent with
                random particle contamination. No process action needed.

  systematic  — Multiple clusters found, roughly equal in size.
                Consistent with a repeating pattern (e.g. reticle defect,
                chuck contamination, systematic tool issue).
                Requires engineering investigation.

  excursion   — One dominant cluster containing >50% of clustered defects.
                Consistent with a single process event (particle shower,
                equipment fault, handling scratch).
                Requires immediate process hold review.

DBSCAN parameters
-----------------
epsilon_mm    : Maximum distance between two defects to be considered
                neighbours. Defaults from substrate profile
                match_distance_threshold — physically meaningful because
                that threshold already encodes the spatial resolution of
                the inspection system.

min_samples   : Minimum defects to form a core cluster point.
                Default: 3 (conservative — avoids false positives).

References
----------
Ester et al., "A Density-Based Algorithm for Discovering Clusters in Large
Spatial Databases with Noise", KDD-96, 1996.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from openyield.db.connection import get_placeholder, is_postgres
from openyield.synthetic.substrate_profiles import get_profile

logger = logging.getLogger(__name__)

Connection = Any


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ClusterResult:
    panel_id:        str
    n_clusters:      int
    n_noise:         int
    classification:  str          # 'random', 'systematic', 'excursion'
    largest_cluster: int          # defect count in largest cluster
    epsilon_mm:      float
    min_samples:     int
    cluster_summary: dict         # {cluster_label: defect_count}
    defect_labels:   dict[int, int]  # {defect_id: cluster_label} (-1 = noise)


# ---------------------------------------------------------------------------
# Pure DBSCAN implementation (no sklearn dependency)
# ---------------------------------------------------------------------------

def _euclidean(p1: tuple, p2: tuple) -> float:
    return math.sqrt((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2)


def _dbscan(
    points: list[tuple[float, float]],
    epsilon: float,
    min_samples: int,
) -> list[int]:
    """
    Pure Python DBSCAN implementation.

    Parameters
    ----------
    points      : List of (x, y) coordinate tuples in mm
    epsilon     : Neighbourhood radius in mm
    min_samples : Minimum points to form a core point

    Returns
    -------
    list[int] : Cluster label per point. -1 = noise.
    """
    n = len(points)
    labels = [-2] * n   # -2 = unvisited
    cluster_id = 0

    def region_query(idx: int) -> list[int]:
        return [
            j for j in range(n)
            if _euclidean(points[idx], points[j]) <= epsilon
        ]

    def expand_cluster(idx: int, neighbours: list[int], cid: int) -> None:
        labels[idx] = cid
        i = 0
        while i < len(neighbours):
            nb = neighbours[i]
            if labels[nb] == -2:    # unvisited
                labels[nb] = cid
                nb_neighbours = region_query(nb)
                if len(nb_neighbours) >= min_samples:
                    neighbours.extend(nb_neighbours)
            elif labels[nb] == -1:  # previously noise → now border
                labels[nb] = cid
            i += 1

    for idx in range(n):
        if labels[idx] != -2:
            continue
        neighbours = region_query(idx)
        if len(neighbours) < min_samples:
            labels[idx] = -1    # noise
        else:
            expand_cluster(idx, neighbours, cluster_id)
            cluster_id += 1

    return labels


# ---------------------------------------------------------------------------
# Classification logic
# ---------------------------------------------------------------------------

def _classify(
    n_clusters: int,
    cluster_counts: list[int],
    total_defects: int,
) -> str:
    """
    Classify the defect spatial pattern based on cluster statistics.

    Returns 'random', 'systematic', or 'excursion'.
    """
    if n_clusters == 0:
        return "random"

    total_clustered = sum(cluster_counts)
    largest = max(cluster_counts)

    # Excursion: largest cluster holds >30% of ALL defects (not just clustered)
    # This prevents a tiny cluster from being called an excursion
    if largest / max(total_defects, 1) > 0.30:
        return "excursion"

    # Systematic: multiple clusters of roughly equal size
    if n_clusters >= 2:
        return "systematic"

    return "random"


# ---------------------------------------------------------------------------
# Main clustering function
# ---------------------------------------------------------------------------

def cluster_panel(
    conn: Connection,
    panel_id: str,
    *,
    epsilon_mm: float | None = None,
    min_samples: int = 3,
    persist: bool = True,
) -> ClusterResult:
    """
    Run DBSCAN spatial clustering on system_a defects for a panel.

    Parameters
    ----------
    conn        : Database connection
    panel_id    : Panel to analyse
    epsilon_mm  : DBSCAN neighbourhood radius in mm.
                  Defaults to substrate profile match_distance_threshold.
    min_samples : Minimum defects to form a cluster core point (default 3)
    persist     : Save results to cluster_results and defect_clusters tables

    Returns
    -------
    ClusterResult
    """
    ph = get_placeholder(conn)

    # Fetch panel metadata
    panel = conn.execute(
        f"SELECT * FROM panels WHERE panel_id={ph}", (panel_id,)
    ).fetchone()
    if panel is None:
        raise ValueError(f"Panel not found: {panel_id!r}")

    substrate_type = panel["substrate_type"]
    profile = get_profile(substrate_type)

    # Default epsilon from profile
    if epsilon_mm is None:
        epsilon_mm = profile.match_distance_threshold

    # Fetch system_a defects on active dies only
    rows = conn.execute(
        f"""SELECT d.defect_id, d.x, d.y
            FROM defects d
            JOIN components c
              ON c.panel_id=d.panel_id
             AND c.component_row=d.component_row
             AND c.component_col=d.component_col
            WHERE d.panel_id={ph}
              AND d.source_system='system_a'
              AND c.active=1""",
        (panel_id,)
    ).fetchall()

    if not rows:
        logger.warning("No system_a defects found for panel %s", panel_id)
        result = ClusterResult(
            panel_id=panel_id, n_clusters=0, n_noise=0,
            classification="random", largest_cluster=0,
            epsilon_mm=epsilon_mm, min_samples=min_samples,
            cluster_summary={}, defect_labels={},
        )
        if persist:
            _save_cluster_result(conn, result)
        return result

    defect_ids  = [r["defect_id"] for r in rows]
    points      = [(r["x"], r["y"]) for r in rows]

    # Run DBSCAN
    labels = _dbscan(points, epsilon_mm, min_samples)

    # Build summary
    cluster_counts: dict[int, int] = {}
    for lbl in labels:
        if lbl >= 0:
            cluster_counts[lbl] = cluster_counts.get(lbl, 0) + 1

    n_clusters      = len(cluster_counts)
    n_noise         = labels.count(-1)
    cluster_list    = sorted(cluster_counts.values(), reverse=True)
    largest_cluster = cluster_list[0] if cluster_list else 0

    classification = _classify(n_clusters, cluster_list, len(rows))

    defect_labels = {
        defect_ids[i]: labels[i]
        for i in range(len(defect_ids))
    }

    result = ClusterResult(
        panel_id=panel_id,
        n_clusters=n_clusters,
        n_noise=n_noise,
        classification=classification,
        largest_cluster=largest_cluster,
        epsilon_mm=epsilon_mm,
        min_samples=min_samples,
        cluster_summary=cluster_counts,
        defect_labels=defect_labels,
    )

    logger.info(
        "[%s] Clustering: %d clusters | %d noise | classification=%s | "
        "largest=%d defects | ε=%.1fmm",
        panel_id, n_clusters, n_noise, classification,
        largest_cluster, epsilon_mm,
    )

    if persist:
        _save_cluster_result(conn, result)

    return result


def cluster_all_panels(
    conn: Connection,
    *,
    substrate_type: str | None = None,
    persist: bool = True,
) -> list[ClusterResult]:
    """Run clustering analysis on all panels, optionally filtered by substrate."""
    ph = get_placeholder(conn)
    if substrate_type:
        rows = conn.execute(
            f"SELECT panel_id FROM panels WHERE substrate_type={ph}",
            (substrate_type,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT panel_id FROM panels").fetchall()

    results = []
    for row in rows:
        try:
            r = cluster_panel(conn, row["panel_id"], persist=persist)
            results.append(r)
        except Exception as exc:
            logger.error("Clustering failed for %s: %s", row["panel_id"], exc)
    return results


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _save_cluster_result(conn: Connection, result: ClusterResult) -> None:
    ph = get_placeholder(conn)
    now = datetime.now(timezone.utc).isoformat()

    with conn:
        # Save cluster result summary
        conn.execute(
            f"INSERT INTO cluster_results "
            f"(panel_id, calculated_at, n_clusters, n_noise, classification, "
            f"largest_cluster, epsilon_mm, min_samples, cluster_summary) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
            (
                result.panel_id, now,
                result.n_clusters, result.n_noise,
                result.classification, result.largest_cluster,
                result.epsilon_mm, result.min_samples,
                json.dumps(result.cluster_summary),
            )
        )

        # Save per-defect cluster labels
        if result.defect_labels:
            if is_postgres(conn):
                for defect_id, label in result.defect_labels.items():
                    conn.execute(
                        f"INSERT INTO defect_clusters "
                        f"(defect_id, panel_id, cluster_label, is_noise) "
                        f"VALUES ({ph},{ph},{ph},{ph}) "
                        f"ON CONFLICT (defect_id, panel_id) DO UPDATE SET "
                        f"cluster_label=EXCLUDED.cluster_label, "
                        f"is_noise=EXCLUDED.is_noise",
                        (defect_id, result.panel_id, label, int(label == -1))
                    )
            else:
                conn.executemany(
                    f"INSERT OR REPLACE INTO defect_clusters "
                    f"(defect_id, panel_id, cluster_label, is_noise) "
                    f"VALUES ({ph},{ph},{ph},{ph})",
                    [
                        (did, result.panel_id, lbl, int(lbl == -1))
                        for did, lbl in result.defect_labels.items()
                    ]
                )


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------

def print_cluster_report(results: list[ClusterResult]) -> None:
    if not results:
        print("No clustering results to report.")
        return

    print(f"\n{'='*70}")
    print(f"  CLUSTERING REPORT  ({len(results)} panel(s))")
    print(f"{'='*70}")
    print(f"  {'Panel ID':<20} {'Clusters':>8} {'Noise':>6} "
          f"{'Largest':>8} {'ε(mm)':>7}  Classification")
    print(f"  {'-'*20} {'-'*8} {'-'*6} {'-'*8} {'-'*7}  {'-'*14}")

    for r in results:
        flag = (
            "⚠ EXCURSION" if r.classification == "excursion"
            else "~ systematic" if r.classification == "systematic"
            else "✓ random"
        )
        print(
            f"  {r.panel_id:<20} {r.n_clusters:>8} {r.n_noise:>6} "
            f"{r.largest_cluster:>8} {r.epsilon_mm:>7.1f}  {flag}"
        )

    excursions  = sum(1 for r in results if r.classification == "excursion")
    systematics = sum(1 for r in results if r.classification == "systematic")
    randoms     = sum(1 for r in results if r.classification == "random")

    print(f"{'='*70}")
    print(f"  random={randoms}  systematic={systematics}  excursion={excursions}")
    print()
