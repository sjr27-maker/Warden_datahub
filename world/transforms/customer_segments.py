"""HOLE #3: a pandas transform. No SQL parser can extract lineage from this,
so the edge customer_ltv -> customer_segments is inferable but never parsed.
Warden's Scoper must infer it and tag it Provenance.INFERRED."""

from pathlib import Path

import duckdb
import pandas as pd

WAREHOUSE = Path(__file__).parent.parent / "warehouse.duckdb"


def main() -> None:
    con = duckdb.connect(str(WAREHOUSE))
    ltv = con.execute("select * from customer_ltv").df()

    ltv["segment"] = pd.cut(
        ltv["lifetime_value_usd"],
        bins=[-1, 500, 2000, 10000, float("inf")],
        labels=["low", "mid", "high", "whale"],
    ).astype(str)

    segments = ltv[["cust_id", "lifetime_value_usd", "segment"]]
    con.register("_seg", segments)
    con.execute("CREATE OR REPLACE TABLE customer_segments AS SELECT * FROM _seg")
    print(f"  customer_segments    {len(segments):>6} rows")
    con.close()


if __name__ == "__main__":
    main()