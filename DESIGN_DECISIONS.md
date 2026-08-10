# Design decisions

What was decided, what it cost, and what a reviewer would reasonably push back
on.

---

## The premise

Everything here follows from one observation: **DataHub can tell you what it
knows, and cannot tell you what it doesn't.**

That asymmetry is not a defect. A catalog records what has been ingested; there
is no coherent way for it to enumerate systems nobody has connected. But it
means a lineage query returns the same shape — an empty list — for two
completely different states of the world, and the response carries no field to
distinguish them.

Humans absorb this without noticing. A data engineer who queries lineage and
sees nothing thinks *"right, but we've never connected Looker"* — knowledge that
lives in their head and is nowhere in the response. An agent has only the
response.

Every design decision below is an attempt to give the agent the part the human
supplies from memory, or to make it act correctly when it cannot.

---

## Decisions

### Coverage is arithmetic, not judgment

**Decided:** the coverage score is computed from graph properties — reachable
entities against a declared estate, dark platforms, parsed-versus-inferred edge
ratio. No model is consulted.

**Why:** a claim that varies with which LLM you configured is not a claim, it's
a mood. Making coverage deterministic means the central behaviour — this graph
produces a refusal — can be proven in CI with no model, no network, no catalog.
That property is worth more than any sophistication a model could add to the
scoring.

**Cost:** the score is cruder than a model-assisted one might be. It cannot
reason about whether a particular dark platform is *likely* to matter; it only
knows whether it *could*.

**Reviewer's objection:** *the formula is hand-rolled and the threshold is
picked.* Correct on both counts. The formula multiplies three factors so that
any one being poor caps the result — averaging would let a strong factor mask a
fatal one — but the specific shape is judgment. The threshold at 0.6 is judgment
too. Calibrating against real outcomes is the obvious next step and has not been
done.

### The auditor cannot see the conclusion

**Decided:** the Skeptic's function signature accepts a subgraph and registry
records. It never receives an impact assessment, a verdict, or anything derived
from one.

**Why:** an agent asked to critique its own answer produces a rationalisation.
Independence has to be structural — enforced by what the function can accept —
rather than a convention someone might quietly break during a refactor. There is
a test asserting the signature contains no verdict type, so the boundary fails
loudly if crossed.

**Cost:** the Skeptic cannot use the assessment to focus its audit. It examines
the whole retrieved subgraph rather than the parts that turned out to matter.

### The gate is coverage, not severity

**Decided:** finding real breakage does not license acting on it. If
consumer-hosting platforms are dark, generation is blocked regardless of how
confident the impact list looks.

**Why:** this one was wrong for two batches before the offline A/B exposed it.
The original condition blocked only when coverage was thin *and* nothing had
been found — which meant a rename against a dark graph sailed through, because
it had found real breakage. Every unit test passed, because none of them ran the
same change against both profiles.

The reasoning that fixes it: a confident impact list from an incomplete graph is
still a partial list. Fixing four of eight consumers, in a PR that presents
itself as a complete fix, is precisely the failure the gate exists to prevent.
Finding *something* is not the same as finding *everything*, and only the latter
licenses writing code.

**Cost:** more refusals, including some a human would have handled fine.

### Some safety is intrinsic, and the gate must not touch it

**Decided:** changes that are safe by their own semantics — widening a type,
adding a nullable column — bypass the coverage gate entirely.

**Why:** a widened type cannot break a reader whether or not that reader is
visible. Its safety does not rest on having looked, so coverage is irrelevant to
it. Gating it anyway would mean refusing work for no gain, and a tool that
refuses for no gain trains people to stop reading its output.

The distinction that matters: **safety by absence of evidence** requires
coverage; **safety by construction** does not. Conflating the two makes the gate
either too permissive or too noisy, depending on which way you conflate them.

**Cost:** an extra concept, and a list of change kinds that must be maintained
correctly. Get an entry wrong in that list and the gate silently stops applying
to a case that needed it.

### Not every gap blocks

**Decided:** blind spots declare whether they could hide a downstream consumer.
A dark platform holding only raw source tables is reported and does not block. A
dark platform holding dashboards blocks.

