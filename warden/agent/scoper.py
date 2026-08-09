"""Context selection: resolve what a change refers to, and pull the minimal
relevant subgraph.

Precision over recall. An agent handed nine irrelevant tables and one correct
one performs worse than one handed three correct ones, so expansion stops when
marginal relevance drops rather than at a fixed hop count.
"""

import logging
import re

from warden.agent.mcp_client import MCPClient
from warden.agent.models import (
    AmbiguousReferent,
    EntityRef,
    LineageEdge,
    Provenance,
    Subgraph,
)

logger = logging.getLogger(__name__)

MAX_HOPS = 4
MIN_MARGINAL_YIELD = 1  # stop when a hop adds fewer than this many new entities

_URN_RE = re.compile(r"urn:li:dataset:\(urn:li:dataPlatform:([^,]+),([^,]+),([^)]+)\)")


def parse_urn(urn: str) -> EntityRef | None:
    match = _URN_RE.match(urn)
    if not match:
        return None
    platform, name, _env = match.groups()
    return EntityRef(urn=urn, platform=platform, name=name, entity_type="dataset")


class Scoper:
    def __init__(self, client: MCPClient) -> None:
        self._client = client

    async def resolve(self, reference: str) -> tuple[EntityRef | None, AmbiguousReferent | None]:
        """Resolve a model name from a diff to a graph entity.

        Two equally-plausible candidates produce a flagged ambiguity, never a
        guess. Most systems have no representation for 'I found two and cannot
        choose'; that absence is where silent wrong answers come from.
        """
        result = await self._client.search(reference, num_results=10)
        candidates = [
            ref
            for urn in _extract_urns(result)
            if (ref := parse_urn(urn)) and ref.name == reference
        ]

        if not candidates:
            return None, None
        if len(candidates) == 1:
            return candidates[0], None

        return None, AmbiguousReferent(
            query=reference,
            candidates=candidates,
            reason=(
                f"{len(candidates)} entities named '{reference}' across platforms "
                f"{sorted({c.platform for c in candidates})}"
            ),
        )

    async def scope(self, reference: str, column: str | None = None) -> Subgraph:
        root, ambiguity = await self.resolve(reference)

        if root is None:
            placeholder = EntityRef(
                urn=f"unresolved:{reference}", platform="?", name=reference, entity_type="dataset"
            )
            return Subgraph(
                root=placeholder,
                entities=[],
                edges=[],
                ambiguities=[ambiguity] if ambiguity else [],
                relevance_trace={},
            )

        entities: dict[str, EntityRef] = {root.urn: root}
        edges: list[LineageEdge] = []
        trace: dict[str, str] = {root.urn: "root: the entity the change targets"}

        frontier = [root]
        for hop in range(1, MAX_HOPS + 1):
            added = await self._expand(frontier, column, entities, edges, trace, hop)
            if len(added) < MIN_MARGINAL_YIELD:
                logger.debug("stopping at hop %d: marginal yield %d", hop, len(added))
                break
            frontier = added

        return Subgraph(
            root=root,
            entities=list(entities.values()),
            edges=edges,
            ambiguities=[ambiguity] if ambiguity else [],
            relevance_trace=trace,
        )

    async def _expand(
        self,
        frontier: list[EntityRef],
        column: str | None,
        entities: dict[str, EntityRef],
        edges: list[LineageEdge],
        trace: dict[str, str],
        hop: int,
    ) -> list[EntityRef]:
        newly_added: list[EntityRef] = []
        for node in frontier:
            result = await self._client.get_lineage(node.urn, column=column if hop == 1 else None)
            for urn in _extract_urns(result):
                ref = parse_urn(urn)
                if ref is None or ref.urn in entities:
                    continue
                entities[ref.urn] = ref
                newly_added.append(ref)
                edges.append(
                    LineageEdge(
                        upstream=node,
                        downstream=ref,
                        provenance=Provenance.PARSED,
                        confidence=1.0,
                    )
                )
                trace[ref.urn] = f"hop {hop} from {node.name}"
        return newly_added


def _extract_urns(payload: object) -> list[str]:
    """MCP responses nest differently by tool and version. Walk the structure
    and collect anything URN-shaped rather than assuming one schema."""
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, str):
            if node.startswith("urn:li:dataset:"):
                found.append(node)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return list(dict.fromkeys(found))