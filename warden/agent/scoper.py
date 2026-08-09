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

MAX_HOPS = 3
MIN_MARGINAL_YIELD = 1

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
        choose'; that absence is where silent wrong answers begin.
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
        """Build the subgraph relevant to a proposed change.

        Downstream is what matters for blast radius — what breaks if this
        changes. Upstream is pulled to one hop for provenance context, since
        knowing where a column comes from informs what a change to it means.
        """
        root, ambiguity = await self.resolve(reference)

        if root is None:
            placeholder = EntityRef(
                urn=f"unresolved:{reference}",
                platform="?",
                name=reference,
                entity_type="dataset",
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

        await self._collect(
            root, upstream=False, column=column, entities=entities, edges=edges, trace=trace
        )
        await self._collect(
            root, upstream=True, column=column, entities=entities, edges=edges, trace=trace
        )

        return Subgraph(
            root=root,
            entities=list(entities.values()),
            edges=edges,
            ambiguities=[ambiguity] if ambiguity else [],
            relevance_trace=trace,
        )

    async def _collect(
        self,
        root: EntityRef,
        upstream: bool,
        column: str | None,
        entities: dict[str, EntityRef],
        edges: list[LineageEdge],
        trace: dict[str, str],
    ) -> None:
        """Expand one direction, widening hop by hop until the marginal yield
        drops. The server supports multi-hop directly, so each call replaces a
        frontier walk."""
        direction = "upstream" if upstream else "downstream"
        seen_before = len(entities)

        for hops in range(1, MAX_HOPS + 1):
            result = await self._client.get_lineage(
                root.urn,
                upstream=upstream,
                column=column if hops == 1 else None,
                max_hops=hops,
            )

            added = 0
            for urn in _extract_urns(result):
                ref = parse_urn(urn)
                if ref is None or ref.urn in entities:
                    continue
                entities[ref.urn] = ref
                added += 1
                edge = (
                    LineageEdge(
                        upstream=ref, downstream=root, provenance=Provenance.PARSED, confidence=1.0
                    )
                    if upstream
                    else LineageEdge(
                        upstream=root, downstream=ref, provenance=Provenance.PARSED, confidence=1.0
                    )
                )
                edges.append(edge)
                trace[ref.urn] = f"{direction}, within {hops} hop(s) of {root.name}"

            if added < MIN_MARGINAL_YIELD:
                logger.debug(
                    "%s expansion stopped at %d hop(s): marginal yield %d", direction, hops, added
                )
                break

        logger.debug("%s added %d entities", direction, len(entities) - seen_before)


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
