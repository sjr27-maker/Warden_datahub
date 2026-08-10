from tests.fakes.ceilings import DARK, GOOD
from warden.agent.models import (
    BreakageTier,
    ChangeKind,
    Decision,
    EntityRef,
    FileEdit,
    FixStrategy,
    ImpactedAsset,
    ProposedChange,
    Remediation,
    VerificationAttempt,
    VerificationResult,
    Verdict,
)
from warden.agent.report import render_pr_body, render_refusal


def _ref(name: str, platform: str = "dbt") -> EntityRef:
    return EntityRef(
        urn=f"urn:li:dataset:(urn:li:dataPlatform:{platform},{name},PROD)",
        platform=platform,
        name=name,
        entity_type="dataset",
    )


def _verdict(ceiling, names=("fct_orders", "exec_dashboard")) -> Verdict:
    return Verdict(
        change=ProposedChange(
            model="stg_orders",
            kind=ChangeKind.COLUMN_RENAMED,
            column="cust_id",
            new_value="customer_id",
        ),
        impacted=[
            ImpactedAsset(
                entity=_ref(n, "dbt" if n.startswith("fct") else "tableau"),
                tier=BreakageTier.BREAKS,
                reasoning="",
            )
            for n in names
        ],
        overall=BreakageTier.BREAKS,
        ceiling=ceiling,
    )


def _remediation(paths=("world/dbt_project/models/marts/fct_orders.sql",)) -> Remediation:
    return Remediation(
        strategy=FixStrategy.UPDATE_REFERENCES,
        alternatives=[FixStrategy.COMPATIBILITY_ALIAS],
        rationale="Updating references directly is atomic.",
        edits=[FileEdit(path=p, original="a", modified="b") for p in paths],
    )


PASSED = VerificationResult(
    attempts=[VerificationAttempt(attempt=1, command="dbt build", exit_code=0, output_tail="ok")]
)


def test_impact_list_is_a_floor_when_the_graph_is_dark():
    """Warden gates safety claims on coverage but not completeness claims. It
    must say so, or the list reads as a census it cannot support."""
    body = render_pr_body(_verdict(DARK), _remediation(), PASSED, Decision(is_blocked=False))

    assert "at least" in body
    assert "floor, not a census" in body


def test_impact_list_is_exhaustive_when_coverage_is_complete():
    body = render_pr_body(_verdict(GOOD), _remediation(), PASSED, Decision(is_blocked=False))

    assert "exhaustive" in body
    assert "at least" not in body


def test_unfixable_consumers_are_marked_as_such():
    """Of two broken consumers only one lives in this repo. Reporting both as
    fixed would be false."""
    body = render_pr_body(_verdict(GOOD), _remediation(), PASSED, Decision(is_blocked=False))

    assert "different system" in body


def test_pr_body_states_that_code_was_executed():
    body = render_pr_body(_verdict(GOOD), _remediation(), PASSED, Decision(is_blocked=False))

    assert "executed before this PR was opened" in body


def test_refusal_names_what_would_unblock_it():
    """A refusal that reads as evasive is worse than no tool."""
    decision = Decision(is_blocked=True, blocked_on="lineage ingestion for: tableau")
    text = render_refusal(_verdict(DARK), Remediation(strategy=FixStrategy.UPDATE_REFERENCES), decision)

    assert "tableau" in text
    assert "resumes automatically" in text
    assert "No pull request was opened" in text


def test_refusal_with_no_findings_states_the_ambiguity():
    verdict = _verdict(DARK, names=())
    decision = Decision(is_blocked=True, blocked_on="lineage ingestion for: tableau")
    text = render_refusal(verdict, Remediation(strategy=FixStrategy.UPDATE_REFERENCES), decision)

    assert "not evidence of absence" in text