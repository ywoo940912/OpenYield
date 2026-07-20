"""
synthetic/substrate_profiles.py
--------------------------------
Author: Yeonkuk Woo

Substrate-specific inspection parameters for the OpenYield synthetic
data generator.

OpenYield focuses on semiconductor and semiconductor-adjacent substrates
relevant to U.S. domestic manufacturing under the CHIPS and Science Act:

  - Silicon wafer  : advanced logic, memory, and analog process nodes
  - Glass panel    : flat panel display (OLED/LCD) for defense, aerospace,
                     and consumer electronics — CHIPS-adjacent manufacturing

Each SubstrateProfile encodes everything that differs between substrate
types. The generator itself never branches on substrate type; it reads
whatever the profile says.

Adding a new substrate type
---------------------------
1. Add a value to SubstrateType.
2. Define a SubstrateProfile dataclass instance.
3. Register it in _PROFILES.
No changes required anywhere else.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from openyield.defect_types import WAFER_DEFECT_TYPES, GLASS_PANEL_DEFECT_TYPES


# ---------------------------------------------------------------------------
# Substrate type enum
# ---------------------------------------------------------------------------

class SubstrateType(str, Enum):
    GLASS_PANEL = "glass_panel"
    WAFER       = "wafer"


# ---------------------------------------------------------------------------
# Profile dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SubstrateProfile:
    """
    All substrate-specific constants consumed by the generator.

    Spatial parameters
    ------------------
    component_pitch_mm        : Center-to-center distance between unit cells (mm).
    component_half_width_mm   : Half the unit cell width — bounds cluster centers.
    cluster_std_mm            : Gaussian spread of defects around a cluster center.
    n_clusters                : Number of spatial cluster centers per unit cell.
    match_distance_threshold  : Max mm distance for cross-system defect matching.

    Defect generation
    -----------------
    mean_defect_count         : Poisson λ — expected defects per active unit cell.
    defect_types              : Vocabulary of defect class labels for this substrate.
    size_lognormal_mean       : Log-scale mean for defect size distribution (mm).
    size_lognormal_sigma      : Log-scale sigma for defect size distribution.

    System A — high-throughput automated scanner (noisier, more FPs)
    ----------------------------------------------------------------
    system_a_noise_std        : Gaussian coordinate noise std dev (mm).
    system_a_fp_rate          : False-positive injection rate (fraction of TP count).
    system_a_confidence_lo/hi : Uniform confidence score range.

    System B — verification / review tool (tighter, fewer detections)
    -----------------------------------------------------------------
    system_b_noise_std        : Gaussian coordinate noise std dev (mm).
    system_b_detection_rate   : Fraction of ground-truth defects detected.
    system_b_confidence_lo/hi : Uniform confidence score range.

    Metadata
    --------
    substrate_type            : SubstrateType enum value.
    product_types             : Pool of product labels for random assignment.
    """

    substrate_type:             SubstrateType
    product_types:              tuple[str, ...]

    # Spatial
    component_pitch_mm:         float
    component_half_width_mm:    float
    cluster_std_mm:             float
    n_clusters:                 int
    match_distance_threshold:   float

    # Defect generation
    mean_defect_count:          float
    defect_types:               tuple[str, ...]
    size_lognormal_mean:        float
    size_lognormal_sigma:       float

    # System A
    system_a_noise_std:         float
    system_a_fp_rate:           float
    system_a_confidence_lo:     float
    system_a_confidence_hi:     float

    # Yield modelling
    clustering_alpha_default:   float   # fixed α for profile-based method
    use_empirical_alpha:        bool    # True = fit from data; False = use profile value

    # Critical area extraction (Maly linear expansion model)
    layout_density:             float   # fraction of die area covered by killable features
    min_feature_mm:             float   # minimum critical feature dimension (mm)

    # System B
    system_b_noise_std:         float
    system_b_detection_rate:    float
    system_b_confidence_lo:     float
    system_b_confidence_hi:     float


# ---------------------------------------------------------------------------
# Glass panel profile
# Covers two product families under a single substrate type:
#
#   GCS / PLP  — Glass Core Substrate with Through-Glass Vias (TGV),
#                e.g. Absolics, AGC, Schott, Corning.  Inspection tooling:
#                wide-field optical (system_a) + laser confocal (system_b).
#
#   FPD        — Flat Panel Display backplane (TFT-LCD, OLED, AMOLED) for
#                defense, aerospace HMI, and consumer electronics.
#                Inspection: Gen-8/10 AOI (system_a) + confocal (system_b).
#
# Spatial parameters reflect Gen-8 panel scale (2200×2500 mm) for FPD.
# For GCS (e.g. SEMI M77 510×515 mm), the generator pitch can be overridden
# via lot metadata — the defect types and system noise models are shared.
# ---------------------------------------------------------------------------

_GLASS_PANEL_PROFILE = SubstrateProfile(
    substrate_type=SubstrateType.GLASS_PANEL,
    product_types=(
        # Generic GCS/PLP labels — users define their own product identifiers
        "GCS-TGV-TYPE-A", "GCS-TGV-TYPE-B", "GCS-PLP-TYPE-A",
    ),

    # Gen-8 panel: ~2200×2500mm. 6×6 grid → ~370mm pitch (typical array cell).
    # For GCS 510×515mm panels the pitch is ~85mm — profile shared; seeder
    # overrides pitch via lot metadata when needed.
    component_pitch_mm=370.0,
    component_half_width_mm=170.0,
    cluster_std_mm=12.0,
    n_clusters=3,
    match_distance_threshold=15.0,

    # Full defect vocabulary — all glass panel defect types
    mean_defect_count=3.2,
    defect_types=tuple(GLASS_PANEL_DEFECT_TYPES),
    size_lognormal_mean=-1.2,   # median ~0.30 mm
    size_lognormal_sigma=0.6,

    # System A: wide-field AOI — moderate noise, ~15% FP rate
    system_a_noise_std=2.5,
    system_a_fp_rate=0.15,
    system_a_confidence_lo=0.55,
    system_a_confidence_hi=0.90,

    # System B: confocal review — tight noise, detects ~80% of GT defects
    system_b_noise_std=0.8,
    system_b_detection_rate=0.80,
    system_b_confidence_lo=0.80,
    system_b_confidence_hi=0.99,

    # Yield modelling: glass panels have fewer unit cells — use fixed α
    clustering_alpha_default=1.0,
    use_empirical_alpha=False,

    # Critical area: TFT arrays / RDL ~55% of panel area; min feature ~100µm
    layout_density=0.55,
    min_feature_mm=0.100,
)


# ---------------------------------------------------------------------------
# Wafer profile
# Represents optical scanner (system_a) + e-beam review (system_b).
# 300mm wafer, 10×10 die grid with edge exclusion.
# Directly relevant to U.S. advanced node fabs (logic, DRAM, flash).
# ---------------------------------------------------------------------------

_WAFER_PROFILE = SubstrateProfile(
    substrate_type=SubstrateType.WAFER,
    product_types=("LOGIC-7NM", "DRAM-1ALPHA", "FLASH-3D-128L", "ANALOG-180NM"),

    # 300mm wafer, 10×10 die grid → ~28mm pitch (typical logic die)
    component_pitch_mm=28.0,
    component_half_width_mm=12.0,
    cluster_std_mm=1.2,
    n_clusters=2,
    match_distance_threshold=2.0,

    # Advanced node wafers: very low defect density
    mean_defect_count=1.4,
    defect_types=tuple(WAFER_DEFECT_TYPES),
    size_lognormal_mean=-2.5,   # median ~0.082 mm (82 µm)
    size_lognormal_sigma=0.5,

    # System A: optical scanner — tighter than AOI but noisier than e-beam
    system_a_noise_std=0.4,
    system_a_fp_rate=0.20,
    system_a_confidence_lo=0.50,
    system_a_confidence_hi=0.85,

    # System B: e-beam review — very tight, detects ~70% (SEM is selective)
    system_b_noise_std=0.08,
    system_b_detection_rate=0.70,
    system_b_confidence_lo=0.85,
    system_b_confidence_hi=0.99,

    # Yield modelling: wafer has 100 dies — enough to fit α empirically
    clustering_alpha_default=0.5,
    use_empirical_alpha=True,

    # Critical area: logic die routing covers ~30% of area; 50µm inspection-scale feature
    layout_density=0.30,
    min_feature_mm=0.050,
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_PROFILES: dict[SubstrateType, SubstrateProfile] = {
    SubstrateType.GLASS_PANEL: _GLASS_PANEL_PROFILE,
    SubstrateType.WAFER:       _WAFER_PROFILE,
}


def get_profile(substrate_type: SubstrateType | str) -> SubstrateProfile:
    """
    Return the SubstrateProfile for the given substrate type.

    Accepts SubstrateType enum values or plain strings
    ('glass_panel', 'wafer').
    """
    if isinstance(substrate_type, str):
        try:
            substrate_type = SubstrateType(substrate_type)
        except ValueError:
            valid = [e.value for e in SubstrateType]
            raise ValueError(
                f"Unknown substrate type: {substrate_type!r}. "
                f"Valid options: {valid}"
            )
    if substrate_type not in _PROFILES:
        raise KeyError(f"No profile registered for: {substrate_type}")
    return _PROFILES[substrate_type]


def list_substrate_types() -> list[str]:
    """Return all registered substrate type strings."""
    return [st.value for st in _PROFILES]
