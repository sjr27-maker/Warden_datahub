# Near-miss gauntlet

Changes that look dangerous and are not, run alongside changes that really
break things. A detector that fires on both is noise; a detector that fires
on neither is decoration.

## Profile: `covered`

- False alarms on safe changes: **0 / 3**
- Missed real breakage: **0 / 2**
- Safe changes held pending metadata: **1 / 3**

| Change | Expected | Verdict | Held | Trap |
|---|---|---|---|---|
| `type_widened on stg_order_items.quantity` | safe | safe | no | type change on a widely-consumed column — widening cannot break a reader |
| `column_added on stg_order_items.discount_usd` | safe | safe | no | schema change on a model four marts read — additions are invisible to readers |
| `column_dropped on stg_refunds.reason` | safe | touches | yes | a dropped column, which nothing downstream selects |
| `column_renamed on stg_orders.cust_id` | breaks | breaks | no |  |
| `type_narrowed on stg_payments.amount_usd` | degrades | degrades | no |  |

## Profile: `dark`

- False alarms on safe changes: **0 / 3**
- Missed real breakage: **0 / 2**
- Safe changes held pending metadata: **1 / 3**

| Change | Expected | Verdict | Held | Trap |
|---|---|---|---|---|
| `type_widened on stg_order_items.quantity` | safe | safe | no | type change on a widely-consumed column — widening cannot break a reader |
| `column_added on stg_order_items.discount_usd` | safe | safe | no | schema change on a model four marts read — additions are invisible to readers |
| `column_dropped on stg_refunds.reason` | safe | touches | yes | a dropped column, which nothing downstream selects |
| `column_renamed on stg_orders.cust_id` | breaks | breaks | yes |  |
| `type_narrowed on stg_payments.amount_usd` | degrades | degrades | yes |  |
