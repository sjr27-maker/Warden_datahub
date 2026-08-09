import inspect

from warden.agent import skeptic
from warden.agent.models import (
    EntityRef,
    LineageEdge,
    Provenance,
    Subgraph,
    Verdict,
)
from warden.registry import PlatformRecord


def _ref(name: str, platform: str = "dbt") -> EntityRef:
    return EntityRef(
        urn=f"urn:li:dataset:(urn:li:dataPlatform:{platform},{name},PROD)",
        platform=platform,
        name=name,
        entity_type="dataset",
    )


def _subgraph(entity_names: list[str]) -> Subgraph:
    root = _ref(entity_names[0])
    others = [_ref(n) for n in entity_names[1:]]
    return Subgraph(
        root=root,
        entities=[root, *others],
        edges=[
            LineageEdge(upstream=root, downstream=o, provenance=Provenance.PARSED, confidence=1.0)
            for o in others
        ],
    )


COVERED_REGISTRY = [
    PlatformRecord(platform=p, lineage_connector_configured=True, expected_entity_count=n, note="")
    for p, n in [("dbt", 16), ("duckdb", 6), ("tableau", 4), ("python", 1)]
]

DARK_REGISTRY = [
    PlatformRecord(
        platform=p, lineage_connector_configured=configured, expected_entity_count=n, note=""
    )
    for p, n, configured in [
        ("dbt", 16, True),
        ("duckdb", 6, True),
        ("tableau", 4, False),
        ("python", 1, False),
    ]
]


def test_identical_input_yields_different_ceiling_by_graph():
    """The ceiling must be a function of the graph, not of the question.

    Same subgraph, two registries. If coverage were cosmetic — a caveat
    appended to prose rather than a computed cap — these would agree.
    """
    subgraph = _subgraph(
        [
            "fct_revenue",
            "stg_payments",
            "stg_orders",
            "stg_refunds",
            "customer_ltv",
            "dim_customers",
        ]
    )

    covered = skeptic.assess(subgraph, COVERED_REGISTRY)
    dark = skeptic.assess(subgraph, DARK_REGISTRY)

    assert covered.report.score > dark.report.score
    assert covered.may_assert_safe is True
    assert dark.may_assert_safe is False


def test_dark_platform_is_named_not_scored():
    """'confidence: medium' is not actionable. A blind spot must name the
    platform so the refusal converts into a work item."""
    subgraph = _subgraph(["fct_revenue", "stg_payments"])
    ceiling = skeptic.assess(subgraph, DARK_REGISTRY)

    described = " ".join(s.description for s in ceiling.report.blind_spots)
    assert "tableau" in described
    assert any(s.affected_platform == "tableau" for s in ceiling.report.blind_spots)


def test_skeptic_cannot_see_downstream_conclusions():
    """Structural independence, not a convention.

    If assess() ever accepts a Verdict or an impact assessment, it can
    rationalise a conclusion instead of auditing the evidence. This test
    fails the moment that boundary is crossed.
    """
    signature = inspect.signature(skeptic.assess)
    annotations = [str(p.annotation) for p in signature.parameters.values()]

    assert not any("Verdict" in a for a in annotations)
    assert not any("Impact" in a for a in annotations)
    assert not any("Assessment" in a for a in annotations)
    assert Verdict is not None  # imported so the name check above is meaningful


def test_coverage_denominator_excludes_retrieval_size():
    """A larger retrieval must not, by itself, raise coverage.

    Measuring 'how much of what I found did I understand' always reports 100%.
    The denominator has to come from the declared estate.
    """
    small = skeptic.assess(_subgraph(["fct_revenue", "stg_orders"]), DARK_REGISTRY)
    large = skeptic.assess(
        _subgraph(["fct_revenue", "stg_orders", "stg_payments", "stg_refunds", "raw_orders"]),
        DARK_REGISTRY,
    )

    assert small.may_assert_safe is False
    assert large.may_assert_safe is False


def test_orphan_entity_is_flagged():
    """mart_finance_summary has no knowable upstream in any profile."""
    root = _ref("stg_orders")
    orphan = _ref("mart_finance_summary")
    subgraph = Subgraph(root=root, entities=[root, orphan], edges=[])

    ceiling = skeptic.assess(subgraph, COVERED_REGISTRY)
    assert any(orphan.urn in s.affected_urns for s in ceiling.report.blind_spots)
