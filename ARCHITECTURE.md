# Architecture

How Warden is put together, and why each piece exists.

---

## The organising principle

The decomposition is **by failure mode, not by task step**.

A pipeline split by steps produces components that are arbitrary — you could
merge any two and lose nothing but tidiness. A pipeline split by failure mode
produces components that each guard against a specific, nameable way the system
returns a wrong answer.

| Component | The failure it guards against |
|---|---|
| Scoper | Retrieving irrelevant context, or silently guessing at an ambiguous reference |
| Skeptic | Reading an empty result as evidence of absence |
| Assessor | Undifferentiated impact lists that read as noise and get ignored |
| Remediator | Writing code against a blast radius that was never established |
| Verifier | Opening a PR containing code that was never executed |
| Scribe | Findings that die with the run instead of improving the graph |

The test suite pins these boundaries. Merging the Skeptic into the Assessor
would let one component both conclude and validate; merging the Verifier into
the Remediator would let generation report its own success.

---

## Data flow

```
                    ┌───────────────────────────────────────────┐
                    │              DataHub (OSS)                 │
                    │  datasets · lineage · tags · documents ·    │
                    │  custom properties · platform registry      │
                    └──────────┬──────────────────┬──────────────┘
                               │ reads            │ writes
                    ┌──────────▼──────────────────▼──────────────┐
                    │          mcp-server-datahub (stdio)         │
                    │  search · get_entities · get_lineage ·       │
                    │  add_tags · update_description ·             │
                    │  save_document                               │
                    └──────────┬──────────────────▲──────────────┘
                               │                  │
┌──────────────────────────────▼──────────────────┴─────────────────────┐
│                              Warden                                    │
│                                                                        │
│  diff ──► scoper ──► skeptic ──┬── blocking gap ──► HELD ──────────┐  │
│              │                  │                                   │  │
│              │                  └── clear ──► assessor              │  │
│              │                                   │                  │  │
│              │                        intrinsically safe ──► no-op  │  │
│              │                                   │                  │  │
│              │                                   ▼                  │  │
│              │                              remediator              │  │
│              │                                   │                  │  │
│              │                                   ▼                  │  │
│              │                               verifier ◄──┐          │  │
│              │                                   │       │ retry    │  │
│              │                              fail ─┘       │          │  │
│              │                                   │ pass   │          │  │
│              ▼                                   ▼        │          │  │
│       (inferred edges)                          PR        │          │  │
│              │                                   │        │          │  │
│              └───────────────┬───────────────────┴────────┘          │  │
│                              ▼                                       │  │
│                           scribe ◄────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────┘
```

Warden spawns `mcp-server-datahub` as a stdio subprocess — the same server an
IDE assistant would talk to. No component makes a direct REST or GraphQL call.

A second entry point replaces the MCP client with a snapshot replayer. The agent
code is identical; only the client differs, so an offline run exercises the real
logic rather than an imitation of it.

---

## Components

### Scoper — `warden/agent/scoper.py`

Resolves what a diff refers to, and selects the subgraph that matters.

**Iterative expansion, not one-shot retrieval.** Widen hop by hop and stop when
the marginal yield drops. A fixed "everything within three hops" query returns
noise, and noise measurably degrades output — an agent handed nine irrelevant
tables and one correct one performs worse than one handed three correct ones.

**Direction matters and the default is wrong for this purpose.** `get_lineage`
defaults to `upstream=true`. Blast radius needs downstream, explicitly. Upstream
is pulled to a shallow depth for provenance context, because knowing where a
column comes from informs what changing it means.

**Column scoping where available.** `get_lineage(urn, column=...)` returns
correctly filtered results where `FineGrainedLineage` has been emitted — on a
dataset with three table-level upstreams, scoping to a column fed by one of them
returns exactly one. Where fine-grained lineage was never emitted it returns
nothing, which is correct and easy to mistake for the feature not working.

**Ambiguity is a first-class state.** Two entities matching a reference equally
well produces a flagged ambiguity, not a choice. Most systems have no
representation for "I found two candidates and cannot choose"; that absence is
where silent wrong answers begin.

Emits a `Subgraph` with a relevance trace recording why each node was included.

### Skeptic — `warden/agent/skeptic.py`

Audits the retrieved subgraph and caps what everything downstream may claim.

**Structurally blind to conclusions.** Its signature accepts a subgraph and
registry records. It never receives a verdict or an impact assessment. A test
asserts this, so the boundary fails loudly if a refactor crosses it.

**Coverage is deterministic.** Three factors, multiplied so any one being poor
caps the result:

```
observable    = 1 - (entities on dark platforms / entities in the estate)
parsed_ratio  = parsed edges / total edges in the subgraph
reach         = 1.0 if the traversal found anything useful, else 0.0

score = observable × parsed_ratio × reach
```

