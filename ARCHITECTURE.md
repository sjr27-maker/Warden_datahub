# Architecture

How Warden is put together, and why each piece exists.

---

## The organising principle

The decomposition is **by failure mode, not by task step**.

A pipeline decomposed by steps gives you agents that are arbitrary — you could merge any
two of them and lose nothing. A pipeline decomposed by failure mode gives you agents that
each guard against a specific, nameable way the system produces a wrong answer. Every
component below exists because something goes wrong there that nothing else catches.

| Component | The failure it guards against |
|---|---|
| Scoper | Retrieving the wrong context, or silently guessing at an ambiguous referent |
| Skeptic | Treating an empty result as evidence of absence |
| Assessor | Undifferentiated impact lists that read as noise and get ignored |
| Remediator | Generating fixes for assets that were never actually identified |
| Verifier | Opening a PR containing code that was never executed |
| Scribe | Findings that die with the run instead of improving the graph |

---

## Data flow

```
                    ┌──────────────────────────────────────────┐
                    │              DataHub (OSS)                │
                    │  datasets · lineage · glossary · tags ·    │
                    │  structured properties · documents         │
                    └──────────┬──────────────────┬─────────────┘
                               │ reads            │ writes
                    ┌──────────▼──────────────────▼─────────────┐
                    │          mcp-server-datahub (stdio)        │
                    │  search · get_entities · get_lineage ·      │
                    │  grep_documents · update_description ·      │
                    │  add_tags · add_structured_properties ·     │
                    │  save_document                              │
                    └──────────┬──────────────────▲─────────────┘
                               │                  │
┌──────────────────────────────▼──────────────────┴──────────────────────┐
│                              Warden                                     │
│                                                                         │
│  PR event ──► scoper ──► skeptic ──┬── ceiling too low ──► REFUSE ──┐  │
│                  │                  │                                │  │
│                  │                  └── ceiling ok ──► assessor      │  │
│                  │                                        │          │  │
│                  │                                        ▼          │  │
│                  │                                   remediator      │  │
│                  │                                        │          │  │
│                  │                                        ▼          │  │
│                  │                                    verifier ◄──┐  │  │
│                  │                                        │       │  │  │
│                  │                                    fail─┘       │  │  │
│                  │                                        │ pass   │  │  │
│                  ▼                                        ▼        │  │  │
│            (inferred edges)                              PR        │  │  │
│                  │                                        │        │  │  │
│                  └────────────────┬───────────────────────┴────────┘  │  │
│                                   ▼                                   │  │
│                                scribe ◄───────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

Warden spawns `mcp-server-datahub` as a stdio subprocess — the same server Claude Code or
Cursor would talk to. No component makes a direct REST or GraphQL call.

---

## Components

### Scoper — `warden/agent/scoper.py`

Resolves what a diff refers to in the graph, and selects the minimal subgraph that matters.

**Iterative expansion, not one-shot retrieval.** Start at the changed entity, expand a hop,
score marginal relevance, stop when it drops. A one-shot "get everything within 3 hops"
query returns noise, and noise measurably degrades LLM output — an agent handed nine
irrelevant tables and one correct one performs worse than one handed three correct ones.
Precision over recall.

**Referent ambiguity is a first-class state.** If two entities match a reference equally
well — two tables named `customers` on different platforms — the correct output is not a
guess. It's a flagged ambiguity that propagates to the Skeptic and lowers the ceiling.
Most systems have no representation for "I found two candidates and can't choose."

**Emits:** the selected subgraph, a relevance trace (why each node was included), and any
lineage it had to infer to connect things, tagged as inferred.

### Skeptic — `warden/agent/skeptic.py`

The differentiator. Independently audits the retrieved subgraph and caps what everything
downstream may claim.

Three properties that make it work:

**Blind to the conclusion.** The Skeptic sees the subgraph and nothing else — never the
Assessor's output. A self-critiquing agent rationalises its own answer; an independent
auditor doesn't. This is a structural choice, and it's testable: the Skeptic's function
signature takes no impact assessment.

**Emits a ceiling, not an annotation.** The output is a cap enforced in code, not a caveat
appended to prose. A ceiling of 0.4 makes it *impossible* for the Assessor to return a
verdict of `safe` — the type system forbids it, not a prompt instruction.

**Names blind spots, doesn't score them vaguely.** Not "confidence: medium" but "Tableau
has no connector registered; two upstream systems have no lineage edges at all; four of
these eleven edges are inferred rather than parsed."

**Coverage is deterministic.** It's arithmetic over the graph:

```
coverage = f(
    reachable_nodes / expected_nodes_from_platform_registry,
    parsed_edges / total_edges,
    platforms_with_lineage / platforms_present,
    ambiguous_referents,
)
```

No LLM in this path. That matters more than it sounds — it means the central claim
(*given this graph, Warden refuses*) can be hard-gated in CI with zero model dependency.

### Assessor — `warden/agent/assessor.py`

Classifies breakage. Three tiers, because an undifferentiated impact list is noise:

- **Breaks** — column dropped and explicitly selected downstream
- **Degrades** — renamed but consumed via `SELECT *`; type narrowed
- **Touches** — type widened; nullable column added

Its verdict type is constructed with the ceiling as a constraint. Above a coverage
threshold it may return `safe`. Below it, `safe` is not a constructible value — only
`risk` or `unknown`.

### Remediator — `warden/agent/remediator.py`

Generates the actual fixes: dbt model updates, DAG changes, migration scripts. Only for
assets the Assessor identified, and only when the coverage gate has passed.

**Multiple valid strategies.** A column rename admits at least three: update every
downstream reference; add a backward-compatible view or alias; two-phase
deprecate-then-migrate. These are trade-offs, not right-and-wrong.

The rule: **Warden decides when the choice is determined by facts it has; escalates when it
depends on facts it doesn't.** Downstream owner is a different team? That's in the graph —
reason about it. Whether the org tolerates a breaking change this sprint? Not in any
catalog — escalate. Output is the recommended path *with alternatives named*, not one
option silently chosen from three.

### Verifier — `warden/agent/verifier.py`

Runs the generated code. `dbt parse`, `dbt build`, dry runs. Reads errors, feeds them back,
retries within a bounded budget.

This is the second gate, and it's why "works on the first try" is a claim Warden can make
rather than assert. A PR containing code that doesn't compile burns reviewer trust, and
after two of those nobody looks at the third.

The retry transcript is committed to `examples/` — generated, failed, read the error,
corrected, passed. That transcript is worth more to a reviewer than a polished final
artifact alone.

### Scribe — `warden/agent/scribe.py`

Write-back, split by who can act on it.

**Agent-closable — makes the graph more complete:**
- Lineage the Scoper inferred, tagged `inferred, unverified, confidence: 0.x`
- Confirmed impact edges from a completed analysis
- Descriptions filled during investigation

**Human-closable — makes the graph more honest:**
- Coverage score and named dark platforms as structured properties on affected entities
- The held decision: blocked-on fact, timestamp, what would unblock it

The second category is the more novel half. Nothing else in the ecosystem persists "I don't
know" as a first-class, queryable fact. It turns generic completeness metrics ("47 tables
lack owners") into a work queue derived from decisions actually blocked right now.

### Run — `warden/agent/run.py`

Orchestration. Trigger on PR open, wire the pipeline, render output.

Two output shapes:

**PR** — diff, blast-radius reasoning, coverage report, alternatives considered.
**Refusal** — no PR, a named work item, the held decision written to the graph.

Both are first-class. The refusal message is a designed artifact, not an error string.

---

## Design decisions that shape everything

### The PR body is a deliverable, not a byproduct

A judge spends four minutes on this project and most of it reading one generated PR body.
If that reads like a competent senior engineer explaining their reasoning — what changed,
what it touches, what I could and couldn't see, why this fix, what would make me more
certain — the project has succeeded before anyone runs it.

The refusal message matters even more. A refusal that reads as *helpful* rather than
evasive is a genuinely novel artifact.

### MCP-only, deliberately

Every read and write goes through `mcp-server-datahub`. Where the surface can't express
something — column-level lineage traversal being the known case — that's reported as a
limitation and filed upstream, not bypassed with a GraphQL call.

This costs precision. It buys a claim that matters more: Warden works against any DataHub
instance through the same interface any other agent uses, with no privileged access.

### Provenance on every claim

Each fact carries its URN, its aspect, and a trust tier: **human-curated**,
**machine-parsed**, or **agent-inferred**. Three tiers, visible in output. Cheap to
implement, and it's what makes the reasoning auditable rather than magical.

### Deterministic core, LLM at the edges

| Layer | LLM? | Why |
|---|---|---|
| Coverage arithmetic | No | Must be reproducible and CI-gateable |
| Gate decisions | No | Follows deterministically from coverage |
| Breakage classification | Partly | Rules where possible, judgment where not |
| Referent disambiguation | Yes | Reading prose and making a judgment call |
| Remediation | Yes | Writing code |
| Explanation | Yes | Cannot invent a finding; explains a proven one |

The LLM never *establishes* a fact the graph didn't support. It selects, judges, writes,
and explains. Findings are proven by traversal.

---

## Validation strategy

### Deterministic gate (blocking)

Reset → seed a dark graph → run Warden → assert no PR was generated and the blocked-on
aspect exists. No LLM in the path, so no model choice can break it. This runs in CI on
every push and fails the build if Warden generates a PR against a deliberately dark graph.

That's the inverse of the usual CI test: it fails on the *happy* path when the happy path
shouldn't have happened.

### Reported, non-blocking

- **Ablation** — no-context / full-context / scoped-context correctness on the same PRs
- **Calibration** — stated confidence versus actual correctness across runs
- **Near-miss gauntlet** — safe-but-scary changes that should produce no alarm
- **Held-out validation** — coverage assessment against DataHub's public showcase datapack,
  which Warden was never tuned on

Separating these matters. A judge running the demo with a different model still sees the
central claim fire, because the central claim never depended on the model.

---

## Layout

```
warden/
├── world/                     synthetic estate with deliberate coverage holes
│   ├── generate_data.py
│   └── dbt_project/           staging + mart models on DuckDB
├── warden/
│   ├── ingest.py              loads the world into DataHub
│   └── agent/
│       ├── mcp_client.py      async wrapper over mcp-server-datahub (stdio)
│       ├── models.py          Pydantic types: Subgraph, Coverage, Verdict, Decision
│       ├── config.py          env-driven settings
│       ├── scoper.py
│       ├── skeptic.py
│       ├── assessor.py
│       ├── remediator.py
│       ├── verifier.py
│       ├── scribe.py
│       ├── llm.py             backend abstraction + deterministic fallback
│       └── run.py
├── examples/                  committed sample output — PRs, refusals, traces
├── tests/                     offline, no Docker required
├── skills/                    DataHub Skill contribution
└── .github/workflows/         CI including the refusal gate
```

Files split by duty, mirroring the conventions DataHub's own maintainers prefer. Structured
data moves as Pydantic models, never tuples.