import pytest

from core.county_registry import COVERED_COUNTIES, HAZARD_BLUEPRINTS, get_county_advisory


def test_covered_counties_are_eastern_kenya():
    assert "Kitui" in COVERED_COUNTIES
    assert "Machakos" in COVERED_COUNTIES
    assert len(COVERED_COUNTIES) == 8
    assert len(set(COVERED_COUNTIES)) == len(COVERED_COUNTIES)


def test_advisory_returns_required_keys():
    advisory = get_county_advisory("Kitui", "Severe Drought")
    assert set(advisory.keys()) == {
        "primary_calamity",
        "vulnerability_drivers",
        "cascading_effects",
        "proactive_solutions",
    }
    assert advisory["primary_calamity"] == "Severe Drought"
    assert advisory["cascading_effects"]
    assert advisory["proactive_solutions"]


def test_advisory_drought_detection():
    assert get_county_advisory("Meru", "Severe Drought")["primary_calamity"] == "Severe Drought"
    assert get_county_advisory("Meru", "Aridity Risk")["primary_calamity"] == "Aridity Risk"


def test_advisory_defaults_to_flooding():
    advisory = get_county_advisory("Meru", "Heavy Rainfall")
    assert advisory["primary_calamity"] == "Heavy Rainfall"


def test_advisory_localizes_river_solution():
    advisory = get_county_advisory("Kitui", "Flooding & Landslides")
    localized = [
        s for s in advisory["proactive_solutions"]
        if s.startswith("Build and reinforce sandbag dykes")
    ]
    assert localized and "Ewaso Ng'iro" in localized[0]


def test_blueprints_have_solutions_and_effects():
    for blueprint in HAZARD_BLUEPRINTS.values():
        assert blueprint["cascading_effects"]
        assert blueprint["proactive_solutions"]
