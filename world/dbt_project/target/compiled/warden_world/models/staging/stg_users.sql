select
    user_id,
    email,
    signup_date,
    country
from "warehouse"."main"."raw_users"
where not is_test