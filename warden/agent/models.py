"""Core types shared across every agent. This is the contract between
Scoper/Skeptic (A) and Assessor/Remediator/Verifier/Scribe (B) — changes
here ripple everywhere, so extend rather than restructure once B is building
against it.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class Provenance(str, Enum):
    """How a fact entered the graph. Never collapse this distinction —
    it's what makes Warden's claims auditable rather than asserted."""

    CURATED = "curated"  # human-entered in DataHub
    PARSED = "parsed"  # extracted by a DataHub ingestion connector
    INFERRED = "inferred"  # Warden reasoned it from code the parsers missed


class EntityRef(BaseModel):
    urn: str
    platform: str
    name: str
    entity_type: str  # dataset, dashboard, mlModel, etc.


class LineageEdge(BaseModel):
    upstream: EntityRef
    downstream: EntityRef
    provenance: Provenance
    confidence: float = Field(ge=0.0, le=1.0)


class AmbiguousReferent(BaseModel):
    """Emitted by the Scoper when a diff's reference resolves to more than
    one equally-plausible entity. Never silently pick one."""

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
    """The hard cap. Downstream verdict types are constructed against this —
    see Verdict.safe being unconstructible below threshold."""

    report: CoverageReport
    may_assert_safe: bool
    threshold_used: float


class BreakageTier(str, Enum):
    BREAKS = "breaks"
    DEGRADES = "degrades"
    TOUCHES = "touches"
    SAFE = "safe"  # only constructible when ceiling.may_assert_safe is True


class ImpactedAsset(BaseModel):
    entity: EntityRef
    tier: BreakageTier
    reasoning: str


class Verdict(BaseModel):
    """Assessor output. Construction should be validated against a
    CoverageCeiling by the caller — see assessor.py in batch 5."""

    impacted: list[ImpactedAsset]
    ceiling: CoverageCeiling
    generated_at: datetime = Field(default_factory=datetime.utcnow)


class Decision(BaseModel):
    """The held decision — Warden's most distinctive write-back. Represents
    either a completed action or a blocked one waiting on a named fact."""

    is_blocked: bool
    blocked_on: str | None = None  # precise, actionable: "ingest Tableau lineage"
    verdict: Verdict | None = None
    pr_url: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    resumed_from: str | None = None  # decision id this resumed, if any