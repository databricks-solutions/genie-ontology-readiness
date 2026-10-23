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

**Signals**

```sql
-- schemas (exclude the per-catalog information_schema)
SELECT COUNT(*) AS n_schemas
FROM <SCHEMATA> WHERE schema_name <> 'information_schema';

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

Let `n_catalogs` = user (non-internal) catalogs in scope. If `non_uc` is
readable, `uc_coverage_pct = pct(total, total + non_uc)`.

**Score** (start at 0):
- `+40` if `n_catalogs > 0`
- `+30` if `total > 0`
- `+15` if `total >= 50`
- `+15` if `n_schemas >= 5`

**Signals to display:** Catalogs (`n_catalogs`), Schemas (`n_schemas`), Managed
(`pct(managed, total)`%). If `non_uc` is known, also: In Unity Catalog
(`uc_coverage_pct`%) and Not in Unity Catalog (`non_uc` tables).

**Gaps:**
- `n_catalogs == 0` → "No user catalogs found — Unity Catalog may not be in active use."
- `total < 50` → "Limited table footprint; broaden UC adoption beyond an initial workload."
- `non_uc > 0` → "`{non_uc}` table(s) (`{100 - uc_coverage_pct}`%) are still in the legacy hive_metastore (not in Unity Catalog) — migrate them into UC (see UCX)."

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

**Signals**

```sql
-- table comment coverage
SELECT COUNT(*) AS total,
       SUM(CASE WHEN comment IS NOT NULL AND comment <> '' THEN 1 ELSE 0 END) AS commented
FROM <TABLES> WHERE table_schema <> 'information_schema';

-- column comment coverage (heaviest read; on a wide metastore, scan in
-- per-catalog batches and sum, rather than one metastore-wide query that times out)
SELECT COUNT(*) AS total,
       SUM(CASE WHEN comment IS NOT NULL AND comment <> '' THEN 1 ELSE 0 END) AS commented
FROM <COLUMNS> WHERE table_schema <> 'information_schema';

-- governed-tag reach (best-effort; treat failure as unknown)
SELECT COUNT(DISTINCT table_name) AS tagged_tables FROM <TABLE_TAGS>;
```

`table_pct = pct(table_commented, table_total)`;
`col_pct = pct(col_commented, col_total)`.

**Score:** `round(0.5 * table_pct + 0.5 * col_pct, 1)`

**Signals to display:** Tables commented (`table_pct`%), Columns commented
(`col_pct`%), and Tagged tables (`tagged_tables`) when known.

**Gaps:**
- `table_pct < 80` → "Only `{table_pct}`% of tables have descriptions — Genie relies on these to understand data."
- `col_pct < 60` → "Only `{col_pct}`% of columns are commented; aim for high coverage on gold-layer columns."
- `tagged_tables == 0` (when `table_total > 0`) → "No governed tags found; tags aid discovery and domain organization."

**Drill-down — Comment coverage by schema (worst first)** (which schemas drag the score down):

```sql
WITH t AS (
  SELECT table_catalog AS cat, table_schema AS sch, COUNT(*) AS tables,
         SUM(CASE WHEN comment IS NOT NULL AND comment <> '' THEN 1 ELSE 0 END) AS t_comm
  FROM <TABLES> WHERE table_schema <> 'information_schema' GROUP BY table_catalog, table_schema
), c AS (
  SELECT table_catalog AS cat, table_schema AS sch, COUNT(*) AS cols,
         SUM(CASE WHEN comment IS NOT NULL AND comment <> '' THEN 1 ELSE 0 END) AS c_comm
  FROM <COLUMNS> WHERE table_schema <> 'information_schema' GROUP BY table_catalog, table_schema
)
SELECT COALESCE(t.cat, c.cat) AS catalog, COALESCE(t.sch, c.sch) AS schema, t.tables AS tables,
       ROUND(100.0 * t.t_comm / NULLIF(t.tables,0), 1) AS table_comment_pct,
       ROUND(100.0 * c.c_comm / NULLIF(c.cols,0), 1)  AS column_comment_pct
FROM t FULL OUTER JOIN c ON t.cat = c.cat AND t.sch = c.sch
ORDER BY (COALESCE(ROUND(100.0*t.t_comm/NULLIF(t.tables,0),1),0)
        + COALESCE(ROUND(100.0*c.c_comm/NULLIF(c.cols,0),1),0)) ASC
