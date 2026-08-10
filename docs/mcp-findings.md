# Findings against mcp-server-datahub

Encountered while building an agent that reads and writes DataHub exclusively
through MCP. Verified on DataHub 1.7.0 with `mcp-server-datahub@latest`,
August 2026.

## 1. `structuredProperties` is not a supported aspect on `dataPlatform`

`add_structured_properties` against a `urn:li:dataPlatform:*` URN fails with:

    Unknown aspect structuredProperties for entity dataPlatform

This matters for agents reasoning about coverage: there is no entity type
designed to carry metadata *about a platform*, so "this platform exists but has
no connector configured" cannot be recorded natively. The workaround is a
materialised registry dataset per platform carrying custom properties.

## 2. There is no lineage mutation tool

An agent can write tags, terms, owners, descriptions, structured properties and
documents — but cannot contribute a lineage edge. Lineage inferred from code
the parsers miss (stored procedures, pandas transforms) therefore cannot be
written back as lineage, only recorded as prose.

This is the single largest limitation for agents that aim to make the graph
more complete rather than only more annotated.

## 3. `get_lineage` defaults to `upstream=true`

Undocumented in the tool description. Blast-radius analysis needs downstream
and will silently return upstream results if the parameter is omitted.

## 4. `get_lineage(urn, column=...)` works

An earlier report ([#141](https://github.com/acryldata/mcp-server-datahub/pull/141))
noted column-level lineage as stored but not traversable via MCP. On the
version above it is: the query switches to `queryType: column-level-lineage`
and returns a correctly filtered subset. On a dataset with three table-level
upstreams, scoping to a column fed by one of them returned exactly one.

It returns zero when no `FineGrainedLineage` has been emitted, which is correct
but easy to mistake for the feature not working.

## 5. Search parameter names differ from intuition

`search` takes `query`, `filter` (a string), `num_results`, `sort_by` — not
`count` or `entity_types`.

## 6. Tool responses are not uniformly JSON

Some tools return JSON, others formatted text. Clients need a fallback rather
than assuming `json.loads` will succeed.

## 7. `get_entities` returns a bare list

Not a dict with an `entities` key, as some documentation examples suggest.