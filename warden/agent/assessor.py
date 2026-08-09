"""Blast radius classification under a coverage ceiling.

Two design commitments here.

The change kind decides the verdict wherever it can. A widened type cannot
break a reader; an added nullable column cannot break a reader. Reaching for
an LLM on cases the rules already settle wastes tokens and introduces
variance where there should be none.

And the ceiling is enforced structurally, not by instruction. SAFE is not a
value the Assessor can return when the Skeptic says the graph is too dark to
support it — build_verdict downgrades it. A prompt saying "be careful when
coverage is low" is a suggestion; this is a gate.
"""

import logging

from warden.agent.llm import Reasoner
from warden.agent.models import (
    BreakageTier,
    ChangeKind,
    CoverageCeiling,
    EntityRef,
    ImpactedAsset,
    ProposedChange,
    Subgraph,
    Verdict,
)

logger = logging.getLogger(__name__)

# Change kinds whose consequence is determined by the kind alone, regardless
# of what consumes the column. Widening and adding are strictly permissive.
_ALWAYS_SAFE: frozenset[ChangeKind] = frozenset({ChangeKind.COLUMN_ADDED, ChangeKind.TYPE_WIDENED})

# Change kinds whose consequence depends on whether anything consumes the
# column, but not on any judgment beyond that.
_TIER_WHEN_CONSUMED: dict[ChangeKind, BreakageTier] = {
    ChangeKind.COLUMN_DROPPED: BreakageTier.BREAKS,
    ChangeKind.COLUMN_RENAMED: BreakageTier.BREAKS,
    ChangeKind.TYPE_NARROWED: BreakageTier.DEGRADES,
}

_TIER_SEVERITY = [
    BreakageTier.BREAKS,
    BreakageTier.DEGRADES,
    BreakageTier.TOUCHES,
    BreakageTier.SAFE,
]


def build_verdict(
    change: ProposedChange,
    impacted: list[ImpactedAsset],
    ceiling: CoverageCeiling,
    reasoning: str = "",
    *,
    safety_is_intrinsic: bool = False,
) -> Verdict:
    """Construct a Verdict with the ceiling enforced.

    A SAFE conclusion normally asserts a negative — that nothing breaks —
    which is only available when the Skeptic confirms the graph was complete
    enough to see consequences.

    `safety_is_intrinsic` marks the exception: changes that are safe by their
    own semantics rather than by absence of evidence. A widened type cannot
    break a reader whether or not that reader is visible, so no amount of
    missing lineage should downgrade it. Gating these would be crying wolf,
    which is how a tool teaches people to ignore it.
    """
    overall = _worst_tier(impacted)
    abstained = False

    if overall is BreakageTier.SAFE and not ceiling.may_assert_safe and not safety_is_intrinsic:
        overall = BreakageTier.TOUCHES
        abstained = True
        blind = "; ".join(s.description for s in ceiling.report.blind_spots)
        reasoning = (
            f"Found no impact, but cannot assert safety: "
            f"{blind or 'coverage below threshold'}. "
            f"An empty result is not evidence of absence when the graph is incomplete."
        )

    return Verdict(
        change=change,
        impacted=impacted,
        overall=overall,
        ceiling=ceiling,
        abstained=abstained,
        reasoning=reasoning,
    )


def _worst_tier(impacted: list[ImpactedAsset]) -> BreakageTier:
    if not impacted:
        return BreakageTier.SAFE
    tiers = {a.tier for a in impacted}
    for tier in _TIER_SEVERITY:
        if tier in tiers:
            return tier
    return BreakageTier.SAFE


class Assessor:
    def __init__(self, reasoner: Reasoner | None = None) -> None:
        self._reasoner = reasoner

    def assess(
        self,
        change: ProposedChange,
        subgraph: Subgraph,
        ceiling: CoverageCeiling,
    ) -> Verdict:
        consumers = [e for e in subgraph.entities if e.urn != subgraph.root.urn]

        # Cheap gate first. Additive changes need no reasoning, and no LLM call
        # is made for them.
        if change.kind in _ALWAYS_SAFE:
            return build_verdict(
                change,
                [],
                ceiling,
                reasoning=(
                    f"{change.kind.value} is strictly permissive; "
                    f"existing readers are unaffected regardless of visibility."
                ),
                safety_is_intrinsic=True,
            )

        if not consumers:
            return build_verdict(
                change,
                [],
                ceiling,
                reasoning="No consumers found in the retrieved subgraph.",
            )

        tier = _TIER_WHEN_CONSUMED.get(change.kind)
        if tier is not None:
            impacted = [
                ImpactedAsset(
                    entity=entity,
                    tier=tier,
                    reasoning=self._explain(change, entity, tier, subgraph),
                )
                for entity in consumers
            ]
            return build_verdict(change, impacted, ceiling)

        # Logic changes are the case rules cannot settle — the consequence
        # depends on what the logic actually does.
        return self._assess_with_judgment(change, consumers, ceiling)

    def _explain(
        self,
        change: ProposedChange,
        entity: EntityRef,
        tier: BreakageTier,
        subgraph: Subgraph,
    ) -> str:
        why = subgraph.relevance_trace.get(entity.urn, "downstream")
        column = f".{change.column}" if change.column else ""

        if tier is BreakageTier.BREAKS:
            return (
                f"{entity.name} depends on {change.model}{column} ({why}); "
                f"the reference will not resolve."
            )
        if tier is BreakageTier.DEGRADES:
            return (
                f"{entity.name} reads {change.model}{column} ({why}); nothing errors, "
                f"but values change silently under the narrowed type."
            )
        return f"{entity.name} is downstream of {change.model}{column} ({why})."

    def _assess_with_judgment(
        self,
        change: ProposedChange,
        consumers: list[EntityRef],
        ceiling: CoverageCeiling,
    ) -> Verdict:
        """Only reached when the change kind doesn't determine the outcome.

        The model is given the change and the consumers — deliberately not any
        heuristic guess of our own. Showing it a provisional answer invites
        agreement rather than judgment.
        """
        if self._reasoner is None:
            impacted = [
                ImpactedAsset(
                    entity=e,
                    tier=BreakageTier.TOUCHES,
                    reasoning="Downstream of a logic change; consequence not determined.",
                )
                for e in consumers
            ]
            return build_verdict(
                change,
                impacted,
                ceiling,
                reasoning="No reasoner available; logic change not evaluated.",
            )

        names = ", ".join(e.name for e in consumers)
        prompt = (
            f"A dbt model '{change.model}' has changed its transformation logic.\n"
            f"Old: {change.old_value}\n"
            f"New: {change.new_value}\n"
            f"Downstream consumers: {names}\n\n"
            f"Classify the consequence for consumers as exactly one word: "
            f"BREAKS, DEGRADES, or TOUCHES. Then one sentence of justification."
        )
        response = self._reasoner.judge(prompt)
        tier = _parse_tier(response)

        impacted = [
            ImpactedAsset(entity=e, tier=tier, reasoning=response.strip()[:300]) for e in consumers
        ]
        return build_verdict(change, impacted, ceiling)


def _parse_tier(response: str) -> BreakageTier:
    upper = response.upper()
    for tier in (BreakageTier.BREAKS, BreakageTier.DEGRADES):
        if tier.value.upper() in upper:
            return tier
    return BreakageTier.TOUCHES
