"""Write-back to DataHub.

Two categories, and conflating them would overstate what Warden does.

Agent-closable gaps make the graph more *complete*: descriptions filled while
investigating, impact edges confirmed by a completed analysis. Warden writes
these and the catalog is genuinely better.

Human-closable gaps make the graph more *honest*: an unconfigured connector
cannot be fixed by an agent — it needs credentials and someone deciding it is
worth ingesting. For these Warden records precisely what is missing and holds
the decision open. Claiming to have fixed them would be overreach.

The second category is the more distinctive half. Nothing else in the
ecosystem persists "I don't know" as a queryable fact.
"""

import json
import logging
from datetime import datetime

from warden.agent.mcp_client import MCPClient
from warden.agent.models import (
    Decision,
    Provenance,
    Remediation,
    Subgraph,
    VerificationResult,
    Verdict,
)

logger = logging.getLogger(__name__)

TAG_BLOCKED = "urn:li:tag:warden-blocked"
TAG_VERIFIED = "urn:li:tag:warden-verified"
TAG_COVERAGE_GAP = "urn:li:tag:warden-coverage-gap"

DOC_TYPE_DECISION = "Decision"
DOC_TYPE_ANALYSIS = "Analysis"

_HELD_MARKER = "warden:held-decision"
_RESOLVED_MARKER = "warden:resolved"


class Scribe:
    def __init__(self, client: MCPClient) -> None:
        self._client = client

    async def record(
        self,
        verdict: Verdict,
        subgraph: Subgraph,
        remediation: Remediation | None = None,
        verification: VerificationResult | None = None,
        pr_url: str | None = None,
    ) -> Decision:
        blocked = remediation is not None and remediation.is_blocked

        decision = Decision(
            is_blocked=blocked,
            blocked_on=self._blocked_on(verdict, remediation) if blocked else None,
            verdict=verdict,
            pr_url=pr_url,
        )

        await self._write_document(decision, subgraph, remediation, verification)
        await self._mark_entities(decision, verdict)
        await self._record_inferred_edges(subgraph)

        return decision

    def _blocked_on(self, verdict: Verdict, remediation: Remediation | None) -> str:
        """The named missing fact.

        Must be specific enough to act on. "Low confidence" is not a work
        item; "ingest tableau lineage" is.
        """
        platforms = [
            s.affected_platform
            for s in verdict.ceiling.report.blind_spots
            if s.affected_platform
        ]
        if platforms:
            return f"lineage ingestion for: {', '.join(sorted(platforms))}"
        if remediation and remediation.blocked_reason:
            return remediation.blocked_reason[:200]
        return "coverage below threshold"

    async def _write_document(
        self,
        decision: Decision,
        subgraph: Subgraph,
        remediation: Remediation | None,
        verification: VerificationResult | None,
    ) -> None:
        """Persist the run as a DataHub Document.

        `Decision` is a first-class document type in DataHub, which is exactly
        what a held decision is — not a note, not an insight, a decision that
        was reached or deliberately deferred.
        """
        verdict = decision.verdict
        assert verdict is not None

        doc_type = DOC_TYPE_DECISION if decision.is_blocked else DOC_TYPE_ANALYSIS
        marker = _HELD_MARKER if decision.is_blocked else _RESOLVED_MARKER
        change = verdict.change

        title = (
            f"Warden: {'held' if decision.is_blocked else 'assessed'} — "
            f"{change.kind.value} on {change.model}"
            f"{'.' + change.column if change.column else ''}"
        )

        body = {
            "marker": marker,
            "recorded_at": decision.created_at.isoformat(),
            "change": change.model_dump(mode="json"),
            "verdict": verdict.overall.value,
            "abstained": verdict.abstained,
            "coverage": verdict.ceiling.report.score,
            "may_assert_safe": verdict.ceiling.may_assert_safe,
            "blind_spots": [s.description for s in verdict.ceiling.report.blind_spots],
            "blocked_on": decision.blocked_on,
            "impacted": [
                {"urn": a.entity.urn, "tier": a.tier.value, "why": a.reasoning}
                for a in verdict.impacted
            ],
            "subgraph_size": len(subgraph.entities),
            "strategy": remediation.strategy.value if remediation else None,
            "alternatives": [a.value for a in remediation.alternatives] if remediation else [],
            "edits": [e.path for e in remediation.edits] if remediation else [],
            "verified": verification.passed if verification else None,
            "pr_url": decision.pr_url,
        }

        await self._client.save_document(
            document_type=doc_type,
            title=title,
            content=json.dumps(body, indent=2),
            urn=subgraph.root.urn,
        )

    async def _mark_entities(self, decision: Decision, verdict: Verdict) -> None:
        """Tag affected entities so the state is visible in the UI, not only
        inside a document someone has to find."""
        urns = [a.entity.urn for a in verdict.impacted]
        if not urns:
            return

        tags = [TAG_BLOCKED] if decision.is_blocked else [TAG_VERIFIED]
        if verdict.ceiling.report.blind_spots:
            tags.append(TAG_COVERAGE_GAP)

        try:
            await self._client.add_tags(tag_urns=tags, entity_urns=urns)
        except Exception as exc:
            logger.warning("tagging failed (%s); document record still written", exc)

    async def _record_inferred_edges(self, subgraph: Subgraph) -> None:
        """Record lineage Warden inferred but no parser produced.

        mcp-server-datahub exposes no lineage mutation tool, so these cannot
        be contributed back as real edges. Recording them on the description
        is a lesser thing, and the gap is reported upstream rather than worked
        around with a direct SDK call.
        """
        inferred = [e for e in subgraph.edges if e.provenance is Provenance.INFERRED]
        if not inferred:
            return

        for edge in inferred:
            note = (
                f"Warden inferred an upstream dependency on {edge.upstream.name} "
                f"(confidence {edge.confidence}). Inferred from code, not parsed by any "
                f"connector — unverified."
            )
            try:
                await self._client.update_description(
                    entity_urn=edge.downstream.urn, description=note, operation="append"
                )
            except Exception as exc:
                logger.warning("could not record inferred edge on %s: %s", edge.downstream.urn, exc)

    async def find_held_decisions(self) -> list[dict]:
        """Blocked decisions waiting on a named fact.

        This is what makes refusal a work queue rather than a dead end — a
        data engineer can ask what is currently blocked on missing metadata,
        which is a better prioritisation signal than a generic completeness
        score because every entry is costing someone something right now.
        """
        result = await self._client.search_documents(query=_HELD_MARKER, num_results=50)
        documents = result.get("documents", result if isinstance(result, list) else [])
        return [d for d in documents if isinstance(d, dict)]

    async def resume(self, held: dict, new_verdict: Verdict, subgraph: Subgraph) -> Decision:
        """Re-record a previously blocked decision now that coverage allows."""
        decision = Decision(
            is_blocked=False,
            verdict=new_verdict,
            resumed_from=str(held.get("urn", "")),
        )
        await self._write_document(decision, subgraph, None, None)
        logger.info("resumed decision previously held on %s", held.get("title"))
        return decision


def summarise(decision: Decision) -> str:
    """One-line summary for logs and CI output."""
    verdict = decision.verdict
    if decision.is_blocked:
        return f"HELD — blocked on {decision.blocked_on}"
    if verdict is None:
        return "no verdict"
    when = decision.created_at.strftime("%Y-%m-%d %H:%M")
    return f"{verdict.overall.value.upper()} — {len(verdict.impacted)} impacted ({when})"