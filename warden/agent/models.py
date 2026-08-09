"""Core types shared across every agent.

This is the contract between Scoper/Skeptic and everything downstream.
Changes here ripple, so extend rather than restructure once other agents are
building against it.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Provenance(StrEnum):
    """How a fact entered the graph. Never collapse this distinction — it is
    what makes Warden's claims auditable rather than asserted."""

    CURATED = "curated"  # human-entered in DataHub
    PARSED = "parsed"  # extracted by a DataHub ingestion connector
    INFERRED = "inferred"  # Warden reasoned it from code the parsers missed


class EntityRef(BaseModel):
    urn: str
    platform: str
    name: str
    entity_type: str


class LineageEdge(BaseModel):
    upstream: EntityRef
    downstream: EntityRef
    provenance: Provenance
    confidence: float = Field(ge=0.0, le=1.0)


class AmbiguousReferent(BaseModel):
    """Emitted by the Scoper when a diff's reference resolves to more than one
    equally-plausible entity. Never silently pick one."""

    query: str
    candidates: list[EntityRef]
    reason: str


class Subgraph(BaseModel):
    """What the Scoper selected as relevant to a proposed change."""

    root: EntityRef
    entities: list[EntityRef]
    edges: list[LineageEdge]
    ambiguities: list[AmbiguousReferent] = Field(default_factory=list)
    relevance_trace: dict[str, str] = Field(
        default_factory=dict, description="urn -> why this node was included"
    )


class BlindSpot(BaseModel):
    """A named, specific gap in coverage — never a vague score alone."""

    description: str
    affected_platform: str | None = None
    affected_urns: list[str] = Field(default_factory=list)


class CoverageReport(BaseModel):
    """Deterministic output of the Skeptic. No LLM produces this."""

    score: float = Field(ge=0.0, le=1.0)
    reachable_nodes: int
    expected_nodes: int
    parsed_edge_ratio: float
    blind_spots: list[BlindSpot]
    inferred_edge_count: int


class CoverageCeiling(BaseModel):
    """The hard cap. Downstream verdicts are constructed against this."""

    report: CoverageReport
    may_assert_safe: bool
    threshold_used: float


class BreakageTier(StrEnum):
    BREAKS = "breaks"
    DEGRADES = "degrades"
    TOUCHES = "touches"
    SAFE = "safe"


class ChangeKind(StrEnum):
    """What kind of change is proposed. The kind alone determines the verdict
    for several cases, which is why the Assessor doesn't reach for an LLM
    unless the rules genuinely can't decide."""

    COLUMN_RENAMED = "column_renamed"
    COLUMN_DROPPED = "column_dropped"
    COLUMN_ADDED = "column_added"
    TYPE_NARROWED = "type_narrowed"
    TYPE_WIDENED = "type_widened"
    LOGIC_CHANGED = "logic_changed"


class ProposedChange(BaseModel):
    """A single change extracted from a diff."""

    model: str
    kind: ChangeKind
    column: str | None = None
    old_value: str | None = None
    new_value: str | None = None


class ImpactedAsset(BaseModel):
    entity: EntityRef
    tier: BreakageTier
    reasoning: str


class Verdict(BaseModel):
    """Assessor output.

    `overall` must be built through `warden.agent.assessor.build_verdict`,
    which enforces the coverage ceiling. Constructing SAFE directly bypasses
    the gate — see test_safe_verdict_unconstructible_below_threshold.
    """

    change: ProposedChange
    impacted: list[ImpactedAsset]
    overall: BreakageTier
    ceiling: CoverageCeiling
    abstained: bool = False
    reasoning: str = ""
    generated_at: datetime = Field(default_factory=datetime.utcnow)


class Decision(BaseModel):
    """The held decision — Warden's most distinctive write-back. Represents
    either a completed action or a blocked one waiting on a named fact."""

    is_blocked: bool
    blocked_on: str | None = None
    verdict: Verdict | None = None
    pr_url: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    resumed_from: str | None = None
