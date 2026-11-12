# Genie Ontology Readiness — Deployment Instructions

A **customer-deployable** Databricks App that assesses a workspace's maturity for
**Genie Ontology / Unity Catalog Business Semantics**, explains each capability
(technical + business value), recommends best practices, and generates tailored
enablement + adoption plans via the customer's **own Foundation Model API**.

It **reads the existing environment** (Unity Catalog `information_schema`, system
tables, Genie/Domains REST). It does **not** seed demo data. Full-stack app:
**FastAPI** backend + **React/Vite** frontend, deployed with **Databricks Asset
Bundles** + `post_deploy.py`.

> **Product accuracy:** Genie Ontology is the *learned* enterprise context layer
> (gated / Private Preview) built on top of the customer's *governed* UC Business
> Semantics (metric views, Pages, domains — largely GA). The foundation
> **feeds** the ontology. The app never positions the learned layer as GA.

## Prerequisites

- **Databricks workspace** with Unity Catalog (a serverless workspace is recommended).
- **Databricks CLI 0.287+** — `databricks --version`.
- **Authenticated profile**: `databricks auth login --host <workspace-url> --profile <name>`.
- **Node.js 18+** and **Python 3.11** with `pip install "databricks-sdk>=0.36.0"`.
- A **SQL Warehouse** (its `id` is needed for assessment queries).

## Deployment

The default path is the **Databricks Apps UI, deploying from this Git repo**. Create a custom
app, set the Git reference to a release tag and the source code path to `app`, add the resources
`sql-warehouse` and (optionally) `lakebase-db`, then deploy. The steps are in the
[README](./README.md) (**Quick start**).

The app config is one file, `app/app.yaml`. It is committed and holds no workspace values. A Git
deploy uses it as committed. `valueFrom` binds `DATABRICKS_WAREHOUSE_ID` to `sql-warehouse` and
`LAKEBASE_HOST` to `lakebase-db`. `app/package.json` runs the frontend build on deploy.

Two advanced paths write their settings into a copy of `app/app.yaml`, never into the repo:
the notebook installer (`notebooks/install.py`, in a Git folder) and the CLI scripts
(`scripts/post_deploy.py`, with `bundle deploy` for a first install and `INFRA=true`). Both reuse
the deployed app's settings, in this order: the value you set, the deployed value, the release
default. They print a table of settings and sources. They stop before any change if the deployed
config cannot be read, or if the run would turn Lakebase history off or point it at another instance
or database. `RESET_SETTINGS=true` starts fresh. The logic is in `scripts/deploy_settings.py`.

The history tables live in `public` for existing installs, and in schema `genie_ontology_readiness` for a new database (chosen at startup in `app/server/history_schema.py`). A Lakebase error at startup turns history off; the app still runs.
Lakebase history is per user (keyed by the `X-Forwarded-Email` identity Databricks Apps inject) and
survives redeploys. Never run `bundle destroy`, and never drop the history database.

The app reads its own identity from the runtime: `DATABRICKS_CLIENT_ID` is the Lakebase user, and
the `lakebase-db` resource gives the database and instance when the settings do not. The grant SQL
for the app's service principal is served at `GET /api/setup/grants`.

**Cutting a release:** write the version into `app/VERSION` (for example `1.4.0`), commit it, then
tag the commit (`v1.4.0`). A Git deploy has no `.git`, so it reads this file for `GOR_VERSION`.

The environment is a bundle **target** (`-t dev` default, `stg`, `prod`) for the CLI path. Each target
has a fixed app name, pinned in `databricks.yml`. There is no `--var app_name`.

Scores are a deterministic function of the technical findings, so repeated runs on the same
workspace are directly comparable **for a given identity**. Under OBO the findings reflect the
viewing user's grants, so signals gated behind grants a viewer lacks (notably the workspace-wide
Adoption system tables) can read lower for a restricted viewer than for the SP — use
`FORCE_SP=true` when you need a single, viewer-independent workspace-wide score.

