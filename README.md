# Warden

**A code-generation agent that establishes what it knows before it writes.**

Warden reviews a proposed data-pipeline change, walks DataHub's lineage graph to work out
what it breaks, generates the downstream fixes, runs them, and opens a PR — or refuses to
generate anything at all and tells you exactly what metadata is missing.

Category: **Metadata-Aware Code Generation & Development** ·
[Build with DataHub: The Agent Hackathon](https://datahub.devpost.com)

> ⚠️ **Status: in development.** This README is updated at the end of every build batch.
> See [Build status](#build-status) for what is actually working right now. Nothing in this
> document claims a result that isn't reproducible by the commands listed.

---

## The problem

Every code-generation agent built on a catalog makes the same silent assumption: that the
catalog can see everything.

It can't. DataHub knows what it is *connected to*. Configure a Snowflake connector and
Snowflake lineage appears; skip the Tableau connector and Tableau simply does not exist as
far as the graph is concerned. Ingestion keeps metadata **fresh**, but freshness is not
**completeness** — and from inside an agent, those two states are indistinguishable.

Ask "what's downstream of this column?" and get back an empty list. That means one of two
things:

1. Nothing depends on this column. Safe to change.
2. Nothing that depends on this column is visible to me. Unknown.

There is no field in the response that tells you which. An agent that treats an empty
result as case 1 will confidently approve a change that breaks a dashboard it could never
see. **Absence of evidence, rendered as evidence of absence.**

For a read-only agent this is survivable — a wrong SQL answer costs thirty seconds and you
ask again. For an agent writing code into a repository it is not. Merged code runs nightly
until someone notices.

## The idea

Warden treats coverage as a first-class input, not a footnote.

Before it generates anything, an independent **Skeptic** audits the retrieved subgraph and
emits a **confidence ceiling** — a hard cap on what everything downstream is permitted to
assert. High coverage permits "this is safe." Low coverage permits only "this is a risk,"
never "safe." And below a threshold, code generation is blocked entirely.

When Warden refuses, it doesn't shrug. It names the missing fact, files the blocked
decision back into DataHub as a queryable aspect, and **holds the question open**. When
the missing metadata arrives — someone configures that Tableau connector — Warden resumes
and delivers the analysis it couldn't produce before.

**Every tool answers "what breaks?" Warden answers "what breaks, how sure am I, and given
that, should I write the fix or admit I can't?"**

---

## How it works

```
PR opens
   │
   ▼
SCOPER      resolve the referent, pull the minimal relevant subgraph
   │        (precision over recall — irrelevant context degrades output)
   ▼
SKEPTIC     independent coverage audit, blind to any conclusion
   │        → emits confidence ceiling + named blind spots
   │
   ├── ceiling too low ──► REFUSE
   │                       no PR · named work item · decision held open
   ▼
ASSESSOR    classify breakage: breaks / degrades / touches
   │        (claims structurally capped by the ceiling)
   ▼
REMEDIATOR  generate the fixes — dbt models, DAG changes, migrations
   │
   ▼
VERIFIER    actually run them. dbt parse, dbt build, dry runs.
   │        errors feed back · retry · nothing reaches a PR unexecuted
   ▼
SCRIBE      write back: confirmed edges, inferred lineage, coverage gaps
   │
   ▼
PR          diff + blast-radius reasoning + coverage report
```

### Two independent gates

These answer different questions and fail for different reasons. Collapsing them would
hide real failures.

| Gate | Question | On failure |
|---|---|---|
| **Coverage** (before generating) | Can I see enough of the graph to know what breaks? | No PR. Emit a named work item. Hold the decision. |
| **Verification** (after generating) | Does the code I wrote actually parse and run? | Retry with the error. Still failing → no PR. |

An agent can have perfect coverage and still write broken SQL. It can write flawless SQL
against a graph too dark to know it's fixing the right files.

### The deferred decision

Refusal creates state, not a dead end.

```
run 1  │ coverage 0.41 · tableau platform dark · REFUSED
       │ → aspect written: blocked-on = tableau lineage ingestion
       │
       │ (someone configures the Tableau connector)
       │
run 2  │ coverage 0.87 · decision resumed · PR opened
```

Every other reaction in the ecosystem is stateless: event arrives, rule fires, done.
Nothing else holds a question open pending a *named* missing fact, and nothing else makes
"I don't know" a queryable property of the graph.

---

## Use of DataHub

Warden is not a tool that happens to read a catalog. Remove any one of these and a named,
demonstrated behaviour breaks.

### Read

| Tool | What breaks without it |
|---|---|
| `search` | Referent resolution — Warden can't find the entity a diff refers to |
| `get_entities` | Schema and description context; the Scoper has nothing to select from |
| `get_lineage` | The entire blast radius. No downstream, no remediation targets |
| `grep_documents` | Context documents that record prior decisions and conventions |

### Write

| Tool | What it carries |
|---|---|
| `update_description` | Descriptions filled during investigation |
| `add_tags` | `warden-verified`, `warden-blocked`, `coverage-gap` on affected entities |
| `add_structured_properties` | Coverage score, dark platforms, inferred-vs-parsed edge counts, the blocked-on fact |
| `save_document` | The run report — reasoning, evidence chain, and the held decision |

The security of the claim lives in the graph itself. No side database, no second system of
record.

**Constraint we hold deliberately:** every read and write goes through
`mcp-server-datahub`. No direct GraphQL or SDK calls. Where the MCP surface can't express
something, that's reported as a limitation and filed upstream rather than bypassed.

---

## Build status

Updated at the end of every batch. Nothing is marked ✅ until it runs on a clean checkout.

| Batch | Component | Owner | Status |
|---|---|---|---|
| 0 | Docs, scaffolding, conventions | — | 🔨 in progress |
| 1 | `mcp_client` — async MCP wrapper, config, models | A | ⬜ not started |
| 2 | `world` — synthetic data, dbt project, deliberate coverage holes | A | ⬜ not started |
| 3 | `ingest` — load into DataHub, verify graph and holes | A | ⬜ not started |
| 4 | `scoper` + `skeptic` — context selection, coverage math, ceiling | A | ⬜ not started |
| 5 | `assessor` — blast radius classification under the ceiling | B | ⬜ not started |
| 6 | `remediator` + `verifier` — codegen and execution loop | B | ⬜ not started |
| 7 | `scribe` — write-back and the held decision | B | ⬜ not started |
| 8 | `run` — orchestration, PR body rendering, real PR output | B | ⬜ not started |
| 9 | Evidence — ablation, coverage A/B, calibration, near-miss gauntlet, CI | A+B | ⬜ not started |
| 10 | Submission — README, video, examples, upstream contribution | A+B | ⬜ not started |

See [BUILD_PLAN.md](BUILD_PLAN.md) for what each batch contains and its checkpoint.

---

## Evidence plan

Assertions are cheap. These four turn the thesis into measurements. None are complete yet;
this section becomes results as batches land.

**Ablation — does context *selection* actually matter?**
The same PR through three configurations: no catalog context, everything dumped in, and
Warden's scoped selection. If full-context underperforms scoped, that's a result nobody
has published, and it's the empirical backbone of the whole design.

**Coverage A/B — does the ceiling actually gate?**
The same PR against two graphs, one well-covered and one with holes. Confident PR in the
first case, refusal plus work item in the second. Same input, different behaviour, because
the agent knows different amounts.

**Calibration — is stated confidence honest?**
At a reported confidence of 0.8, is Warden right 80% of the time? Almost no agent project
measures this. Even a rough table across twenty runs is more rigorous than the field.

**Near-miss gauntlet — does it cry wolf?**
Changes that *look* dangerous but are safe: adding a nullable column, widening a type,
renaming something with genuinely zero consumers. A tool that alarms on these gets ignored,
and a tool that gets ignored is worse than no tool.

### Deterministic proof

The coverage computation is graph arithmetic — reachable nodes, dark platforms, inferred
versus parsed edges. **No LLM in that path.** The refusal follows deterministically once
the number crosses a threshold.

That means CI can hard-gate on the central claim without any model dependency: *given this
graph, Warden refuses.* LLM-driven parts — remediation quality, reasoning prose — are
reported but never gate. A judge running this with Ollama, Gemini, or no model at all still
sees the thesis fire.

---

## Quickstart

Requires Docker (8GB+), Python 3.11. On Windows, use WSL2 — see [SETUP.md](SETUP.md) for
the full walkthrough including the gotchas that cost us time.

```bash
git clone https://github.com/<org>/Warden_datahub.git warden && cd warden
make setup          # venv + pinned deps
make datahub-up     # DataHub OSS with auth enabled
make demo           # build the world, ingest, run Warden
```

Open <http://localhost:9002> (`datahub` / `datahub`).

> Commands above are the target interface. See [Build status](#build-status) for which are
> live today.

---

## Honest limitations

Written before the code, and kept accurate as it lands.

**Column-level lineage is not traversable via MCP.** DataHub stores fine-grained lineage
correctly, but no current MCP tool surfaces it —
[documented upstream](https://github.com/acryldata/mcp-server-datahub/pull/141) by another
entrant. Blast radius is therefore dataset-level with name-based column matching, not true
column-level traversal. We hold the MCP-only constraint rather than dropping to GraphQL to
work around it.

**The coverage holes are seeded.** Warden's demo world contains deliberate dark platforms
and unknown upstreams, because a graph with perfect coverage gives the Skeptic nothing to
detect. That part is authored and we say so. What is not authored is the coverage
arithmetic, which runs identically against any real catalog.

**Search is eventually consistent after a write.** A freshly written entity may not appear
immediately. Warden retries rather than assuming failure.

**Warden cannot close most gaps it finds.** Gaps split into agent-closable (lineage it
inferred while reasoning, missing descriptions) and human-closable (an unconfigured
connector, an entire dark platform). It writes back the first kind. For the second it names
the work precisely and holds — claiming to fix it would be overreach.

---

## Prior art, and what's ours

Full treatment in [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md). In short: impact analysis is
a native DataHub primitive; `datahub-dbt-impact-action` already does pre-merge impact for
dbt and reports it as a PR comment; the Analytics Agent already writes back and surfaces
metadata gaps. Other hackathon entrants have built detection with remediation diffs, and
abstention on ambiguous evidence.

What we could find no precedent for:

1. **Coverage as a veto on generation** — abstaining because the graph is too dark to see
   consequences, as distinct from abstaining because evidence is ambiguous.
2. **Execution before PR** — nothing else runs the code it generates before opening one.
3. **The held decision** — a blocked question that persists as graph state and resumes when
   the named missing fact arrives.

---

## Documentation

- [SETUP.md](SETUP.md) — local development environment, with known gotchas
- [ARCHITECTURE.md](ARCHITECTURE.md) — agent decomposition, data flow, why each piece exists
- [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md) — prior art, trade-offs, the questions a
  reviewer would ask
- [BUILD_PLAN.md](BUILD_PLAN.md) — batched delivery plan and checkpoints

## License

[Apache-2.0](LICENSE).