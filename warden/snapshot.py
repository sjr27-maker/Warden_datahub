"""Capture a live DataHub subgraph to a file, and replay it offline.

Warden's central claim — that coverage arithmetic gates code generation — is
deterministic and needs no live catalog to demonstrate. A committed snapshot
lets a reviewer see the covered/dark contrast in seconds without Docker, an
8GB stack, or a token.

The snapshot is *captured* from a real instance, so it is not a hand-authored
fixture. What it cannot do is prove the live path works; the live demo remains
the evidence for that.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from warden.agent.mcp_client import MCPClient
from warden.registry import PlatformRecord, read_registry

logger = logging.getLogger(__name__)

SNAPSHOT_DIR = Path("snapshots")

# The Scoper widens hop by hop; a snapshot must record each depth separately
# or replay collapses multi-hop traversal to a single hop.
CAPTURED_HOPS = (1, 2, 3)
CAPTURED_COLUMNS: tuple[str | None, ...] = (None, "cust_id", "quantity", "amount_usd")


class GraphSnapshot(BaseModel):
    """A frozen read of the catalog. Write paths are recorded, not performed."""

    profile: str
    captured_at: str
    registry: list[PlatformRecord]
    search: dict[str, list[str]] = Field(default_factory=dict)
    lineage: dict[str, list[str]] = Field(default_factory=dict)

    @staticmethod
    def lineage_key(urn: str, upstream: bool, column: str | None, max_hops: int) -> str:
        return f"{urn}|{'up' if upstream else 'down'}|{column or '*'}|{max_hops}"


class SnapshotClient:
    """Replays a captured snapshot through the MCPClient read interface.

    Writes are accepted and recorded so the pipeline runs end to end, but
    nothing is persisted — an offline run must not claim to have written back.
    """

    def __init__(self, snapshot: GraphSnapshot) -> None:
        self._snapshot = snapshot
        self.writes: list[dict] = []

    @classmethod
    def load(cls, path: Path) -> "SnapshotClient":
        return cls(GraphSnapshot.model_validate_json(path.read_text()))

    @property
    def profile(self) -> str:
        return self._snapshot.profile

    async def search(self, query: str, num_results: int = 10, **_) -> dict:
        urns = self._snapshot.search.get(query, [])
        return {"total": len(urns), "results": [{"urn": u} for u in urns]}

    async def search_with_retry(self, query: str, **_) -> dict:
        return await self.search(query)

    async def get_entities(self, urns: list[str] | str) -> list[dict]:
        wanted = [urns] if isinstance(urns, str) else urns
        return [
            {
                "urn": record.registry_urn,
                "properties": {"customProperties": record.to_custom_properties()},
            }
            for record in self._snapshot.registry
            if record.registry_urn in wanted
        ]

    async def get_lineage(
        self,
        urn: str,
        upstream: bool = True,
        column: str | None = None,
        max_hops: int = 1,
        **_,
    ) -> dict:
        key = GraphSnapshot.lineage_key(urn, upstream, column, max_hops)
        urns = self._snapshot.lineage.get(key, [])
        return {"upstreams": {"total": len(urns)}, "results": [{"urn": u} for u in urns]}

    async def search_documents(self, query: str = "*", num_results: int = 10) -> dict:
        return {"documents": []}

    async def add_tags(self, tag_urns: list[str], entity_urns: list[str], **_) -> dict:
        self.writes.append({"op": "add_tags", "tags": tag_urns, "entities": entity_urns})
        return {"offline": True}

    async def update_description(self, entity_urn: str, description: str, **_) -> dict:
        self.writes.append({"op": "update_description", "urn": entity_urn})
        return {"offline": True}

    async def save_document(self, document_type: str, title: str, content: str, **_) -> dict:
        self.writes.append({"op": "save_document", "type": document_type, "title": title})
        return {"offline": True}


async def capture(client: MCPClient, profile: str, roots: list[str]) -> GraphSnapshot:
    """Record every read Warden makes for the demo scenarios.

    Deliberately narrow: only the queries the committed scenarios issue. A
    full catalog dump would be larger, staler, and no more convincing.
    """
    registry = await read_registry(client, ["dbt", "duckdb", "tableau", "python"])
    snapshot = GraphSnapshot(
        profile=profile,
        captured_at=datetime.now(timezone.utc).isoformat(),
        registry=registry,
    )

    for root in roots:
        found = await client.search(root, num_results=10)
        snapshot.search[root] = _urns(found)

        for urn in snapshot.search[root]:
            for upstream in (True, False):
                for column in CAPTURED_COLUMNS:
                    for hops in CAPTURED_HOPS:
                        result = await client.get_lineage(
                            urn, upstream=upstream, column=column, max_hops=hops
                        )
                        key = GraphSnapshot.lineage_key(urn, upstream, column, hops)
                        snapshot.lineage[key] = _urns(result)

    return snapshot


def _urns(payload: object) -> list[str]:
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, str) and node.startswith("urn:li:dataset:"):
            found.append(node)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return list(dict.fromkeys(found))


def write(snapshot: GraphSnapshot, directory: Path = SNAPSHOT_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{snapshot.profile}.json"
    path.write_text(snapshot.model_dump_json(indent=2))
    return path