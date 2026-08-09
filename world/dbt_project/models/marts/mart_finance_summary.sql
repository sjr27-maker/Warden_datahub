-- HOLE #2: reads only from a hand-maintained extract. Nothing upstream of
-- finance_extract is knowable from any connector.
select
    period,
    region,
    reported_revenue_usd
from {{ source('raw', 'finance_extract') }}