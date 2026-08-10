"""Extract proposed changes from a unified diff.

Deliberately conservative. A change kind that cannot be determined from the
diff alone is reported as LOGIC_CHANGED rather than guessed at — the Assessor
has a path for genuine uncertainty, and feeding it a confident wrong label
would defeat that.
"""

import re
from pathlib import Path

from warden.agent.models import ChangeKind, ProposedChange

_MODEL_RE = re.compile(r"[+-]{3} [ab]/.*/(?P<model>[\w]+)\.sql")
_REMOVED_RE = re.compile(r"^-\s*(?P<body>[^-].*)$", re.MULTILINE)
_ADDED_RE = re.compile(r"^\+\s*(?P<body>[^+].*)$", re.MULTILINE)
_ALIAS_RE = re.compile(r"\b(?P<old>\w+)\s+as\s+(?P<new>\w+)\b", re.IGNORECASE)
_CAST_RE = re.compile(r"cast\s*\(\s*(?P<col>\w+)\s+as\s+(?P<type>[\w()\d,]+)\s*\)", re.IGNORECASE)

_NARROWING = {"decimal", "numeric", "int", "smallint", "varchar"}
_WIDENING = {"bigint", "double", "text", "hugeint"}


def parse_diff(diff: str) -> list[ProposedChange]:
    model = _model_name(diff)
    if model is None:
        return []

    removed = {m.group("body").strip() for m in _REMOVED_RE.finditer(diff)}
    added = {m.group("body").strip() for m in _ADDED_RE.finditer(diff)}

    changes: list[ProposedChange] = []
    changes.extend(_renames(model, removed, added))
    changes.extend(_type_changes(model, removed, added))
    changes.extend(_column_set_changes(model, removed, added))

    if not changes and (removed or added):
        changes.append(ProposedChange(model=model, kind=ChangeKind.LOGIC_CHANGED))

    return changes


def parse_diff_file(path: Path) -> list[ProposedChange]:
    return parse_diff(path.read_text())


def _model_name(diff: str) -> str | None:
    match = _MODEL_RE.search(diff)
    return match.group("model") if match else None


def _renames(model: str, removed: set[str], added: set[str]) -> list[ProposedChange]:
    """`cust_id` becoming `cust_id as customer_id` is a rename — the source
    column persists but consumers must use the new name."""
    changes = []
    for line in added:
        alias = _ALIAS_RE.search(line)
        if not alias:
            continue
        old, new = alias.group("old"), alias.group("new")
        if any(old in r and "as" not in r.lower() for r in removed):
            changes.append(
                ProposedChange(
                    model=model,
                    kind=ChangeKind.COLUMN_RENAMED,
                    column=old,
                    old_value=old,
                    new_value=new,
                )
            )
    return changes


def _type_changes(model: str, removed: set[str], added: set[str]) -> list[ProposedChange]:
    changes = []
    for line in added:
        cast = _CAST_RE.search(line)
        if not cast:
            continue
        column, new_type = cast.group("col"), cast.group("type").lower()
        base = new_type.split("(")[0]
        kind = (
            ChangeKind.TYPE_NARROWED
            if base in _NARROWING
            else ChangeKind.TYPE_WIDENED
            if base in _WIDENING
            else ChangeKind.LOGIC_CHANGED
        )
        changes.append(
            ProposedChange(model=model, kind=kind, column=column, new_value=new_type)
        )
    return changes


def _column_set_changes(model: str, removed: set[str], added: set[str]) -> list[ProposedChange]:
    """Bare column references appearing or disappearing from a select list."""
    changes = []
    removed_cols = {_bare_column(line) for line in removed} - {None}
    added_cols = {_bare_column(line) for line in added} - {None}

    for column in removed_cols - added_cols:
        changes.append(
            ProposedChange(model=model, kind=ChangeKind.COLUMN_DROPPED, column=column)
        )
    for column in added_cols - removed_cols:
        changes.append(
            ProposedChange(model=model, kind=ChangeKind.COLUMN_ADDED, column=column)
        )
    return changes


def _bare_column(line: str) -> str | None:
    candidate = line.rstrip(",").strip()
    return candidate if re.fullmatch(r"\w+", candidate) else None