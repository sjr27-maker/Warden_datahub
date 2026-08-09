with refunds as (
    select order_id, sum(refund_amount_usd) as refunded_usd
    from "warehouse"."main"."stg_refunds"
    group by order_id
)

select
    p.order_id,
    o.cust_id,
    o.order_date,
    p.amount_usd as gross_revenue_usd,
    coalesce(r.refunded_usd, 0) as refunded_usd,
    p.amount_usd - coalesce(r.refunded_usd, 0) as net_revenue_usd
from "warehouse"."main"."stg_payments" p
join "warehouse"."main"."stg_orders" o on p.order_id = o.order_id
left join refunds r on p.order_id = r.order_id