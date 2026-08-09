
  
  create view "warehouse"."main"."stg_order_items__dbt_tmp" as (
    select
    item_id,
    order_id,
    sku,
    quantity,
    unit_price_cents / 100.0 as unit_price_usd
from "warehouse"."main"."raw_order_items"
  );
