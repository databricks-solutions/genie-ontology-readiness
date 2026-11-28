# Pillar signals & per-pillar scoring

The seven pillars, each with the exact SQL to gather its signals and the formula
to turn those signals into a 0-100 technical score, plus the gap conditions.
Each pillar also has a **drill-down** query — a per-catalog/schema (or per-agent /
per-workspace) breakdown that shows *where* the gap is, ordered by impact — so a
gap becomes actionable ("which schemas drag metadata down"). The drill-downs are
optional (skip them for a headline-only score) but recommended for a scorecard.
`<TABLES>`, `<COLUMNS>`, etc. are the source you resolved (see SKILL.md → Source
resolution). All scores are clamped to a max of 100.

Conventions used below:
- `pct(num, den)` = `round(100 * num / den, 1)` when `den > 0`, else `0.0`.
- A "commented" object has `comment IS NOT NULL AND comment <> ''`.
- Every pillar returns: `score` (0-100), `signals` (label/value/detail rows for
  display), and `gaps` (short imperative strings). If the pillar's reads raise a
  permission or missing-table error, return it as **unavailable** (score 0) with
  the reason, rather than a real 0.

---

## 1. Unity Catalog Foundation — weight 15

<!-- BEGIN GENERATED: pillar-uc_foundation -->
<!-- Generated from app/server by scripts/render_skill.py — edit the spec, not this block. -->
**Signals**

```sql
-- schemas (exclude the per-catalog information_schema)
SELECT COUNT(*) AS n_schemas
FROM <SCHEMATA>
WHERE schema_name <> 'information_schema';

-- table footprint + managed share
SELECT COUNT(*) AS total,
       SUM(CASE WHEN table_type IN ('MANAGED','MANAGED_SHALLOW_CLONE') THEN 1 ELSE 0 END) AS managed
FROM <TABLES> WHERE table_schema <> 'information_schema';

-- legacy (non-UC) footprint still in the workspace-local Hive metastore (best-effort;
-- omit this signal if hive_metastore.information_schema isn't readable — e.g. it
-- raises UC_HIVE_METASTORE_DISABLED_EXCEPTION when legacy access is turned off,
-- which just means non_uc is unknown, NOT that the pillar is unavailable)
SELECT COUNT(*) AS non_uc
FROM hive_metastore.information_schema.tables
WHERE table_schema <> 'information_schema';
```

`n_catalogs` = user (non-internal) catalogs in scope.
If `non_uc` is readable: `uc_coverage_pct = pct(total, total + non_uc)`.

**Score:**

Start at 0, then:
- `+40` if `n_catalogs > 0`;
- `+30` if `total > 0`;
- `+15` if `total >= 50`;
- `+15` if `n_schemas >= 5`.

Cap the result at 100.

**Signals to display:** Catalogs (`n_catalogs`), Schemas (`n_schemas`), Managed (`pct(managed, total)`%), In Unity Catalog (`uc_coverage_pct`%) when `non_uc` is known, and Not in Unity Catalog (`non_uc` tables) when `non_uc` is known.

**Gaps:**
- `n_catalogs == 0` → "No user catalogs found — Unity Catalog may not be in active use."
- `total < 50` → "Limited table footprint; broaden UC adoption beyond an initial workload."
- `non_uc > 0` (only when the hive_metastore read succeeded) → "`{non_uc}` table(s) (`{not_uc_pct}`%) are still in the legacy hive_metastore (not in Unity Catalog) — migrate them into UC (see the UCX accelerator)."
<!-- END GENERATED: pillar-uc_foundation -->

When a schema allowlist is in force, skip the `hive_tables` query and its signal: the
workspace's Hive footprint is not that schema's.

**Drill-down — Tables by schema** (which schemas hold the footprint, and how managed each is; largest first):

```sql
SELECT table_catalog AS catalog, table_schema AS schema, COUNT(*) AS tables,
       ROUND(100.0 * SUM(CASE WHEN table_type IN ('MANAGED','MANAGED_SHALLOW_CLONE') THEN 1 ELSE 0 END)
             / NULLIF(COUNT(*),0), 1) AS managed_pct
FROM <TABLES> WHERE table_schema <> 'information_schema'
GROUP BY table_catalog, table_schema ORDER BY tables DESC LIMIT 500;
```

---

## 2. Metadata Richness — weight 22

<!-- BEGIN GENERATED: pillar-metadata -->
<!-- Generated from app/server by scripts/render_skill.py — edit the spec, not this block. -->
**Signals**

