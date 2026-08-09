"""What the estate actually contains, and what each ingestion profile can see.

The data is identical in every profile. What varies is catalog visibility —
that separation is the whole point: nothing about the world changes between
the dark and covered runs, only what DataHub knows about it.
"""

from pydantic import BaseModel


class PlatformSpec(BaseModel):
    name: str
    entity_count: int
    has_lineage_connector: bool
    note: str


class CoverageProfile(BaseModel):
    """An ingestion configuration. `platforms` are ingested with lineage;
    `platforms_present_without_lineage` are known to exist but produce no edges."""

    name: str
    platforms: list[str]
    platforms_present_without_lineage: list[str]
    emit_inferred_edges: bool


# Every platform in the estate, regardless of what any profile ingests.
ESTATE: list[PlatformSpec] = [
    PlatformSpec(
        name="dbt",
        entity_count=16,
        has_lineage_connector=True,
        note="Staging and mart models. Fully parseable.",
    ),
    PlatformSpec(
        name="duckdb",
        entity_count=6,
        has_lineage_connector=True,
        note="Raw landed tables.",
    ),
    PlatformSpec(
        name="tableau",
        entity_count=4,
        has_lineage_connector=False,
        note=(
            "HOLE #1 — the dark platform. Four dashboards consume marts, but no "
            "connector is configured, so DataHub has no record they exist. This is "
            "the case where an empty downstream list means 'invisible', not 'safe'."
        ),
    ),
    PlatformSpec(
        name="python",
        entity_count=1,
        has_lineage_connector=False,
        note=(
            "HOLE #3 — customer_segments is produced by a pandas transform. No SQL "
            "parser can extract this edge; it is inferable but never parsed."
        ),
    ),
]

# The mart with no knowable upstream — HOLE #2.
UNKNOWN_UPSTREAM_URNS = ["mart_finance_summary"]

# HOLE #4 — the same logical name on two platforms, for referent disambiguation.
AMBIGUOUS_NAMES = ["customer_ltv"]

DARK = CoverageProfile(
    name="dark",
    platforms=["dbt", "duckdb"],
    platforms_present_without_lineage=["tableau", "python"],
    emit_inferred_edges=False,
)

COVERED = CoverageProfile(
    name="covered",
    platforms=["dbt", "duckdb", "tableau", "python"],
    platforms_present_without_lineage=[],
    emit_inferred_edges=True,
)

PROFILES = {p.name: p for p in (DARK, COVERED)}


def expected_entity_count(profile: CoverageProfile) -> int:
    """Total entities the estate contains — the denominator for coverage.

    Deliberately counts platforms the profile cannot see. That asymmetry is
    what lets the Skeptic report 'I am missing things' rather than reporting
    100% coverage of what it happened to retrieve.
    """
    return sum(p.entity_count for p in ESTATE)


def platforms_lacking_lineage(profile: CoverageProfile) -> list[PlatformSpec]:
    return [p for p in ESTATE if p.name in profile.platforms_present_without_lineage]