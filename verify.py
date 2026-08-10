#!/usr/bin/env python3
"""The deterministic proof.

Warden's central claim is that coverage arithmetic gates code generation.
That claim is arithmetic, not judgment — no LLM sits in the path — so it can
be proven without a model, a network, or a running catalog.

This script is what CI runs. If it passes with the LLM backend disabled and
no DataHub reachable, the claim holds regardless of which model a reviewer
happens to configure.

Exit 0 = the gate works. Exit 1 = the central claim is broken.
"""

import asyncio
import sys
from pathlib import Path

from warden.agent.models import ChangeKind, ProposedChange
from warden.agent.run import run_once
from warden.snapshot import SnapshotClient

SNAPSHOT_DIR = Path("snapshots")

RENAME = ProposedChange(
    model="stg_orders",
    kind=ChangeKind.COLUMN_RENAMED,
    column="cust_id",
    new_value="customer_id",
)

WIDEN = ProposedChange(
    model="stg_order_items", kind=ChangeKind.TYPE_WIDENED, column="quantity"
)


class Check:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def assert_that(self, condition: bool, description: str) -> None:
        mark = "PASS" if condition else "FAIL"
        print(f"  [{mark}] {description}")
        if not condition:
            self.failures.append(description)


async def main() -> int:
    print("Warden — deterministic verification")
    print("No LLM, no network, no running catalog.\n")

    check = Check()

    for profile in ("covered", "dark"):
        path = SNAPSHOT_DIR / f"{profile}.json"
        if not path.exists():
            print(f"  [FAIL] snapshot missing: {path}")
            return 1

    covered = SnapshotClient.load(SNAPSHOT_DIR / "covered.json")
    dark = SnapshotClient.load(SNAPSHOT_DIR / "dark.json")

    print("Covered graph:")
    result = await run_once(covered, RENAME, verify=False)
    check.assert_that(not result.blocked, "generation proceeds when coverage is complete")
    check.assert_that(len(result.remediation.edits) > 0, "fixes are generated")
    check.assert_that(
        result.verdict.ceiling.may_assert_safe, "safety claims are permitted"
    )

    print("\nDark graph:")
    result = await run_once(dark, RENAME, verify=False)
    check.assert_that(result.blocked, "generation is blocked when consumers are invisible")
    check.assert_that(result.remediation.edits == [], "no code is written while blocked")
    check.assert_that(
        bool(result.decision.blocked_on), "the blocking fact is named, not just scored"
    )
    check.assert_that(
        "tableau" in (result.decision.blocked_on or ""),
        "the named fact identifies the dark platform",
    )

    print("\nIntrinsic safety:")
    result = await run_once(dark, WIDEN, verify=False)
    check.assert_that(
        not result.blocked,
        "a change safe by its own semantics is not held by a dark graph",
    )

    print("\nOverride:")
    result = await run_once(
        dark, RENAME, verify=False, override_reason="team confirmed no BI consumers"
    )
    check.assert_that(not result.blocked, "an explicit override proceeds")
    check.assert_that(
        result.verdict.ceiling.overridden, "the override is recorded, not silent"
    )
    check.assert_that(
        "overridden" in result.document.lower(),
        "the override is visible in the generated document",
    )

    print()
    if check.failures:
        print(f"FAILED — {len(check.failures)} check(s) did not hold:")
        for failure in check.failures:
            print(f"  - {failure}")
        return 1

    print("All checks passed. The gate is a function of the graph, not of the model.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))