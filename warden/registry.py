"""The platform registry — DataHub's record of what it is and isn't connected to.

DataHub tracks what it has ingested. It has no native concept of what it is
missing, which is precisely why an empty lineage result is ambiguous.

Structured properties would be the natural carrier, but DataHub's entity
registry does not support the structuredProperties aspect on dataPlatform
entities ("Unknown aspect structuredProperties for entity dataPlatform").
No entity type is designed to hold connector-coverage metadata, so Warden
materialises one registry dataset per platform and records coverage as
custom properties.

Writes happen through the SDK during ingestion — that layer simulates
connectors, which legitimately use the SDK. Every agent read goes through MCP.
"""

from pydantic import BaseModel

from warden.agent.mcp_client import MCPClient

REGISTRY_PLATFORM = "warden"
KEY_PLATFORM = "platform"
KEY_CONNECTOR = "lineageConnectorConfigured"
KEY_EXPECTED_COUNT = "expectedEntityCount"
KEY_NOTE = "registryNote"

KEY_HOSTS_CONSUMERS = "hostsConsumers"


class PlatformRecord(BaseModel):
    platform: str
    lineage_connector_configured: bool
    expected_entity_count: int
    note: str
    hosts_consumers: bool = True

    def to_custom_properties(self) -> dict[str, str]:
        return {
            KEY_PLATFORM: self.platform,
            KEY_CONNECTOR: str(self.lineage_connector_configured).lower(),
            KEY_EXPECTED_COUNT: str(self.expected_entity_count),
            KEY_NOTE: self.note,
            KEY_HOSTS_CONSUMERS: str(self.hosts_consumers).lower(),
        }

    @classmethod
    def from_custom_properties(cls, props: dict[str, str]) -> "PlatformRecord":
        return cls(
            platform=props.get(KEY_PLATFORM, "unknown"),
            lineage_connector_configured=props.get(KEY_CONNECTOR) == "true",
            expected_entity_count=int(props.get(KEY_EXPECTED_COUNT, 0)),
            note=props.get(KEY_NOTE, ""),
            hosts_consumers=props.get(KEY_HOSTS_CONSUMERS, "true") == "true",
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
    return [
        PlatformRecord.from_custom_properties(props)
        for entity in _as_entities(result)
        if (props := _custom_properties(entity))
    ]


def _as_entities(result: object) -> list[dict]:
    """get_entities returns a bare list on current versions; older shapes nest
    it under an "entities" key."""
    if isinstance(result, list):
        return [e for e in result if isinstance(e, dict)]
    if isinstance(result, dict):
        nested = result.get("entities", [])
        if isinstance(nested, list):
            return [e for e in nested if isinstance(e, dict)]
    return []


def _custom_properties(entity: dict) -> dict[str, str]:
    """Find the customProperties map wherever it sits in the response.

    DataHub nests this differently across entity shapes and versions, so walk
    the structure for the key rather than assuming a path.
    """
    found: dict[str, str] = {}

    def absorb(candidate: object) -> None:
        if isinstance(candidate, dict):
            pairs = {str(k): str(v) for k, v in candidate.items()}
        elif isinstance(candidate, list):
            pairs = {
                str(item.get("key")): str(item.get("value"))
                for item in candidate
                if isinstance(item, dict) and "key" in item
            }
        else:
            return
        if KEY_PLATFORM in pairs:
            found.update(pairs)

    def walk(node: object) -> None:
        if isinstance(node, dict):
            absorb(node.get("customProperties"))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(entity)
    return found
