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
    """A named, specific gap in coverage — never a vague score alone.

    `blocks_generation` distinguishes gaps that could hide downstream
    consumers from gaps that cannot. A dark platform feeding only raw tables
    is a real gap, but it cannot conceal anything the change would break.
    Treating every gap as blocking is what makes a tool cry wolf.
    """

    description: str
    affected_platform: str | None = None
    affected_urns: list[str] = Field(default_factory=list)
    blocks_generation: bool = True


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
    overridden: bool = False
    override_reason: str | None = None

    @property
    def blocking_spots(self) -> list[BlindSpot]:
        return [s for s in self.report.blind_spots if s.blocks_generation]


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


class FixStrategy(StrEnum):
    """How to remediate. These are trade-offs, not right and wrong — which is
    why the Remediator names alternatives rather than silently choosing."""

    UPDATE_REFERENCES = "update_references"  # change every downstream reference
    COMPATIBILITY_ALIAS = "compatibility_alias"  # keep the old name as an alias
    TWO_PHASE_DEPRECATE = "two_phase_deprecate"  # add new, deprecate old, migrate later


class FileEdit(BaseModel):
    path: str
    original: str
    modified: str

    @property
    def changed(self) -> bool:
        return self.original != self.modified


class Remediation(BaseModel):
    """Generated fixes, before verification. `verified` stays False until the
    Verifier has actually executed them."""

    strategy: FixStrategy
    alternatives: list[FixStrategy] = Field(default_factory=list)
    rationale: str = ""
    escalation: str | None = None
    edits: list[FileEdit] = Field(default_factory=list)
    blocked_reason: str | None = None

    @property
    def is_blocked(self) -> bool:
        return self.blocked_reason is not None


class VerificationAttempt(BaseModel):
    attempt: int
    command: str
    exit_code: int
    output_tail: str

    @property
    def passed(self) -> bool:
        return self.exit_code == 0


class VerificationResult(BaseModel):
    """The execution record. Committed to examples/ — a reviewer learns more
    from a failure that was caught and corrected than from a clean artifact."""

    attempts: list[VerificationAttempt] = Field(default_factory=list)
    final_edits: list[FileEdit] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.attempts) and self.attempts[-1].passed
