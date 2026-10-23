---
name: genie-ontology-readiness
description: >-
  Assess how ready a Databricks workspace / Unity Catalog metastore is for Genie
  (and the learned Genie Ontology preview) by running read-only SQL against
  information_schema and system tables. Produces a weighted 0-100 readiness
  score across seven pillars — Unity Catalog foundation, metadata richness,
  relationships & modeling, metric views, Genie Agents, domains & stewardship,
  and adoption — with a maturity level, a readiness stage, and prioritized,
  gap-driven next steps. Use when someone asks "is <workspace> ready for Genie /
  Genie Ontology", "score our Genie readiness", "assess our Unity Catalog
  semantic maturity", or wants a Genie-readiness scorecard for a customer estate.
---

# Genie Ontology Readiness Assessment

Score a Databricks estate's readiness for Genie / the learned Genie Ontology.
Everything here is **read-only** and deterministic: scores are a function of what
the queries find, never a self-assessment, so repeat runs on the same estate are
directly comparable.

The user-defined **UC Business Semantics** foundation (governed catalogs, rich
metadata, metric views, curated Genie Agents, domains) feeds the gated learned
Genie Ontology layer — so "preparing for Genie Ontology" means maturing this
foundation. This skill measures that foundation.

## Prerequisites

- A way to run SQL against the target workspace (a SQL warehouse / the agent's
  SQL execution tool). All seven pillars are answerable with SQL alone.
- The assessing identity needs, at minimum, `USE CATALOG` + `SELECT` on the
  catalogs to assess. Several pillars are richer if the identity can also read
  the **system tables** (`system.information_schema`, `system.access.audit`,
  `system.query.history`, `system.access.table_lineage`). Missing access
  degrades a pillar to *unavailable* (score 0, flagged) — it never fails the run.
- *Optional:* an authenticated REST GET (workspace host + token) enables the
  native UC Domains count for the Domains pillar. Without it, that pillar uses
  the governed-tag proxy (SQL) — the common path anyway.

## Workflow

1. **Resolve the SQL source** (once, up front) — see "Source resolution" below.
   Every catalog-metadata pillar reads through the source you pick here.
2. **Run each pillar's signals.** For all seven pillars, run the queries in
   `references/pillar-signals.md`, then apply that pillar's **score formula** to
   the results to get a 0-100 technical score, a `signals` list, and a `gaps`
   list. Each pillar is independent — run them in any order (or concurrently).
   - If a pillar's reads fail on a permission/missing-table error, mark it
     **unavailable** (score 0) and record why; do not report a swallowed failure
     as a confident 0. Keep going.
   - Each pillar also has an optional **drill-down** query (a per-catalog/schema,
     per-agent, or per-workspace breakdown). Run it for any pillar that ends up
     with a gap, so the scorecard can show *where* the gap is — skip it for a
     headline-only score.
3. **Compute the overall result** — weighted score, maturity level, readiness
   stage, and prioritized gaps — per `references/scoring-and-output.md`.
4. **Emit the scorecard** using the template in `references/scoring-and-output.md`.

## Source resolution

Most pillars read Unity Catalog `information_schema` views (`tables`, `columns`,
`schemata`, `table_constraints`, `table_tags`, `schema_tags`). Pick the source
once and reuse it everywhere:

1. **Prefer the metastore-wide system view.** Try:
   ```sql
   SELECT 1 FROM system.information_schema.tables LIMIT 1
   ```
   If it succeeds, use `system.information_schema.<view>` for every view — one
   query covers the whole metastore. When you group or count over it, **exclude
   internal catalogs**: add
   `AND <cat_col> NOT IN ('system','__databricks_internal','samples','hive_metastore') AND <cat_col> NOT RLIKE '^__'`,
   where `<cat_col>` is the view's catalog column — **`table_catalog`** for
   `tables` / `columns` / `table_constraints`, and **`catalog_name`** for
   `catalogs` / `schemata` / `table_tags` / `schema_tags`.
2. **Otherwise fall back to per-catalog union.** Enumerate catalogs with
   `SHOW CATALOGS`, drop the internal ones (`system`, `__databricks_internal`,
   `samples`, `hive_metastore`, and any `__`-prefixed), and for the catalogs
   whose `information_schema` you can actually read, `UNION ALL` each catalog's
   own view:
   ```sql
   SELECT * FROM `catalog_a`.information_schema.tables
   UNION ALL SELECT * FROM `catalog_b`.information_schema.tables
   -- ...one per readable catalog
   ```
   Per-catalog mode needs only catalog-level `SELECT` (grantable by any catalog
   owner), so it works when the SP/user isn't an account admin. A catalog that
   `SHOW CATALOGS` lists but whose `information_schema` you can't query should be
   dropped from the union (test each with a `SELECT 1 ... LIMIT 1`).

   **Always quote enumerated names.** Catalog names come from the metastore, not
   from you, so a name can contain a backtick, a hyphen, or a space. Wrap every
   catalog name you interpolate as an identifier in backticks **and double any
   backtick inside it** (`` my`cat `` → `` `my``cat` ``). Skip any name that
   contains a newline, carriage return, or NUL. This applies to the readability
   probe, the `UNION ALL`, and the per-catalog drill-downs. Where a catalog name
   is used as a string literal instead (for example a `table_catalog IN (...)`
   batch filter), single-quote it and double any single quote inside it. The
   App does the same with `quote_ident` / `quote_literal` in
   `app/server/security.py`.

Throughout `references/pillar-signals.md`, **`<TABLES>`**, **`<COLUMNS>`**,
**`<SCHEMATA>`**, **`<TABLE_CONSTRAINTS>`**, **`<TABLE_TAGS>`**, **`<SCHEMA_TAGS>`**
stand for the source you resolved (either `system.information_schema.<view>` with
the internal-catalog filter on the right catalog column — see above — or the
per-catalog UNION). `n_catalogs` = the number of user (non-internal) catalogs in
scope. Validated live against a metastore-wide `system` source; per-catalog union
mode is scoped to its enumerated catalogs so it needs no internal-catalog filter.

The `system.*` pillars (Genie Agents, Adoption, and the "top accessed certified"
signal) always read the account-level system tables directly — they don't use
`<...>` placeholders.

## Scoring at a glance

- Seven pillars, each scored 0-100. Pillar **weights sum to 100**:
  UC foundation 15 · metadata 22 · relationships 12 · metrics 20 ·
  Genie Agents 16 · domains 10 · adoption 5.
- **Overall** = weighted average of the pillar scores (an unavailable pillar
  contributes 0). Map it to a maturity **level** (0-4) and a **readiness stage**.
- **Prioritized gaps** = each pillar's gaps, ordered lowest-score / highest-weight
  first, top 6.

Full formulas, level/stage tables, and the scorecard template are in
`references/scoring-and-output.md`. Read both reference files before running.
