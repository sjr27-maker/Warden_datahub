"""The judge path: both profiles, one command, no infrastructure.

Coverage arithmetic and the gate are deterministic, so the central claim —
that the same change produces a PR against one graph and a refusal against
another — can be demonstrated without Docker, an 8GB stack, or a token.

What this cannot show is the live write-back or the verification loop. Those
need a real catalog and a real warehouse, and the README says so.
"""

import asyncio
from pathlib import Path

from warden.agent.diff import parse_diff
from warden.agent.run import run_once, write_output
from warden.snapshot import SnapshotClient

SNAPSHOT_DIR = Path("snapshots")
SCENARIO = Path("world/scenarios/rename_cust_id.diff")


async def main() -> None:
    changes = parse_diff(SCENARIO.read_text())
    if not changes:
        raise SystemExit(f"could not parse {SCENARIO}")
    change = changes[0]

    print("Warden — offline demo")
    print(f"Change: {change.kind.value} on {change.model}.{change.column}")
    print("Identical change, identical code. Two graphs.\n")

    for profile in ("covered", "dark"):
        snapshot = SNAPSHOT_DIR / f"{profile}.json"
        if not snapshot.exists():
            print(f"  {profile}: snapshot missing at {snapshot}")
            continue

        client = SnapshotClient.load(snapshot)
        result = await run_once(client, change, verify=False)
        report = result.verdict.ceiling.report

        outcome = (
            f"HELD — blocked on {result.decision.blocked_on}"
            if result.blocked
            else f"PR — {len(result.remediation.edits)} file(s) to fix"
        )

        print(f"  {profile:8} coverage {report.score:<6} {outcome}")
        for spot in report.blind_spots:
            print(f"           └─ {spot.description}")

        path = write_output(result, profile)
        print(f"           written to {path}\n")

    print("The graph changed. The code did not.")


if __name__ == "__main__":
    asyncio.run(main())
