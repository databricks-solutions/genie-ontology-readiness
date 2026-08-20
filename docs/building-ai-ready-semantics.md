# Building AI-Ready Business Semantics

A practical, self-guided method for curating **metric views**, **Genie Agents**, and
the **governed tags/domains** that make them discoverable — so an AI agent answers
your business questions accurately and consistently.

Trustworthy talk-to-data rests on a unified, curated semantic foundation. Metric
views and Genie Agents are the tools for building it, but they only pay off with
deliberate curation: the integrity of the numbers, the richness of the metadata,
and the discipline of testing are what separate a demo from something a business
team relies on.

This guide is the human-readable companion to the app's runtime methodology
(`app/server/content/methodology.py`), which grounds the **Plan** tab's generated
roadmaps. Both describe the same five-phase process; edit them together.

---

## Before you start

**Business inputs**

- A catalog of KPIs — names, business descriptions, and technical definitions.
- The business domain and subdomain each KPI belongs to.
- The dimensions each KPI is filtered and grouped by.
- A handful of real questions per KPI with known, ground-truth answers.
- The business and technical owner of each KPI.

**Technical inputs**

- The fact and dimension tables behind each KPI, governed in Unity Catalog.
- Source documentation and the data model (tables and their relationships).
- Permission to create metric views and Genie Agents.
- If you are migrating an existing BI semantic model: the measure definitions and
  the reports that consume them, so nothing is silently dropped.

---

## The process at a glance

The work runs across five phases. Each builds on the last, and the final two form
a loop you stay in as the semantic layer grows.

| Phase | You end with |
|---|---|
| 1. Prepare | A scoped KPI list mapped to its Unity Catalog sources of truth. |
| 2. Build the semantic layer | Governed metric views — one fact source each — with rich metadata. |
| 3. Organize by domain | Genie Agents and metric views structured around the business. |
| 4. Test incrementally | Validated measures, saved example SQL, and question benchmarks. |
| 5. Validate & release | Regression-tested agents, piloted with real users, feedback looping back. |

---

## Phase 1 — Prepare

**Goal: scope the KPIs and pin their source of truth in Unity Catalog.**

- Pick the KPIs to onboard from your KPI catalog.
- For each, identify its **single source** (a table, view, or existing metric view)
  and the dimension tables it needs for filtering and group-by.
- Record the dimensions, the owners, and a few ground-truth questions per KPI —
  you will test against these later.
- Migrating a BI model? Locate the corresponding measure definitions and the
  reports that use them first.

---

## Phase 2 — Build the semantic layer

**Goal: create governed metric views in Unity Catalog, one fact source each, with
metadata an agent can reason over.**

### The one-source rule

Every metric view has **exactly one source** — a fact table, a view, or another
metric view. Never join two fact sources directly inside a metric view. When a KPI
spans multiple fact tables or contains nested logic, build a **base view** first
(join the sources with CTEs), then build the metric view on top of it.

> Always put a metric view on top of a base view before exposing it to Genie —
> even when the base view already returns the right numbers. A base view holds
> unaggregated, row-level data, so the agent still has to infer the aggregation and
> may get it wrong. A metric view **pre-defines** the aggregation, which cuts
> hallucination risk. Once the metric view exists, **remove the raw base view and
> tables from the Genie Agent** and expose only the metric view — keeping both
> creates redundancy and ambiguity.

### Building it up

1. Define the source, then add **LEFT OUTER JOINs** to the dimension tables the KPI
   filters or groups by (for example, a date dimension for MTD/YTD).
2. Add the **dimensions** (the columns available for filter/group-by).
3. Add and validate **one measure at a time**. Confirm each measure matches the
   trusted number before adding the next measure to the same view.
4. Co-locate measures in one metric view **only** when they share the exact same
   single source **and** the exact same dimension tables. Use the `MEASURE()`
   function for composability — a measure can reference other measures or
   dimensions within the same view.

### Metadata is not optional

- **Comment at all three levels** — metric view, dimension, and measure. The agent
  reasons over every level, so keep comments consistent with names and definitions:
  spell out abbreviations, and document expected value formats (for example,
  all-caps codes) directly in the column comment, where the agent will actually see
  them.
- **Synonyms** — add the business terms people actually say, on measures and
  dimensions (up to ten each), so the agent maps language to the right field.
- **Format specification** — declare formats (for example, date formats) in the
  metric view definition so the agent understands them without guessing.

