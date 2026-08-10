"""Does Warden cry wolf?

A tool that alarms on safe changes gets ignored, and a tool that gets ignored
is worse than no tool. This measures two things the field generally does not
report: how often Warden flags a change that was safe, and how often it holds
a decision it could have handled.
"""

import asyncio
import json
from pathlib import Path

from warden.agent.models import BreakageTier, ChangeKind, ProposedChange
from warden.agent.run import run_once
from warden.snapshot import SnapshotClient

SNAPSHOT_DIR = Path("snapshots")
RESULTS = Path("examples/evidence")

# Changes that look alarming and are not. Each names the trap it sets.
NEAR_MISSES = [
    (
        ProposedChange(
            model="stg_order_items", kind=ChangeKind.TYPE_WIDENED, column="quantity"
        ),
        "type change on a widely-consumed column — widening cannot break a reader",
    ),
    (
        ProposedChange(
            model="stg_order_items", kind=ChangeKind.COLUMN_ADDED, column="discount_usd"
        ),
        "schema change on a model four marts read — additions are invisible to readers",
    ),
    (
        ProposedChange(model="stg_refunds", kind=ChangeKind.COLUMN_DROPPED, column="reason"),
        "a dropped column, which nothing downstream selects",
    ),
]

BREAKING = [
    (
        ProposedChange(
            model="stg_orders",
            kind=ChangeKind.COLUMN_RENAMED,
            column="cust_id",
            new_value="customer_id",
        ),
        BreakageTier.BREAKS,
    ),
    (
        ProposedChange(
            model="stg_payments", kind=ChangeKind.TYPE_NARROWED, column="amount_usd"
        ),
        BreakageTier.DEGRADES,
    ),
]


async def _evaluate(profile: str) -> dict:
    client = SnapshotClient.load(SNAPSHOT_DIR / f"{profile}.json")
    rows = []

    for change, trap in NEAR_MISSES:
        result = await run_once(client, change, verify=False)
        alarmed = result.verdict.overall in (BreakageTier.BREAKS, BreakageTier.DEGRADES)
        rows.append(
            {
                "change": f"{change.kind.value} on {change.model}.{change.column}",
                "expected": "safe",
                "verdict": result.verdict.overall.value,
                "held": result.blocked,
                "false_alarm": alarmed,
                "trap": trap,
            }
        )

    for change, expected in BREAKING:
        result = await run_once(client, change, verify=False)
        rows.append(
            {
                "change": f"{change.kind.value} on {change.model}.{change.column}",
                "expected": expected.value,
                "verdict": result.verdict.overall.value,
                "held": result.blocked,
                "missed": result.verdict.overall
                not in (BreakageTier.BREAKS, BreakageTier.DEGRADES),
                "trap": "",
            }
        )

    false_alarms = sum(1 for r in rows if r.get("false_alarm"))
    missed = sum(1 for r in rows if r.get("missed"))
    held_safe = sum(1 for r in rows if r["expected"] == "safe" and r["held"])

    return {
        "profile": profile,
        "cases": len(rows),
        "false_alarms": false_alarms,
        "missed_breakage": missed,
        "safe_changes_held": held_safe,
        "rows": rows,
    }


def _render(results: list[dict]) -> str:
    lines = [
        "# Near-miss gauntlet",
        "",
        "Changes that look dangerous and are not, run alongside changes that really",
        "break things. A detector that fires on both is noise; a detector that fires",
        "on neither is decoration.",
        "",
    ]

    for result in results:
        lines += [
            f"## Profile: `{result['profile']}`",
            "",
            f"- False alarms on safe changes: **{result['false_alarms']} / 3**",
            f"- Missed real breakage: **{result['missed_breakage']} / 2**",
            f"- Safe changes held pending metadata: **{result['safe_changes_held']} / 3**",
            "",
            "| Change | Expected | Verdict | Held | Trap |",
            "|---|---|---|---|---|",
        ]
        for row in result["rows"]:
            lines.append(
                f"| `{row['change']}` | {row['expected']} | {row['verdict']} | "
                f"{'yes' if row['held'] else 'no'} | {row['trap']} |"
            )
        lines.append("")

    return "\n".join(lines)


async def main() -> None:
    results = [await _evaluate(p) for p in ("covered", "dark")]

    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "gauntlet.json").write_text(json.dumps(results, indent=2))
    (RESULTS / "gauntlet.md").write_text(_render(results))

    for result in results:
        print(
            f"{result['profile']:8} false_alarms={result['false_alarms']}/3  "
            f"missed={result['missed_breakage']}/2  "
            f"safe_held={result['safe_changes_held']}/3"
        )
    print(f"\nwritten to {RESULTS}/gauntlet.md")


if __name__ == "__main__":
    asyncio.run(main())