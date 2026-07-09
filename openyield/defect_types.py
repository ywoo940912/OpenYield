"""
openyield/defect_types.py
--------------------------
Author: Yeonkuk Woo

Single source of truth for the OpenYield defect taxonomy.

Two separate vocabularies exist because wafer and glass panel substrates
fail through fundamentally different physical mechanisms:

  - Wafer defects:       process contamination, lithography, etch, CMP, and
                         metallisation artifacts on silicon.
  - Glass panel defects: glass bulk/surface integrity, through-glass via (TGV)
                         formation, and display backplane circuit failures.
                         Covers both glass core substrates (GCS/PLP — e.g. Absolics,
                         AGC, Schott) and flat panel displays (TFT-LCD, OLED, …).

Import from here everywhere; do not hardcode defect type strings elsewhere.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Wafer — semiconductor process defects (silicon, SiC, GaN, …)
# ---------------------------------------------------------------------------

WAFER_DEFECT_TYPES: list[str] = [
    "particle",       # foreign particle on wafer surface
    "scratch",        # surface mechanical damage from CMP or handling
    "void",           # missing material / unfilled trench or contact
    "pit",            # surface depression / etch pit
    "contamination",  # chemical spread (metals, organics)
    "pinhole",        # ultra-thin spot in dielectric or metal film
    "line_defect",    # broken or anomalous metal line (lithography / etch)
    "open_circuit",   # open in metal interconnect
    "short_circuit",  # unintended metal bridge
    "metal_spike",    # Al hillock / electromigration spike
    "bridging",       # resist or metal bridging between adjacent lines
    "crystal_defect", # stacking fault, dislocation, epitaxial grain boundary
]

# ---------------------------------------------------------------------------
# Glass Panel — glass core substrate (GCS/TGV) + flat panel display defects
# ---------------------------------------------------------------------------

# Surface and bulk defects — common to both GCS and FPD
GLASS_SURFACE_DEFECT_TYPES: list[str] = [
    "particle",       # foreign particle on glass surface
    "scratch",        # surface mechanical scratch from handling / polishing
    "contamination",  # chemical contamination spread
    "pinhole",        # pinholes in coating / film stack
    "inclusion",      # foreign material embedded inside the glass bulk
    "micro_crack",    # hairline crack in glass — critical killer defect for GCS
    "chipping",       # edge or corner chipping / missing glass material
    "delamination",   # interlayer separation in glass stack or coating
]

# TGV (Through-Glass Via) defects — GCS / panel-level packaging specific
GLASS_TGV_DEFECT_TYPES: list[str] = [
    "tgv_open",       # via failed to form fully — no conductive path
    "tgv_misalign",   # via drilled off nominal target location
    "tgv_partial",    # via incompletely etched — does not reach through
]

# Circuit / display defects — TFT backplane (FPD) and redistribution layers (GCS)
GLASS_CIRCUIT_DEFECT_TYPES: list[str] = [
    "open_circuit",   # broken metal trace in backplane / RDL
    "short_circuit",  # unintended metal bridge
    "line_defect",    # dead or anomalous pixel line (FPD) / RDL line defect
    "pixel_defect",   # single bright or dark pixel (FPD-specific)
    "mura",           # luminance non-uniformity / spatial brightness variation (FPD)
]

GLASS_PANEL_DEFECT_TYPES: list[str] = (
    GLASS_SURFACE_DEFECT_TYPES
    + GLASS_TGV_DEFECT_TYPES
    + GLASS_CIRCUIT_DEFECT_TYPES
)

# ---------------------------------------------------------------------------
# Combined registry
# ---------------------------------------------------------------------------

DEFECT_TYPES_BY_SUBSTRATE: dict[str, list[str]] = {
    "wafer":       WAFER_DEFECT_TYPES,
    "glass_panel": GLASS_PANEL_DEFECT_TYPES,
}

ALL_DEFECT_TYPES: list[str] = sorted(
    set(WAFER_DEFECT_TYPES + GLASS_PANEL_DEFECT_TYPES)
)


def get_defect_types(substrate_type: str) -> list[str]:
    """Return the defect type list for a given substrate type string."""
    types = DEFECT_TYPES_BY_SUBSTRATE.get(substrate_type)
    if types is None:
        raise ValueError(
            f"Unknown substrate type {substrate_type!r}. "
            f"Valid: {list(DEFECT_TYPES_BY_SUBSTRATE)}"
        )
    return types


def is_valid_defect_type(defect_type: str, substrate_type: str | None = None) -> bool:
    """Return True if defect_type is valid for the given substrate (or any if None)."""
    if substrate_type is None:
        return defect_type in ALL_DEFECT_TYPES
    return defect_type in DEFECT_TYPES_BY_SUBSTRATE.get(substrate_type, [])