```sql
-- table comment coverage
SELECT COUNT(*) AS total,
       SUM(CASE WHEN comment IS NOT NULL AND comment <> '' THEN 1 ELSE 0 END) AS commented
FROM <TABLES> WHERE table_schema <> 'information_schema';

-- column comment coverage: the heaviest read. Run it once per BATCH of
-- catalogs and add the results up (see "Running column_comments" below).
SELECT table_catalog AS cat, table_schema AS sch, COUNT(*) AS total,
       SUM(CASE WHEN comment IS NOT NULL AND comment <> '' THEN 1 ELSE 0 END) AS commented
FROM <COLUMNS>
WHERE table_catalog IN (<CATALOG_BATCH>) AND table_schema <> 'information_schema'
GROUP BY table_catalog, table_schema;

-- governed-tag reach (best-effort; treat failure as unknown). Count by
-- fully-qualified name so same-named tables in different schemas/catalogs
-- are counted separately (matches the Domains pillar).
SELECT COUNT(DISTINCT concat_ws('.', catalog_name, schema_name, table_name)) AS tagged_tables
FROM <TABLE_TAGS> WHERE schema_name <> 'information_schema';
```

**Running `column_comments`:**

1. **Never** run this as one query over the whole metastore: on a large estate it runs for many minutes and times out.
2. Take the catalogs in scope (the non-internal catalogs from Source resolution, or the catalogs the user asked for) and count their tables in one query: `SELECT lower(table_catalog) AS cat, COUNT(*) AS n FROM <TABLES> WHERE table_schema <> 'information_schema' GROUP BY 1`. Skip catalogs with no tables.
3. Pack the catalogs into batches by table count: a catalog with more than **5,000** tables is a batch on its own; put the others together until the next one would take the batch past 5,000 tables (in per-catalog mode, also stop at 25 catalogs). **Every catalog goes into exactly one batch and is named in that batch's `IN (...)` list. Never use `NOT IN` to cover "the rest"**: that is an unbounded scan of the whole metastore.
4. Run the query once per batch, with `<CATALOG_BATCH>` set to that batch's catalog names, quoted and in **lower case** (`table_catalog` is stored in lower case). In per-catalog mode, build `<COLUMNS>` from only that batch's catalogs. Run several batches at the same time if you can.
5. Add up `total` and `commented` over every row of the batches that succeeded: those sums are `col_total` and `col_commented`. Keep the per-schema rows; the drill-down below reuses them, so columns are never scanned twice.
6. If some batches fail, compute coverage over the catalogs you could read, and add a note: "column coverage over N of M catalogs".
7. If every batch fails, or no batch finishes for **5 minutes**, mark the pillar **unavailable**. Do not count column coverage as 0.
8. On a very large estate, suggest that the user limits the assessment to the catalogs that matter.

`table_pct = pct(table_commented, table_total)`;
`col_pct = pct(col_commented, col_total)`.

**Score:** `round(0.5 * table_pct + 0.5 * col_pct, 1)`

**Signals to display:** Tables commented (`table_pct`%), Columns commented (`col_pct`%), and Tagged tables (`tagged_tables`) when known.

**Gaps:**
- `table_pct < 80` → "Only `{table_pct}`% of tables have descriptions — Genie relies on these to understand data."
- `col_pct < 60` → "Only `{col_pct}`% of columns are commented; aim for high coverage on gold-layer columns."
- `table_total > 0 and tagged_tables == 0` (only when the tag read succeeded) → "No governed tags found; tags aid discovery and domain organization."
<!-- END GENERATED: pillar-metadata -->

**Drill-down — Comment coverage by schema (worst first)** (which schemas drag the score down):

Don't scan columns again. Use the per-schema rows you kept from the `column_comments`
batches, and get the table side with this scan:

```sql
SELECT table_catalog AS cat, table_schema AS sch, COUNT(*) AS tables,
       SUM(CASE WHEN comment IS NOT NULL AND comment <> '' THEN 1 ELSE 0 END) AS t_comm
FROM <TABLES> WHERE table_schema <> 'information_schema'
GROUP BY table_catalog, table_schema;
```

For each (`cat`, `sch`) that appears in either result:

- `table_comment_pct = round(100 * t_comm / tables, 1)`
- `column_comment_pct = round(100 * commented / total, 1)` (from the column batches)

Treat a missing side as 0. Sort by `table_comment_pct + column_comment_pct`, lowest first,
and show at most 500 rows.

---

## 3. Relationships & Modeling — weight 12

<!-- BEGIN GENERATED: pillar-relationships -->
<!-- Generated from app/server by scripts/render_skill.py — edit the spec, not this block. -->
**Signals**