**Why:** the first version treated any gap as blocking, which meant Warden held
work for gaps that could not possibly conceal breakage from the change in
question. That is crying wolf with extra steps.

Ambiguous referents block, because if Warden cannot tell which entity the change
targets, everything downstream of that is unfounded. Inferred edges and orphan
entities do not block — they are honest caveats that belong in the report but
should not stop work.

**Cost:** requires knowing which platforms host consumers, which is another
piece of metadata DataHub does not natively carry. It goes in the platform
registry alongside connector status.

### Refusal creates state

**Decided:** a blocked decision is written into DataHub as a `Decision` document
naming the specific missing fact, discoverable by searching a marker.

**Why:** refusal without persistence is a dead end. The engineer reads a message,
closes the tab, and nothing changes. Refusal *with* persistence turns the same
event into a work queue: querying what is currently blocked on missing metadata
gives you gaps that are actively costing someone something, which is a much
better prioritisation signal than a generic completeness score telling you 47
tables lack owners.

**Cost:** documents accumulate. There is no deduplication or expiry, so the same
change evaluated repeatedly writes repeatedly.

### The override exists, and is loud

**Decided:** `--override "reason"` proceeds despite insufficient coverage. The
reason appears in the generated PR body, marked as an override.

**Why:** the team knows things the catalog does not. If they are confident the
dark platform holds no consumers of this change, blocking them is the tool being
an obstacle rather than a safeguard. Warden's job is to prevent an *accidental*
partial fix — nobody merging something they did not realise was incomplete — not
to be the final authority.

Making it loud is the whole design. A silent override is worse than no gate,
because it produces PRs that look identical to properly-gated ones.

**Cost:** the override can be routine rather than deliberate. Nothing enforces
that the stated reason is true.

### Every read and write goes through MCP

**Decided:** no agent makes a direct GraphQL or SDK call. Where the MCP surface
cannot express something, that is reported as a limitation.

**Why:** it keeps a claim honest — Warden works against any DataHub instance
through the interface any other agent would use, with no privileged access. It
also surfaces gaps in that interface rather than routing around them, which is
where the upstream findings came from.

**Cost:** real. There is no lineage mutation tool, so lineage Warden infers from
code the parsers miss cannot be written back as lineage — only recorded on the
description with its provenance stated. That is a lesser thing, and dropping to
the SDK would fix it. We are not doing that.

The ingestion layer does use the SDK, and that is a deliberate exception: it
simulates connectors, and connectors legitimately use the SDK. The constraint
applies to the agents.

### Nothing reaches a PR unexecuted

**Decided:** the Verifier copies the project to a sandbox, applies the proposed
change *and* the generated fixes, runs `dbt build`, and retries on failure
within a bounded budget.

**Why:** a PR containing code that does not compile burns reviewer trust, and
after two of those nobody reads the third — which costs more than the tool ever
saved.

Applying the change alongside the fixes took a second pass to get right. Testing
the fixes alone compiles them against a source that still has the old shape, so
the compiler reports the fix as broken when in fact the change it anticipates
simply has not happened yet. What must be verified is the combination, because
that is what a merge produces.

**Cost:** verification needs a real warehouse, so offline runs skip it and say
so.

### The generated documents are the product

**Decided:** the PR body and the refusal message are designed artifacts with
their own tests, not templated byproducts.

**Why:** a reviewer spends most of their time inside one of those two documents.
If the PR body reads like a competent engineer explaining their reasoning — what
changed, what it touches, what I could and could not see, why this fix, what
would make me more certain — the work has succeeded before anyone runs the code.

The refusal matters more. A refusal that reads as evasive is worse than no tool.
It has to name the missing fact, say what would unblock it, and offer the
override. There are tests asserting each of those, because prose regressions are
invisible otherwise.

**Cost:** wording changes break tests, which is mildly annoying and entirely the
point.

### Impact lists are a floor, not a census

**Decided:** whenever a consumer-hosting platform is dark, the PR body says "at
least N affected" and names the platform.

**Why:** Warden gates *safety* claims on coverage but not *completeness* claims —
it can prove a consumer exists without proving it found them all. Presenting a
partial list in the grammar of a complete one is a quiet lie, and it is the kind
that gets discovered at the worst moment.

