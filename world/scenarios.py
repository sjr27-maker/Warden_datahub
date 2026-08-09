"""Proposed changes Warden evaluates. Each is a real diff against the dbt
project, paired with what a correct system should conclude.

The near-miss cases matter as much as the breaking ones: a tool that alarms
on safe changes gets ignored, and a tool that gets ignored is worse than no
tool at all.
"""

from enum import StrEnum

from pydantic import BaseModel


class ExpectedOutcome(StrEnum):
    BREAKS = "breaks"
    DEGRADES = "degrades"
    SAFE = "safe"


class Scenario(BaseModel):
    id: str
    title: str
    description: str
    target_model: str
    diff_path: str
    expected: ExpectedOutcome
    expected_impacted: list[str]
    is_near_miss: bool = False
    notes: str = ""


SCENARIOS: list[Scenario] = [
    Scenario(
        id="rename-cust-id",
        title="Rename cust_id to customer_id",
        description=(
            "The canonical case. stg_orders exposes cust_id; three marts "
            "reference it. Under the dark profile, Tableau dashboards also "
            "consume two of those marts — invisibly."
        ),
        target_model="stg_orders",
        diff_path="world/scenarios/rename_cust_id.diff",
        expected=ExpectedOutcome.BREAKS,
        expected_impacted=["fct_orders", "fct_revenue", "dim_customers"],
        notes=(
            "The A/B case. Covered profile: confident PR listing all consumers. "
            "Dark profile: coverage below threshold, refuse, name Tableau."
        ),
    ),
    Scenario(
        id="drop-refund-reason",
        title="Drop the reason column from stg_refunds",
        description="A column downstream models do not currently select.",
        target_model="stg_refunds",
        diff_path="world/scenarios/drop_refund_reason.diff",
        expected=ExpectedOutcome.SAFE,
        expected_impacted=[],
        is_near_miss=True,
        notes=(
            "Looks destructive — dropping a column. Is not. No downstream "
            "reference exists. Warden must stay quiet."
        ),
    ),
    Scenario(
        id="narrow-amount-type",
        title="Narrow amount_usd from double to decimal(10,2)",
        description="Type narrowing on a column three marts aggregate.",
        target_model="stg_payments",
        diff_path="world/scenarios/narrow_amount_type.diff",
        expected=ExpectedOutcome.DEGRADES,
        expected_impacted=["fct_revenue", "customer_ltv"],
        notes="Nothing errors. Values silently round. The degrades tier exists for this.",
    ),
    Scenario(
        id="add-nullable-column",
        title="Add a nullable discount_usd column to stg_order_items",
        description="Purely additive change.",
        target_model="stg_order_items",
        diff_path="world/scenarios/add_nullable_column.diff",
        expected=ExpectedOutcome.SAFE,
        expected_impacted=[],
        is_near_miss=True,
        notes="Touches a widely-consumed model. Additive changes are safe.",
    ),
    Scenario(
        id="widen-quantity-type",
        title="Widen quantity from int to bigint",
        description="Type widening — strictly more permissive.",
        target_model="stg_order_items",
        diff_path="world/scenarios/widen_quantity_type.diff",
        expected=ExpectedOutcome.SAFE,
        expected_impacted=[],
        is_near_miss=True,
        notes="Type change on a joined column. Widening cannot break a reader.",
    ),
    Scenario(
        id="rename-in-dark-mart",
        title="Rename reported_revenue_usd in mart_finance_summary",
        description=(
            "Touches the mart whose upstream is a hand-maintained extract. "
            "Warden cannot see where these numbers originate."
        ),
        target_model="mart_finance_summary",
        diff_path="world/scenarios/rename_dark_mart.diff",
        expected=ExpectedOutcome.BREAKS,
        expected_impacted=[],
        notes=(
            "The refusal case in both profiles. Even fully covered, the upstream "
            "is unknowable — so the honest answer is always 'I cannot tell you'."
        ),
    ),
]

BY_ID = {s.id: s for s in SCENARIOS}
NEAR_MISSES = [s for s in SCENARIOS if s.is_near_miss]