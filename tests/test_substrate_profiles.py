"""
tests/test_substrate_profiles.py
---------------------------------
Unit tests for synthetic/substrate_profiles.py
"""

import pytest
from openyield.synthetic.substrate_profiles import (
    SubstrateType, SubstrateProfile, get_profile, list_substrate_types
)


def test_all_three_types_registered():
    types = list_substrate_types()
    assert "glass_panel" in types
    assert "wafer" in types


def test_get_profile_by_enum():
    profile = get_profile(SubstrateType.WAFER)
    assert profile.substrate_type == SubstrateType.WAFER


def test_get_profile_by_string():
    profile = get_profile("wafer")
    assert profile.substrate_type == SubstrateType.WAFER


def test_get_profile_invalid_string():
    with pytest.raises(ValueError, match="Unknown substrate type"):
        get_profile("silicon_carbide")


@pytest.mark.parametrize("st", ["glass_panel", "wafer"])
def test_profile_fields_are_positive(st):
    p = get_profile(st)
    assert p.component_pitch_mm > 0
    assert p.component_half_width_mm > 0
    assert p.cluster_std_mm > 0
    assert p.n_clusters >= 1
    assert p.mean_defect_count > 0
    assert p.match_distance_threshold > 0
    assert p.size_lognormal_sigma > 0


@pytest.mark.parametrize("st", ["glass_panel", "wafer"])
def test_confidence_ranges_valid(st):
    p = get_profile(st)
    assert 0.0 <= p.system_a_confidence_lo <= p.system_a_confidence_hi <= 1.0
    assert 0.0 <= p.system_b_confidence_lo <= p.system_b_confidence_hi <= 1.0


@pytest.mark.parametrize("st", ["glass_panel", "wafer"])
def test_detection_and_fp_rates_valid(st):
    p = get_profile(st)
    assert 0.0 < p.system_b_detection_rate <= 1.0
    assert 0.0 <= p.system_a_fp_rate < 1.0


@pytest.mark.parametrize("st", ["glass_panel", "wafer"])
def test_defect_types_nonempty(st):
    p = get_profile(st)
    assert len(p.defect_types) >= 1
    for dt in p.defect_types:
        assert isinstance(dt, str) and len(dt) > 0


@pytest.mark.parametrize("st", ["glass_panel", "wafer"])
def test_product_types_nonempty(st):
    p = get_profile(st)
    assert len(p.product_types) >= 1


def test_profile_is_immutable():
    p = get_profile("wafer")
    with pytest.raises(Exception):
        p.mean_defect_count = 999.0


def test_wafer_has_smaller_pitch_than_glass():
    wafer = get_profile("wafer")
    glass = get_profile("glass_panel")
    assert wafer.component_pitch_mm < glass.component_pitch_mm


def test_wafer_has_tighter_noise_than_glass():
    wafer = get_profile("wafer")
    glass = get_profile("glass_panel")
    assert wafer.system_a_noise_std < glass.system_a_noise_std
    assert wafer.system_b_noise_std < glass.system_b_noise_std