### Nested and multi-fact KPIs

- If a parent KPI and its child KPIs share the same single source, the parent can
  live in a separate metric view that references the view holding the children. If
  their sources differ, build a base view to join them first.
- Even when nested KPIs could be combined into one view, prefer **separate metric
  views per KPI group** — when a large combined view breaks, it is much harder to
  tell which measure caused it.
- You do not need to pre-join every dimension into a base view. If the base view
  exposes a join key, add the remaining dimension joins in the metric view itself.

---

## Phase 3 — Organize by domain, not by report

**Goal: structure Genie Agents and metric views around the business.**

- **Genie Agent = a business domain or subdomain** (for example, "Online
  Marketing"). **Metric view = a KPI group** within it (for example, "Conversion
  Metrics").
- Name metric views by convention: `{subdomain}_{kpi_group}`.
- **Tag** Genie Agents and metric views with their domain/subdomain for
  discoverability and observability. Optionally mirror the structure in Unity
  Catalog — one schema per domain or subdomain.
- Keep each agent focused. A Genie Agent supports up to **30** tables/views/metric
  views, and the tighter the domain, the better the agent performs. As you approach
  the limit, split the domain into more granular subdomain agents.

---

## Phase 4 — Test incrementally

**Goal: onboard one measure at a time, prove it, and capture what works.**

- Add **one measure** to the agent, ask sample questions, and validate the answer
  before adding the next.
- Save each validated query as an **example SQL query** in the agent so it
  reuses it or learns from it. Keep example SQL **simple** — prefer `WHERE` over
  `CASE`; complexity adds to the agent's reasoning load.
- Leave **prompt matching** enabled on columns so the agent maps user language to
  real values and tolerates misspellings. For ambiguous categorical values, add
  exact-match filter instructions.
- Always give the **agent itself a name and a description**. Routing across multiple
  agents (and multi-agent setups) depends on the description to delegate a question
  to the right agent — without one, an orchestrating agent cannot reliably choose.
- Structure agent instructions as: **(1) trigger condition** — when the user asks
  about X; **(2) required action** — then always do Y; **(3) example** — a sample
  question and the expected behavior.

### Benchmarks

Once a measure is validated, add it to a benchmark: **two to four phrasings** of the
same question, each with ground-truth SQL. Users ask the same thing many ways, so
the benchmark should reflect that. Add more phrasings for questions that are complex
or easy to misread. Repeat for each KPI until the whole KPI group is covered.

---

## Phase 5 — Validate & release

**Goal: regression-test every change, then pilot with real users.**

- **Re-run all benchmarks after every change** — a new measure, a new instruction.
  If a previously passing benchmark now fails, the latest addition is the likely
  cause. Use the failure analysis and fix-review tools to diagnose it.
- Regression testing is **per agent** — adding a new agent does not affect existing
  ones. If you connect multiple agents (a supervisor or custom multi-agent system),
  also test the **cross-agent** interactions.
- **Pilot with business users.** Share the curated agent, and have testers use the
  built-in feedback to flag and comment on responses. Keep conversations
  **reviewable by agent managers** so curators can see the feedback — a private
  conversation hides the response from the curator.
- Feed **both** regression failures and user feedback back into Phase 4. This is a
  loop, not a one-time release.

---

## Where to accelerate

Several public Databricks accelerators shorten this work — the app's **Learn** tab
lists them per pillar, and the **Plan** tab names the right one for each gap. In
particular, tools that auto-generate metric-view definitions from existing measure
definitions, and that compose complex base views (CTEs) from natural-language
input, can turn the manual authoring in Phase 2 into a review-and-approve step.
Always review generated output before you apply it.

---

## Quick reference — the non-negotiables

- **One source per metric view.** Base view (CTEs) first for multi-fact/nested KPIs,
  then the metric view on top.
- **Validate one measure at a time** against the trusted number.
- **Comment at view/dimension/measure level**; add **synonyms** and **format specs**.
- **One Genie Agent per domain**, under 30 items, **with a description**.
- **Save validated queries as example SQL** and keep it simple.
- **Instructions:** trigger → action → example.
- **Benchmark** 2–4 phrasings per question with ground-truth SQL; **regression-test
  after every change**.
- **Name** metric views `{subdomain}_{kpi_group}`; **tag** agents and metric views
  with their domain.
- **Certify** canonical assets and **name a steward** per domain.
