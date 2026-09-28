# Genie Ontology Readiness — Demo Seed

Idempotent scripts that provision a **synthetic, governed star schema** so the
[`genie-ontology-readiness`](../../README.md) app produces a **full, representative
readiness report across all 7 pillars**. The seed reads nothing customer-specific;
it populates an existing catalog (`ontology_demo`) end-to-end: managed Delta tables
with real rows, table/column comments, UC governed tags (`subject_area` / `steward` /
`certification_status`), PK/FK (RELY) constraints, UC metric views, a curated Genie
space, and query activity.

Everything is **safe to re-run** (`CREATE ... IF NOT EXISTS`, guarded `INSERT`s,
`CREATE OR REPLACE`, drop-then-add constraints, `SET TAGS` overwrite). No secrets
are stored — auth comes from your Databricks CLI profile.

> The design is **grounded in what the app actually measures** — see
> `app/server/assessment/probes.py` and `app/server/pillars.py`. Each seed step is
> annotated with the pillar and exact signal it drives.

---

## Prerequisites

- Databricks CLI authenticated profile with **write access to the target catalog**
  (USE CATALOG + CREATE SCHEMA). The catalog must **already exist** — the seed does
  **not** run `CREATE CATALOG` (that needs metastore admin and isn't required).
- A **running serverless SQL warehouse** (DBR **17.2+** for metric-view YAML v1.1).
- Python 3.11 with `pip install "databricks-sdk>=0.36.0"`.

### This deployment (defaults baked into `_dbx.py`)

| Setting | Value |
|---|---|
| CLI profile | `e2-demo-field-eng` |
| Workspace | `https://e2-demo-field-eng.cloud.databricks.com` |
| SQL Warehouse ID | `abacc2038735a065` (`akunashko-demos-DO-NOT-DELETE`, 2X-Small serverless) |
| Catalog (pre-existing) | `ontology_demo` |

Override any of these with `--profile`, `--warehouse-id`, `--catalog` (or the env
vars `DATABRICKS_CLI_PROFILE` / `DATABRICKS_CONFIG_PROFILE`, `DATABRICKS_WAREHOUSE_ID`,
`GOR_DEMO_CATALOG`).

---

## Run it

```bash
cd scripts/demo_seed
pip install "databricks-sdk>=0.36.0"
export DATABRICKS_CLI_PROFILE=e2-demo-field-eng
export DATABRICKS_WAREHOUSE_ID=abacc2038735a065

# 1. schemas + tables + data + comments + tags + PK/FK + metric views + grants
python3 seed.py                      # idempotent; prints a per-pillar verification summary

# 2. query activity (Adoption pillar; system tables lag a few hours)
python3 generate_activity.py --rounds 3

# 3. create + curate + exercise a Genie space (Genie Agents pillar; audit lags)
python3 genie_setup.py               # find-or-create by title (idempotent) + curate + ask
# or, to curate/ask a specific space:  python3 genie_setup.py --space-id <SPACE_ID>
```

Run a single step with `python3 seed.py --only 07_metric_views`.

---

## What gets seeded (files run in order)

| File | Creates | Pillar(s) driven |
|---|---|---|
| `sql/01_catalog_schemas.sql` | 5 schemas under the existing catalog (`reference`, `sales`, `finance`, `operations`, `marts`) | UC Foundation |
| `sql/02_tables.sql` | 11 managed Delta tables (star schema) with NOT NULL keys | UC Foundation, Relationships |
| `sql/03_load_data.sql` | deterministic synthetic rows + 2 gold summary tables | UC Foundation, Adoption |
| `sql/04_comments.sql` | table comments (13/13) + column comments (~90%) | Metadata Richness |
| `sql/05_tags.sql` | `subject_area`, `steward`, `certification_status` tags + column tags | Metadata, Domains & Stewardship |
| `sql/06_constraints.sql` | 11 PRIMARY KEY + 15 FOREIGN KEY (RELY) constraints | Relationships & Modeling |
| `sql/07_metric_views.sql` | 7 described UC metric views in `marts` | Metrics |
| `sql/08_grants.sql` | read grants to `account users` | (enables the app to scan) |
| `genie_setup.py` | curated Genie space over sales/finance/ops + asks questions | Genie Agents |
| `generate_activity.py` | batch of varied queries | Adoption & Activity |

### Star schema

```
reference.dim_date (date_key PK) ── reference.dim_geography (geo_key PK)
sales.dim_customer (customer_key PK, geo_key FK)
sales.dim_product  (product_key PK)
sales.fact_orders  (order_key PK; customer_key/product_key/date_key FK)
finance.dim_account (account_key PK)
finance.fact_payments (payment_key PK; customer_key/account_key/date_key FK)
finance.fact_gl_entries (entry_key PK; account_key/date_key FK)
operations.dim_warehouse (warehouse_key PK)
operations.fact_shipments (shipment_key PK; order_key/warehouse_key/date_key FK)
operations.fact_inventory (inventory_key PK; product_key/warehouse_key/date_key FK)
marts.gold_daily_sales, marts.gold_customer_360   (gold summaries)
marts.mv_*   (7 metric views)
```

---

## Running the readiness app against the seed

The app scans the **whole metastore** by default (when `system.information_schema`
is readable). To see the *representative* report for this seed — undiluted by other
catalogs — **scope the assessment to `ontology_demo`**:

```bash
export ASSESS_CATALOGS="ontology_demo"   # before post_deploy.py / app start
```

- **Catalog-scoped pillars** (UC Foundation, Metadata, Relationships, Metrics,
  Domains) then reflect only the seeded catalog.
- **Workspace-wide pillars** (Genie Agents, Adoption) read system tables across the
  whole workspace, so the seeded Genie space and query activity **add to** whatever
  else exists in the workspace.

---

## Verification — pillar → signal → live result → expected score

Verified live on **2026-09-28** against `ontology_demo` (profile `e2-demo-field-eng`,
warehouse `abacc2038735a065`). Scores follow the probe formulas in
`app/server/assessment/probes.py` + `scoring.py`.

| # | Pillar | Seeded signal | Live verification (measured) | Expected |
|---|---|---|---|---|
| 1 | **UC Foundation** | catalog + 5 schemas + managed tables | **6 schemas** (5 seeded + `default`), **13 managed tables** | **~85 / L4** (+40 catalog, +30 tables>0, +15 schemas≥5; no +15 because <50 tables → realistic "broaden footprint" gap) |
| 2 | **Metadata Richness** | comments + governed tags | **tables 100%**, **columns 89.7%** commented; **13 tagged tables** | **~95 / L4** (0.5·table% + 0.5·col%) |
| 3 | **Relationships & Modeling** | PK/FK (RELY) + gold layer | **11 PK**, **15 FK**, gold `dim_`/`fact_`/`gold_` present | **100 / L4** (+50 gold, +35 FK, +15 PK) |
| 4 | **Metrics** | described metric views | **7 metric views, 100% described**, all queryable via `MEASURE()` | **~88 / L4** (30 + 40·7/10 + 30·100%) |
| 5 | **Genie Agents** | curated space, asked & answered | space **`01f1bb177b4a150abc62ea122990b0b1`** created + curated (11 tables, instructions, 5 sample Qs, 3 verified SQL); **5/5 questions answered COMPLETED** | **100 / L4** once audit lands (+40 exists, +40 active, +20 ratio). **Lags — see caveats.** |
| 6 | **Domains & Stewardship** | domain/steward/cert tags (tag proxy) | **5 distinct domains** (analytics/finance/operations/sales/shared), **13 stewarded**, **6 certified** | **100 / L4** via tag proxy (native Domains API is off here → tag proxy; +40+40 domains, +20 steward) |
| 7 | **Adoption & Activity** | real query activity | **51 queries executed OK** across tables + metric views | **~70 / L3** once system tables land (1 active-user band + queries>0). **Lags.** |

**Overall:** ~**93 / "Ontology-ready"** once the Genie + Adoption system-table
signals land; ~**73 / "Curated and validating"** in the interim (those two pillars
read 0 until `system.access.audit` / `system.query.history` ingestion catches up).

---

## Genie space

`genie_setup.py` is **idempotent**: it first looks up an existing space by its stable
title (`Ontology Demo — Sales, Finance & Operations`) via `w.genie.list_spaces`; if
found it re-curates that space with `w.genie.update_space`, otherwise it creates one
with `w.genie.create_space`. Either way the `serialized_space` is fully curated: 11
data-source tables (with descriptions), general instructions (scope + revenue
definitions), 5 sample questions, and 3 verified example SQL queries. It then asks the
5 sample questions, treats **only `COMPLETED` as answered**, and exits non-zero if any
question fails to complete. Repeated runs converge on this single space rather than
creating duplicates.

- **Live space id:** `01f1bb177b4a150abc62ea122990b0b1`
- **URL:** `https://e2-demo-field-eng.cloud.databricks.com/genie/rooms/01f1bb177b4a150abc62ea122990b0b1`

> The app counts the Genie pillar from `system.access.audit` (aibiGenie) only — it
> does not read space curation. Curation depth is captured by the app's
> self-assessment / "Genie Agent Quality Workshop" accelerator.

### Manual fallback (only if `create_space` fails on your workspace)

1. **New → Genie space**; pick the SQL warehouse.
2. Add the `ontology_demo` sales/finance/operations facts + dims (and optionally the
   `marts.mv_*` metric views).
3. Paste `INSTRUCTIONS` (Settings → General instructions) and add the
   `SAMPLE_QUESTIONS` from `genie_setup.py`; add a couple of example SQLs.
4. Copy the space id from the URL and run `python3 genie_setup.py --space-id <id>`.

---

## UC Domains (native) — unavailable on this workspace

The native **Unity Catalog Domains** API is a gated preview and is **not enabled**
here. Both create attempts were made and captured:

```
POST /api/2.1/unity-catalog/data-domains  ->  Error: No API found for 'POST /unity-catalog/data-domains'
POST /api/2.0/data-domains                ->  Error: Not Found
```

**Fallback (what the app actually does):** `probe_domains` calls the native Domains
API first; on any non-200 it falls back to the **governed-tag proxy** — which this
seed fully populates (`subject_area` domains, `steward`, `certification_status`).
No manual step is required; the Domains & Stewardship pillar scores from the tags.
If your workspace *does* have native Domains, create one domain per subject area in
**Catalog Explorer → Domains**, assign the schemas and set a steward; the probe will
then prefer the native count.

---

## Caveats (read before reviewing the report)

- **System-table lag.** `system.access.audit`, `system.query.history` and
  `system.access.table_lineage` ingest on a delay (often minutes, up to a few
  hours). The **Genie Agents** and **Adoption** pillars — and the "top-accessed /
  certified" signal in Domains — may read **0 / not-available immediately after
  seeding** and rise once ingestion catches up. Re-run the assessment later.
- **Scope to the catalog.** Without `ASSESS_CATALOGS=ontology_demo`, the
  metadata/coverage percentages are computed across every catalog in the workspace
  and will not reflect this clean seed.
- **Tag policies.** Some metastores enforce a UC *tag policy* restricting allowed
  values for certain governed tag keys. On this workspace `domain` / `data_domain` /
  `business_domain` are policy-constrained, so the seed uses the unconstrained
  domain-style key **`subject_area`** (also recognised by the probe). Adjust
  `sql/05_tags.sql` if your metastore constrains `subject_area`.
- **Metric views need DBR 17.2+** for YAML v1.1. Older runtimes fail step 07; the
  rest of the seed still applies.
- **`account users` grant.** `08_grants.sql` opens read access to all account users
  so the app (OBO or SP) can scan the catalog. Narrow to a specific group if broad
  read is undesirable.
- **Idempotency.** Re-running rebuilds gold tables and re-applies comments/tags/
  constraints; guarded inserts never duplicate fact rows.
```
