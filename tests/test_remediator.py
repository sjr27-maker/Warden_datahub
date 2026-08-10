from pathlib import Path

from tests.fakes.ceilings import GOOD, ceiling
from warden.agent.models import (
    BreakageTier,
    ChangeKind,
    CoverageCeiling,
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


def _verdict(tier: BreakageTier, cov: CoverageCeiling, names: list[str]) -> Verdict:
    return Verdict(
        change=ProposedChange(
            model="stg_orders",
            kind=ChangeKind.COLUMN_RENAMED,
            column="cust_id",
            new_value="customer_id",
        ),
        impacted=[ImpactedAsset(entity=_ref(n), tier=tier, reasoning="") for n in names],
        overall=tier,
        ceiling=cov,
    )


def test_generation_blocked_when_coverage_thin(tmp_path: Path):
    """The gate, at the point where code would be written.

    A fix generated against an incomplete blast radius addresses only the
    consumers Warden can see, while presenting itself as complete.
    """
    verdict = _verdict(BreakageTier.TOUCHES, ceiling(may_assert_safe=False, score=0.3), [])
    remediation = Remediator(tmp_path).remediate(verdict)

    assert remediation.is_blocked
    assert remediation.edits == []


def test_blocked_reason_names_what_would_unblock_it():
    verdict = _verdict(BreakageTier.TOUCHES, ceiling(may_assert_safe=False), [])
    remediation = Remediator().remediate(verdict)

    assert remediation.blocked_reason is not None
    assert "tableau" in remediation.blocked_reason


def test_rename_recommends_update_with_alternatives_named(tmp_path: Path):
    """Three strategies are legitimate here. Silently picking one hides a
    decision the reviewer should see."""
    verdict = _verdict(BreakageTier.BREAKS, GOOD, ["fct_orders"])
    remediation = Remediator(tmp_path).remediate(verdict)

    assert remediation.strategy is FixStrategy.UPDATE_REFERENCES
    assert FixStrategy.COMPATIBILITY_ALIAS in remediation.alternatives
    assert remediation.rationale


def test_edits_rewrite_only_whole_column_references(tmp_path: Path):
    """A substring rename would corrupt `cust_id_hash` into
    `customer_id_hash`. Word boundaries are load-bearing."""
    model = tmp_path / "fct_orders.sql"
    model.write_text("select cust_id, cust_id_hash from {{ ref('stg_orders') }}")

    verdict = _verdict(BreakageTier.BREAKS, GOOD, ["fct_orders"])
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
                entity=_ref("exec_dashboard", "tableau"),
                tier=BreakageTier.BREAKS,
                reasoning="",
            ),
        ],
        overall=BreakageTier.BREAKS,
        ceiling=GOOD,
    )
    remediation = Remediator(tmp_path).remediate(verdict)

    assert remediation.escalation is not None
    assert "tableau" in remediation.escalation


def test_safe_verdict_produces_no_edits(tmp_path: Path):
    verdict = _verdict(BreakageTier.SAFE, GOOD, [])
    remediation = Remediator(tmp_path).remediate(verdict)

    assert remediation.edits == []
    assert not remediation.is_blocked


def test_breakage_does_not_license_generation_on_a_dark_graph(tmp_path: Path):
    """Finding real breakage is not permission to act on it.

    A confident impact list from an incomplete graph is still partial. Fixing
    what is visible, in a PR that implies completeness, is the exact failure
    the coverage gate exists to prevent.
    """
    verdict = _verdict(BreakageTier.BREAKS, ceiling(may_assert_safe=False), ["fct_orders"])
    remediation = Remediator(tmp_path).remediate(verdict)

    assert remediation.is_blocked
    assert remediation.edits == []
