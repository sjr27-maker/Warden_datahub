
  
  create view "warehouse"."main"."stg_users__dbt_tmp" as (
    select
    user_id,
    email,
    signup_date,
    country
from "warehouse"."main"."raw_users"
where not is_test
  );
