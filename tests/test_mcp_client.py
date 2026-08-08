import pytest

from tests.fakes.mcp_double import FakeMCPClient
from warden.agent.models import EntityRef, LineageEdge, Provenance


@pytest.fixture
def fake_client() -> FakeMCPClient:
    return FakeMCPClient()


@pytest.mark.asyncio
async def test_search_finds_seeded_entity(fake_client: FakeMCPClient):
    fake_client.seed_entity("urn:li:dataset:(urn:li:dataPlatform:dbt,orders,PROD)")
    result = await fake_client.search("orders")
    assert result["total"] == 1


@pytest.mark.asyncio
async def test_search_misses_unrelated_query(fake_client: FakeMCPClient):
    fake_client.seed_entity("urn:li:dataset:(urn:li:dataPlatform:dbt,orders,PROD)")
    result = await fake_client.search("nonexistent")
    assert result["total"] == 0


@pytest.mark.asyncio
async def test_write_then_read_tags(fake_client: FakeMCPClient):
    urn = "urn:li:dataset:(urn:li:dataPlatform:dbt,orders,PROD)"
    await fake_client.add_tags(urn, ["warden-verified"])
    assert "warden-verified" in fake_client.written_tags[urn]


@pytest.mark.asyncio
async def test_lineage_carries_provenance(fake_client: FakeMCPClient):
    upstream = EntityRef(
        urn="urn:li:dataset:(urn:li:dataPlatform:dbt,raw_orders,PROD)",
        platform="dbt",
        name="raw_orders",
        entity_type="dataset",
    )
    downstream = EntityRef(
        urn="urn:li:dataset:(urn:li:dataPlatform:dbt,orders,PROD)",
        platform="dbt",
        name="orders",
        entity_type="dataset",
    )
    edge = LineageEdge(
        upstream=upstream, downstream=downstream, provenance=Provenance.INFERRED, confidence=0.7
    )
    fake_client.seed_lineage(downstream.urn, [edge])

    result = await fake_client.get_lineage(downstream.urn)
    assert result["edges"][0]["provenance"] == "inferred"