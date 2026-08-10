from tests.fakes.ceilings import ceiling
from warden.agent.models import BlindSpot, CoverageCeiling, CoverageReport


def _ceiling_with(spots: list[BlindSpot]) -> CoverageCeiling:
    return CoverageCeiling(
        report=CoverageReport(
            score=0.5,
            reachable_nodes=6,
            expected_nodes=27,
            parsed_edge_ratio=1.0,
            blind_spots=spots,
            inferred_edge_count=0,
        ),
        may_assert_safe=False,
        threshold_used=0.6,
    )


def test_non_blocking_spots_are_reported_but_do_not_gate():
    """A dark source platform is a real gap that cannot hide breakage.
    Treating every gap as blocking is what makes a tool cry wolf."""
    spots = [
        BlindSpot(description="inferred edges", blocks_generation=False),
        BlindSpot(description="orphan entity", blocks_generation=False),
    ]
    assert _ceiling_with(spots).blocking_spots == []


def test_consumer_hosting_platform_blocks():
    spots = [
        BlindSpot(
            description="tableau has no connector",
            affected_platform="tableau",
            blocks_generation=True,
        )
    ]
    assert len(_ceiling_with(spots).blocking_spots) == 1


def test_override_is_recorded_not_silent():
    """A team that knows its own estate must be able to proceed — on the
    record. An override that leaves no trace is worse than no gate."""
    overridden = CoverageCeiling(
        report=ceiling(may_assert_safe=False).report,
        may_assert_safe=True,
        threshold_used=0.6,
        overridden=True,
        override_reason="team confirmed no BI consumers",
    )
    assert overridden.overridden
    assert overridden.override_reason