## Auth: on-behalf-of-user by default, service-principal fallback

The assessment runs **on-behalf-of-user (OBO)** by default, falling back to the
app **service principal (SP)** whenever OBO isn't available or a specific read
can't succeed as the viewer.

**On-behalf-of-user (OBO):** if the workspace admin enables **user authorization**
(Public Preview) and the app carries the `sql` user scope, Databricks forwards the
viewer's token in `x-forwarded-access-token`. The app then runs **every** signal —
both `information_schema`/tags **and** the system-table signals
(`system.access.audit`, `system.query.history`, `system.access.table_lineage`) — as
the **logged-in user**, so the assessment reflects what that user can see. The
policy lives in `sql_client.execute_sql`:

1. `force_sp=True` → **SP-only override** (no OBO attempt); used for reads the OBO
   token can't do — the Genie REST API (not covered by the `sql` scope) and
   Lakebase credential minting.
2. Otherwise, when a forwarded token is present, the read runs **OBO**; if it
   fails (e.g. the viewer lacks a system-table grant the SP holds) it
   **transparently falls back to the SP** rather than dropping the signal.
3. With no forwarded token (OBO disabled, scheduled/unattended, local dev), the
   read runs as the **SP** directly.

**Deploy-time override:** set `FORCE_SP=true` (env in `app/app.yaml`, or
`export FORCE_SP=true` before `post_deploy.py`) to force **SP-only mode** — the
app never attempts OBO and runs every read as the service principal, even when
user authorization is enabled. Use it to guarantee consistent workspace-wide
system-table signals (e.g. Adoption) independent of the viewer's grants; the SP
must then hold the read grants. Default is `false` (OBO-first with SP fallback).

So the SP still needs the grants below to cover the unattended path and the
fallback; under OBO with a fully-granted viewer the SP need not be granted.

Enabling the scope: `user_api_scopes: [sql]` is declared in `databricks.yml`, but
`bundle deploy` only applies it on app **create** — on an already-created app set it
with `databricks apps update <name> --json '{"name":"<name>","user_api_scopes":["sql"],
"resources":[...]}'` (preserve the warehouse resource). `dashboards.genie` etc. are
NOT declared, so the app never calls the Genie REST API on the user's behalf — the
Genie pillar is counted from `system.access.audit` only. `BROWSE` on a catalog is the
least-privilege grant for metadata-only visibility (viewer or SP).

`scripts/setup_app_permissions.py` (called by `post_deploy.py`) grants the SP:

- `USE CATALOG`/`USE SCHEMA`/`SELECT` on `system.information_schema`, `system.access`, `system.query`
- `USE CATALOG`/`USE SCHEMA`/`SELECT` on each assessed catalog

For an **org-wide** assessment, an account admin can instead grant at the metastore
level. If the SP can't read `system.information_schema`, the app automatically
falls back to reading each accessible catalog's own `information_schema` (only
catalog-level SELECT is needed). Pillars whose data the SP can't read degrade
gracefully to "not available" plus a self-assessment fallback — they never crash
the app.

**Genie Agents (pillar 5):** Genie Agents have their own ACLs. The app needs
`CAN_RUN` on an agent to **list and count** it. Reading an agent's **curation
detail** (instructions, sample questions, example/verified SQL, functions,
benchmarks) comes from the agent's serialized definition, which Databricks gates
behind **`CAN_EDIT`** — so the per-agent curation breakdown is best-effort: the
app assesses whatever agents it can read and reports the rest as "curation not
assessed (needs CAN_EDIT)" rather than as uncurated. Granting an app SP
`CAN_EDIT` across every agent is a heavy ask; for a lighter footprint, grant
`CAN_RUN` (counts only) and rely on the self-assessment for curation depth, or
have an editor run the deep pass.

## Architecture

