# Warden

A code generation agent that establishes what it knows before it writes.

Warden reviews a proposed change to a data pipeline, walks DataHub's lineage
graph to work out what it breaks, generates the downstream fixes, runs them,
and opens a pull request — or declines to generate anything and reports exactly
which metadata is missing.

Built for the *Metadata-Aware Code Generation & Development* track.

---

## Try it without installing anything

Two committed graph snapshots, one command, no Docker:

```bash
git clone https://github.com/sjr27-maker/Warden_datahub.git warden && cd warden
make setup
make demo-offline
```

```
Warden — offline demo
Change: column_renamed on stg_orders.cust_id
Identical change, identical code. Two graphs.

  covered   coverage 1.0     PR — 4 file(s) to fix
            written to examples/covered-stg_orders-pr.md

  dark      coverage 0.679   HELD — blocked on lineage ingestion for: python, tableau
            └─ tableau has no lineage connector configured; 4 entities are
               invisible, and any consumers among them cannot be detected
            └─ python has no lineage connector configured; 1 entities are
               invisible, and any consumers among them cannot be detected
            written to examples/dark-stg_orders-refusal.md

The graph changed. The code did not.
```

The two output files are the deliverables. Read
[the PR](examples/covered-stg_orders-pr.md) and
[the refusal](examples/dark-stg_orders-refusal.md) 
For the full pipeline against a live catalog, including verification and
write-back, see [Running against DataHub](#running-against-datahub).

---

## The problem

Ask DataHub what is downstream of a column. Get back an empty list.

That means one of two things:

1. Nothing depends on this column. Safe to change.
2. Nothing that depends on this column is visible to me. Unknown.

There is no field in the response that distinguishes them.

DataHub knows what it has ingested. Configure a Snowflake connector and
Snowflake lineage appears; skip the Tableau connector and Tableau does not
exist as far as the graph is concerned. Ingestion keeps metadata **fresh**.
Freshness is not **completeness**, and from inside an agent the two states look
identical.

A human reading a lineage graph handles the ambiguity by knowing their own
estate. An agent writing code into a repository has no such knowledge, and the
default reading — empty means safe — produces a confident fix for the four
consumers it can see while three others break silently after merge.

For a read-only agent this is survivable: a wrong SQL answer costs thirty
seconds and you ask again. Code merged to main runs nightly until someone
notices.

## The approach

Warden treats coverage as an input, not a footnote.

Before generating anything, an independent auditor examines the retrieved
subgraph and computes a **coverage score** — deterministic arithmetic over
reachable entities, dark platforms, and parsed-versus-inferred edges. That score
caps what everything downstream may assert. High coverage permits "this is
safe." Low coverage permits only "this is a risk." Below the threshold, code
generation is blocked entirely.

When Warden declines, it names the missing fact, writes the blocked decision
into DataHub as a queryable document, and holds the question open. When the
metadata arrives, the analysis resumes.

Two kinds of claim, gated differently:

**Safety claims are gated.** Concluding that nothing breaks asserts a negative,
and that assertion is only available when the graph was complete enough to see
consequences.

**Completeness claims are stated as a floor.** Warden can prove a consumer
exists without proving it found them all. Impact lists say "at least N" and name
the dark platform whenever one exists.

## Pipeline

```
PR opens
   │
   ▼
SCOPER      resolve the referent, pull the minimal relevant subgraph
   │        ambiguous references are flagged, never guessed
   ▼
SKEPTIC     independent coverage audit, blind to any conclusion
   │        → coverage score + named blind spots
   │
   ├── consumer-hosting platform dark ──► HELD
   │                                      no PR · named work item · decision persisted
   ▼
ASSESSOR    classify breakage: breaks / degrades / touches / safe
   │        claims structurally capped by the ceiling
   ▼
REMEDIATOR  generate fixes — dbt models, migration code
   │
   ▼
VERIFIER    run them. dbt parse, dbt build. errors feed back, retry
   │        nothing reaches a PR unexecuted
   ▼
SCRIBE      write back: findings, coverage gaps, the decision itself
   │
   ▼
PR          diff + reasoning + coverage report
```

The decomposition is by failure mode, not by task step. Each agent exists
because something goes wrong there that nothing else catches — retrieving
irrelevant context, reading an empty result as evidence of absence, producing an
undifferentiated impact list that gets ignored as noise, opening a PR containing
code that does not compile.

Details in [ARCHITECTURE.md](ARCHITECTURE.md).

### Two independent gates

They answer different questions and fail for different reasons.

| Gate | Question | On failure |
|---|---|---|
| **Coverage**, before generating | Can I see enough of the graph to know what breaks? | No PR. Named work item. Decision held. |
| **Verification**, after generating | Does the code I wrote actually parse and run? | Retry with the error. Still failing → no PR. |

An agent can have perfect coverage and write broken SQL. It can write flawless
SQL against a graph too dark to know it is fixing the right files.

### Three exceptions the gate makes

A gate that refuses everything teaches people to ignore it. Warden does not
block:

**Changes safe by their own semantics.** Widening a type or adding a nullable
column cannot break a reader whether or not that reader is visible. Their safety
does not rest on having looked, so coverage is irrelevant to them.

**Gaps that cannot hide consumers.** A platform with no lineage connector that
holds only raw source tables is a real gap worth reporting, but it cannot
conceal anything downstream of the change. Only consumer-hosting platforms
block.

**Teams that know their own estate.** `--override "reason"` proceeds anyway, and
the reason appears in the generated PR body, marked. Warden's job is to prevent
an accidental partial fix, not to be the final authority.

---

## Evidence

### Deterministic proof

```bash
python verify.py
```

Coverage is arithmetic. No LLM sits in that path, so the central claim can be
proven without a model, a network, or a running catalog.

```
Covered graph:
  [PASS] generation proceeds when coverage is complete
  [PASS] fixes are generated
  [PASS] safety claims are permitted

Dark graph:
  [PASS] generation is blocked when consumers are invisible
  [PASS] no code is written while blocked
  [PASS] the blocking fact is named, not just scored
  [PASS] the named fact identifies the dark platform

Intrinsic safety:
  [PASS] a change safe by its own semantics is not held by a dark graph

Override:
  [PASS] an explicit override proceeds
  [PASS] the override is recorded, not silent
  [PASS] the override is visible in the generated document

All checks passed. The gate is a function of the graph, not of the model.
```

CI runs this on every push with the LLM backend disabled. A reviewer running
Warden with a different model gets the same result.

### Near-miss gauntlet

```bash
python -m evidence.gauntlet
```

Three changes that look dangerous and are not, alongside two that really break
things. A detector that fires on both is noise; one that fires on neither is
decoration.

| | covered | dark |
|---|---|---|
| False alarms on safe changes | 0 / 3 | 0 / 3 |
| Missed real breakage | 0 / 2 | 0 / 2 |
| Safe changes held pending metadata | 1 / 3 | 1 / 3 |

The held case is a dropped column, and it stays held. Unlike widening a type —
safe by construction regardless of who is watching — a drop is safe only if
nothing downstream selects that column, which is a claim about having looked.
When the subgraph is thin, Warden declines to make it. That is the design
working rather than a number to tune away.

### Verification, live

Against a running DataHub on the covered profile:

```
--- remediation ---
strategy    : update_references
alternatives: ['compatibility_alias', 'two_phase_deprecate']
edits       : 4
verified    : True
   attempt 1: dbt build exit=0
```

The generated changes were executed before the PR was written.

---

## Use of DataHub

Every read and write goes through `mcp-server-datahub`. No direct GraphQL, no
SDK calls from any agent. Where the MCP surface cannot express something, that
is reported as a limitation rather than worked around.

| Tool | What breaks without it |
|---|---|
| `search` | Referent resolution — Warden cannot find the entity a diff refers to |
| `get_entities` | The platform registry, and therefore the coverage denominator |
| `get_lineage` | The entire blast radius |
| `add_tags` | Affected entities are not marked in the UI |
| `update_description` | Inferred lineage has nowhere to be recorded |
| `save_document` | Held decisions do not persist, and refusal becomes a dead end |

### The platform registry

DataHub has no native concept of what it is *not* connected to. Recording
connector status as a structured property on a `dataPlatform` entity fails —
that aspect is not in the entity registry for that type.

Warden materialises one registry dataset per platform, carrying connector status
and expected entity count as custom properties. The Skeptic reads this back over
MCP and uses it as the coverage denominator.

This matters more than it sounds. An agent that measures "how much of what I
retrieved did I understand" always reports complete coverage. The gap has to be
measurable from outside the retrieval to be visible at all.

### Write-back

Two categories, deliberately not conflated.

**Agent-closable** gaps make the graph more *complete*: lineage inferred from
code no parser reads, tagged `inferred, unverified`; confirmed impact edges;
descriptions filled while investigating.

**Human-closable** gaps make the graph more *honest*: an unconfigured connector
needs credentials and someone deciding it is worth ingesting. Warden records
what is missing and holds the decision, as a DataHub `Decision` document
carrying the named blocking fact. Searching `warden:held-decision` returns the
current queue of work blocked on missing metadata — a better prioritisation
signal than a completeness score, because every entry is costing someone
something now.

---

## Running against DataHub

Requires Docker with 8GB+ and Python 3.11. On Windows use WSL2; the gotchas that
cost us time are in [SETUP.md](SETUP.md).

```bash
make setup
make datahub-up          # DataHub OSS with metadata service auth enabled
# generate an access token at localhost:9002, put it in .env
make build-world         # synthetic estate, dbt models on DuckDB
make ingest-covered      # or ingest-dark
make run-live
```

The synthetic world contains four deliberate coverage holes:

| Hole | What it demonstrates |
|---|---|
| BI platform with no connector | The empty-result ambiguity, with real consumers hidden behind it |
| A mart fed by a hand-maintained spreadsheet | Origins no connector can ever reach |
| A pandas transform producing an edge | Lineage that is inferable but never parsed |
| The same model name on two platforms | Referent ambiguity that must be flagged, not guessed |

These are authored, and we say so. What is not authored is the coverage
arithmetic, which computes identically against any catalog.

---

## Limitations

Written before the code and kept accurate.

**Scope.** One repository, dbt only, four recognised change kinds. Consumers on
other platforms are identified but cannot be repaired here — the PR body
distinguishes what was fixed from what was merely found.

**The threshold is chosen, not derived.** 0.6, by judgment. Calibrating it
against real outcomes is the obvious next step and has not been done.

**Column-level lineage is available but conditional.** `get_lineage(urn,
column=...)` returns correctly scoped results where `FineGrainedLineage` has been
emitted, and returns nothing where it has not — correct behaviour, but easy to
mistake for a missing feature.

**Warden cannot close most gaps it finds.** Ingesting a connector is an
organisational action. Claiming otherwise would be overreach.

**Search is eventually consistent after a write.** Warden retries rather than
assuming failure.


---

## Prior art

Lineage impact analysis is a native DataHub capability, available through the UI
and GraphQL — graph traversal is something Warden consumes, not something it
contributes. A GitHub Action already exists that posts dbt impact as a PR
comment; DataHub's own documentation notes that other use cases require custom
integration. Reference agents already read the catalog and write results back,
so write-back alone is not novel.

What we could find no precedent for:

**Coverage as a veto on generation.** Declining to write code because the graph
is too dark to see consequences, as distinct from declining because the change
itself is ambiguous.

**Execution before the PR.** Generated code is run — `dbt parse`, `dbt build` —
and errors feed back before anything is opened.

**The held decision.** A blocked question persisted as graph state, naming the
specific missing fact, resumable when that fact arrives. Existing reactions are
stateless: event arrives, rule fires, done.

---

## Contributions back

[`skills/coverage-aware-impact/`](skills/coverage-aware-impact/SKILL.md) — a
DataHub Skill for assessing blast radius while reporting what the catalog could
not see.

[`docs/mcp-findings.md`](docs/mcp-findings.md) — seven verified findings against
`mcp-server-datahub`, including two that constrain any agent aiming to make the
graph more complete: structured properties are unsupported on `dataPlatform`
entities, and there is no lineage mutation tool.

---

## Documentation

- [SETUP.md](SETUP.md) — local environment, with the gotchas
- [ARCHITECTURE.md](ARCHITECTURE.md) — agent decomposition and why each exists
- [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md) — trade-offs and the questions a
  reviewer would ask
- [examples/](examples/) — generated PRs, refusals, evidence results

## License

[Apache-2.0](LICENSE)