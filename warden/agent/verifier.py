"""Execution before publication.

Nothing reaches a PR unexecuted. A PR containing code that does not compile
burns reviewer trust, and after two of those nobody reads the third — which
costs more than the tool ever saved.

Verification applies the proposed change *and* the generated fixes together,
because that combination is what a merge would produce. Testing the fixes
alone would compile against a source that still has the old shape, and prove
nothing.
"""

import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml

from warden.agent.models import (
    FileEdit,
    ProposedChange,
    Remediation,
    VerificationAttempt,
    VerificationResult,
)

logger = logging.getLogger(__name__)

DBT_PROJECT_DIR = Path("world/dbt_project")
WAREHOUSE = Path("world/warehouse.duckdb")
MAX_ATTEMPTS = 3
_OUTPUT_TAIL_CHARS = 2000
_DBT_TIMEOUT_SECONDS = 300


class Verifier:
    def __init__(
        self,
        project_dir: Path = DBT_PROJECT_DIR,
        warehouse: Path = WAREHOUSE,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> None:
        self._project_dir = project_dir
        self._warehouse = warehouse
        self._max_attempts = max_attempts

    def verify(
        self, remediation: Remediation, change: ProposedChange | None = None
    ) -> VerificationResult:
        """Apply the change and its fixes in a scratch copy, then run dbt.

        The working tree is never touched — a failed verification must leave no
        trace, and a passing one hands its edits to the PR rather than having
        already applied them. The warehouse is copied rather than shared, since
        dbt writes tables and a verification run must not mutate real data.
        """
        if remediation.is_blocked or not remediation.edits:
            return VerificationResult()

        result = VerificationResult(final_edits=list(remediation.edits))

        with tempfile.TemporaryDirectory(prefix="warden-verify-") as tmp:
            root = Path(tmp)
            sandbox = root / "dbt_project"
            shutil.copytree(
                self._project_dir, sandbox, ignore=shutil.ignore_patterns("target", "logs")
            )

            warehouse_copy = root / self._warehouse.name
            if self._warehouse.exists():
                shutil.copy2(self._warehouse, warehouse_copy)
            else:
                logger.warning(
                    "warehouse missing at %s; dbt will build from empty", self._warehouse
                )

            self._point_profile_at(sandbox, warehouse_copy)
            if change is not None:
                self._apply_change(sandbox, change)
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

    def _apply_change(self, sandbox: Path, change: ProposedChange) -> None:
        """Apply the proposed change to its own model.

        Without this, the fixes compile against a source that still has the
        old shape — the compiler reports the fix as broken when in fact the
        change it anticipates simply has not happened yet.
        """
        if change.column is None or change.new_value is None:
            return

        matches = list(sandbox.rglob(f"{change.model}.sql"))
        if not matches:
            logger.warning("no source file for changed model %s", change.model)
            return

        target = matches[0]
        sql = target.read_text()
        pattern = rf"\b{re.escape(change.column)}\b"
        target.write_text(re.sub(pattern, f"{change.column} as {change.new_value}", sql, count=1))

    def _point_profile_at(self, sandbox: Path, warehouse: Path) -> None:
        """Rewrite the profile to an absolute path.

        The committed profile uses a relative path, which resolves against the
        project directory — correct in the repository, wrong in a sandbox.
        """
        profile_path = sandbox / "profiles.yml"
        if not profile_path.exists():
            logger.warning("no profiles.yml in sandbox at %s", profile_path)
            return

        profile = yaml.safe_load(profile_path.read_text())
        for project in profile.values():
            for output in project.get("outputs", {}).values():
                if output.get("type") == "duckdb":
                    output["path"] = str(warehouse.resolve())
        profile_path.write_text(yaml.safe_dump(profile, sort_keys=False))

    def _apply_edits(self, sandbox: Path, edits: list[FileEdit]) -> None:
        for edit in edits:
            relative = Path(edit.path).relative_to(self._project_dir)
            target = sandbox / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(edit.modified)

    def _run_dbt(self, sandbox: Path, attempt: int) -> VerificationAttempt:
        command = "dbt build"
        env = dict(os.environ)
        env["DBT_PROFILES_DIR"] = "."
        try:
            completed = subprocess.run(
                ["dbt", "build"],
                cwd=sandbox,
                env=env,
                capture_output=True,
                text=True,
                timeout=_DBT_TIMEOUT_SECONDS,
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
        if _unresolved_column(failure.output_tail) is None:
            logger.info("no automatic repair available for attempt %d", failure.attempt)
            return False
        return False  # repair strategies land as real failure modes are observed


def _unresolved_column(output: str) -> str | None:
    for marker in ("Referenced column", "column not found", "Binder Error"):
        if marker in output:
            return marker
    return None