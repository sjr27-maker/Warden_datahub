"""Coverage audit and confidence ceiling.

Deterministic by design — coverage is arithmetic over the graph, with no LLM
in the path. That is what lets CI hard-gate the central claim: given this
graph, Warden refuses, regardless of which model is configured or reachable.

The Skeptic never sees a downstream conclusion. Its signature takes a
Subgraph and registry records, and nothing else. A self-critiquing agent
rationalises its own answer; an independent auditor cannot.
"""

import logging

from warden.agent.config import settings
from warden.agent.mcp_client import MCPClient
from warden.agent.models import (
    BlindSpot,
    CoverageCeiling,
    CoverageReport,
    Provenance,
    Subgraph,
)
from warden.registry import PlatformRecord, read_registry

logger = logging.getLogger(__name__)

KNOWN_PLATFORMS = ["dbt", "duckdb", "tableau", "python"]

# A change with real consequences touches more than a couple of entities.
# Below this, the traversal itself is the limiting factor, not the graph.
_EXPECTED_SUBGRAPH_SIZE = 6


async def load_registry(client: MCPClient) -> list[PlatformRecord]:
    return await read_registry(client, KNOWN_PLATFORMS)


def assess(subgraph: Subgraph, registry: list[PlatformRecord]) -> CoverageCeiling:
    """Compute coverage from the retrieved subgraph against the declared estate.

    The denominator comes from the registry, not from what was retrieved. An
    agent that measures 'how much of what I found did I understand' always
    reports complete coverage — the gap has to be measurable from outside the
    retrieval to be visible at all.

    Three independent factors, multiplied so that any one of them being poor
    caps the result. Averaging would let a strong factor mask a fatal one.
    """
    expected = sum(r.expected_entity_count for r in registry) or 1

    dark_entities = sum(
        r.expected_entity_count for r in registry if not r.lineage_connector_configured
    )
    # What fraction of the estate is observable at all, in principle.
    observable = 1.0 - (dark_entities / expected)

    parsed = sum(1 for e in subgraph.edges if e.provenance is Provenance.PARSED)
    inferred = sum(1 for e in subgraph.edges if e.provenance is Provenance.INFERRED)
    parsed_ratio = parsed / len(subgraph.edges) if subgraph.edges else 0.0

    # A subgraph that reaches nothing tells us nothing, however clean the
    # platforms are. Saturates once the traversal has real breadth.
    reach = min(1.0, len(subgraph.entities) / _EXPECTED_SUBGRAPH_SIZE)

    blind_spots = _blind_spots(subgraph, registry)

    score = round(observable * parsed_ratio * reach, 3)
    score = max(0.0, min(1.0, score))

    report = CoverageReport(
        score=score,
        reachable_nodes=len(subgraph.entities),
        expected_nodes=expected,
        parsed_edge_ratio=round(parsed_ratio, 3),
        blind_spots=blind_spots,
        inferred_edge_count=inferred,
    )

    return CoverageCeiling(
        report=report,
        may_assert_safe=score >= settings.coverage_threshold and not blind_spots,
        threshold_used=settings.coverage_threshold,
    )


def _blind_spots(subgraph: Subgraph, registry: list[PlatformRecord]) -> list[BlindSpot]:
    """Named, specific gaps. Never a vague score alone — 'confidence: medium'
    tells an engineer nothing they can act on."""
    spots: list[BlindSpot] = []

    for record in registry:
        if not record.lineage_connector_configured:
            spots.append(
                BlindSpot(
                    description=(
                        f"{record.platform} has no lineage connector configured; "
                        f"{record.expected_entity_count} entities are invisible, and any "
                        f"consumers among them cannot be detected"
                    ),
                    affected_platform=record.platform,
                )
            )

    orphans = _entities_without_upstream(subgraph)
    if orphans:
        spots.append(
            BlindSpot(
                description=(
                    "entities with no recorded upstream — origin is unknowable "
                    "from the catalog alone"
                ),
                affected_urns=orphans,
            )
        )

    if subgraph.ambiguities:
        spots.append(
            BlindSpot(
                description=(
                    f"{len(subgraph.ambiguities)} reference(s) resolved to multiple "
                    f"candidates; the change target is not uniquely determined"
                ),
                affected_urns=[c.urn for a in subgraph.ambiguities for c in a.candidates],
            )
        )

    inferred = [e for e in subgraph.edges if e.provenance is Provenance.INFERRED]
    if inferred:
        spots.append(
            BlindSpot(
                description=(
                    f"{len(inferred)} lineage edge(s) were inferred rather than parsed "
                    f"and have not been verified"
                ),
                affected_urns=[e.downstream.urn for e in inferred],
            )
        )

    return spots


# Platforms whose entities are landing zones — having no upstream there is
# expected, not a gap.
_SOURCE_PLATFORMS = frozenset({"duckdb"})


def _entities_without_upstream(subgraph: Subgraph) -> list[str]:
    """Entities whose origin the catalog cannot account for.

    A raw landed table having no upstream is expected. A derived model having
    none means its origin is outside anything DataHub can observe.
    """
    has_upstream = {e.downstream.urn for e in subgraph.edges}
    return [
        entity.urn
        for entity in subgraph.entities
        if entity.urn not in has_upstream
        and entity.urn != subgraph.root.urn
        and entity.platform not in _SOURCE_PLATFORMS
    ]
