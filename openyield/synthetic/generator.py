"""
synthetic/generator.py
----------------------
Author: Yeonkuk Woo

Generalized inspection data generator for panel-based manufacturing systems.

Supports three substrate classes — glass panel, silicon wafer, PCB — through
a unified API. All substrate-specific behavior (defect taxonomy, noise levels,
inspection technology, spatial parameters) is encapsulated in SubstrateProfile
objects defined in substrate_profiles.py.

Design decisions
----------------
* SubstrateProfile drives ALL substrate-specific constants — generator is
  substrate-agnostic. Adding a new substrate type requires only a new profile.
* Defect counts follow a Poisson distribution (rare-event arrival model).
* Spatial clustering uses Gaussian mixture centers seeded per unit cell,
  reproducing the spatial correlation seen in real process-driven defect maps.
* system_a simulates a high-throughput automated scanner (higher FP, noisier).
* system_b simulates a verification / review tool (subset detection, tighter).
* Greedy nearest-neighbor cross-system matching assigns match_id pairs.
* Wafer substrates support optional edge-exclusion zone (no die sites within
  a configurable radius of the wafer edge).
* Coordinate mirroring (mirror_x) simulates dual-side inspection workflows.
"""

from __future__ import annotations

import csv
import uuid
import math
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .substrate_profiles import (
    SubstrateProfile,
    SubstrateType,
    get_profile,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ComponentSpec:
    panel_id:       str
    component_row:  int
    component_col:  int
    region_id:      str
    center_x:       float
    center_y:       float
    active:         bool = True   # False = edge-excluded (wafer only)


@dataclass
class DefectRecord:
    panel_id:         str
    component_row:    int
    component_col:    int
    source_system:    str
    defect_type:      str
    x:                float
    y:                float
    size:             float
    confidence_score: float
    match_id:         str | None = None
    created_at:       datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


@dataclass
class SyntheticPanel:
    panel_id:       str
    product_type:   str
    substrate_type: str
    rows:           int
    cols:           int
    components:     list[ComponentSpec]
    defects:        list[DefectRecord]   # combined system_a + system_b


# ---------------------------------------------------------------------------
# Panel ID
# ---------------------------------------------------------------------------

def generate_panel_id(substrate_type: SubstrateType) -> str:
    prefix = {
        SubstrateType.GLASS_PANEL: "GP",
        SubstrateType.WAFER:       "WF",
    }.get(substrate_type, "XX")
    return f"{prefix}_{uuid.uuid4().hex[:8].upper()}"


# ---------------------------------------------------------------------------
# Region assignment
# ---------------------------------------------------------------------------

def assign_region(
    row: int, col: int,
    total_rows: int, total_cols: int,
    substrate_type: SubstrateType,
) -> str:
    """
    Assign a spatial region label to a unit cell.

    Glass panel / PCB  — quadrant grid (NW / NE / SW / SE)
    Wafer              — concentric ring zones (center / mid / edge)
                         mirrors wafer-map defect clustering patterns
    """
    if substrate_type == SubstrateType.WAFER:
        # Radial distance from geometric center (normalized 0→1)
        cr = (total_rows - 1) / 2.0
        cc = (total_cols - 1) / 2.0
        r_norm = math.hypot(row - cr, col - cc) / math.hypot(cr, cc)
        if r_norm < 0.35:
            return "zone_center"
        elif r_norm < 0.70:
            return "zone_mid"
        else:
            return "zone_edge"
    else:
        r_half = total_rows // 2
        c_half = total_cols // 2
        quadrant_r = "N" if row < r_half else "S"
        quadrant_c = "W" if col < c_half else "E"
        return f"region_{quadrant_r}{quadrant_c}"


# ---------------------------------------------------------------------------
# Component / unit cell generation
# ---------------------------------------------------------------------------

def _wafer_active(
    row: int, col: int,
    total_rows: int, total_cols: int,
    edge_exclusion_fraction: float = 0.08,
) -> bool:
    """
    Return False for die sites that fall within the wafer edge-exclusion zone.
    edge_exclusion_fraction is the fraction of the half-diameter to exclude.
    """
    cr = (total_rows - 1) / 2.0
    cc = (total_cols - 1) / 2.0
    max_r = math.hypot(cr, cc)
    dist  = math.hypot(row - cr, col - cc)
    return dist <= max_r * (1.0 - edge_exclusion_fraction)


def generate_components(
    panel_id: str,
    rows: int,
    cols: int,
    profile: SubstrateProfile,
    mirror_x: bool = False,
    edge_exclusion_fraction: float = 0.08,
) -> list[ComponentSpec]:
    """
    Generate a 2-D grid of unit cells with physical (x, y) centers.

    mirror_x  : flip the x-axis (simulates back-side inspection).
    edge_exclusion_fraction : wafer-only; fraction of radius to mark inactive.
    """
    pitch = profile.component_pitch_mm
    is_wafer = profile.substrate_type == SubstrateType.WAFER

    components: list[ComponentSpec] = []
    for r in range(rows):
        for c in range(cols):
            cx = c * pitch
            cy = r * pitch
            if mirror_x:
                cx = (cols - 1) * pitch - cx

            active = True
            if is_wafer:
                active = _wafer_active(
                    r, c, rows, cols, edge_exclusion_fraction
                )

            components.append(ComponentSpec(
                panel_id=panel_id,
                component_row=r,
                component_col=c,
                region_id=assign_region(
                    r, c, rows, cols, profile.substrate_type
                ),
                center_x=round(cx, 4),
                center_y=round(cy, 4),
                active=active,
            ))
    return components


# ---------------------------------------------------------------------------
# Defect generation (ground truth)
# ---------------------------------------------------------------------------

def _cluster_centers(
    comp: ComponentSpec,
    half_width: float,
    n_clusters: int,
    rng: np.random.Generator,
) -> list[tuple[float, float]]:
    return [
        (
            comp.center_x + rng.uniform(-half_width, half_width),
            comp.center_y + rng.uniform(-half_width, half_width),
        )
        for _ in range(n_clusters)
    ]


def _generate_ground_truth(
    comp: ComponentSpec,
    profile: SubstrateProfile,
    rng: np.random.Generator,
) -> list[DefectRecord]:
    """Generate ground-truth defects for one unit cell."""
    n = int(rng.poisson(profile.mean_defect_count))
    if n == 0:
        return []

    centers = _cluster_centers(
        comp,
        profile.component_half_width_mm,
        profile.n_clusters,
        rng,
    )

    records: list[DefectRecord] = []
    for _ in range(n):
        cx, cy = centers[rng.integers(len(centers))]
        x = cx + rng.normal(0, profile.cluster_std_mm)
        y = cy + rng.normal(0, profile.cluster_std_mm)
        size = float(rng.lognormal(
            profile.size_lognormal_mean,
            profile.size_lognormal_sigma,
        ))
        records.append(DefectRecord(
            panel_id=comp.panel_id,
            component_row=comp.component_row,
            component_col=comp.component_col,
            source_system="__ground_truth__",
            defect_type=str(rng.choice(profile.defect_types)),
            x=round(float(x), 4),
            y=round(float(y), 4),
            size=round(size, 4),
            confidence_score=1.0,
        ))
    return records


# ---------------------------------------------------------------------------
# System A simulation
# ---------------------------------------------------------------------------

def _simulate_system_a(
    ground_truth: list[DefectRecord],
    profile: SubstrateProfile,
    rng: np.random.Generator,
) -> list[DefectRecord]:
    """
    High-throughput automated scanner simulation.
    All GT defects are reported with Gaussian coordinate noise.
    False positives are injected at profile.system_a_fp_rate.
    """
    records: list[DefectRecord] = []
    noise = profile.system_a_noise_std

    for gt in ground_truth:
        records.append(DefectRecord(
            panel_id=gt.panel_id,
            component_row=gt.component_row,
            component_col=gt.component_col,
            source_system="system_a",
            defect_type=gt.defect_type,
            x=round(gt.x + float(rng.normal(0, noise)), 4),
            y=round(gt.y + float(rng.normal(0, noise)), 4),
            size=round(gt.size * float(rng.uniform(0.85, 1.20)), 4),
            confidence_score=round(float(rng.uniform(
                profile.system_a_confidence_lo,
                profile.system_a_confidence_hi,
            )), 4),
        ))

    # False positives — spatially near real defects but with extra scatter
    n_fp = int(len(records) * profile.system_a_fp_rate)
    if ground_truth:
        for _ in range(n_fp):
            ref = ground_truth[rng.integers(len(ground_truth))]
            records.append(DefectRecord(
                panel_id=ref.panel_id,
                component_row=ref.component_row,
                component_col=ref.component_col,
                source_system="system_a",
                defect_type=str(rng.choice(profile.defect_types)),
                x=round(ref.x + float(rng.normal(0, noise * 4.0)), 4),
                y=round(ref.y + float(rng.normal(0, noise * 4.0)), 4),
                size=round(float(rng.lognormal(0.5, 0.5)), 4),
                confidence_score=round(float(rng.uniform(0.25, 0.55)), 4),
            ))

    return records


# ---------------------------------------------------------------------------
# System B simulation
# ---------------------------------------------------------------------------

def _simulate_system_b(
    ground_truth: list[DefectRecord],
    profile: SubstrateProfile,
    rng: np.random.Generator,
) -> list[DefectRecord]:
    """
    Verification / review tool simulation.
    Detects profile.system_b_detection_rate fraction of GT defects,
    with tighter coordinate noise and higher confidence scores.
    """
    records: list[DefectRecord] = []
    noise = profile.system_b_noise_std

    for gt in ground_truth:
        if rng.random() > profile.system_b_detection_rate:
            continue
        records.append(DefectRecord(
            panel_id=gt.panel_id,
            component_row=gt.component_row,
            component_col=gt.component_col,
            source_system="system_b",
            defect_type=gt.defect_type,
            x=round(gt.x + float(rng.normal(0, noise)), 4),
            y=round(gt.y + float(rng.normal(0, noise)), 4),
            size=round(gt.size * float(rng.uniform(0.95, 1.05)), 4),
            confidence_score=round(float(rng.uniform(
                profile.system_b_confidence_lo,
                profile.system_b_confidence_hi,
            )), 4),
        ))

    return records


# ---------------------------------------------------------------------------
# Cross-system defect matching
# ---------------------------------------------------------------------------

def match_defects(
    system_a: list[DefectRecord],
    system_b: list[DefectRecord],
    distance_threshold: float,
) -> tuple[list[DefectRecord], list[DefectRecord]]:
    """
    Greedy nearest-neighbor spatial matching between system_a and system_b
    defects within the same unit cell.

    Matched pairs receive a shared match_id (UUID fragment).
    Unmatched defects retain match_id = None.

    For production scale, replace with scipy.optimize.linear_sum_assignment
    (Hungarian algorithm) to guarantee optimal global matching.
    """
    matched_b: set[int] = set()

    for a in system_a:
        best_dist = float("inf")
        best_j    = -1

        for j, b in enumerate(system_b):
            if j in matched_b:
                continue
            if a.component_row != b.component_row:
                continue
            if a.component_col != b.component_col:
                continue
            dist = math.hypot(a.x - b.x, a.y - b.y)
            if dist < best_dist:
                best_dist = dist
                best_j    = j

        if best_j >= 0 and best_dist <= distance_threshold:
            mid = f"match_{uuid.uuid4().hex[:8]}"
            a.match_id = mid
            system_b[best_j].match_id = mid
            matched_b.add(best_j)

    return system_a, system_b


# ---------------------------------------------------------------------------
# Top-level panel generator
# ---------------------------------------------------------------------------

def generate_panel(
    rows: int = 6,
    cols: int = 6,
    substrate_type: SubstrateType | str = SubstrateType.GLASS_PANEL,
    product_type: str | None = None,
    mirror_x: bool = False,
    edge_exclusion_fraction: float = 0.08,
    seed: int | None = None,
) -> SyntheticPanel:
    """
    Generate a complete synthetic substrate panel with unit cells and
    inspection defects from both system_a and system_b.

    Parameters
    ----------
    rows / cols              : Grid dimensions of the unit cell array.
    substrate_type           : One of 'glass_panel', 'wafer', 'pcb'.
    product_type             : Optional product label; randomly chosen from
                               the substrate profile if not supplied.
    mirror_x                 : Flip x-axis (back-side inspection simulation).
    edge_exclusion_fraction  : Wafer only — fraction of radius to exclude.
    seed                     : RNG seed for reproducibility.
    """
    profile = get_profile(substrate_type)
    rng     = np.random.default_rng(seed)

    panel_id     = generate_panel_id(profile.substrate_type)
    product_type = product_type or str(rng.choice(profile.product_types))

    components = generate_components(
        panel_id=panel_id,
        rows=rows,
        cols=cols,
        profile=profile,
        mirror_x=mirror_x,
        edge_exclusion_fraction=edge_exclusion_fraction,
    )

    # Only active unit cells generate defects
    active_components = [c for c in components if c.active]

    all_system_a: list[DefectRecord] = []
    all_system_b: list[DefectRecord] = []

    for comp in active_components:
        gt = _generate_ground_truth(comp, profile, rng)
        all_system_a.extend(_simulate_system_a(gt, profile, rng))
        all_system_b.extend(_simulate_system_b(gt, profile, rng))

    all_system_a, all_system_b = match_defects(
        all_system_a,
        all_system_b,
        distance_threshold=profile.match_distance_threshold,
    )

    n_active   = len(active_components)
    n_inactive = len(components) - n_active
    logger.info(
        "[%s] %s | %dx%d grid | %d active cells (%d edge-excluded) | "
        "%d system_a defects | %d system_b defects",
        profile.substrate_type.value, panel_id,
        rows, cols, n_active, n_inactive,
        len(all_system_a), len(all_system_b),
    )

    return SyntheticPanel(
        panel_id=panel_id,
        product_type=product_type,
        substrate_type=profile.substrate_type.value,
        rows=rows,
        cols=cols,
        components=components,
        defects=all_system_a + all_system_b,
    )


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

DEFECT_FIELDS = [
    "panel_id", "component_row", "component_col", "source_system",
    "defect_type", "x", "y", "size", "confidence_score",
    "match_id", "created_at",
]

COMPONENT_FIELDS = [
    "panel_id", "component_row", "component_col",
    "region_id", "center_x", "center_y", "active",
]


def write_defects_csv(panel: SyntheticPanel, output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=DEFECT_FIELDS)
        writer.writeheader()
        for d in panel.defects:
            writer.writerow({
                "panel_id": d.panel_id,
                "component_row": d.component_row,
                "component_col": d.component_col,
                "source_system": d.source_system,
                "defect_type": d.defect_type,
                "x": d.x,
                "y": d.y,
                "size": d.size,
                "confidence_score": d.confidence_score,
                "match_id": d.match_id or "",
                "created_at": d.created_at.isoformat(),
            })
    logger.info("Wrote %d defect rows → %s", len(panel.defects), output_path)
    return output_path


def write_components_csv(panel: SyntheticPanel, output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COMPONENT_FIELDS)
        writer.writeheader()
        for c in panel.components:
            writer.writerow({
                "panel_id": c.panel_id,
                "component_row": c.component_row,
                "component_col": c.component_col,
                "region_id": c.region_id,
                "center_x": c.center_x,
                "center_y": c.center_y,
                "active": int(c.active),
            })
    return output_path