**Cost:** the PR body is less punchy. That is an acceptable trade.

### Fix counts distinguish repaired from identified

**Decided:** the rationale counts what was edited, not what was found.

**Why:** of eight broken consumers, four may be dbt models in this repository
and four dashboards elsewhere. "Fixed 8" would be false. The PR table marks each
consumer as fixed here or belonging to a different system, and the escalation
names the platforms that need coordination.

This was also a bug before it was a decision — an early rationale claimed all
broken references were in the repository while the escalation, two lines below,
said they spanned three platforms.

---

## Things a reviewer would push on

**The coverage holes are seeded.** Yes, and we say so in the README. A graph
with perfect coverage gives the Skeptic nothing to detect, so the demo world
contains authored gaps. What is *not* authored is the coverage arithmetic, which
runs identically against any catalog.

**The threshold does less work than it appears to.** True. In practice the
refusal fires on the presence of a consumer-hiding blind spot rather than on the
score crossing 0.6 — both observed profiles score above the threshold. The score
is currently a reported quantity more than a control. Making it discriminating
requires calibration data we do not have.

**Why not always generate, with a caveat?** Because a caveat attached to a PR
gets skimmed, and a PR that does not exist cannot be merged by accident. The
cost asymmetry decides it: a false refusal costs a human five minutes reading a
named work item, and the message tells them exactly what to do about it. A false
generation merges broken code that runs nightly, and spends the reviewer's trust
in every future PR from the same tool.

**Does the agent actually decide anything?** Three places where judgment is real
and checkable in code: the Scoper chooses how far to expand and when marginal
relevance has dropped, rather than using a fixed hop count; the Remediator picks
among valid fix strategies on graph facts and escalates when the choice depends
on facts no catalog holds; the Skeptic is structurally blind to the conclusion it
is auditing.

And three places the model is deliberately kept out: coverage arithmetic, gate
decisions, and the deterministic breakage rules. Findings are proven by
traversal, then explained by a model that cannot invent them.

**What happens when the LLM is unavailable?** The pipeline runs. There is a
deterministic fallback that returns an unknown-shaped answer rather than a
guess, which is consistent with the rest of the design: under uncertainty,
decline rather than assert. `verify.py` runs with the backend disabled
specifically to prove this.

**Is the multi-agent split justified, or is it decoration?** Each agent exists
because a specific failure occurs there that nothing else catches. Merging the
Skeptic into the Assessor would remove the structural independence and let a
single component both conclude and validate. Merging the Verifier into the
Remediator would let generation report its own success. The test suite pins
these boundaries.

---

## Code conventions

Mirroring what DataHub's maintainers document in their contributor guide, so
anything contributed upstream lands cleanly and the code reads as native to the
ecosystem.

- Pydantic models for structured data. Never pass tuples around — they are
  untrackable.
- Files split by duty. One file per agent, `config.py`, `models.py`,
  `mcp_client.py`. Not one module doing everything.
- No docstrings restating the filename. Comments only where the reasoning is not
  obvious from the code.
- Explicit type annotations rather than `Any`.
- Structured reporting over bare logging — warnings and counters that surface to
  the operator.
- Tests run offline. No unit test requires Docker or a live DataHub; a transport
  double stands in for MCP while the logic under test stays real.

---

## Open questions

Tracked rather than resolved.

**Threshold calibration.** The right value should come from measuring how often
Warden is correct at a stated confidence, not from judgment. That needs more runs
than a hackathon affords.

**Weighting dark platforms by consequence.** Losing four dashboards costs 4/27 of
the entity count and 100% of consumer-facing visibility. Entity count is a poor
proxy for what a gap actually hides.

**Resumption trigger.** The held decision is written and discoverable, but
nothing currently watches for the missing metadata to arrive. Polling and the
metadata change stream are both plausible.

**Multi-repository remediation.** Downstream fixes often live in repositories the
change author does not own. Warden is scoped to one repository; the general case
needs a model of ownership and cross-repo PR coordination.

**Deduplicating held decisions.** The same change evaluated repeatedly writes a
new document each time.