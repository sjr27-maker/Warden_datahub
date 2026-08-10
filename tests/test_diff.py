from warden.agent.diff import parse_diff
from warden.agent.models import ChangeKind

RENAME = """--- a/world/dbt_project/models/staging/stg_orders.sql
+++ b/world/dbt_project/models/staging/stg_orders.sql
@@ -1,7 +1,7 @@
 select
     order_id,
-    cust_id,
+    cust_id as customer_id,
     order_date,
     status
 from {{ source('raw', 'raw_orders') }}
"""

DROP = """--- a/world/dbt_project/models/staging/stg_refunds.sql
+++ b/world/dbt_project/models/staging/stg_refunds.sql
@@ -1,6 +1,5 @@
 select
     refund_id,
     order_id,
-    reason
 from {{ source('raw', 'raw_refunds') }}
"""

ADD = """--- a/world/dbt_project/models/staging/stg_order_items.sql
+++ b/world/dbt_project/models/staging/stg_order_items.sql
@@ -1,5 +1,6 @@
 select
     item_id,
     quantity,
+    discount_usd
 from {{ source('raw', 'raw_order_items') }}
"""


def test_alias_addition_is_a_rename():
    changes = parse_diff(RENAME)
    rename = next(c for c in changes if c.kind is ChangeKind.COLUMN_RENAMED)
    assert rename.model == "stg_orders"
    assert rename.column == "cust_id"
    assert rename.new_value == "customer_id"


def test_removed_column_is_a_drop():
    changes = parse_diff(DROP)
    assert any(
        c.kind is ChangeKind.COLUMN_DROPPED and c.column == "reason" for c in changes
    )


def test_added_column_is_an_addition():
    changes = parse_diff(ADD)
    assert any(
        c.kind is ChangeKind.COLUMN_ADDED and c.column == "discount_usd" for c in changes
    )


def test_unrecognised_change_falls_back_to_logic_rather_than_guessing():
    """A confident wrong label defeats the Assessor's uncertainty path."""
    diff = """--- a/models/staging/stg_users.sql
+++ b/models/staging/stg_users.sql
@@ -1,3 +1,3 @@
-where not is_test
+where not is_test and country != 'XX'
"""
    changes = parse_diff(diff)
    assert changes
    assert changes[0].kind is ChangeKind.LOGIC_CHANGED