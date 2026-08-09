select
    r.cust_id,
    count(distinct r.order_id) as paid_order_count,
    sum(r.net_revenue_usd) as lifetime_value_usd,
    min(r.order_date) as first_paid_date,
    max(r.order_date) as last_paid_date
from "warehouse"."main"."fct_revenue" r
group by r.cust_id