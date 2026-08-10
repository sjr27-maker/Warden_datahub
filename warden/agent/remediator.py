"""Fix generation, gated on coverage.

Two commitments.

Generation is blocked when the Skeptic says the graph is too dark — and the
gate is coverage, not severity. Finding real breakage does not license acting
on it: a confident impact list from an incomplete graph is still partial, and
fixing what is visible inside a PR that implies completeness is precisely the
failure this exists to prevent.

And where several strategies are legitimate, Warden recommends one and names
the rest. A column rename admits at least three approaches that differ in
their trade-offs, not in correctness. Silently picking one hides a decision
the reviewer should see.
"""

import logging
import re
from pathlib import Path

from warden.agent.models import (
    BreakageTier,
    ChangeKind,
    FileEdit,
    FixStrategy,
    ProposedChange,
    Remediation,
    Verdict,
)

logger = logging.getLogger(__name__)

DBT_MODELS_DIR = Path("world/dbt_project/models")

# Only dbt models have source files in this repository. Other platforms are
# real consumers Warden can identify but cannot repair.
REPAIRABLE_PLATFORM = "dbt"

_STRATEGIES: dict[ChangeKind, tuple[FixStrategy, list[FixStrategy]]] = {
    ChangeKind.COLUMN_RENAMED: (
        FixStrategy.UPDATE_REFERENCES,
        [FixStrategy.COMPATIBILITY_ALIAS, FixStrategy.TWO_PHASE_DEPRECATE],
    ),
    ChangeKind.COLUMN_DROPPED: (
        FixStrategy.TWO_PHASE_DEPRECATE,
        [FixStrategy.UPDATE_REFERENCES],
    ),
    ChangeKind.TYPE_NARROWED: (
        FixStrategy.COMPATIBILITY_ALIAS,
        [FixStrategy.UPDATE_REFERENCES],
    ),
}


class Remediator:
    def __init__(self, models_dir: Path = DBT_MODELS_DIR) -> None:
        self._models_dir = models_dir

    def remediate(self, verdict: Verdict) -> Remediation:
        """Generate fixes, or refuse.

        The gate is coverage, not severity: finding real breakage on a dark
        graph does not license acting on it, because the impact list is still
        partial.

        But a verdict of SAFE that the Assessor reached *without* relying on
        having looked — a widened type, an added column — is not a coverage
        judgment at all. Blocking those would refuse work for no gain, which
        is how a tool teaches people to ignore it.
        """
        if verdict.overall is BreakageTier.SAFE:
            return Remediation(
                strategy=FixStrategy.UPDATE_REFERENCES,
                rationale="No breakage to remediate.",
            )

        if not verdict.ceiling.may_assert_safe:
            return self._blocked(verdict)

        if verdict.overall is BreakageTier.TOUCHES:
            return Remediation(
                strategy=FixStrategy.UPDATE_REFERENCES,
                rationale="Change touches downstream assets but breaks nothing.",
            )

        if verdict.change.kind not in _STRATEGIES:
            return Remediation(
                strategy=FixStrategy.UPDATE_REFERENCES,
                rationale=f"No remediation template for {verdict.change.kind.value}.",
                escalation="Change kind requires human judgment.",
            )

        strategy, alternatives = _STRATEGIES[verdict.change.kind]
        edits = self._generate_edits(verdict, strategy)

        return Remediation(
            strategy=strategy,
            alternatives=alternatives,
            rationale=self._rationale(verdict, strategy, len(edits)),
            escalation=self._escalation(verdict),
            edits=edits,
        )

    def _blocked(self, verdict: Verdict) -> Remediation:
        """Coverage too thin to write code. The message must name what would
        unblock it — a refusal that says 'low confidence' is not actionable."""
        blind = "; ".join(s.description for s in verdict.ceiling.report.blind_spots)
        return Remediation(
            strategy=FixStrategy.UPDATE_REFERENCES,
            blocked_reason=(
                f"Coverage {verdict.ceiling.report.score} is insufficient to generate "
                f"code. {blind} Any fix written now would address only the consumers "
                f"Warden can see, while presenting itself as complete."
            ),
        )

    def _generate_edits(self, verdict: Verdict, strategy: FixStrategy) -> list[FileEdit]:
        change = verdict.change
        if change.column is None:
            return []

        edits: list[FileEdit] = []
        for asset in verdict.impacted:
            if asset.tier is not BreakageTier.BREAKS:
                continue
            if asset.entity.platform != REPAIRABLE_PLATFORM:
                continue

            path = self._find_model_file(asset.entity.name)
            if path is None:
                logger.debug("no source file for %s", asset.entity.name)
                continue

            original = path.read_text()
            modified = self._apply(original, change, strategy)
            if original != modified:
                edits.append(FileEdit(path=str(path), original=original, modified=modified))
        return edits

    def _apply(self, sql: str, change: ProposedChange, strategy: FixStrategy) -> str:
        old, new = change.column, change.new_value
        if not old or not new:
            return sql

        if strategy is FixStrategy.UPDATE_REFERENCES:
            # Word-boundary match so `cust_id` doesn't also rewrite `cust_id_hash`.
            return re.sub(rf"\b{re.escape(old)}\b", new, sql)

        if strategy is FixStrategy.COMPATIBILITY_ALIAS:
            return re.sub(rf"\b{re.escape(old)}\b", f"{new} as {old}", sql, count=1)

        return sql

    def _find_model_file(self, model_name: str) -> Path | None:
        matches = list(self._models_dir.rglob(f"{model_name}.sql"))
        return matches[0] if matches else None

    def _rationale(self, verdict: Verdict, strategy: FixStrategy, fixed: int) -> str:
        """Counts what was repaired, not what was found.

        Of eight broken consumers, four may be dbt models here and four
        dashboards elsewhere. Reporting eight as fixed would be false.
        """
        total = len([a for a in verdict.impacted if a.tier is BreakageTier.BREAKS])

        if strategy is FixStrategy.UPDATE_REFERENCES:
            elsewhere = total - fixed
            suffix = (
                f" The remaining {elsewhere} live in other systems and need separate coordination."
                if elsewhere
                else ""
            )
            return (
                f"{fixed} of {total} broken references are dbt models in this "
                f"repository, so updating them directly is a single atomic change "
                f"with no transitional state.{suffix}"
            )

        if strategy is FixStrategy.COMPATIBILITY_ALIAS:
            return (
                "Preserving the old name as an alias keeps existing readers working "
                "while the new one is adopted."
            )

        return (
            "Adding the new form before removing the old lets consumers migrate on "
            "their own schedule."
        )

    def _escalation(self, verdict: Verdict) -> str | None:
        """Escalate when the choice depends on facts no catalog holds.

        Ownership and dependency are in the graph and can be reasoned about.
        Release cadence and change-freeze policy are not.
        """
        platforms = {a.entity.platform for a in verdict.impacted}
        if len(platforms) > 1:
            return (
                f"Impacted assets span {sorted(platforms)}. Whether a coordinated "
                f"breaking change is acceptable this cycle is not recorded in the "
                f"catalog — confirm with the owning teams."
            )
        return None