```sql
-- declared constraints (if table_constraints isn't available, note it and score gold-layer only)
SELECT constraint_type, COUNT(*) AS n
FROM <TABLE_CONSTRAINTS>
WHERE table_schema <> 'information_schema'
GROUP BY constraint_type;

-- curated gold/mart layer, detected by schema or table naming
SELECT COUNT(*) AS gold_tables
FROM <TABLES>
WHERE table_schema <> 'information_schema'
  AND (lower(table_schema) RLIKE '(gold|mart|marts|analytics|semantic|presentation|reporting|dwh)'
       OR lower(table_name) RLIKE '^(gold_|mart_|dim_|fact_)');
```

`pk` = count of `'PRIMARY KEY'` rows; `fk` = count of `'FOREIGN KEY'` rows.

**Score:**

Start at 0, then:
- `+50` if `gold_tables > 0`;
- if constraints are available: `+35` if `fk > 0`; `+15` if `pk > 0`.

Cap the result at 100.

**Signals to display:** Gold-layer tables (`gold_tables`), Primary keys (`pk`) when constraints are available, and Foreign keys (`fk`) when constraints are available.

**Gaps:**
- `gold_tables == 0` → "No clearly-named gold/mart layer detected; Genie performs best on curated, pre-joined tables."
- `constraints_available and fk == 0` (only when constraint metadata is readable) → "No foreign-key constraints declared; PK/FK relationships let Genie infer joins reliably."
<!-- END GENERATED: pillar-relationships -->

If `table_constraints` was unreadable, add the note: *"Constraint metadata not available;
relationship score is based on the gold layer only."*

**Drill-down — Modeling by schema (thinnest first)** (where the gold layer / constraints are thin). Drop the PK/FK columns if `table_constraints` wasn't available:

```sql
WITH gold AS (
  SELECT table_catalog AS cat, table_schema AS sch, COUNT(*) AS gold_tables
  FROM <TABLES>
  WHERE lower(table_schema) RLIKE '(gold|mart|marts|analytics|semantic|presentation|reporting|dwh)'
     OR lower(table_name)   RLIKE '^(gold_|mart_|dim_|fact_)'
  GROUP BY table_catalog, table_schema
), con AS (
  SELECT table_catalog AS cat, table_schema AS sch,
         SUM(CASE WHEN constraint_type = 'PRIMARY KEY' THEN 1 ELSE 0 END) AS primary_keys,
         SUM(CASE WHEN constraint_type = 'FOREIGN KEY' THEN 1 ELSE 0 END) AS foreign_keys
  FROM <TABLE_CONSTRAINTS> GROUP BY table_catalog, table_schema
)
SELECT COALESCE(g.cat, c.cat) AS catalog, COALESCE(g.sch, c.sch) AS schema,
       COALESCE(g.gold_tables, 0) AS gold_tables,
       COALESCE(c.primary_keys, 0) AS primary_keys, COALESCE(c.foreign_keys, 0) AS foreign_keys
FROM gold g FULL OUTER JOIN con c ON g.cat = c.cat AND g.sch = c.sch
ORDER BY gold_tables ASC, foreign_keys ASC LIMIT 500;
```

---

## 4. Metrics — weight 20

<!-- BEGIN GENERATED: pillar-metrics -->
<!-- Generated from app/server by scripts/render_skill.py — edit the spec, not this block. -->
**Signals**

```sql
-- metric view count (table_type spelling varies by runtime: try 'METRIC_VIEW' then 'METRIC VIEW')
SELECT COUNT(*) AS metric_views
FROM <TABLES>
WHERE table_type = 'METRIC_VIEW' AND table_schema <> 'information_schema';

-- described metric views (a read that fails counts as 0)
SELECT COUNT(*) AS commented
FROM <TABLES>
WHERE table_type = 'METRIC_VIEW' AND comment IS NOT NULL AND trim(comment) <> '' AND table_schema <> 'information_schema';
```

`uncommented = metric_views - commented`.

**Score:**

If `metric_views == 0`, the score is **0** and only the gap below applies.

Otherwise:

`30 + 40 * min(metric_views, 10) / 10 + 30 * (commented / metric_views)`, rounded to 1 decimal and capped at 100.

**Signals to display:** Metric views (`metric_views`) and Commented (`pct(commented, metric_views)`%) when `metric_views > 0`.

