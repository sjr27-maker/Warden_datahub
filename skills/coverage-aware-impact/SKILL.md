---
name: coverage-aware-impact
description: Assess the blast radius of a proposed schema change, and report what the catalog could not see. Use before generating code that depends on knowing every downstream consumer.
---

# Coverage-aware impact analysis

DataHub tells you what it has ingested. It has no way to tell you what it is
missing — so an empty downstream result means either "nothing depends on this"
or "nothing that depends on this is visible to me", with no field to
distinguish them.

For a human reading a lineage graph that ambiguity is manageable. For an agent
about to write code, it is not: an empty result read as evidence of absence
produces a confident fix for four consumers while three others break silently.

## When to use this

Before acting on lineage results in any way that assumes completeness —
generating migration code, approving a schema change, declaring a change safe.

## Procedure

1. **Resolve the referent.** `search` for the changed entity. If more than one
   candidate matches equally well, stop and report the ambiguity rather than
   picking one.

2. **Traverse downstream.** `get_lineage(urn, upstream=false, max_hops=N)`.
   Note that `upstream` defaults to `true`; blast radius needs it explicitly
   set to false. Pass `column=` to scope to a single column where
   FineGrainedLineage has been emitted.

3. **Establish what platforms exist.** Compare the platforms present in the
   results against the platforms the estate actually contains. DataHub does
   not surface this — you need an external inventory, or a convention such as
   recording connector status as custom properties on a per-platform dataset.

4. **Classify each gap by whether it can hide a consumer.** A platform with no
   lineage connector that hosts dashboards can conceal breakage. One that
   holds only raw source tables cannot. Treating every gap as blocking
   produces refusals that teach people to ignore the tool.

5. **Report the impact list as a floor, not a census**, whenever a
   consumer-hosting platform is dark. "At least N affected; platform X has no
   connector" is honest. "N affected" is not.

## What this cannot establish

- Whether an unconfigured connector hides consumers, only that it might
- Lineage through transforms no parser reads — stored procedures, pandas,
  dynamic SQL
- Column-level paths where FineGrainedLineage was never emitted

## Reference implementation

[Warden](https://github.com/sjr27-maker/Warden_datahub) implements this as a
deterministic coverage score gating code generation, with `verify.py` proving
the gate holds with no LLM in the path.