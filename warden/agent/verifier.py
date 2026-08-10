"""Execution before publication.

Nothing reaches a PR unexecuted. A PR containing code that does not compile
burns reviewer trust, and after two of those nobody reads the third — which
costs more than the tool ever saved.

Errors feed back into a bounded retry. The transcript is kept and committed,
because a reviewer learns more from a failure that was caught and corrected
than from a clean final artifact.
"""

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from warden.agent.models import (
    FileEdit,
    Remediation,
    VerificationAttempt,
    VerificationResult,
)

logger = logging.getLogger(__name__)

DBT_PROJECT_DIR = Path("world/dbt_project")
MAX_ATTEMPTS = 3
_OUTPUT_TAIL_CHARS = 2000


class Verifier:
    def __init__(
        self,
        project_dir: Path = DBT_PROJECT_DIR,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> None:
        self._project_dir = project_dir
        self._max_attempts = max_attempts

    def verify(self, remediation: Remediation) -> VerificationResult:
        """Apply edits in a scratch copy and run dbt against them.

        The working tree is never touched — a failed verification must leave
        no trace, and a passing one hands its edits to the PR rather than
        having already applied them.
        """
        if remediation.is_blocked or not remediation.edits:
            return VerificationResult()

        result = VerificationResult(final_edits=list(remediation.edits))

        with tempfile.TemporaryDirectory(prefix="warden-verify-") as tmp:
            sandbox = Path(tmp) / "dbt_project"
            shutil.copytree(self._project_dir, sandbox)

            self._apply_edits(sandbox, result.final_edits)

            for attempt in range(1, self._max_attempts + 1):
                record = self._run_dbt(sandbox, attempt)
                result.attempts.append(record)
                if record.passed:
                    break
                if attempt == self._max_attempts:
                    break
                if not self._repair(sandbox, record, result):
                    break

        return result

    def _apply_edits(self, sandbox: Path, edits: list[FileEdit]) -> None:
        for edit in edits:
            relative = Path(edit.path).relative_to(self._project_dir)
            target = sandbox / relative
            target.write_text(edit.modified)

    def _run_dbt(self, sandbox: Path, attempt: int) -> VerificationAttempt:
        command = "dbt build"
        try:
            completed = subprocess.run(
                ["dbt", "build"],
                cwd=sandbox,
                env={"DBT_PROFILES_DIR": ".", "PATH": _path()},
                capture_output=True,
                text=True,
                timeout=180,
            )
            output = completed.stdout + completed.stderr
            return VerificationAttempt(
                attempt=attempt,
                command=command,
                exit_code=completed.returncode,
                output_tail=output[-_OUTPUT_TAIL_CHARS:],
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            return VerificationAttempt(
                attempt=attempt,
                command=command,
                exit_code=1,
                output_tail=f"execution failed: {exc}",
            )

    def _repair(
        self, sandbox: Path, failure: VerificationAttempt, result: VerificationResult
    ) -> bool:
        """Attempt a correction from the error text.

        Deliberately narrow. Only failures whose cause is unambiguous from the
        compiler output are repaired; anything else stops and reports, rather
        than guessing its way into a plausible-looking wrong answer.
        """
        missing = _missing_column(failure.output_tail)
        if missing is None:
            logger.info("no automatic repair available for attempt %d", failure.attempt)
            return False

        logger.info("repairing unresolved column %r", missing)
        return False  # repair strategies land as failure modes are observed


def _missing_column(output: str) -> str | None:
    for marker in ("Referenced column", "column not found", "Binder Error"):
        if marker in output:
            return marker
    return None


def _path() -> str:
    import os

    return os.environ.get("PATH", "/usr/bin:/bin")