**Gaps:**
- `metric_views == 0` → "No metric views found. Metric views are the GA foundation that feeds Genie Ontology — define KPIs centrally here."
- `0 < metric_views < 3` → "Few metric views; expand coverage so common KPIs are centrally defined and certified."
- `uncommented > 0` → "`{uncommented}` metric view(s) lack a description — Genie reads metric-view, dimension, and measure comments to reason; add them."
<!-- END GENERATED: pillar-metrics -->

If neither `table_type` spelling is recognized by the metastore, mark the pillar
**unavailable** (reason: not enabled on this metastore version).

**Drill-down — Metric views by schema** (where the semantic layer lives, and how much is described):

```sql
SELECT table_catalog AS catalog, table_schema AS schema, COUNT(*) AS metric_views,
       SUM(CASE WHEN comment IS NOT NULL AND trim(comment) <> '' THEN 1 ELSE 0 END) AS commented
FROM <TABLES> WHERE table_type = 'METRIC_VIEW'
GROUP BY table_catalog, table_schema ORDER BY metric_views DESC LIMIT 500;
```

---

## 5. Genie Agents — weight 16

Scored from the **audit system table** (workspace-wide, via the viewer's own
system-table access) — *not* the Genie REST API, which is permission/scope-gated
and only reflects what one principal can see. The score is **graded on real
conversation usage** of the **standalone** Genie-agent population. A space is
classified **dashboard-embedded** (an AI/BI Dashboard agent) when its `space_id`
**exactly equals** the `space_id`/`genie_space_id` value parsed from the
`response.result` JSON of a `getDashboardGenie`/`updateDashboardGenie` row (an exact
match on the parsed id, never a substring — a substring match would mis-mark
overlapping ids); otherwise it is a **standalone** agent (an absence-of-evidence
default — i.e. no dashboard association was observed in the audit window, not a
positively-confirmed standalone; label it so, don't overstate it).
Dashboard-embedded agents are classified out of the
scored population and counted separately so they don't skew the pillar. "Real usage"
is measured from **conversation actions only** (`genieStartConversationMessage`,
`createConversationMessage`, `genieCreateConversationMessage`), never raw events —
raw events are dominated by `genieGetSpace` polling and service/eval accounts and
overstate usage.

<!-- BEGIN GENERATED: pillar-genie_agents -->
<!-- Generated from app/server by scripts/render_skill.py — edit the spec, not this block. -->
**Signals**

```sql
-- Graded usage for STANDALONE Genie Agents, from the audit log over the last 90 days (aibiGenie), minus trashed spaces. A space is DASHBOARD-EMBEDDED (counted separately, not scored) when its space_id EXACTLY equals the space_id/genie_space_id value parsed from the response.result JSON of a getDashboardGenie/updateDashboardGenie row (exact match, not a substring); otherwise STANDALONE (absence-of-evidence default). Real usage is measured from CONVERSATION actions only (not raw events, which overstate via polling): questions_30d, distinct_users_30d and active_30d are over the last 30 days.
WITH genie_events AS (
  SELECT request_params.space_id AS space_id, action_name, event_date,
         user_identity.email AS user_email
  FROM system.access.audit
  WHERE service_name = 'aibiGenie'
    AND request_params.space_id IS NOT NULL
    AND request_params.space_id <> 'new'
    AND event_date >= current_date() - INTERVAL 90 DAYS
),
dashboard_refs AS (
  SELECT get_json_object(response.result, '$.space_id') AS sid1,
         get_json_object(response.result, '$.genie_space_id') AS sid2
  FROM system.access.audit
  WHERE lower(action_name) IN ('getdashboardgenie', 'updatedashboardgenie')
    AND response.result IS NOT NULL
    AND event_date >= current_date() - INTERVAL 90 DAYS
),
dashboard_space_ids AS (
  SELECT sid1 AS space_id FROM dashboard_refs WHERE sid1 IS NOT NULL
  UNION SELECT sid2 FROM dashboard_refs WHERE sid2 IS NOT NULL
),
per_space AS (
  SELECT space_id,
         MAX(CASE WHEN lower(action_name) = 'trashspace' THEN 1 ELSE 0 END) AS trashed,
         SUM(CASE WHEN lower(action_name) IN ('geniestartconversationmessage', 'createconversationmessage', 'geniecreateconversationmessage')
                   AND event_date >= current_date() - INTERVAL 30 DAYS
                  THEN 1 ELSE 0 END) AS questions_30d,
         MAX(event_date) AS last_active_date
  FROM genie_events GROUP BY space_id
),
classified AS (
  SELECT space_id, trashed, questions_30d, last_active_date,
         CASE WHEN space_id IN (SELECT space_id FROM dashboard_space_ids)
              THEN 1 ELSE 0 END AS dashboard_embedded
  FROM per_space
),
standalone AS (
  SELECT * FROM classified WHERE trashed = 0 AND dashboard_embedded = 0
)
SELECT
  (SELECT COUNT(*) FROM standalone) AS total,
  (SELECT COUNT(*) FROM standalone WHERE questions_30d > 0) AS active_30d,
  (SELECT COALESCE(SUM(questions_30d), 0) FROM standalone) AS questions_30d,
  (SELECT COUNT(DISTINCT e.user_email) FROM genie_events e
     WHERE lower(e.action_name) IN ('geniestartconversationmessage', 'createconversationmessage', 'geniecreateconversationmessage')
       AND e.event_date >= current_date() - INTERVAL 30 DAYS
       AND e.space_id IN (SELECT space_id FROM standalone)) AS distinct_users_30d,
  (SELECT MAX(last_active_date) FROM standalone) AS last_active_date,
  (SELECT COUNT(*) FROM classified WHERE trashed = 0 AND dashboard_embedded = 1) AS dashboard_embedded;
```

`recency_days` = days since `last_active_date` (the most recent standalone-agent event);
`active_share` = `active_30d / total` when `total > 0`.

**Score:**

If no standalone Genie Agent exists (`total == 0`), the score is **0** and only the `no_genie_agents` gap applies.

Otherwise start at `+15` for having a standalone agent, then add the highest tier reached in each graded real-usage band (capped at 100):

- **Active share** (`active_30d / total`): `+8` at `>= 0.1`, `+16` at `>= 0.3`, `+25` at `>= 0.6`;
- **Distinct users (30d)**: `+8` at `>= 1`, `+16` at `>= 5`, `+25` at `>= 20`;
- **Questions (30d)**: `+7` at `>= 1`, `+14` at `>= 25`, `+20` at `>= 100`;
- **Recency** (days since last activity): `+15` within 7d, `+10` within 14d, `+5` within 30d.

Dashboard-embedded agents (AI/BI Dashboard agents) are classified out of this population and counted separately; they never affect the score.

**Signals to display:** Genie Agents (standalone) (`total`), Active agents (30d) (`active_30d`), Users (30d) (`distinct_users_30d`), Questions (30d) (`questions_30d`), and Dashboard-embedded (`dashboard_embedded`) counted separately; not scored in this pillar.

**Gaps:**
- `total == 0` → "No standalone Genie Agents found in the audit log — create a curated Genie Agent as the entry point to natural-language analytics."
- `total > 0 and active_30d == 0` → "`{total}` standalone Genie Agent(s) exist but none had a conversation in the last 30 days — drive adoption or retire stale agents."
<!-- END GENERATED: pillar-genie_agents -->

If `system.access.audit` is unreadable, mark the pillar **unavailable** (reason:
insufficient permission — the identity needs `SELECT` on `system.access.audit`).

**Best-effort per-agent curation (where visible, NOT scored).** Reading an agent's
curation detail (instructions, example/verified SQL, functions, benchmarks, sample
questions, data-source tables) comes from the agent's **serialized space**, which
Databricks gates behind **CAN_EDIT** and which only reads for agents co-located in
the scorer's own workspace. So assess whatever agents you can read and label the
rest **"not readable / not assessed"** — *never* 0/absent, so a visibility limit
isn't mistaken for poor curation. Label the whole breakdown **"where visible"**.
This is quality context only and **must not affect the numeric score**. (Serialized
read: `GET {host}/api/2.0/genie/spaces/{space_id}?include_serialized_space=true`; a
401/403/404 or a missing `serialized_space` payload → "not readable".)

**Drill-down — Genie Agents by audit activity** (top agents by event volume, bounded to 200), each with its **type** (standalone vs dashboard-embedded) and 30-day conversation usage (`questions`, distinct `users`, `active_30d`). The `names` CTE resolves each space's latest display name over a wider 90-day window (names only appear on create/update events, not conversation events); the `dashboard_refs` CTE drives the type classification. On a very large or shared metastore this scan can be slow — keep the `LIMIT` and the 90-day bound. Add a `workspace` column (join `system.access.workspaces_latest` on `workspace_id`) when more than one workspace is in scope:

```sql
WITH names AS (
  SELECT request_params.space_id AS space_id,
         max_by(request_params.display_name, event_time) AS space_name
  FROM system.access.audit
  WHERE service_name = 'aibiGenie' AND request_params.space_id IS NOT NULL
    AND request_params.display_name IS NOT NULL
    AND event_date >= current_date() - INTERVAL 90 DAYS
  GROUP BY request_params.space_id
),
dashboard_refs AS (
  SELECT get_json_object(response.result, '$.space_id') AS sid1,
         get_json_object(response.result, '$.genie_space_id') AS sid2
  FROM system.access.audit
  WHERE lower(action_name) IN ('getdashboardgenie', 'updatedashboardgenie')
    AND response.result IS NOT NULL
    AND event_date >= current_date() - INTERVAL 90 DAYS
),
dashboard_space_ids AS (
  SELECT sid1 AS space_id FROM dashboard_refs WHERE sid1 IS NOT NULL
  UNION SELECT sid2 FROM dashboard_refs WHERE sid2 IS NOT NULL
)
SELECT COALESCE(nm.space_name, a.space_id) AS agent, a.space_id AS space_id, a.events AS events,
       a.questions AS questions, a.users AS users,
       CASE WHEN a.active_30d = 1 THEN 'Yes' ELSE 'No' END AS active_30d,
       CASE WHEN a.space_id IN (SELECT space_id FROM dashboard_space_ids)
            THEN 'Dashboard-embedded' ELSE 'Standalone' END AS type
FROM (
  SELECT request_params.space_id AS space_id, COUNT(*) AS events,
         SUM(CASE WHEN lower(action_name) IN ('geniestartconversationmessage', 'createconversationmessage', 'geniecreateconversationmessage')
                   AND event_date >= current_date() - INTERVAL 30 DAYS THEN 1 ELSE 0 END) AS questions,
         COUNT(DISTINCT CASE WHEN lower(action_name) IN ('geniestartconversationmessage', 'createconversationmessage', 'geniecreateconversationmessage')
                              AND event_date >= current_date() - INTERVAL 30 DAYS THEN user_identity.email END) AS users,
         MAX(CASE WHEN lower(action_name) = 'trashspace' THEN 1 ELSE 0 END) AS trashed,
         MAX(CASE WHEN lower(action_name) IN ('geniestartconversationmessage', 'createconversationmessage', 'geniecreateconversationmessage')
                   AND event_date >= current_date() - INTERVAL 30 DAYS THEN 1 ELSE 0 END) AS active_30d
  FROM system.access.audit
  WHERE service_name = 'aibiGenie' AND request_params.space_id IS NOT NULL
    AND request_params.space_id <> 'new'
    AND event_date >= current_date() - INTERVAL 90 DAYS
  GROUP BY request_params.space_id
) a LEFT JOIN names nm ON a.space_id = nm.space_id
WHERE a.trashed = 0 ORDER BY a.events DESC LIMIT 200;
```

---

## 6. Domains & Stewardship — weight 10

**Domains live in Discover.** A Discover domain is a governed tag keyed by the
domain name (`Finance`); a subdomain is keyed `{parentTag}/{subdomain}`
(`Finance/Tax`), usually with an empty value. Tagging an asset with a subdomain
does **not** also tag it with the parent. Docs:
[Domains and subdomains](https://docs.databricks.com/aws/en/uc-semantics/domains).

Domains are **account-level and catalog-agnostic**: catalogs, schemas and tables
belong to domains, not the other way round. The domain count is the same in every
run; coverage is what the assessed catalogs contribute.

**Step 1: list domains with the Domains API** (needs an authenticated REST GET):
`GET {host}/api/2.0/domains?page_size=100` (alternate path `/api/discover/v1/domains`),
following `next_page_token`. Each item has `tag_key`, `parent_domain_id` (empty for a
top-level domain) and `effective_draft`. The caller needs **MANAGE DISCOVER**
(workspace and account admins have it by default; an account admin grants it).
- **If the API returns 401/403 or doesn't answer: stop here.** The pillar is **not
  scored**: report it as not available, leave it out of the overall score (see
  scoring-and-output.md), and note that domains need MANAGE DISCOVER. **Don't infer
  domains from tags**: tags alone can't tell domains apart from other governed tags
  (about 50% right in testing).
- Count **published** domains only: drop items with `effective_draft: true` (drafts are
  visible to curators only; `effective_draft` also covers a subdomain under a draft parent).
- `distinct_domains` = published domains with no `parent_domain_id` (account-wide);
  `subdomains` = the other published domains; `draft_domains` = the dropped drafts.
- `domain_keys` = every published domain's `tag_key`, lowercased.

**Step 2: the governed-tag queries.** Key sets (match case-insensitively):
- domain keys: `domain_keys` from step 1 (a subdomain's key is `Parent/Sub`)
- steward keys: `owner, data_owner, steward, data_steward`
- cert keys: `system.certification_status, certification_status` (value `certified`)

`<domain_pred>` = `lower(tag_name) IN (<domain_keys>)`;
`<domain_label>` = `split(tag_name, '/')[0]` (a subdomain rolls up to its top-level domain).

```sql
-- domains used here + assignments: published domains assigned to an assessed
-- catalog, schema or table (catalog tags have no schema_name)
SELECT COUNT(DISTINCT lower(domain)) AS used_domains, COUNT(*) AS assignments FROM (
  SELECT <domain_label> AS domain FROM <TABLE_TAGS>   WHERE <domain_pred>
  UNION ALL
  SELECT <domain_label> AS domain FROM <SCHEMA_TAGS>  WHERE <domain_pred>
  UNION ALL
  SELECT <domain_label> AS domain FROM <CATALOG_TAGS> WHERE <domain_pred>
);

-- stewarded assets
SELECT COUNT(*) AS stewarded FROM <TABLE_TAGS> WHERE lower(tag_name) IN (<steward_keys>);

-- certified assets (distinct fully-qualified tables tagged certified)
SELECT COUNT(DISTINCT concat_ws('.', catalog_name, schema_name, table_name)) AS certified
FROM <TABLE_TAGS>
WHERE lower(tag_name) IN (<cert_keys>) AND lower(tag_value) = 'certified';

-- governance coverage over the table footprint
SELECT COUNT(*) AS total_tables FROM <TABLES> WHERE table_schema <> 'information_schema';
SELECT COUNT(DISTINCT concat_ws('.', catalog_name, schema_name, table_name)) AS governed_tagged FROM <TABLE_TAGS>;
-- a table is in a domain if the table, its schema or its catalog is assigned to one
SELECT COUNT(*) AS domain_tagged FROM <TABLES>
WHERE table_schema <> 'information_schema' AND (
     (table_catalog, table_schema, table_name) IN (SELECT catalog_name, schema_name, table_name FROM <TABLE_TAGS> WHERE <domain_pred>)
  OR (table_catalog, table_schema) IN (SELECT catalog_name, schema_name FROM <SCHEMA_TAGS> WHERE <domain_pred>)
  OR table_catalog IN (SELECT catalog_name FROM <CATALOG_TAGS> WHERE <domain_pred>));
```

`pct_tagged = pct(governed_tagged, total_tables)`;
`pct_in_domain = pct(domain_tagged, total_tables)`.

**Certification of the most-used assets** (best-effort; needs
`system.access.table_lineage`): of the top-10 most-accessed tables in the last
90 days, how many are certified?

```sql
WITH top AS (
  SELECT source_table_full_name AS name, COUNT(DISTINCT created_by) AS n
  FROM system.access.table_lineage
  WHERE source_table_full_name IS NOT NULL
    AND source_table_catalog NOT IN ('system','__databricks_internal','samples')
    AND source_table_schema <> 'information_schema'
    AND event_date >= current_date() - INTERVAL 90 DAYS
  GROUP BY source_table_full_name ORDER BY n DESC LIMIT 10
), cert AS (
  SELECT concat_ws('.', catalog_name, schema_name, table_name) AS name
  FROM system.information_schema.table_tags
  WHERE lower(tag_name) IN ('system.certification_status','certification_status')
    AND lower(tag_value) = 'certified'
)
SELECT t.name, t.n AS accesses, CASE WHEN c.name IS NOT NULL THEN 1 ELSE 0 END AS certified
FROM top t LEFT JOIN cert c ON t.name = c.name ORDER BY t.n DESC;
-- top_accessed = row count; top_certified = rows where certified = 1
```

**Score** (start at 0; only when the Domains API answered):
- if `distinct_domains > 0`: `+ 40 + 40 * min(distinct_domains, 5)/5` (account-wide count)
- `+20` if `stewarded > 0`

**Signals to display:** Domains (Discover) (`distinct_domains`, with `subdomains` and
`draft_domains` not counted), Domains used here (`used_domains`), Domain assignments
(`assignments`), Stewarded assets (`stewarded`), Certified assets (`certified`); plus
Tables tagged (`pct_tagged`%), Tables in a domain (`pct_in_domain`%, directly or through
the schema or catalog) when `total_tables > 0`; and Top accessed certified
(`top_certified` / `top_accessed`) when available.

**Note to include:** "Domains are account-wide: the count is the account's published
domains from the Discover Domains API (drafts excluded), the same for every scope.
Coverage is for the assessed catalogs: the domains used here and the tables in a domain,
directly or through their schema or catalog."

**Gaps:**
- `distinct_domains == 0` → "No published domains in Discover. Create business-aligned domains and subdomains, publish them, and assign catalogs, schemas or tables to them."
- `distinct_domains > 0 AND used_domains == 0` → "None of the account's published domains is assigned to the assessed catalogs, schemas or tables. Assign them to domains in Discover so their assets roll up to a business area."
- `stewarded == 0` → "No stewardship tags (owner/steward) found; assign a named steward per domain."
- `certified == 0` → "No certified assets found — certify canonical gold tables so users (and Genie) know which to trust."
- `total_tables > 0 AND pct_tagged < 50` → "Only `{pct_tagged}`% of tables carry any UC governed tag — tag eligible assets (PII, domain, certification) to power governed discovery."
- `total_tables > 0 AND distinct_domains > 0 AND pct_in_domain < 50` → "Only `{pct_in_domain}`% of tables are in a domain — assign their catalog, schema or the table to a domain so assets roll up to business-aligned domains."
- `top_accessed > 0 AND top_certified < top_accessed` → "Only `{top_certified}` of your top `{top_accessed}` most-accessed resources are certified — certify high-traffic tables so Genie/ontology can trust your busiest data."

**Drill-down — Governance tags by schema** (which schemas lack domain / steward / certification tags; most-covered first). Uses `<domain_pred>` for the domain column (tables tagged directly):

```sql
SELECT catalog_name AS catalog, schema_name AS schema,
  COUNT(DISTINCT CASE WHEN <domain_pred>
        THEN concat_ws('.', catalog_name, schema_name, table_name) END) AS domain_tagged,
  COUNT(DISTINCT CASE WHEN lower(tag_name) IN (<steward_keys>)
        THEN concat_ws('.', catalog_name, schema_name, table_name) END) AS stewarded,
  COUNT(DISTINCT CASE WHEN lower(tag_name) IN (<cert_keys>) AND lower(tag_value) = 'certified'
        THEN concat_ws('.', catalog_name, schema_name, table_name) END) AS certified
FROM <TABLE_TAGS> GROUP BY catalog_name, schema_name ORDER BY domain_tagged DESC LIMIT 500;
```

---

## 7. Adoption & Activity — weight 5

<!-- BEGIN GENERATED: pillar-adoption -->
<!-- Generated from app/server by scripts/render_skill.py — edit the spec, not this block. -->
**Signals**

```sql
-- active users, last 30 days (system.access.audit)
SELECT COUNT(DISTINCT user_identity.email) AS active_users
FROM system.access.audit
WHERE event_date >= current_date() - INTERVAL 30 DAYS;

-- query volume, last 30 days (system.query.history)
SELECT COUNT(*) AS queries_30d
FROM system.query.history
WHERE start_time >= current_timestamp() - INTERVAL 30 DAYS;
```

**Score:**

Start at 0, then add the band for `active_users`:

| active_users | points |
|---|---|
| `<= 0` | 0 |
| `1–4` | 20 |
| `5–19` | 35 |
| `20–49` | 45 |
| `>= 50` | 50 |

Then add `+50` if `queries_30d > 0`. Cap the result at 100.

**Signals to display:** Active users (30d) (`active_users`) when readable and Queries (30d) (`queries_30d`) when readable.

**Gaps:** none (the pillar emits no gap text).
<!-- END GENERATED: pillar-adoption -->

If **both** reads fail, mark the pillar **unavailable** (system.access / system.query
not enabled or not granted). If either returns, score with what you have.

Adoption is informational: it adds to the score but emits no next-step gaps.

**Drill-down — Adoption by workspace** (only meaningful when more than one workspace is in scope; with a single workspace, adoption is one workspace-wide number with no breakdown):

```sql
SELECT COALESCE(w.workspace_name, CAST(u.workspace_id AS STRING)) AS workspace,
       u.active_users AS active_users, COALESCE(q.queries, 0) AS queries
FROM (
  SELECT workspace_id, COUNT(DISTINCT user_identity.email) AS active_users
  FROM system.access.audit WHERE event_date >= current_date() - INTERVAL 30 DAYS
  GROUP BY workspace_id
) u
LEFT JOIN (
  SELECT workspace_id, COUNT(*) AS queries FROM system.query.history
  WHERE start_time >= current_timestamp() - INTERVAL 30 DAYS GROUP BY workspace_id
) q ON CAST(u.workspace_id AS STRING) = CAST(q.workspace_id AS STRING)
LEFT JOIN system.access.workspaces_latest w
  ON CAST(u.workspace_id AS STRING) = CAST(w.workspace_id AS STRING)
ORDER BY u.active_users DESC LIMIT 200;
```
