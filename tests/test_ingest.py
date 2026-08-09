from warden.ingest import DBT_LINEAGE, PYTHON_OUTPUTS, TABLEAU_DASHBOARDS
from world.estate import COVERED, DARK


def test_finance_mart_has_no_upstream():
    """HOLE #2 — the mart whose origin is a spreadsheet. Even under full
    coverage this must stay empty; some things are genuinely unknowable."""
    assert DBT_LINEAGE["mart_finance_summary"] == []


def test_tableau_consumes_marts_that_dark_profile_cannot_see():
    """HOLE #1 — these consumers exist in reality regardless of profile."""
    consumed = {m for ups in TABLEAU_DASHBOARDS.values() for m in ups}
    assert "fct_revenue" in consumed
    assert "tableau" not in DARK.platforms
    assert "tableau" in COVERED.platforms


def test_python_edge_only_emitted_when_inference_enabled():
    """HOLE #3 — no parser produces this edge. It exists only if inferred."""
    assert PYTHON_OUTPUTS["customer_segments"] == ["customer_ltv"]
    assert DARK.emit_inferred_edges is False
    assert COVERED.emit_inferred_edges is True
