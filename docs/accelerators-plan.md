# Plan: Pillar Accelerators — self-serve scripts that raise the readiness score

## Goal

Today the app **assesses** readiness and **explains** each pillar (Learn) and
**plans** an engagement (Plan). It tells a customer *what* to fix and *why*, but
not *with what*. This plan adds a third content type — **Accelerators**: curated,
runnable assets (notebooks, SQL, DABs, dashboards, repos) a customer can
self-implement to move a specific pillar up the maturity scale. Each accelerator
is tied to the assessment signal(s) it improves, surfaced in **Learn**, woven
into the generated **Plan**, and designed to grow over time.

The reference accelerator is the **AI-Comments RAG notebook** (`RAG for column
descriptions`) for the Metadata pillar — it fills a real gap: AI Comments has no
API endpoint yet, so customers can't programmatically bulk-generate and apply
glossary-grounded comments. The notebook does exactly that.

---

## 1. Core concept: the Accelerator

An accelerator is a data-model record + a packaged artifact.

```
Accelerator
  key                e.g. "metadata-ai-comments"
  title              "AI-generated, glossary-grounded column comments"
  summary            one line
  capability         which capability/pillar it improves (FK to pillars)
  type               notebook | sql | dab | dashboard | repo
  effort             "~1 hour", "half a day"
  prerequisites      ["Vector Search enabled", "a model serving / FMAPI endpoint", "SELECT on target catalog"]
  what_it_does       2-3 sentences
  improves_signals   ["metadata.comment_coverage_columns", ...]  # ties to probes.py signal keys
  target_level       the maturity level it helps reach (0-4)
  steps              ["Import the notebook", "Set scope params", "Run in dry-run", "Review, then apply"]
  artifact_path      "accelerators/metadata-ai-comments/"   # bundled with the app
  source             { title, url }   # repo path or docs
  valid_as_of        "2026-06"        # freshness; supersedes/superseded_by for lifecycle
  review_mode        true             # generates a review table before applying (matches our "accept-with-review" best practice)
```

Lives in `app/server/content/accelerators.py` as a data registry (mirrors
`content/library.py`). Exposed via the existing `/api/content` (attached to each
capability) plus a new `GET /api/accelerators` for the full list. Actual artifacts
(notebook source, README, optional `databricks.yml`) ship under
`app/accelerators/<key>/` so they can be downloaded or deployed.

---

## 2. Worked example — AI-Comments RAG accelerator (Metadata Richness)

**Source notebook today:** `RAG for column descriptions` — hard-coded to one
table (`users.sascha_vetter.items`), creates demo data, builds a VS index over
three inline `.txt` docs, generates comments with `VECTOR_SEARCH` + `ai_query`,
then loops `ALTER TABLE … ALTER COLUMN … COMMENT` + `SET TAGS('validated','no')`.

**What we change to make it a customer-deployable accelerator:**

