"""The platform registry — DataHub's record of what it is and isn't connected to.

DataHub tracks what it has ingested. It has no native concept of what it is
missing, which is precisely why an empty lineage result is ambiguous.

Structured properties would be the natural carrier, but DataHub's entity
registry does not support the structuredProperties aspect on dataPlatform
entities ("Unknown aspect structuredProperties for entity dataPlatform").
There is no entity type designed to hold connector-coverage metadata, so
Warden materialises one registry dataset per platform and records coverage
as custom properties.

Write happens through the SDK during ingestion — that layer simulates
connectors, which legitimately use the SDK. Every *agent* read goes through
MCP.
"""

from pydantic import BaseModel

from warden.agent.mcp_client import MCPClient

REGISTRY_PLATFORM = "warden"
KEY_PLATFORM = "platform"
KEY_CONNECTOR = "lineageConnectorConfigured"
KEY_EXPECTED_COUNT = "expectedEntityCount"
KEY_NOTE = "registryNote"


class PlatformRecord(BaseModel):
    platform: str
    lineage_connector_configured: bool
    expected_entity_count: int
    note: str

    @property
    def registry_urn(self) -> str:
        return (
            f"urn:li:dataset:(urn:li:dataPlatform:{REGISTRY_PLATFORM},"
            f"registry.{self.platform},PROD)"
        )

    def to_custom_properties(self) -> dict[str, str]:
        return {
            KEY_PLATFORM: self.platform,
            KEY_CONNECTOR: str(self.lineage_connector_configured).lower(),
            KEY_EXPECTED_COUNT: str(self.expected_entity_count),
            KEY_NOTE: self.note,
        }

    @classmethod
    def from_custom_properties(cls, props: dict[str, str]) -> "PlatformRecord":
        return cls(
            platform=props.get(KEY_PLATFORM, "unknown"),
            lineage_connector_configured=props.get(KEY_CONNECTOR) == "true",
            expected_entity_count=int(props.get(KEY_EXPECTED_COUNT, 0)),
            note=props.get(KEY_NOTE, ""),
        )


def registry_urn_for(platform: str) -> str:
    return f"urn:li:dataset:(urn:li:dataPlatform:{REGISTRY_PLATFORM},registry.{platform},PROD)"


async def read_registry(client: MCPClient, platforms: list[str]) -> list[PlatformRecord]:
    """Read the registry back over MCP.

    The Skeptic uses this and must never read estate.py directly — otherwise
    coverage becomes self-certifying.
    """
    urns = [registry_urn_for(p) for p in platforms]
    result = await client.get_entities(urns)
    records: list[PlatformRecord] = []
    for entity in result.get("entities", result if isinstance(result, list) else []):
        props = _custom_properties(entity)
        if props:
            records.append(PlatformRecord.from_custom_properties(props))
    return records


def _custom_properties(entity: dict) -> dict[str, str]:
    """Custom properties nest differently across DataHub response shapes."""
    for path in (
        ("properties", "customProperties"),
        ("datasetProperties", "customProperties"),
        ("customProperties",),
    ):
        node: object = entity
        for key in path:
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(key)
        if isinstance(node, dict):
            return {str(k): str(v) for k, v in node.items()}
    return {}
