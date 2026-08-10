## Warden held this decision — `stg_orders.cust_id`

**No pull request was opened.** Warden can see part of what this change affects, but not enough to write a fix that would be complete. A partial fix presented as a whole one is worse than no fix, because the PR carries an implicit claim that it handled everything.

### What is missing

- tableau has no lineage connector configured; 4 entities are invisible, and any consumers among them cannot be detected
- python has no lineage connector configured; 1 entities are invisible, and any consumers among them cannot be detected

### What would unblock this

Ingest lineage for the platform(s) above. Warden has recorded this decision in DataHub as blocked on **lineage ingestion for: python, tableau**; when that metadata arrives, the analysis resumes automatically and this PR gets opened.

### What Warden did find

- 🔴 `dbt:dim_customers` — breaks
- 🔴 `dbt:fct_orders` — breaks
- 🔴 `dbt:fct_revenue` — breaks
- 🔴 `dbt:customer_ltv` — breaks

These are real, but they are a floor rather than a full list.

---
Coverage 0.679 against a threshold of 0.6. Query the held decision in DataHub by searching `warden:held-decision`.