import pytest

from warden.registry import PlatformRecord
from warden.snapshot import GraphSnapshot, SnapshotClient


def _snapshot(dark: bool) -> GraphSnapshot:
    return GraphSnapshot(
        profile="dark" if dark else "covered",
        captured_at="2026-08-10T00:00:00Z",
        registry=[
            PlatformRecord(
                platform="dbt",
                lineage_connector_configured=True,
                expected_entity_count=16,
                note="",
            ),
            PlatformRecord(
                platform="tableau",
                lineage_connector_configured=not dark,
                expected_entity_count=4,
                note="",
            ),
        ],
        search={"stg_orders": ["urn:li:dataset:(urn:li:dataPlatform:dbt,stg_orders,PROD)"]},
        lineage={},
    )


@pytest.mark.asyncio
async def test_snapshot_replays_registry_state():
    client = SnapshotClient(_snapshot(dark=True))
    entities = await client.get_entities(
        ["urn:li:dataset:(urn:li:dataPlatform:warden,registry.tableau,PROD)"]
    )
    props = entities[0]["properties"]["customProperties"]
    assert props["lineageConnectorConfigured"] == "false"


@pytest.mark.asyncio
async def test_offline_writes_are_recorded_but_not_claimed():
    """An offline run must not present itself as having written back."""
    client = SnapshotClient(_snapshot(dark=True))
    result = await client.save_document(document_type="Decision", title="t", content="{}")

    assert result["offline"] is True
    assert client.writes[0]["op"] == "save_document"


@pytest.mark.asyncio
async def test_search_returns_captured_urns():
    client = SnapshotClient(_snapshot(dark=False))
    result = await client.search("stg_orders")
    assert result["total"] == 1