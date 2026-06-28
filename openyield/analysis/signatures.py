"""
analysis/signatures.py
-----------------------
Author: Yeonkuk Woo

Defect spatial pattern signature library for OpenYield.

Matches observed defect spatial patterns against a library of known
process-driven signatures. This is the core of KLA Klarity's root cause
suggestion engine — when the tool detects a pattern, it suggests the
most likely process source.

Signature matching approach
-----------------------------
Each signature is defined by spatial rules applied to the cluster
result and die-level defect distribution:

  center_cluster   : defects concentrated in zone_center dies
  edge_cluster     : defects concentrated in zone_edge dies
  scratch_linear   : defects aligned along a roughly linear path
                     (high aspect ratio bounding box)
  ring_pattern     : defects forming a ring (zone_mid overrepresented)
  random_scatter   : defects uniformly distributed (no zone bias)
  reticle_repeat   : same die positions repeat across panels
                     (detected by correlation module)
  edge_exclusion   : defects clustered near the wafer edge exclusion zone
  quadrant_bias    : >60% of defects in one quadrant (glass panel)

Each signature maps to a suggested root cause and recommended action.

Matching is rule-based (not ML) for transparency and auditability —
a fab engineer must be able to understand and verify every flag.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from openyield.db.connection import get_placeholder

logger = logging.getLogger(__name__)
Connection = Any


# ---------------------------------------------------------------------------
# Signature definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Signature:
    name:           str
    description:    str
    root_cause:     str
    recommended_action: str


SIGNATURE_LIBRARY: dict[str, Signature] = {
    "center_cluster": Signature(
        name="center_cluster",
        description="Defects concentrated in center zone dies.",
        root_cause=(
            "Center-region process non-uniformity. Common causes: "
            "gas flow pattern, RF power distribution, center-heavy "
            "deposition or etch rate."
        ),
        recommended_action=(
            "Check process uniformity maps. Compare center vs edge "
            "deposition rates. Inspect gas injector and showerhead."
        ),
    ),
    "edge_cluster": Signature(
        name="edge_cluster",
        description="Defects concentrated at wafer/panel edge zones.",
        root_cause=(
            "Edge effect. Common causes: edge ring wear, edge bead "
            "removal issues, edge-heavy particle shedding, edge seal "
            "contamination."
        ),
        recommended_action=(
            "Inspect edge ring and edge seal components. "
            "Review edge bead removal process. Check edge exclusion settings."
        ),
    ),
    "scratch_linear": Signature(
        name="scratch_linear",
        description="Defects aligned in a roughly linear pattern.",
        root_cause=(
            "Handling scratch or tool contact. Common causes: "
            "wafer handling arm contact, cassette damage, "
            "robotic end-effector contamination."
        ),
        recommended_action=(
            "Inspect all wafer handling components. Check end-effector "
            "pads. Review load/unload sequence for contact issues."
        ),
    ),
    "ring_pattern": Signature(
        name="ring_pattern",
        description="Defects forming a concentric ring in zone_mid.",
        root_cause=(
            "Ring-shaped contamination source. Common causes: "
            "O-ring particle shedding, focus ring wear, "
            "annular gas flow artifact."
        ),
        recommended_action=(
            "Inspect O-rings and focus ring for wear. "
            "Check for ring-shaped deposits on chamber walls."
        ),
    ),
    "random_scatter": Signature(
        name="random_scatter",
        description="Defects uniformly distributed with no zone bias.",
        root_cause=(
            "Random particle contamination. Common causes: "
            "airborne particles, process gas impurities, "
            "normal wear-level background contamination."
        ),
        recommended_action=(
            "Monitor trend over time. No immediate tool action required. "
            "Review filter maintenance schedule if density increases."
        ),
    ),
    "quadrant_bias": Signature(
        name="quadrant_bias",
        description="More than 60% of defects concentrated in one quadrant.",
        root_cause=(
            "Directional contamination or non-uniform process. "
            "Common causes: asymmetric gas flow, one-sided particle "
            "source, asymmetric chuck contact."
        ),
        recommended_action=(
            "Identify which quadrant is affected. Check process "
            "symmetry in that direction. Inspect nearby equipment components."
        ),
    ),
    "edge_exclusion_bleed": Signature(
        name="edge_exclusion_bleed",
        description=(
            "Defects appearing just inside the edge exclusion boundary."
        ),
        root_cause=(
            "Edge exclusion zone contamination bleeding inward. "
            "Common causes: edge bead residue, edge seal degradation."
        ),
        recommended_action=(
            "Review edge exclusion parameters. Inspect edge seal. "
            "Consider increasing edge exclusion radius."
        ),
    ),
}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class SignatureMatch:
    signature_name:     str
    confidence:         float    # 0.0–1.0 how well the pattern matches
    description:        str
    root_cause:         str
    recommended_action: str
    evidence:           str      # what spatial data triggered this match


@dataclass
class SignatureResult:
    panel_id:       str
    substrate_type: str
    calculated_at:  str
    matches:        list[SignatureMatch]   # sorted by confidence desc
    top_match:      SignatureMatch | None
    defect_count:   int
    zone_fractions: dict[str, float]       # zone → fraction of defects


# ---------------------------------------------------------------------------
# Core matching engine
# ---------------------------------------------------------------------------

def match_signatures(
    conn: Connection,
    panel_id: str,
    *,
    source_system: str = "system_a",
) -> SignatureResult:
    """
    Match defect spatial pattern against the signature library.

    Parameters
    ----------
    conn          : Database connection
    panel_id      : Panel to analyse
    source_system : Inspection system to use (default: system_a)

    Returns
    -------
    SignatureResult with ranked signature matches
    """
    ph = get_placeholder(conn)

    # Verify panel
    panel = conn.execute(
        f"SELECT * FROM panels WHERE panel_id={ph}", (panel_id,)
    ).fetchone()
    if panel is None:
        raise ValueError(f"Panel not found: {panel_id!r}")

    substrate_type = panel["substrate_type"]

    # Fetch defects on active dies with zone info
    rows = conn.execute(
        f"""SELECT d.defect_type, d.x, d.y, c.region_id,
                   d.component_row, d.component_col
            FROM defects d
            JOIN components c
              ON c.panel_id      = d.panel_id
             AND c.component_row = d.component_row
             AND c.component_col = d.component_col
            WHERE d.panel_id={ph}
              AND d.source_system={ph}
              AND c.active=1""",
        (panel_id, source_system)
    ).fetchall()

    if not rows:
        return SignatureResult(
            panel_id=panel_id,
            substrate_type=substrate_type,
            calculated_at=datetime.now(timezone.utc).isoformat(),
            matches=[],
            top_match=None,
            defect_count=0,
            zone_fractions={},
        )

    defect_count = len(rows)

    # Build zone distribution
    zone_counts: dict[str, int] = {}
    for r in rows:
        z = r["region_id"]
        zone_counts[z] = zone_counts.get(z, 0) + 1

    zone_fractions = {
        z: round(n / defect_count, 4)
        for z, n in zone_counts.items()
    }

    # Collect (x, y) coordinates for geometry checks
    xs = [r["x"] for r in rows]
    ys = [r["y"] for r in rows]

    matches: list[SignatureMatch] = []

    # ── Match each signature ──────────────────────────────────────────────

    # 1. center_cluster (wafer only)
    if substrate_type == "wafer":
        center_frac = zone_fractions.get("zone_center", 0.0)
        if center_frac > 0.4:
            conf = min(1.0, (center_frac - 0.4) / 0.4 + 0.5)
            matches.append(SignatureMatch(
                signature_name="center_cluster",
                confidence=round(conf, 3),
                **_sig_fields("center_cluster"),
                evidence=f"{center_frac*100:.1f}% of defects in zone_center",
            ))

    # 2. edge_cluster (wafer only)
    if substrate_type == "wafer":
        edge_frac = zone_fractions.get("zone_edge", 0.0)
        if edge_frac > 0.4:
            conf = min(1.0, (edge_frac - 0.4) / 0.4 + 0.5)
            matches.append(SignatureMatch(
                signature_name="edge_cluster",
                confidence=round(conf, 3),
                **_sig_fields("edge_cluster"),
                evidence=f"{edge_frac*100:.1f}% of defects in zone_edge",
            ))

    # 3. ring_pattern (wafer only) — zone_mid overrepresented
    if substrate_type == "wafer":
        mid_frac  = zone_fractions.get("zone_mid", 0.0)
        cen_frac  = zone_fractions.get("zone_center", 0.0)
        edge_frac = zone_fractions.get("zone_edge", 0.0)
        if mid_frac > 0.5 and mid_frac > cen_frac and mid_frac > edge_frac:
            conf = min(1.0, (mid_frac - 0.5) / 0.3 + 0.5)
            matches.append(SignatureMatch(
                signature_name="ring_pattern",
                confidence=round(conf, 3),
                **_sig_fields("ring_pattern"),
                evidence=(
                    f"zone_mid={mid_frac*100:.1f}% dominates "
                    f"(center={cen_frac*100:.1f}%, edge={edge_frac*100:.1f}%)"
                ),
            ))

    # 4. scratch_linear — high aspect ratio bounding box
    if defect_count >= 5:
        x_range = max(xs) - min(xs)
        y_range = max(ys) - min(ys)
        min_range = min(x_range, y_range)
        max_range = max(x_range, y_range)
        if min_range > 0:
            aspect = max_range / min_range
            if aspect > 4.0:
                conf = min(1.0, (aspect - 4.0) / 6.0 + 0.5)
                matches.append(SignatureMatch(
                    signature_name="scratch_linear",
                    confidence=round(conf, 3),
                    **_sig_fields("scratch_linear"),
                    evidence=(
                        f"Bounding box aspect ratio={aspect:.1f} "
                        f"({max_range:.1f}mm × {min_range:.1f}mm)"
                    ),
                ))

    # 5. quadrant_bias (glass panel only)
    if substrate_type == "glass_panel":
        for region, frac in zone_fractions.items():
            if frac > 0.6:
                conf = min(1.0, (frac - 0.6) / 0.3 + 0.5)
                matches.append(SignatureMatch(
                    signature_name="quadrant_bias",
                    confidence=round(conf, 3),
                    **_sig_fields("quadrant_bias"),
                    evidence=(
                        f"{frac*100:.1f}% of defects in {region}"
                    ),
                ))
                break

    # 6. random_scatter — fallback if nothing else matches well
    if not matches or max(m.confidence for m in matches) < 0.5:
        # Compute evenness of zone distribution
        n_zones = len(zone_fractions)
        if n_zones > 0:
            expected = 1.0 / n_zones
            unevenness = sum(
                abs(f - expected) for f in zone_fractions.values()
            ) / n_zones
            if unevenness < 0.2:
                matches.append(SignatureMatch(
                    signature_name="random_scatter",
                    confidence=round(0.9 - unevenness * 2, 3),
                    **_sig_fields("random_scatter"),
                    evidence=(
                        f"Zone distribution is uniform "
                        f"(unevenness={unevenness:.3f})"
                    ),
                ))

    # Sort by confidence descending
    matches.sort(key=lambda m: m.confidence, reverse=True)
    top_match = matches[0] if matches else None

    logger.info(
        "[%s] Signature match: top=%s (conf=%.2f) | %d candidates",
        panel_id,
        top_match.signature_name if top_match else "none",
        top_match.confidence if top_match else 0.0,
        len(matches),
    )

    return SignatureResult(
        panel_id=panel_id,
        substrate_type=substrate_type,
        calculated_at=datetime.now(timezone.utc).isoformat(),
        matches=matches,
        top_match=top_match,
        defect_count=defect_count,
        zone_fractions=zone_fractions,
    )


def match_all_panels(
    conn: Connection,
    *,
    substrate_type: str | None = None,
    source_system:  str = "system_a",
) -> list[SignatureResult]:
    """Run signature matching on all panels."""
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
            r = match_signatures(conn, row["panel_id"],
                                 source_system=source_system)
            results.append(r)
        except Exception as exc:
            logger.error("Signature match failed for %s: %s",
                         row["panel_id"], exc)
    return results


def _sig_fields(name: str) -> dict:
    """Extract description/root_cause/recommended_action from library."""
    sig = SIGNATURE_LIBRARY[name]
    return {
        "description":        sig.description,
        "root_cause":         sig.root_cause,
        "recommended_action": sig.recommended_action,
    }


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------

def print_signature_report(results: list[SignatureResult]) -> None:
    print(f"\n{'='*72}")
    print(f"  DEFECT SIGNATURE REPORT  ({len(results)} panel(s))")
    print(f"{'='*72}")

    for r in results:
        print(f"\n  Panel: {r.panel_id}  [{r.substrate_type}]  "
              f"{r.defect_count} defects")
        print(f"  Zone distribution: " +
              "  ".join(f"{z}={f*100:.0f}%" for z, f in r.zone_fractions.items()))

        if not r.matches:
            print("  No signature matched.")
            continue

        for i, m in enumerate(r.matches[:3]):
            marker = "  ►" if i == 0 else "   "
            print(f"{marker} [{m.confidence*100:.0f}%] {m.signature_name}")
            print(f"     Evidence: {m.evidence}")
            if i == 0:
                print(f"     Root cause: {m.root_cause[:80]}...")
                print(f"     Action: {m.recommended_action[:80]}...")

    print(f"\n{'='*72}\n")
