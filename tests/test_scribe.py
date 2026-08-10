import json

import pytest

from tests.fakes.ceilings import DARK, GOOD
from warden.agent.models import (
    BreakageTier,
    ChangeKind,
    EntityRef,
    FixStrategy,
    ImpactedAsset,
    LineageEdge,
    ProposedChange,
    Provenance,
    Remediation,
    Subgraph,
    Verdict,
)
from warden.agent.scribe import TAG_BLOCKED, TAG_VERIFIED, Scribe


class _Recorder:
    def __init__(self) -> None:
        self.documents: list[dict] = []
        self.tags: list[dict] = []
        self.descriptions: list[dict] = []

    async def save_document(self, document_type: str, title: str, content: str, urn=None) -> dict:
        self.documents.append(
            {"type": document_type, "title": title, "content": content, "urn": urn}
        )
        return {"ok": True}

    async def add_tags(self, tag_urns: list[str], entity_urns: list[str], **_) -> dict:
        self.tags.append({"tags": tag_urns, "entities": entity_urns})
        return {"ok": True}

    async def update_description(self, entity_urn: str, description: str, **_) -> dict:
        self.descriptions.append({"urn": entity_urn, "description": description})
        return {"ok": True}

    async def search_documents(self, query: str = "*", num_results: int = 10) -> dict:
        return {"documents": []}


def _ref(name: str, platform: str = "dbt") -> EntityRef:
    return EntityRef(
        urn=f"urn:li:dataset:(urn:li:dataPlatform:{platform},{name},PROD)",
        platform=platform,
        name=name,
        entity_type="dataset",
    )


def _verdict(ceiling, tier=BreakageTier.BREAKS, names=("fct_orders",)) -> Verdict:
    return Verdict(
        change=ProposedChange(model="stg_orders", kind=ChangeKind.COLUMN_RENAMED, column="cust_id"),
        impacted=[ImpactedAsset(entity=_ref(n), tier=tier, reasoning="") for n in names],
        overall=tier,
        ceiling=ceiling,
    )


def _subgraph(inferred: bool = False) -> Subgraph:
    root = _ref("stg_orders")
    child = _ref("customer_segments", "python")
    edges = (
        [
            LineageEdge(
                upstream=root, downstream=child, provenance=Provenance.INFERRED, confidence=0.7
            )
        ]
        if inferred
        else []
    )
    return Subgraph(root=root, entities=[root, child], edges=edges)


@pytest.mark.asyncio
async def test_blocked_decision_names_the_missing_fact():
    """A held decision must be actionable. "Coverage low" is not a work item;
    "ingest tableau lineage" is."""
    recorder = _Recorder()
    remediation = Remediation(
        strategy=FixStrategy.UPDATE_REFERENCES, blocked_reason="coverage insufficient"
    )

    decision = await Scribe(recorder).record(_verdict(DARK), _subgraph(), remediation)

    assert decision.is_blocked
    assert "tableau" in decision.blocked_on


@pytest.mark.asyncio
async def test_held_decision_is_recorded_as_a_decision_document():
    """DataHub has a first-class Decision document type, which is exactly what
    a deferred decision is — not a note, not an insight."""
    recorder = _Recorder()
    remediation = Remediation(
        strategy=FixStrategy.UPDATE_REFERENCES, blocked_reason="coverage insufficient"
    )

    await Scribe(recorder).record(_verdict(DARK), _subgraph(), remediation)

    assert recorder.documents[0]["type"] == "Decision"
    body = json.loads(recorder.documents[0]["content"])
    assert body["marker"] == "warden:held-decision"
    assert body["blocked_on"]


@pytest.mark.asyncio
async def test_completed_run_is_recorded_as_analysis_not_decision():
    recorder = _Recorder()
    await Scribe(recorder).record(_verdict(GOOD), _subgraph())

    assert recorder.documents[0]["type"] == "Analysis"


@pytest.mark.asyncio
async def test_blocked_and_verified_entities_are_tagged_differently():
    blocked_recorder = _Recorder()
    await Scribe(blocked_recorder).record(
        _verdict(DARK),
        _subgraph(),
        Remediation(strategy=FixStrategy.UPDATE_REFERENCES, blocked_reason="thin"),
    )

    clean_recorder = _Recorder()
    await Scribe(clean_recorder).record(_verdict(GOOD), _subgraph())

    assert TAG_BLOCKED in blocked_recorder.tags[0]["tags"]
    assert TAG_VERIFIED in clean_recorder.tags[0]["tags"]


@pytest.mark.asyncio
async def test_inferred_edges_are_recorded_as_unverified():
    """Warden must never present an edge it reasoned out as one a connector
    parsed. mcp-server-datahub has no lineage mutation tool, so these land on
    the description with their provenance stated."""
    recorder = _Recorder()
    await Scribe(recorder).record(_verdict(GOOD), _subgraph(inferred=True))

    assert recorder.descriptions
    assert "unverified" in recorder.descriptions[0]["description"]


@pytest.mark.asyncio
async def test_coverage_report_travels_with_the_decision():
    recorder = _Recorder()
    await Scribe(recorder).record(_verdict(DARK), _subgraph())

    body = json.loads(recorder.documents[0]["content"])
    assert body["coverage"] == DARK.report.score
    assert body["blind_spots"]
