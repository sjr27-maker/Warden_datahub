"""Shared coverage-ceiling fixtures.

Kept in one place so the two states — a graph complete enough to license a
safety claim, and one that is not — stay identical across every test that
depends on the distinction.
"""

from warden.agent.models import BlindSpot, CoverageCeiling, CoverageReport


def ceiling(*, may_assert_safe: bool, score: float = 0.9) -> CoverageCeiling:
    blind = (
        []
        if may_assert_safe
        else [
            BlindSpot(
                description="tableau has no lineage connector",
                affected_platform="tableau",
            )
        ]
    )
    return CoverageCeiling(
        report=CoverageReport(
            score=score,
            reachable_nodes=6,
            expected_nodes=27,
            parsed_edge_ratio=1.0,
            blind_spots=blind,
            inferred_edge_count=0,
        ),
        may_assert_safe=may_assert_safe,
        threshold_used=0.6,
    )


GOOD = ceiling(may_assert_safe=True)
DARK = ceiling(may_assert_safe=False, score=0.4)
