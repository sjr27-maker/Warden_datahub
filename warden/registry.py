"""The platform registry — DataHub's record of what it is and isn't connected to.

DataHub tracks what it has ingested. It has no native concept of what it is
missing, which is precisely why an empty lineage result is ambiguous. Warden
writes that inventory into the graph as structured properties so the gap
becomes a queryable fact rather than an assumption.
"""

from pydantic import BaseModel

from warden.agent.mcp_client import MCPClient

PROP_NAMESPACE = "urn:li:structuredProperty:warden"

PROP_CONNECTOR_CONFIGURED = f"{PROP_NAMESPACE}.lineageConnectorConfigured"
PROP_EXPECTED_COUNT = f"{PROP_NAMESPACE}.expectedEntityCount"
PROP_NOTE = f"{PROP_NAMESPACE}.registryNote"


class PlatformRecord(BaseModel):
    platform: str
    lineage_connector_configured: bool
    expected_entity_count: int
    note: str

    @property
    def urn(self) -> str:
        return f"urn:li:dataPlatform:{self.platform}"


async def write_registry(client: MCPClient, records: list[PlatformRecord]) -> None:
    for record in records:
        await client.add_structured_properties(
            property_values={
                PROP_CONNECTOR_CONFIGURED: [str(record.lineage_connector_configured).lower()],
                PROP_EXPECTED_COUNT: [record.expected_entity_count],
                PROP_NOTE: [record.note],
            },
            entity_urns=[record.urn],
        )


async def read_registry(client: MCPClient, platforms: list[str]) -> list[PlatformRecord]:
    """Read the registry back from DataHub. The Skeptic uses this — it must
    never read estate.py directly, or coverage becomes self-certifying."""
    urns = [f"urn:li:dataPlatform:{p}" for p in platforms]
    result = await client.get_entities(urns)
    records: list[PlatformRecord] = []
    for entity in result.get("entities", []):
        props = _extract_properties(entity)
        records.append(
            PlatformRecord(
                platform=entity["urn"].split(":")[-1],
                lineage_connector_configured=props.get(PROP_CONNECTOR_CONFIGURED) == "true",
                expected_entity_count=int(props.get(PROP_EXPECTED_COUNT, 0)),
                note=props.get(PROP_NOTE, ""),
            )
        )
    return records


def _extract_properties(entity: dict) -> dict[str, str]:
    """Structured properties come back nested; shape varies by DataHub version.
    Verify against a live instance before trusting this parser."""
    out: dict[str, str] = {}
    for prop in entity.get("structuredProperties", {}).get("properties", []):
        urn = prop.get("structuredProperty", {}).get("urn")
        values = prop.get("values", [])
        if urn and values:
            out[urn] = str(values[0])
    return out