Multiplication rather than averaging, because a strong factor should not mask a
fatal one. `reach` is a floor rather than a sliding penalty — a change to a leaf
model legitimately has few consumers, and penalising a small blast radius would
conflate "nothing depends on this" with "I could not see."

The denominator comes from the platform registry, not from what was retrieved.
An agent measuring "how much of what I found did I understand" always reports
complete coverage.

**Blind spots are named and classified.** Not "confidence: medium" but "tableau
has no lineage connector configured; four entities are invisible." Each declares
whether it can hide a consumer:

| Gap | Blocks generation |
|---|---|
| Dark platform hosting consumers | yes |
| Dark platform holding only source tables | no |
| Ambiguous referent | yes |
| Orphan entity with no knowable upstream | no |
| Inferred rather than parsed edges | no |

Ambiguity blocks because if Warden cannot tell which entity the change targets,
everything downstream is unfounded. The others are honest caveats that belong in
the report but should not stop work.

**No LLM in this path.** That is what allows CI to hard-gate the central claim
with no model configured.

### Assessor — `warden/agent/assessor.py`

Classifies breakage into four tiers, because an undifferentiated impact list is
noise:

- **breaks** — column dropped or renamed, and explicitly selected downstream
- **degrades** — type narrowed; values change silently, nothing errors
- **touches** — downstream but unaffected
- **safe** — no consequence

**Rules first, model second.** Where the change kind determines the answer, no
model is consulted. Widening and adding are strictly permissive. Dropping and
renaming break anything that references the column. Only logic changes reach the
model, because only there does the consequence depend on what the logic actually
does.

When the model is consulted, it is given the change and the consumers —
deliberately not any provisional answer of Warden's own. Showing a model your
guess invites agreement rather than judgment.

**Two kinds of safety, gated differently.** Verdicts are constructed through a
factory that enforces the ceiling, so a caller cannot assemble one that bypasses
it:

*Safety by absence of evidence* — nothing was found downstream — requires
coverage. Under a dark graph it downgrades to `touches`, records that it
abstained, and states why.

*Safety by construction* — a widened type cannot break a reader whether or not
that reader is visible — does not. Coverage is irrelevant to it, and gating it
would refuse work for no gain.

### Remediator — `warden/agent/remediator.py`

Generates fixes, or declines to.

**The gate is coverage, not severity.** Finding real breakage does not license
acting on it. A confident impact list from an incomplete graph is still partial,
and fixing what is visible inside a PR that implies completeness is exactly the
failure this exists to prevent.

Intrinsically safe verdicts short-circuit before the gate, since the Assessor
already established that their safety does not rest on coverage.

**Strategies are recommended, not silently chosen.** A rename admits at least
three approaches — update every reference, keep the old name as a
compatibility alias, or a two-phase deprecate-then-migrate. These differ in
trade-offs, not correctness. The output names the recommendation and the
alternatives.

**Escalation when the choice needs facts no catalog holds.** Ownership and
dependency are in the graph and can be reasoned about. Whether the organisation
tolerates a coordinated breaking change this cycle is not, and that gets
escalated rather than assumed.

**Only repairable platforms are edited.** dbt models have source files here.
Dashboards and external transforms are real consumers Warden can identify and
cannot fix, and the counts distinguish the two.

### Verifier — `warden/agent/verifier.py`

Runs the generated code before anything is published.

Copies the project and the warehouse to a scratch directory, rewrites the dbt
profile to an absolute path, applies the **proposed change and its fixes
together**, and runs `dbt build`. Errors feed back within a bounded retry
budget.

Applying the change alongside the fixes matters. Testing the fixes alone
compiles them against a source that still has the old shape, so the compiler
reports the fix as broken when in fact the change it anticipates has not
happened yet. What must be verified is the combination, because that is what a
merge produces.

The working tree is never touched: a failed verification leaves no trace, and a
passing one hands its edits to the PR rather than having already applied them.

### Scribe — `warden/agent/scribe.py`

Write-back, split by who can act on it.

**Agent-closable — the graph becomes more complete:**
lineage inferred from code no parser reads, tagged with its provenance;
confirmed impact edges; descriptions filled while investigating.

**Human-closable — the graph becomes more honest:**
the coverage report, and the held decision itself. A `Decision` document
carrying the named blocking fact, discoverable by marker search. Refusal becomes
a work queue rather than a dead end.

The second is the more distinctive half. Nothing else in the ecosystem persists
"I don't know" as a queryable fact.

