from tests.fakes.ceilings import DARK, GOOD
from warden.agent.assessor import Assessor, build_verdict
from warden.agent.models import (
    BreakageTier,
    ChangeKind,
    EntityRef,
    ProposedChange,
    Subgraph,
)


def _ref(name: str) -> EntityRef:
    return EntityRef(
        urn=f"urn:li:dataset:(urn:li:dataPlatform:dbt,{name},PROD)",
        platform="dbt",
        name=name,
        entity_type="dataset",
    )


def _subgraph(root: str, consumers: list[str]) -> Subgraph:
    return Subgraph(
        root=_ref(root),
        entities=[_ref(root), *[_ref(c) for c in consumers]],
        edges=[],
        relevance_trace={_ref(c).urn: "downstream, within 1 hop(s)" for c in consumers},
    )


def test_rename_with_consumers_breaks():
    change = ProposedChange(model="stg_orders", kind=ChangeKind.COLUMN_RENAMED, column="cust_id")
    verdict = Assessor().assess(
        change, _subgraph("stg_orders", ["fct_orders", "fct_revenue"]), GOOD
    )

    assert verdict.overall is BreakageTier.BREAKS
    assert len(verdict.impacted) == 2


def test_narrowing_degrades_rather_than_breaks():
    """Nothing errors. Values round. The middle tier exists for exactly this."""
    change = ProposedChange(
        model="stg_payments", kind=ChangeKind.TYPE_NARROWED, column="amount_usd"
    )
    verdict = Assessor().assess(change, _subgraph("stg_payments", ["fct_revenue"]), GOOD)

    assert verdict.overall is BreakageTier.DEGRADES


def test_widening_is_safe_even_with_consumers():
    """A near-miss: a type change on a widely-consumed column that cannot
    break a reader. Alarming here is how a tool teaches people to ignore it."""
    change = ProposedChange(
        model="stg_order_items", kind=ChangeKind.TYPE_WIDENED, column="quantity"
    )
    verdict = Assessor().assess(
        change, _subgraph("stg_order_items", ["fct_orders", "customer_ltv"]), GOOD
    )

    assert verdict.overall is BreakageTier.SAFE
    assert verdict.impacted == []


def test_added_column_is_safe():
    change = ProposedChange(
        model="stg_order_items", kind=ChangeKind.COLUMN_ADDED, column="discount_usd"
    )
    verdict = Assessor().assess(change, _subgraph("stg_order_items", ["fct_orders"]), GOOD)

    assert verdict.overall is BreakageTier.SAFE


def test_safe_verdict_unconstructible_below_threshold():
    """The gate, proven.

    Identical input — a drop with no consumers found. Under good coverage that
    is genuinely safe. Under a dark graph the same empty result means only that
    nothing was visible, and SAFE must not be reachable.
    """
    change = ProposedChange(model="stg_refunds", kind=ChangeKind.COLUMN_DROPPED, column="reason")
    subgraph = _subgraph("stg_refunds", [])

    good = Assessor().assess(change, subgraph, GOOD)
    dark = Assessor().assess(change, subgraph, DARK)

    assert good.overall is BreakageTier.SAFE
    assert good.abstained is False

    assert dark.overall is not BreakageTier.SAFE
    assert dark.abstained is True
    assert "tableau" in dark.reasoning


def test_intrinsic_safety_survives_a_dark_graph():
    """Widening cannot break a reader whether or not that reader is visible.
    Gating it would be crying wolf — the failure mode that makes tools ignored.

    Contrast with test_safe_verdict_unconstructible_below_threshold, where
    safety rests on having looked and found nothing.
    """
    change = ProposedChange(
        model="stg_order_items", kind=ChangeKind.TYPE_WIDENED, column="quantity"
    )
    verdict = Assessor().assess(change, _subgraph("stg_order_items", ["fct_orders"]), DARK)

    assert verdict.overall is BreakageTier.SAFE
    assert verdict.abstained is False


def test_abstention_names_the_gap():
    """A refusal that says 'low confidence' is not actionable. It has to name
    what would change the answer."""
    change = ProposedChange(model="stg_refunds", kind=ChangeKind.COLUMN_DROPPED, column="reason")
    verdict = Assessor().assess(change, _subgraph("stg_refunds", []), DARK)

    assert "not evidence of absence" in verdict.reasoning


def test_build_verdict_downgrades_directly():
    """Enforcement lives in the factory, so it cannot be skipped by a caller
    that assembles impacts differently."""
    change = ProposedChange(model="x", kind=ChangeKind.COLUMN_DROPPED)
    verdict = build_verdict(change, [], DARK)

    assert verdict.overall is BreakageTier.TOUCHES
    assert verdict.abstained is True
