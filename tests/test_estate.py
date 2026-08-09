from world.estate import COVERED, DARK, ESTATE, expected_entity_count, platforms_lacking_lineage
from world.scenarios import NEAR_MISSES, SCENARIOS, ExpectedOutcome


def test_dark_profile_cannot_see_tableau():
    dark_gaps = {p.name for p in platforms_lacking_lineage(DARK)}
    assert "tableau" in dark_gaps


def test_covered_profile_has_no_gaps():
    assert platforms_lacking_lineage(COVERED) == []


def test_expected_count_exceeds_dark_visibility():
    """The denominator must include what the dark profile cannot see —
    otherwise coverage always computes to 100%."""
    dark_visible = sum(p.entity_count for p in ESTATE if p.name in DARK.platforms)
    assert expected_entity_count(DARK) > dark_visible


def test_suite_contains_near_misses():
    """A scenario set with no safe cases cannot demonstrate absence of
    false positives."""
    assert len(NEAR_MISSES) >= 3
    assert all(s.expected == ExpectedOutcome.SAFE for s in NEAR_MISSES)


def test_scenario_ids_are_unique():
    ids = [s.id for s in SCENARIOS]
    assert len(ids) == len(set(ids))