```
app/
  app.py                     — FastAPI entry (port from DATABRICKS_APP_PORT)
  app/app.yaml                — committed app config (Git deploy); CLI and notebook render a copy
  server/
    config.py                — SP/local auth, host resolution, env config
    sql_client.py            — Statement Execution API
    genie_client.py          — Genie Conversation API (answer-quality test)
    lakebase_client.py       — optional snapshot persistence pool
    snapshots.py             — optional assessment history (Lakebase)
    pillars.py               — canonical 7 readiness pillars + maturity model
    signal_spec.py           — per-pillar signal SQL/thresholds/gap text shared with the skill
    assessment/
      probes.py              — read-only SQL/REST probes (graceful degradation)
      scoring.py             — combine technical + self-assessment → scorecard
    content/library.py       — embedded technical+business enablement content
    content/accelerators.py  — runnable accelerators that lift a pillar's score
    content/methodology.py   — build methodology (5 phases); grounds the Plan LLM
    routes/
      config.py  assess.py  content.py  plan.py  chat.py  genie.py  _shared.py
  frontend/                  — React + Vite + Tailwind (4 tabs: Assess/Learn/Plan/Assistant)
notebooks/
  install.py                 — workspace notebook installer (no CLI / Node.js needed)
scripts/
  post_deploy.py             — render app/app.yaml, grant SP, publish
  setup_app_permissions.py   — UC read grants for the app SP
  render_skill.py            — render skills/…/references from the App spec (--check in CI)
skills/genie-ontology-readiness/ — app-free Genie Code skill (generated regions: don't hand-edit)
databricks.yml               — DABs: app + sql-warehouse resource
```

## Readiness pillars (each scored 0-4)

Unity Catalog Foundation · Metadata Richness · Relationships & Modeling ·
Metrics · Genie Agents · Domains & Stewardship ·
Adoption & Activity. The overall score maps to a generic, engagement-neutral
readiness tier (Foundation building, Core foundation in place, Semantics and
Genie forming, Curated and validating, Ontology-ready), and the next-step
guidance is derived from the customer's actual pillar gaps rather than a fixed
per-tier agenda.

## Local development

```bash
# Backend (uses your CLI profile)
cd app
export DATABRICKS_CLI_PROFILE=<your-profile>
export DATABRICKS_WAREHOUSE_ID=<warehouse_id>
pip install -r requirements.txt
python app.py            # http://localhost:8000

# Frontend (proxies /api → :8000)
cd app/frontend && npm install && npm run dev   # http://localhost:3000
```

## Optional: snapshot history (Lakebase)

To trend readiness over time, add a Lakebase instance in `databricks.yml`
(`database_instances` + a `lakebase-db` app resource, see the commented block),
set `USE_LAKEBASE=true` before `post_deploy.py`, and uncomment `PGHOST`/`PGDATABASE`
in `app/app.yaml`. The app creates its snapshot table on startup.

## Branding for a customer

Apply the customer's brand per the `customer-branding/` convention: set
`BRAND_NAME`, and update the `--color-databricks-*` scale in the `@theme` block of
`app/frontend/src/index.css` to the customer's primary/secondary colors
before `npm run build`. Default is Databricks brand.

## Common issues

| Issue | Fix |
|-------|-----|
| Pillars show "not available" | Grant the app SP read access — re-run `scripts/setup_app_permissions.py`. |
| Assessment empty | Confirm `system.information_schema` is enabled and the SP has SELECT on target catalogs. |
| AI plan / chat returns an error | The workspace must have Foundation Model API / model serving enabled; check the selected model exists. |
| Metric views show 0 / "not available" | Metric view metadata depends on UC version; the self-assessment covers the semantic layer regardless. |
| App won't start | Ensure the app binds `DATABRICKS_APP_PORT`; `DATABRICKS_HOST` is scheme-less (handled in `config.py`). |
| Frontend not served | Run `npm run build` so `app/frontend/dist` exists before publishing. |
```
