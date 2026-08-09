import pytest

from warden.agent.models import AmbiguousReferent
from warden.agent.scoper import Scoper, parse_urn


class _FakeSearchClient:
    def __init__(self, urns: list[str]) -> None:
        self._urns = urns

    async def search(self, query: str, num_results: int = 10, **_):
        return {"results": [{"urn": u} for u in self._urns]}

    async def get_lineage(self, urn: str, column: str | None = None, **_):
        return {"upstreams": {"total": 0}}


def _urn(platform: str, name: str) -> str:
    return f"urn:li:dataset:(urn:li:dataPlatform:{platform},{name},PROD)"


def test_parse_urn_extracts_platform_and_name():
    ref = parse_urn(_urn("dbt", "fct_revenue"))
    assert ref is not None
    assert ref.platform == "dbt"
    assert ref.name == "fct_revenue"


@pytest.mark.asyncio
async def test_single_match_resolves_cleanly():
    scoper = Scoper(_FakeSearchClient([_urn("dbt", "fct_revenue")]))
    ref, ambiguity = await scoper.resolve("fct_revenue")
    assert ref is not None
    assert ambiguity is None


@pytest.mark.asyncio
async def test_two_candidates_flag_ambiguity_rather_than_guessing():
    """Picking one of two equally-plausible referents is how a silent wrong
    answer begins. The correct output is a flagged state, not a choice."""
    scoper = Scoper(
        _FakeSearchClient([_urn("dbt", "customer_ltv"), _urn("python", "customer_ltv")])
    )
    ref, ambiguity = await scoper.resolve("customer_ltv")

    assert ref is None
    assert isinstance(ambiguity, AmbiguousReferent)
    assert len(ambiguity.candidates) == 2


@pytest.mark.asyncio
async def test_ambiguity_propagates_into_subgraph():
    scoper = Scoper(
        _FakeSearchClient([_urn("dbt", "customer_ltv"), _urn("python", "customer_ltv")])
    )
    subgraph = await scoper.scope("customer_ltv")
    assert len(subgraph.ambiguities) == 1
