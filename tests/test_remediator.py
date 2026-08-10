from pathlib import Path

from warden.agent.models import (
    BlindSpot,
    BreakageTier,
    ChangeKind,
    CoverageCeiling,
    CoverageReport,
    EntityRef,
    FixStrategy,
    ImpactedAsset,
    ProposedChange,
    Verdict,
)
from warden.agent.remediator import Remediator


def _ref(name: str, platform: str = "dbt") -> EntityRef:
    return EntityRef(
        urn=f"urn:li:dataset:(urn:li:dataPlatform:{platform},{name},PROD)",
        platform=platform,
        name=name,
        entity_type="dataset",
    )


def _ceiling(*, may_assert_safe: bool, score: float = 0.9) -> CoverageCeiling:
    blind = (
        []
        if may_assert_safe
        else [BlindSpot(description="tableau has no lineage connector", affected_platform="tableau")]
    )
    return CoverageCeiling(
        report=CoverageReport(
            score=score,
            reachable_nodes=6,
            expected_nodes=27,
            parsed_edge_ratio=1.0,
            blind_spots=blind,
            inferred_edge_count=0,
        ),
        may_assert_safe=may_assert_safe,
        threshold_used=0.6,
    )


def _verdict(tier: BreakageTier, ceiling: CoverageCeiling, names: list[str]) -> Verdict:
    return Verdict(
        change=ProposedChange(
            model="stg_orders",
            kind=ChangeKind.COLUMN_RENAMED,
            column="cust_id",
            new_value="customer_id",
        ),
        impacted=[ImpactedAsset(entity=_ref(n), tier=tier, reasoning="") for n in names],
        overall=tier,
        ceiling=ceiling,
    )


def test_generation_blocked_when_coverage_thin(tmp_path: Path):
    """The gate, at the point where code would be written.

    A fix generated against an incomplete blast radius addresses only the
    consumers Warden can see, while presenting itself as complete.
    """
    verdict = _verdict(BreakageTier.TOUCHES, _ceiling(may_assert_safe=False, score=0.3), [])
    remediation = Remediator(tmp_path).remediate(verdict)

    assert remediation.is_blocked
    assert remediation.edits == []


def test_blocked_reason_names_what_would_unblock_it():
    verdict = _verdict(BreakageTier.TOUCHES, _ceiling(may_assert_safe=False), [])
    remediation = Remediator().remediate(verdict)

    assert "tableau" in remediation.blocked_reason


def test_rename_recommends_update_with_alternatives_named(tmp_path: Path):
    """Three strategies are legitimate here. Silently picking one hides a
    decision the reviewer should see."""
    verdict = _verdict(BreakageTier.BREAKS, _ceiling(may_assert_safe=True), ["fct_orders"])
    remediation = Remediator(tmp_path).remediate(verdict)

    assert remediation.strategy is FixStrategy.UPDATE_REFERENCES
    assert FixStrategy.COMPATIBILITY_ALIAS in remediation.alternatives
    assert remediation.rationale


def test_edits_rewrite_only_whole_column_references(tmp_path: Path):
    """A substring rename would corrupt `cust_id_hash` into
    `customer_id_hash`. Word boundaries are load-bearing."""
    model = tmp_path / "fct_orders.sql"
    model.write_text("select cust_id, cust_id_hash from {{ ref('stg_orders') }}")

    verdict = _verdict(BreakageTier.BREAKS, _ceiling(may_assert_safe=True), ["fct_orders"])
    remediation = Remediator(tmp_path).remediate(verdict)

    assert len(remediation.edits) == 1
    modified = remediation.edits[0].modified
    assert "customer_id," in modified
    assert "cust_id_hash" in modified
    assert "customer_id_hash" not in modified


def test_cross_platform_impact_escalates(tmp_path: Path):
    """Ownership is in the graph. Whether a breaking change is acceptable
    this cycle is not."""
    verdict = Verdict(
        change=ProposedChange(
            model="stg_orders",
            kind=ChangeKind.COLUMN_RENAMED,
            column="cust_id",
            new_value="customer_id",
        ),
        impacted=[
            ImpactedAsset(entity=_ref("fct_orders", "dbt"), tier=BreakageTier.BREAKS, reasoning=""),
            ImpactedAsset(
                entity=_ref("exec_dashboard", "tableau"), tier=BreakageTier.BREAKS, reasoning=""
            ),
        ],
        overall=BreakageTier.BREAKS,
        ceiling=_ceiling(may_assert_safe=True),
    )
    remediation = Remediator(tmp_path).remediate(verdict)

    assert remediation.escalation is not None
    assert "tableau" in remediation.escalation


def test_safe_verdict_produces_no_edits(tmp_path: Path):
    verdict = _verdict(BreakageTier.SAFE, _ceiling(may_assert_safe=True), [])
    remediation = Remediator(tmp_path).remediate(verdict)

    assert remediation.edits == []
    assert not remediation.is_blocked