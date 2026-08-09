with items as (
    select
        order_id,
        sum(quantity) as total_units,
        sum(quantity * unit_price_usd) as gross_value_usd
    from "warehouse"."main"."stg_order_items"
    group by order_id
)

select
    o.order_id,
    o.cust_id,
    o.order_date,
    o.status,
    o.channel,
    coalesce(i.total_units, 0) as total_units,
    coalesce(i.gross_value_usd, 0) as gross_value_usd
from "warehouse"."main"."stg_orders" o
left join items i on o.order_id = i.order_id