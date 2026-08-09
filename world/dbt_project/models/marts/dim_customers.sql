with orders as (
    select
        cust_id,
        min(order_date) as first_order_date,
        max(order_date) as last_order_date,
        count(*) as order_count
    from {{ ref('stg_orders') }}
    group by cust_id
)

select
    u.user_id,
    u.email,
    u.country,
    u.signup_date,
    o.first_order_date,
    o.last_order_date,
    coalesce(o.order_count, 0) as order_count
from {{ ref('stg_users') }} u
left join orders o on u.user_id = o.cust_id