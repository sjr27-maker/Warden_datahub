
  
  create view "warehouse"."main"."stg_refunds__dbt_tmp" as (
    select
    refund_id,
    order_id,
    refund_amount_cents / 100.0 as refund_amount_usd,
    refund_date,
    reason
from "warehouse"."main"."raw_refunds"
  );
