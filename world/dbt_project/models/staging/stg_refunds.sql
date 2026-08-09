select
    refund_id,
    order_id,
    refund_amount_cents / 100.0 as refund_amount_usd,
    refund_date,
    reason
from {{ source('raw', 'raw_refunds') }}