**A known limitation:** `mcp-server-datahub` exposes no lineage mutation tool.
Inferred edges cannot be contributed back as lineage — only recorded on the
description with provenance stated. Dropping to the SDK would fix this. We do
not, because the MCP-only constraint is load-bearing for the claim that Warden
works through the same interface as any other agent.

### Run — `warden/agent/run.py`

Orchestration and output rendering.

Two output shapes, both first-class:

**PR** — diff, impact table marking what was fixed here versus elsewhere,
reasoning, alternatives, verification transcript, coverage report.

**Refusal** — no PR, the named missing fact, what would unblock it, and the
override instruction.

---

## The platform registry

DataHub records what it has ingested. It has no native way to record what it is
not connected to — attempting to attach a structured property to a
`dataPlatform` entity fails, because that aspect is not in the entity registry
for that type.

Warden materialises one registry dataset per platform:

```
urn:li:dataset:(urn:li:dataPlatform:warden,registry.tableau,PROD)
  customProperties:
    platform                    = tableau
    lineageConnectorConfigured  = false
    expectedEntityCount         = 4
    hostsConsumers              = true
    registryNote                = "no connector; consumers invisible"
```

The Skeptic reads this back over MCP. It must never read the estate definition
directly, or coverage becomes self-certifying.

`hostsConsumers` is what makes the gate discriminating rather than blanket. A
platform holding only raw source tables is a real gap that cannot conceal
anything downstream of a change.

---

## Cross-cutting decisions

### Provenance on every fact

Each fact carries its URN, its aspect, and a trust tier — human-curated,
machine-parsed, or agent-inferred. Cheap to implement, and it is what makes the
reasoning auditable rather than magical. Warden must never present an edge it
reasoned out as one a connector parsed.

### Deterministic core, model at the edges

| Layer | Model? | Why |
|---|---|---|
| Coverage arithmetic | no | Must be reproducible and CI-gateable |
| Gate decisions | no | Follows deterministically from coverage |
| Breakage classification | partly | Rules where the change kind decides; judgment where it does not |
| Referent disambiguation | yes | Reading prose and making a call |
| Remediation | yes | Writing code |
| Explanation | yes | Cannot invent a finding; explains a proven one |

The model never *establishes* a fact the graph did not support. It selects,
judges, writes, and explains. Findings are proven by traversal.

A deterministic fallback returns an unknown-shaped answer when no backend is
reachable, rather than a guess — consistent with the rest of the design: under
uncertainty, decline rather than assert.

### The generated documents are the product

The PR body and refusal message have their own tests, because prose regressions
are otherwise invisible. Tests assert that impact lists say "at least" when the
graph is dark, that unfixable consumers are marked as belonging to a different
system, that the refusal names the missing fact, and that it offers the
override.

---

## Validation

### Blocking — `verify.py`

Loads both committed snapshots, runs the pipeline, and asserts eleven properties
of the gate. No LLM, no network, no running catalog. CI runs it on every push
with the backend disabled.

This is the inverse of the usual CI test: it fails when a PR is generated
against a graph where one should not have been.

### Reported, non-blocking

**Near-miss gauntlet** — three changes that look dangerous and are not,
alongside two that really break things. Measures false alarms, missed breakage,
and safe changes held pending metadata.

Separating blocking from reported matters. A reviewer running the demo with a
different model still sees the central claim fire, because the central claim
never depended on the model.

---

## Layout

```
warden/
├── world/                     synthetic estate with deliberate coverage holes
│   ├── generate_data.py
│   ├── estate.py              what exists, and what each profile can see
│   ├── scenarios.py           proposed changes, including near-misses
│   ├── transforms/            a pandas step no SQL parser can read
│   └── dbt_project/           staging and mart models on DuckDB
├── warden/
│   ├── ingest.py              loads a coverage profile into DataHub
│   ├── registry.py            the platform registry, read and write
│   ├── snapshot.py            capture and replay for offline runs
│   └── agent/
│       ├── mcp_client.py      async stdio wrapper — the only DataHub access
│       ├── models.py          shared Pydantic types
│       ├── config.py          environment-driven settings
│       ├── diff.py            unified diff → proposed changes
│       ├── scoper.py
│       ├── skeptic.py
│       ├── assessor.py
│       ├── remediator.py
│       ├── verifier.py
│       ├── scribe.py
│       ├── report.py          PR body and refusal rendering
│       ├── llm.py             backend abstraction with deterministic fallback
│       └── run.py
├── evidence/                  gauntlet and measurement harnesses
├── verify.py                  the deterministic gate proof
├── snapshots/                 captured graphs for offline replay
├── examples/                  committed output — PRs, refusals, results
├── skills/                    DataHub Skill contribution
└── tests/                     offline; no Docker required
```

Files split by duty. Structured data moves as Pydantic models, never tuples.