1. **Parameterize scope** — catalog/schema/table (or "all gold tables in catalog
   X"), instead of one hard-coded table. Drive from a widget/params cell.
2. **Real docs source** — point the VS index at the customer's existing data
   dictionary / glossary table or a docs Volume, not three inline strings.
3. **Pluggable endpoints** — embedding endpoint and LLM endpoint as params
   (default to `databricks-bge-large-en` + a current FMAPI model).
4. **Review-then-apply** — write generated comments to a `*_comment_review` table
   tagged `validated='no'`; a second cell applies only rows a steward marks
   approved. This matches the app's repeated "accept-with-review, don't
   auto-publish" guidance and keeps the learned ontology fed high-authority
   signals.
5. **Dry-run flag** — default to generate-only; applying is an explicit switch.
6. **Idempotent + scoped** — only target columns where `comment IS NULL`, log
   every change.

**Packaging:** `app/accelerators/metadata-ai-comments/` = generalized notebook +
README + optional `databricks.yml` (so an SA can `databricks bundle deploy` it
straight into the customer workspace).

**The loop it closes:** run → comment coverage rises → re-run the app's
assessment → Metadata pillar score goes up → overall readiness stage advances.

---

## 3. The comb-through: accelerators per pillar

★ = build/ship first.  Mix of **existing** Databricks assets and **build** items.

### Unity Catalog Foundation (weight 15)
- **UCX toolkit** (existing) — Hive→UC assessment + migration (group migration,
  table inventory, grants). Link + "how to run" wrapper.
- **System-schema enabler** (build, SQL) — enable `access`/`query`/`lineage`
  system schemas and grant the assessment SP — the measurement backbone.
- **Grant-to-group converter** (build) — find user-level grants and emit
  group-based `GRANT` rewrites.

### Metadata Richness (weight 20)
- ★ **AI-Comments RAG notebook** (the worked example above).
- **Comment-coverage dashboard** (build, Lakeview) — % tables/columns commented
  by schema/domain; the visible metric our deep-dive recommends.
- **Bulk governed-tagging script** (build, SQL/py) — apply PII/domain/certified
  tags from a mapping sheet.

### Relationships & Modeling (weight 12)
- **PK/FK candidate generator** (build) — profile `information_schema` + data to
  propose keys, emit `ALTER TABLE … ADD CONSTRAINT` for review.
- **Pre-joined gold view scaffolder** (build) — generate wide/star views for the
  common join paths Genie should see.

### Metrics / Metric Views (weight 18)
- **Metric-view scaffolder** (build) — from a KPI inventory sheet (or from query
  history) generate metric-view YAML stubs (measures/dimensions/joins/synonyms).
- **KPI-drift finder** (build) — mine query history for the same KPI computed
  differently across dashboards → prioritize which metric views to define first.

### Genie Agents (weight 16)
- **Genie Agent bootstrapper** (build, leverages `genie-rooms` skill) — create an
  agent scoped to a domain's gold tables + metric views, seed instructions from
  metadata.
- **Benchmark harness** (build, extends `genie_client.py`) — run a benchmark
  question set via the Conversation API, score answers, track regressions.
- **Verified-SQL seeder** (build) — turn the most common query-history queries
  into example question→SQL pairs.

### Domains & Stewardship (weight 10)
- **Domain/steward tagging kit** (build) — apply governed `domain` + `owner`/
  `steward` tags from a mapping (the pre-native-Domains structure the app already
  assesses).
- **Certification tagger** (build) — mark canonical assets certified.

### Adoption & Activity (weight 5)
- **Adoption dashboard** (build, Lakeview) — active users, query volume, Genie
  usage, comment-coverage trend, from system tables.
- **Readiness-trend job** (build) — schedule the app's own assessment (Lakebase
  snapshots) to chart the score climbing as accelerators land.

---

## 4. Implementation phases

**Phase 1 — Model + Learn surfacing (smallest shippable).**
`accelerators.py` registry; attach accelerators to each capability in
`/api/content`; add an "Accelerators" section to `CapabilityExplainer.tsx` (cards:
type badge, effort, prerequisites, steps, source link). Add `Accelerator` to
`types.ts`. Ship the AI-Comments accelerator as the first, fully-authored entry.
No new infra.

**Phase 2 — Plan integration.**
Feed the accelerators relevant to the customer's *low* pillars into the
`_generate_system` prompt in `routes/plan.py`, so "Top recommendations" name the
concrete accelerator and link it. Add an "Accelerators to deploy" section to the
generated plan + PDF.

**Phase 3 — Packaging & deployment.**
Store notebook sources under `app/accelerators/<key>/`; add a download / "copy
import command" affordance in the UI; optional per-accelerator DAB. Optional
backend endpoint to import a notebook into the connected workspace via the
Workspace API (SP-permission gated).

**Phase 4 — Close the assessment loop.**
Tie each accelerator to the signal keys it improves (`probes.py`), so scorecard
gap items get a direct "Fix this →" link to the accelerator, and a re-assessment
visibly shows the lift.

---

## 5. How each pillar's material improves over time

- **Data-driven contribution** — the registry is just data; the FE team adds
  accelerators via PR (same model as `customer-branding/`). A short schema + a
  CONTRIBUTING note keeps quality consistent. Every accelerator is versioned with
  `valid_as_of`.
- **Telemetry-driven prioritization** — with Lakebase snapshots on, track which
  gaps are most common and lowest-scoring across deployments → build accelerators
  for the highest-frequency pain first.
- **Usage feedback** — log which accelerators get recommended/opened; prune
  low-value, invest in high-value.
- **Freshness & lifecycle** — when Databricks ships a real API (e.g. AI Comments
  gets an endpoint, or native Domains GA), retire/replace the stop-gap via
  `supersedes`/`superseded_by`; sources and notebooks reviewed each release.
- **Learn co-evolves** — each accelerator is a runnable example sitting beside the
  prose, so Learn moves from *what/why* → *what/why/how-with-a-script*. The
  deep-dive and the accelerator reinforce each other.

---

## 6. Recommended first step

Build **Phase 1 + the AI-Comments accelerator** as the reference implementation:
Metadata is the second-heaviest pillar (weight 20) and AI Comments' missing API
makes this the highest-value, most defensible accelerator to lead with. Once the
pattern is proven end-to-end (registry → Learn card → packaged notebook), the
remaining pillars' accelerators are additive data + artifacts.
