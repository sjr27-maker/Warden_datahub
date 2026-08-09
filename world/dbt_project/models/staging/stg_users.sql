select
    user_id,
    email,
    signup_date,
    country
from {{ source('raw', 'raw_users') }}
where not is_test