LIMIT 500;
```

---

## 3. Relationships & Modeling — weight 12

**Signals**

```sql
-- declared constraints (if table_constraints isn't available, note it and score gold-layer only)
SELECT constraint_type, COUNT(*) AS n FROM <TABLE_CONSTRAINTS> GROUP BY constraint_type;
-- -> pk = count of 'PRIMARY KEY', fk = count of 'FOREIGN KEY'

-- curated gold/mart layer, detected by schema or table naming
SELECT COUNT(*) AS gold_tables
FROM <TABLES>
WHERE lower(table_schema) RLIKE '(gold|mart|marts|analytics|semantic|presentation|reporting|dwh)'
   OR lower(table_name)   RLIKE '^(gold_|mart_|dim_|fact_)';
```

**Score** (start at 0):
- `+50` if `gold_tables > 0`
- if constraints available: `+35` if `fk > 0`; `+15` if `pk > 0`

**Signals to display:** Gold-layer tables (`gold_tables`); and when constraints
are available, Primary keys (`pk`) and Foreign keys (`fk`).
If `table_constraints` was unreadable, add the note: *"Constraint metadata not
available; relationship score is based on the gold layer only."*

**Gaps:**
- `gold_tables == 0` → "No clearly-named gold/mart layer detected; Genie performs best on curated, pre-joined tables."
- constraints available and `fk == 0` → "No foreign-key constraints declared; PK/FK relationships let Genie infer joins reliably."

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

## 4. Metric Views — weight 20

**Signals**

```sql
-- metric view count (table_type spelling varies by runtime: try 'METRIC_VIEW' then 'METRIC VIEW')
SELECT COUNT(*) AS metric_views FROM <TABLES> WHERE table_type = 'METRIC_VIEW';

-- described metric views
SELECT COUNT(*) AS commented
FROM <TABLES>
WHERE table_type = 'METRIC_VIEW' AND comment IS NOT NULL AND trim(comment) <> '';
```

If neither `table_type` spelling is recognized by the metastore, mark the pillar
**unavailable** (reason: not enabled on this metastore version).

**Score:**
- If `metric_views == 0` → **score 0** (and the single gap below).
- Else: `30 + 40 * min(metric_views, 10)/10 + 30 * (commented / metric_views)`,
  rounded to 1 decimal.

**Signals to display:** Metric views (`metric_views`); Commented
(`pct(commented, metric_views)`%) when `metric_views > 0`.

**Gaps:**
- `metric_views == 0` → "No metric views found. Metric views are the GA foundation that feeds Genie Ontology — define KPIs centrally here."
- `metric_views < 3` (and > 0) → "Few metric views; expand coverage so common KPIs are centrally defined and certified."
- `metric_views - commented > 0` → "`{uncommented}` metric view(s) lack a description — Genie reads metric-view, dimension, and measure comments to reason; add them."

**Drill-down — Metric views by schema** (where the semantic layer lives, and how much is described):

```sql
SELECT table_catalog AS catalog, table_schema AS schema, COUNT(*) AS metric_views,
       SUM(CASE WHEN comment IS NOT NULL AND trim(comment) <> '' THEN 1 ELSE 0 END) AS commented
FROM <TABLES> WHERE table_type = 'METRIC_VIEW'
GROUP BY table_catalog, table_schema ORDER BY metric_views DESC LIMIT 500;
```

---

## 5. Genie Agents — weight 16

Counted from the **audit system table** (workspace-wide, via the viewer's own
system-table access) — *not* the Genie REST API, which is permission/scope-gated
and only reflects what one principal can see.

**Signals** (over the last 30 days; a space is a "Genie Agent"):

```sql
SELECT COUNT(*) AS total,
       SUM(CASE WHEN active_30d = 1 THEN 1 ELSE 0 END) AS active_30d
FROM (
  SELECT request_params.space_id AS space_id,
         MAX(CASE WHEN lower(action_name) = 'trashspace' THEN 1 ELSE 0 END) AS trashed,
         MAX(CASE WHEN event_date >= current_date() - INTERVAL 30 DAYS THEN 1 ELSE 0 END) AS active_30d
  FROM system.access.audit
  WHERE service_name = 'aibiGenie'
    AND request_params.space_id IS NOT NULL
    AND request_params.space_id <> 'new'
    AND event_date >= current_date() - INTERVAL 30 DAYS
  GROUP BY request_params.space_id
) WHERE trashed = 0;
```

If `system.access.audit` is unreadable, mark the pillar **unavailable** (reason:
insufficient permission — the identity needs `SELECT` on `system.access.audit`).

**Score** (start at 0):
- `+40` if `total > 0`
- `+40` if `active_30d > 0`
- `+20` if `total > 0 AND active_30d / total >= 0.3`

**Signals to display:** Genie Agents (`total`), Active agents (30d) (`active_30d`).

**Note to include:** curation quality (instructions, example/verified SQL,
benchmarks) isn't visible in the audit log; recommend the Genie Agent Quality
Workshop to assess and lift it.

**Gaps:**
- `total == 0` → "No Genie Agents found in the audit log — create a curated Genie Agent as the entry point to natural-language analytics."
- else if `active_30d == 0` → "`{total}` Genie Agent(s) exist but none were active in the last 30 days — drive adoption or retire stale agents."

**Drill-down — Genie Agents by audit activity** (top agents by event volume, bounded to 200). The `names` CTE resolves each space's latest display name over a wider 90-day window (names only appear on create/update events, not query events). On a very large or shared metastore this scan can be slow — keep the `LIMIT` and the 90-day bound. Add a `workspace` column (join `system.access.workspaces_latest` on `workspace_id`) when more than one workspace is in scope:

```sql
WITH names AS (
  SELECT request_params.space_id AS space_id,
         max_by(request_params.display_name, event_time) AS space_name
  FROM system.access.audit
  WHERE service_name = 'aibiGenie' AND request_params.space_id IS NOT NULL
    AND request_params.display_name IS NOT NULL
    AND event_date >= current_date() - INTERVAL 90 DAYS
  GROUP BY request_params.space_id
)
SELECT COALESCE(nm.space_name, a.space_id) AS agent, a.space_id AS space_id, a.events AS events,
       CASE WHEN a.active_30d = 1 THEN 'Yes' ELSE 'No' END AS active_30d
FROM (
  SELECT request_params.space_id AS space_id, COUNT(*) AS events,
         MAX(CASE WHEN lower(action_name) = 'trashspace' THEN 1 ELSE 0 END) AS trashed,
         MAX(CASE WHEN event_date >= current_date() - INTERVAL 30 DAYS THEN 1 ELSE 0 END) AS active_30d
  FROM system.access.audit
  WHERE service_name = 'aibiGenie' AND request_params.space_id IS NOT NULL
    AND request_params.space_id <> 'new'
    AND event_date >= current_date() - INTERVAL 30 DAYS
  GROUP BY request_params.space_id
) a LEFT JOIN names nm ON a.space_id = nm.space_id
WHERE a.trashed = 0 ORDER BY a.events DESC LIMIT 200;
```

---

## 6. Domains & Stewardship — weight 10

**Try the native UC Domains API first** (only if you can make an authenticated
REST GET): `GET {host}/api/2.1/unity-catalog/data-domains` (fall back to
`/api/2.0/data-domains`). Count `data_domains` / `domains` / `data`. Native UC
Domains is a gated preview, so this endpoint is usually **absent (404)** — that's
expected; drop to the governed-tag proxy below, which is the normal path.

- If it returns a count `native`:
  **Score** = `40 + 60 * min(native, 5)/5` when `native > 0`, else `0`.
  Signal: Domains (native) = `native`.
  Gap when `native == 0`: "No domains defined; organize assets into business-aligned domains with stewards."
  (Skip the tag proxy — you're done.)

**Otherwise use the governed-tag proxy** (pure SQL). Tag-name key sets
(match case-insensitively):
- domain keys: `domain, data_domain, business_domain, subject_area, data_product`
- steward keys: `owner, data_owner, steward, data_steward`
- cert keys: `system.certification_status, certification_status` (value `certified`)

```sql
-- distinct domains + domain-tag assignments (union table + schema tags)
SELECT COUNT(DISTINCT tag_value) AS distinct_domains, COUNT(*) AS assignments FROM (
  SELECT tag_value FROM <TABLE_TAGS>  WHERE lower(tag_name) IN (<domain_keys>)
  UNION ALL
  SELECT tag_value FROM <SCHEMA_TAGS> WHERE lower(tag_name) IN (<domain_keys>)
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
SELECT COUNT(DISTINCT concat_ws('.', catalog_name, schema_name, table_name)) AS domain_tagged
FROM <TABLE_TAGS> WHERE lower(tag_name) IN (<domain_keys>);
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

**Score (tag proxy)** (start at 0):
- if `distinct_domains > 0`: `+ 40 + 40 * min(distinct_domains, 5)/5`
- `+20` if `stewarded > 0`

**Signals to display:** Distinct domains (via tags) (`distinct_domains`),
Domain-tagged assets (`assignments`), Stewarded assets (`stewarded`), Certified
assets (`certified`); plus Tables tagged (`pct_tagged`%), Tables in a domain
(`pct_in_domain`%) when `total_tables > 0`; and Top accessed certified
(`top_certified` / `top_accessed`) when available.

**Note to include:** assessed via governed tags because native UC Domains is a
gated preview; a self-assessment can capture domain-design maturity tags can't show.

**Gaps:**
- `distinct_domains == 0` → "No domain-style governed tags found (e.g. a `domain` tag). Organize assets into business-aligned domains."
- `stewarded == 0` → "No stewardship tags (owner/steward) found; assign a named steward per domain."
- `certified == 0` → "No certified assets found — certify canonical gold tables so users (and Genie) know which to trust."
- `total_tables > 0 AND pct_tagged < 50` → "Only `{pct_tagged}`% of tables carry any UC governed tag — tag eligible assets (PII, domain, certification) to power governed discovery."
- `total_tables > 0 AND pct_in_domain < 50` → "Only `{pct_in_domain}`% of tables are assigned to a domain — apply domain tags so assets roll up to business-aligned domains."
- `top_accessed > 0 AND top_certified < top_accessed` → "Only `{top_certified}` of your top `{top_accessed}` most-accessed resources are certified — certify high-traffic tables so Genie/ontology can trust your busiest data."

**Drill-down — Governance tags by schema** (which schemas lack domain / steward / certification tags; most-covered first). Tag-proxy path only — the native-API path returns just a count, no breakdown:

```sql
SELECT catalog_name AS catalog, schema_name AS schema,
  COUNT(DISTINCT CASE WHEN lower(tag_name) IN (<domain_keys>)
        THEN concat_ws('.', catalog_name, schema_name, table_name) END) AS domain_tagged,
  COUNT(DISTINCT CASE WHEN lower(tag_name) IN (<steward_keys>)
        THEN concat_ws('.', catalog_name, schema_name, table_name) END) AS stewarded,
  COUNT(DISTINCT CASE WHEN lower(tag_name) IN (<cert_keys>) AND lower(tag_value) = 'certified'
        THEN concat_ws('.', catalog_name, schema_name, table_name) END) AS certified
FROM <TABLE_TAGS> GROUP BY catalog_name, schema_name ORDER BY domain_tagged DESC LIMIT 500;
```

---

## 7. Adoption & Activity — weight 5

**Signals** (both best-effort against system tables):

```sql
SELECT COUNT(DISTINCT user_identity.email) AS active_users
FROM system.access.audit
WHERE event_date >= current_date() - INTERVAL 30 DAYS;

SELECT COUNT(*) AS queries_30d
FROM system.query.history
WHERE start_time >= current_timestamp() - INTERVAL 30 DAYS;
```

If **both** reads fail, mark the pillar **unavailable** (system.access /
system.query not enabled or not granted). If either returns, score with what you have.

**Score:** `band(active_users) + (50 if queries_30d > 0 else 0)`, where `band`
tiers the user count so day-to-day drift rarely moves the score:

| active_users | band |
|---|---|
| `<= 0` | 0 |
| `1–4` | 20 |
| `5–19` | 35 |
| `20–49` | 45 |
| `>= 50` | 50 |

**Signals to display:** Active users (30d) (`active_users`), Queries (30d)
(`queries_30d`) — whichever returned.

**Gaps:** none (adoption is informational; it contributes to the score but
doesn't emit its own next-step gaps).

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
