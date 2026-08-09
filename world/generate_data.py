import random
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pandas as pd
from faker import Faker

SEED = 42
WAREHOUSE = Path(__file__).parent / "warehouse.duckdb"

N_USERS = 500
N_ORDERS = 2000

fake = Faker()
Faker.seed(SEED)
random.seed(SEED)

COUNTRIES = ["IN", "US", "GB", "DE", "SG"]
CHANNELS = ["web", "ios", "android", "partner"]
STATUSES = ["completed", "cancelled", "pending"]
METHODS = ["card", "upi", "wallet", "netbanking"]
REFUND_REASONS = ["damaged", "late_delivery", "changed_mind", "wrong_item"]


def _users() -> pd.DataFrame:
    start = date(2024, 1, 1)
    return pd.DataFrame(
        [
            {
                "user_id": i,
                "email": fake.email(),
                "signup_date": start + timedelta(days=random.randint(0, 600)),
                "country": random.choice(COUNTRIES),
                # is_test exists because real estates have this trap: a filter
                # everyone must remember and nobody documents.
                "is_test": i % 97 == 0,
            }
            for i in range(1, N_USERS + 1)
        ]
    )


def _orders() -> pd.DataFrame:
    start = date(2024, 3, 1)
    return pd.DataFrame(
        [
            {
                "order_id": i,
                # cust_id, not customer_id — this is the rename target that
                # propagates through staging into two marts.
                "cust_id": random.randint(1, N_USERS),
                "order_date": start + timedelta(days=random.randint(0, 500)),
                "status": random.choices(STATUSES, weights=[85, 10, 5])[0],
                "channel": random.choice(CHANNELS),
            }
            for i in range(1, N_ORDERS + 1)
        ]
    )


def _order_items(orders: pd.DataFrame) -> pd.DataFrame:
    rows = []
    item_id = 1
    for order_id in orders["order_id"]:
        for _ in range(random.randint(1, 4)):
            rows.append(
                {
                    "item_id": item_id,
                    "order_id": order_id,
                    "sku": f"SKU-{random.randint(1000, 9999)}",
                    "quantity": random.randint(1, 3),
                    "unit_price_cents": random.randint(19900, 499900),
                }
            )
            item_id += 1
    return pd.DataFrame(rows)


def _payments(order_items: pd.DataFrame, orders: pd.DataFrame) -> pd.DataFrame:
    totals = (
        order_items.assign(line=lambda d: d.quantity * d.unit_price_cents)
        .groupby("order_id")["line"]
        .sum()
    )
    completed = orders[orders.status == "completed"]["order_id"]
    return pd.DataFrame(
        [
            {
                "payment_id": i + 1,
                "order_id": int(oid),
                "amount_cents": int(totals.get(oid, 0)),
                "method": random.choice(METHODS),
                "paid_at": date(2024, 3, 1) + timedelta(days=random.randint(0, 500)),
            }
            for i, oid in enumerate(completed)
        ]
    )


def _refunds(payments: pd.DataFrame) -> pd.DataFrame:
    sample = payments.sample(frac=0.08, random_state=SEED)
    return pd.DataFrame(
        [
            {
                "refund_id": i + 1,
                "order_id": int(row.order_id),
                "refund_amount_cents": int(row.amount_cents * random.uniform(0.2, 1.0)),
                "refund_date": row.paid_at + timedelta(days=random.randint(1, 30)),
                "reason": random.choice(REFUND_REASONS),
            }
            for i, row in enumerate(sample.itertuples())
        ]
    )


def _finance_extract() -> pd.DataFrame:
    """Hand-maintained quarterly extract. HOLE #2: no upstream lineage exists
    because a human pastes this in from a spreadsheet every quarter. Warden
    must recognise it cannot see where these numbers come from."""
    return pd.DataFrame(
        [
            {"period": p, "region": r, "reported_revenue_usd": random.randint(50000, 900000)}
            for p in ["2024Q2", "2024Q3", "2024Q4", "2025Q1"]
            for r in COUNTRIES
        ]
    )


def main() -> None:
    users = _users()
    orders = _orders()
    order_items = _order_items(orders)
    payments = _payments(order_items, orders)
    refunds = _refunds(payments)
    finance = _finance_extract()

    WAREHOUSE.unlink(missing_ok=True)
    con = duckdb.connect(str(WAREHOUSE))
    for name, df in [
        ("raw_users", users),
        ("raw_orders", orders),
        ("raw_order_items", order_items),
        ("raw_payments", payments),
        ("raw_refunds", refunds),
        ("finance_extract", finance),
    ]:
        con.register("_df", df)
        con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM _df")
        print(f"  {name:<20} {len(df):>6} rows")
    con.close()
    print(f"\nwarehouse written to {WAREHOUSE}")


if __name__ == "__main__":
    main()