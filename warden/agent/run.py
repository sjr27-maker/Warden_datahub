"""Pipeline orchestration.

Two entry points: live, which talks to a running DataHub over MCP, and
offline, which replays a captured snapshot. Both run identical agent code —
the only difference is which client is handed in, so an offline run exercises
the same logic rather than a simplified imitation of it.
"""

import argparse
import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from warden.agent import skeptic
from warden.agent.assessor import Assessor
from warden.agent.diff import parse_diff_file
from warden.agent.mcp_client import mcp_client
from warden.agent.models import Decision, ProposedChange, Remediation, Verdict, VerificationResult
from warden.agent.remediator import Remediator
from warden.agent.report import render_pr_body, render_refusal
from warden.agent.scoper import Scoper
from warden.agent.scribe import Scribe
from warden.agent.verifier import Verifier
from warden.snapshot import SnapshotClient

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path("examples")


@dataclass
class RunResult:
    change: ProposedChange
    verdict: Verdict
    remediation: Remediation
    verification: VerificationResult
    decision: Decision
    document: str

    @property
    def blocked(self) -> bool:
        return self.decision.is_blocked


async def run_once(client, change: ProposedChange, verify: bool = True) -> RunResult:
    subgraph = await Scoper(client).scope(change.model, column=change.column)
    registry = await skeptic.load_registry(client)
    ceiling = skeptic.assess(subgraph, registry)

    verdict = Assessor().assess(change, subgraph, ceiling)
    remediation = Remediator().remediate(verdict)

    verification = (
        Verifier().verify(remediation, change)
        if verify and not remediation.is_blocked
        else VerificationResult()
    )

    decision = await Scribe(client).record(verdict, subgraph, remediation, verification)

    document = (
        render_refusal(verdict, remediation, decision)
        if decision.is_blocked
        else render_pr_body(verdict, remediation, verification, decision)
    )

    return RunResult(change, verdict, remediation, verification, decision, document)


def write_output(result: RunResult, profile: str, directory: Path = OUTPUT_DIR) -> Path:
    """Write to examples/ by default rather than opening a PR.

    A fresh clone must not spam GitHub, and a judge should be able to read the
    output without running anything.
    """
    directory.mkdir(parents=True, exist_ok=True)
    kind = "refusal" if result.blocked else "pr"
    path = directory / f"{profile}-{result.change.model}-{kind}.md"
    path.write_text(result.document)
    return path


def _summarise(result: RunResult, profile: str) -> None:
    ceiling = result.verdict.ceiling
    print(f"\n{'=' * 68}")
    print(f"profile      : {profile}")
    print(f"change       : {result.change.kind.value} on {result.change.model}", end="")
    print(f".{result.change.column}" if result.change.column else "")
    print(f"coverage     : {ceiling.report.score} (threshold {ceiling.threshold_used})")
    print(f"verdict      : {result.verdict.overall.value}")
    print(f"impacted     : {len(result.verdict.impacted)}")
    if result.blocked:
        print(f"OUTCOME      : HELD — blocked on {result.decision.blocked_on}")
    else:
        print(f"edits        : {len(result.remediation.edits)}")
        print(f"verified     : {result.verification.passed}")
        print("OUTCOME      : PR")
    print("=" * 68)


async def _live(change: ProposedChange, profile: str) -> RunResult:
    async with mcp_client() as client:
        return await run_once(client, change)


async def _offline(change: ProposedChange, snapshot: Path) -> RunResult:
    client = SnapshotClient.load(snapshot)
    # Verification needs a real dbt project; offline runs skip it and say so.
    return await run_once(client, change, verify=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Warden against a proposed change.")
    parser.add_argument("--diff", type=Path, help="unified diff to evaluate")
    parser.add_argument("--snapshot", type=Path, help="run offline against a captured graph")
    parser.add_argument("--profile", default="live", help="label for output files")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    if not args.diff:
        parser.error("--diff is required")

    changes = parse_diff_file(args.diff)
    if not changes:
        print(f"no recognisable change in {args.diff}")
        return

    for change in changes:
        if args.snapshot:
            result = asyncio.run(_offline(change, args.snapshot))
            profile = args.snapshot.stem
        else:
            result = asyncio.run(_live(change, args.profile))
            profile = args.profile

        _summarise(result, profile)
        path = write_output(result, profile)
        print(f"written      : {path}\n")


if __name__ == "__main__":
    main()
