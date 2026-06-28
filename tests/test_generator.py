"""
tests/test_generator.py
-----------------------
Unit tests for synthetic/generator.py
"""

import pytest
from openyield.synthetic.generator import (
    generate_panel, generate_panel_id, assign_region, match_defects, DefectRecord
)
from openyield.synthetic.substrate_profiles import SubstrateType


# ---------------------------------------------------------------------------
# Panel ID
# ---------------------------------------------------------------------------

def test_panel_id_glass_prefix():
    pid = generate_panel_id(SubstrateType.GLASS_PANEL)
    assert pid.startswith("GP_")

def test_panel_id_wafer_prefix():
    pid = generate_panel_id(SubstrateType.WAFER)
    assert pid.startswith("WF_")

def test_panel_id_unique():
    ids = {generate_panel_id(SubstrateType.WAFER) for _ in range(100)}
    assert len(ids) == 100


# ---------------------------------------------------------------------------
# Region assignment
# ---------------------------------------------------------------------------

def test_glass_panel_regions():
    # top-left → NW
    assert assign_region(0, 0, 6, 6, SubstrateType.GLASS_PANEL) == "region_NW"
    # bottom-right → SE
    assert assign_region(5, 5, 6, 6, SubstrateType.GLASS_PANEL) == "region_SE"

def test_wafer_regions():
    # center → zone_center
    assert assign_region(5, 5, 10, 10, SubstrateType.WAFER) == "zone_center"
    # corner → zone_edge
    assert assign_region(0, 0, 10, 10, SubstrateType.WAFER) == "zone_edge"


# ---------------------------------------------------------------------------
# generate_panel
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("substrate", ["glass_panel", "wafer"])
def test_panel_substrate_type_matches(substrate):
    panel = generate_panel(rows=4, cols=4, substrate_type=substrate, seed=0)
    assert panel.substrate_type == substrate

@pytest.mark.parametrize("substrate", ["glass_panel", "wafer"])
def test_panel_grid_dimensions(substrate):
    panel = generate_panel(rows=4, cols=5, substrate_type=substrate, seed=1)
    assert panel.rows == 4
    assert panel.cols == 5
    assert len(panel.components) == 4 * 5

def test_panel_reproducible_with_seed():
    p1 = generate_panel(rows=4, cols=4, substrate_type="wafer", seed=42)
    p2 = generate_panel(rows=4, cols=4, substrate_type="wafer", seed=42)
    assert len(p1.defects) == len(p2.defects)
    assert p1.defects[0].x == p2.defects[0].x

def test_panel_different_seeds_differ():
    p1 = generate_panel(rows=6, cols=6, substrate_type="glass_panel", seed=1)
    p2 = generate_panel(rows=6, cols=6, substrate_type="glass_panel", seed=2)
    assert p1.panel_id != p2.panel_id

def test_defects_have_valid_source_systems():
    panel = generate_panel(rows=4, cols=4, substrate_type="wafer", seed=7)
    for d in panel.defects:
        assert d.source_system in ("system_a", "system_b")

def test_defects_confidence_in_range():
    panel = generate_panel(rows=4, cols=4, substrate_type="wafer", seed=5)
    for d in panel.defects:
        assert 0.0 <= d.confidence_score <= 1.0

def test_defects_size_positive():
    panel = generate_panel(rows=4, cols=4, substrate_type="glass_panel", seed=3)
    for d in panel.defects:
        assert d.size > 0

def test_system_a_count_gte_system_b():
    panel = generate_panel(rows=6, cols=6, substrate_type="glass_panel", seed=10)
    n_a = sum(1 for d in panel.defects if d.source_system == "system_a")
    n_b = sum(1 for d in panel.defects if d.source_system == "system_b")
    assert n_a >= n_b

def test_wafer_edge_exclusion():
    panel = generate_panel(rows=10, cols=10, substrate_type="wafer", seed=0)
    inactive = [c for c in panel.components if not c.active]
    assert len(inactive) > 0

def test_match_ids_are_symmetric():
    panel = generate_panel(rows=6, cols=6, substrate_type="glass_panel", seed=42)
    from collections import Counter
    match_systems = {}
    for d in panel.defects:
        if d.match_id:
            match_systems.setdefault(d.match_id, set()).add(d.source_system)
    for mid, systems in match_systems.items():
        assert len(systems) == 2, f"match_id {mid} only in {systems}"

def test_defect_types_from_profile():
    from openyield.synthetic.substrate_profiles import get_profile
    panel = generate_panel(rows=4, cols=4, substrate_type="wafer", seed=0)
    profile = get_profile("wafer")
    valid_types = set(profile.defect_types)
    for d in panel.defects:
        assert d.defect_type in valid_types


# ---------------------------------------------------------------------------
# match_defects
# ---------------------------------------------------------------------------

def _make_defect(x, y, system, panel_id="P1", row=0, col=0):
    from datetime import datetime, timezone
    return DefectRecord(
        panel_id=panel_id, component_row=row, component_col=col,
        source_system=system, defect_type="particle",
        x=x, y=y, size=0.5, confidence_score=0.8,
    )

def test_match_defects_close_pair():
    a = [_make_defect(10.0, 10.0, "system_a")]
    b = [_make_defect(10.1, 10.1, "system_b")]
    a_out, b_out = match_defects(a, b, distance_threshold=1.0)
    assert a_out[0].match_id is not None
    assert b_out[0].match_id == a_out[0].match_id

def test_match_defects_too_far():
    a = [_make_defect(0.0, 0.0, "system_a")]
    b = [_make_defect(100.0, 100.0, "system_b")]
    a_out, b_out = match_defects(a, b, distance_threshold=1.0)
    assert a_out[0].match_id is None
    assert b_out[0].match_id is None

def test_match_defects_different_components_not_matched():
    a = [_make_defect(10.0, 10.0, "system_a", row=0, col=0)]
    b = [_make_defect(10.1, 10.1, "system_b", row=0, col=1)]
    a_out, b_out = match_defects(a, b, distance_threshold=5.0)
    assert a_out[0].match_id is None
