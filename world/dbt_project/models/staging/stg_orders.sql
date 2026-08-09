select
    order_id,
    cust_id,
    order_date,
    status,
    channel
from {{ source('raw', 'raw_orders') }}