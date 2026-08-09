select
    payment_id,
    order_id,
    amount_cents / 100.0 as amount_usd,
    method,
    paid_at
from "warehouse"."main"."raw_payments"