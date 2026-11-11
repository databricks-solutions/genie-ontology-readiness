# Genie Ontology Readiness

A self-contained Databricks App that helps a customer **prepare for Genie Ontology**.
Deploy it into a workspace and it will:

- **Assess** the live environment and score maturity across the readiness pillars
  Genie Ontology depends on (Unity Catalog, metadata, relationships, metrics /
  metric views, Genie Agents, domains, adoption).
- **Explain** each capability from a **technical** and a **business** standpoint, with
  accurate GA / preview status.
- **Recommend** best practices for both technical enablement and business adoption.
- **Generate** a tailored, sequenced enablement + adoption plan and answer questions
  via an AI assistant — powered by the customer's **own Foundation Model API**.

The assessment is **read-only** and degrades gracefully when a signal isn't available.

> Genie Ontology is the *learned* enterprise context layer (gated preview) built on the
> customer's *governed* UC Business Semantics foundation (largely GA). Preparing for it =
> maturing that foundation. See `CLAUDE.md` for the product framing and full deploy steps.

## Walkthrough

The app has three tabs. Each walkthrough below is a short, sped-up screen capture.

### Assess — the 7-pillar readiness scorecard

Run a read-only assessment that scores your workspace across seven pillars, rolls up to a
0–100 readiness score and maturity stage, and expands each pillar to its signals and gaps.

![Assess tab walkthrough: running the readiness assessment, viewing the overall score and pillar-maturity radar, and expanding a pillar to see its signals and gaps.](assets/assess-cuj.gif)

### Plan — a tailored action plan

Generate a prioritized, tactical action plan from a saved assessment — grounded in your real
scores and gaps and powered by your workspace's own Foundation Model API.

![Plan tab walkthrough: selecting an assessment and generating an AI action plan with prioritized recommendations, a suggested sequence, and example Genie use cases.](assets/plan-cuj.gif)

### Learn — enablement and accelerators

Explore each capability from a technical and a business angle, with best practices, downloadable
guides, and the public Databricks accelerators that raise each pillar's score.

![Learn tab walkthrough: browsing a capability's technical and business value, best practices, accelerators, and the downloadable AI-ready-semantics handbook.](assets/learn-cuj.gif)

## Quick start

Deploy the app from this Git repo in the Databricks Apps UI. You do not need Node.js, npm,
the Databricks CLI or Python on your laptop. The Apps runtime builds the frontend during the
deploy. Docs: [Deploy a Databricks app](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/deploy)
and [Add resources to a Databricks app](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/resources).

### First install

1. **(Optional) Create a Lakebase database for history.** Use an existing Lakebase instance, and
   create a database for this app. Skip this step to run without history.
2. In the workspace, go to **Apps** → **+ Create app** → **Create a custom app**. Enter a name, for
   example `genie-ontology-readiness`. Under **Configure Git**, enter the repo URL
   `https://github.com/databricks-solutions/genie-ontology-readiness.git`. For **Git reference**,
   enter the latest release tag from the
   [Releases page](https://github.com/databricks-solutions/genie-ontology-readiness/releases),
   for example `v1.3.0`.
3. Click **+ Add resource** and add these:
   - **SQL warehouse**: key `sql-warehouse`, permission **Can use**. Choose your warehouse.
   - **Lakebase database** (only if you did step 1): key `lakebase-db`, permission **Can connect
     and create**. Choose the instance and database from step 1.
4. Click **Create**. Then click **Deploy**, select **From Git**, and set **Source code path** to
   `app`. Click **Deploy**. Wait until the deployment finishes. Then check that the app status shows **Running**. A deployment can succeed while the app fails to start, so do not stop at "succeeded".
5. Run the GRANT SQL below once, as a user who can grant on the `system` schemas and on your
   catalogs. Replace `<app-service-principal-id>` with the app's service principal id. The app
   shows this id, and the full SQL, at `/api/setup/grants`. Without these grants, some signals
   show as not available. The app still runs.
6. Optional: if your workspace offers user authorization, enable the `sql` user scope for the app.

```sql
GRANT USE CATALOG ON CATALOG system TO `<app-service-principal-id>`;
GRANT USE SCHEMA ON SCHEMA system.information_schema TO `<app-service-principal-id>`;
GRANT SELECT ON SCHEMA system.information_schema TO `<app-service-principal-id>`;
GRANT USE SCHEMA ON SCHEMA system.access TO `<app-service-principal-id>`;
GRANT SELECT ON SCHEMA system.access TO `<app-service-principal-id>`;
GRANT USE SCHEMA ON SCHEMA system.query TO `<app-service-principal-id>`;
GRANT SELECT ON SCHEMA system.query TO `<app-service-principal-id>`;
-- For each catalog you want assessed, run these three (replace <catalog>):
GRANT USE CATALOG ON CATALOG `<catalog>` TO `<app-service-principal-id>`;
GRANT USE SCHEMA ON CATALOG `<catalog>` TO `<app-service-principal-id>`;
GRANT SELECT ON CATALOG `<catalog>` TO `<app-service-principal-id>`;
```

The defaults work. The app lets users pick the catalogs to assess. To change the catalogs, the
brand, or the Genie space at deploy time, use the [advanced notebook or CLI path](#advanced-automated-install-notebook).

### Upgrade to a new release

Resources, history, and the app itself are not touched. Only the code changes. No Postgres grant step is needed. The app creates its own history schema on a new database. If Lakebase history cannot start, the app still runs without history, and the status still shows **Running**.

1. Open the app. Click the arrow next to **Deploy**, then **Deploy using a different source**.
2. On the **From Git** tab, enter the new release tag, for example `v1.4.0`. Keep **Source code
   path** as `app`.
3. Click **Deploy**. Wait until the deployment finishes. Then check that the app status shows **Running**. A deployment can succeed while the app fails to start.

**Rollback.** Follow the same steps, and enter an older tag. Only releases that contain
`app/app.yaml` can be deployed from Git. For an older release, use the workspace source
(see *Migrate from an older install* below).

**Do not:**

- Delete the app. Upgrade it instead.
- Drop the Lakebase history database.
- Change the `lakebase-db` resource to another database, unless you want a new, empty history.

### Migrate from an older install

Use this if you installed the app before deploy-from-Git was supported. In that case the app was
installed with the notebook or with `bundle deploy` + `post_deploy.py`, and it runs from a
workspace folder. You keep the same app, so you keep its history, its service principal and its
grants.

1. **Write down your settings.** Open the app's source folder. The app overview shows it as the
   source code path. Open `app.yml` in that folder, and note any value you changed from the
   default: `ASSESS_CATALOGS`, `BRAND_NAME`, `GENIE_SPACE_ID`, `FORCE_SP`, `GENIE_ALLOW_SP_FALLBACK`.
   A deploy from Git uses the defaults in the release's `app/app.yaml`. It does not copy these values.
2. **Check the resources.** On the app's configuration page, the app must have a `sql-warehouse`
   resource. If it uses history, it must also have a `lakebase-db` resource that points to the
   database named in `LAKEBASE_DATABASE` in that `app.yml`. Add a missing resource with
   **+ Add resource**, with the same key.
3. **Deploy from Git.** Do the steps in *Upgrade to a new release*. Enter the repo URL
   `https://github.com/databricks-solutions/genie-ontology-readiness.git` if the app asks for it,
   and use a release tag that contains `app/app.yaml`.
4. **Check.** The status shows **Running**, and your saved runs are in the history. The app finds
   its existing tables and keeps using them. You do not need to run the GRANT SQL again.
5. **Settings.** If step 1 showed values that you changed, and you need them, use the
   [notebook](#advanced-automated-install-notebook) or the [CLI](#advanced-terminal-or-ci) path.
   These paths reuse the deployed values. Otherwise the defaults apply.

**Rollback.** Click the arrow next to **Deploy**, then **Deploy using a different source**. Select
the workspace source, and enter the old source folder from step 1. The old folder is not changed
by a deploy from Git.

### Advanced: automated install (notebook)

Use this path to set the catalogs, the brand, the Genie space, or the reset option at deploy time,
or to run the install as a job. It is optional. Steps:

1. In the workspace, click **Create** → **Git folder**. Paste the repo URL. The default branch is
   fine. See [Create and manage Git folders](https://docs.databricks.com/aws/en/repos/git-operations-with-repos).
2. Open `notebooks/install.py` from the Git folder. Attach it to **Serverless** compute.
3. Set the widgets and click **Run all**. The notebook moves the Git folder to the latest release
   first. If it moved, click **Run all** again. Upgrades use the same steps. Leave the widgets empty
   to keep your settings. To pick a version, set the `release` widget, for example `v1.2.0`, or
   `current` to deploy the folder as it is.
4. `reset_settings=true` starts fresh. A job can run the notebook with the widgets as parameters.
   Do not add a schedule that pulls new releases.

The notebook keeps every setting you did not fill in, and it stops before any change if the
deployed app's settings cannot be read, or if the run would turn Lakebase history off or point it
at another database.

### Advanced: terminal or CI

Use this path only if you already work with the Databricks CLI, or if a pipeline deploys the app.
It needs Node.js, the Databricks CLI, and Python 3.9+. It uses the same settings and stops.

```bash
# Stable: the latest release (recommended)
tag=$(gh release view --repo databricks-solutions/genie-ontology-readiness --json tagName -q .tagName)
git clone --branch "$tag" https://github.com/databricks-solutions/genie-ontology-readiness.git
```

First install, from the repo root:

```bash
# 1. Build the frontend
cd app/frontend && npm install && npm run build && cd ../..

# 2. Create the app and its SQL warehouse resource
databricks bundle deploy -t prod --profile <p> --var="warehouse_id=<id>"

# 3. Grant the app read access, set up history, and deploy the app
export DATABRICKS_PROFILE=<p>
export TARGET=prod
export WAREHOUSE_ID=<id>
export USE_LAKEBASE=true                        # optional: per-user history
export LAKEBASE_INSTANCE_NAME=<instance>        # an existing Lakebase instance
export LAKEBASE_DATABASE=ontology_readiness
python3 scripts/post_deploy.py
```

If you use Lakebase, run the upgrade command below once more after the first install. The app's
service principal id is known only after step 2 creates the app.

Upgrade, from the repo root, after you check out the new tag:

```bash
# 1. Get the new release
git fetch --tags origin
git checkout "$(gh release view --repo databricks-solutions/genie-ontology-readiness --json tagName -q .tagName)"

# 2. Build the frontend
cd app/frontend && npm install && npm run build && cd ../..

# 3. Upgrade the app in place
DATABRICKS_PROFILE=<p> TARGET=prod python3 scripts/post_deploy.py
```

- **Change one setting:** set only that variable. An empty value clears it, for example `ASSESS_CATALOGS=`.
- **Start fresh:** `RESET_SETTINGS=true`. Each setting you do not set uses the release default.
- **Infrastructure change** (the release notes say `databricks.yml` changed): `INFRA=true`. This runs
  `bundle deploy`. It needs the bundle state of the first install. If you are not the user who ran
  it, bind the app first with `databricks bundle deployment bind ontology_readiness <app-name> -t prod`.

The `-t <target>` selects the environment and fixes the app name (`dev` → `genie-ontology-readiness-dev`,
`stg` → `…-stg`, `prod` → the bare `genie-ontology-readiness`). There is no `--var app_name`. Never run
`bundle destroy`, and never drop the history database.

### Run the assessment without the app (Genie Code skill)

If your workspace can't install Databricks Apps (security review, deployment, or
service-principal constraints), use the standalone skill in
[`skills/genie-ontology-readiness/`](skills/genie-ontology-readiness/) instead. The
Genie Code agent runs the same read-only `information_schema` and system-table SQL as
the app, using **your own** Unity Catalog permissions. It produces the same seven-pillar
scorecard, maturity level, readiness stage, and prioritized gaps, with no app, app
service principal, or Lakebase.

1. Publish the skill folder (`SKILL.md` + `references/`) as a Unity Catalog skill in a
   catalog/schema you own, or copy it into your workspace skills folder.
2. In Genie Code, ask *"Assess this workspace's Genie Ontology readiness"* (or invoke
   `genie-ontology-readiness`).
3. You need `USE CATALOG` + `SELECT` on the catalogs to assess. `SELECT` on
   `system.information_schema`, `system.access.audit`, `system.query.history`, and
   `system.access.table_lineage` makes the result richer. A pillar whose reads are
   denied is reported as *unavailable*; it doesn't fail the run.

The skill transcribes the app's scoring (`app/server/pillars.py`,
`app/server/assessment/`). Scoring changes must update both together.

## Permissions required

The app reads only **metadata** (`information_schema`, tags) and **system tables**
(`system.access.*`, `system.query.*`) — never your actual table data. Every signal
defaults to **on-behalf-of-user (OBO)** with an automatic **SP fallback**:

- **Interactive** — a person viewing the app. With **on-behalf-of-user (OBO)**
  authorization enabled, **every signal runs as the viewing user** (their own Unity
  Catalog + system-table grants), so the assessment reflects exactly what *you* can
  see. If a given read fails because you lack a grant the app SP holds (system
  tables are the common case), that read **falls back to the app SP** rather than
  dropping the signal.
- **Scheduled / unattended** — snapshot history or any background run with no user
  present (also local dev). With no forwarded token every read runs as the app
  **service principal (SP)**, so the SP must hold the grants below.

A few reads are **always** the SP (they can't run OBO): the Genie REST API (not
covered by the `sql` user scope) and Lakebase credential minting.

### Who needs what

| Assessment area | Reads from | Grant needed (held by the **viewer** under OBO, or by the **SP** when unattended) |
|---|---|---|
| Run any query | SQL warehouse | `CAN USE` on the warehouse |
| UC Foundation · Metadata · Relationships · Metrics · Domains (tags) | catalog / `system.information_schema` | `BROWSE` on each assessed catalog (metadata-only, least privilege) — or `USE CATALOG`+`USE SCHEMA`+`SELECT` |
| "Not in Unity Catalog" coverage | `hive_metastore.information_schema` | read on `hive_metastore` (if legacy access is enabled) |
| Genie Agents (count + activity) | `system.access.audit` (`aibiGenie` events) | `USE`+`SELECT` on `system.access` |
| Adoption & Activity | `system.access.audit`, `system.query.history` | `USE`+`SELECT` on `system.access` and `system.query` |
| Top‑10 most‑accessed + certified | `system.access.table_lineage` + `information_schema.table_tags` | `USE`+`SELECT` on `system.access` + catalog metadata |
| Plan / Assistant (LLM) | Foundation Model API | model serving / FMAPI enabled for the workspace |

> [!NOTE]
> **SP fallback.** Under OBO each signal is attempted **as the viewer first**. If
> that read fails — most commonly because the viewer lacks a grant the app SP holds
> (system tables such as `system.access` / `system.query`) — the app **transparently
> retries the same read as the service principal** instead of dropping the signal.
> This means a signal can appear in the assessment even when the viewer can't read
> it directly, *provided the SP is granted*. If neither the viewer nor the SP holds
> the grant, the signal degrades to "not available." The fallback is per-read and
> lives in `app/server/sql_client.py` (`execute_sql`); the SP-only reads that never
> attempt OBO (Genie REST, Lakebase) are opted out with `force_sp=True`.
>
> **SP-only mode.** To disable OBO entirely at deploy time, set the `FORCE_SP=true`
> env (in `app.yaml`, or `export FORCE_SP=true` before `post_deploy.py`). Every read
> then runs as the app SP regardless of any forwarded viewer token — useful when you
> want consistent, workspace-wide system-table signals (e.g. Adoption) that don't
> vary by who's viewing. The SP must hold the grants below. Default is `false`.

`scripts/setup_app_permissions.py` (run by `post_deploy.py`) applies the SP grants; see
**[CLAUDE.md](./CLAUDE.md)** for the exact statements and the OBO details.

## Stack

FastAPI + React/Vite + Tailwind, deployed as a Databricks App via Databricks
Asset Bundles.

## License

Provided under the **Databricks License** — see [`LICENSE.md`](./LICENSE.md) and
[`NOTICE.md`](./NOTICE.md). Third-party dependencies are subject to their own licenses,
declared in the respective package manifests.

## Support

This project is a Databricks **Field Engineering solutions example**, published as a
demonstration accelerator. It is **not** an official Databricks product and is **not**
covered by any Databricks Support agreement, SLA, or warranty.

- **Provided AS-IS**, with no warranties or conditions of any kind. There are **no SLAs**
  and no commitment to maintenance, bug fixes, or future updates.
- **Community / field-maintained** on a best-effort basis by the owner below — not
  staffed by Databricks Support or Engineering.
- **Not a substitute for official guidance.** Validate GA / preview status against the
  official [Databricks documentation](https://docs.databricks.com/) before making
  decisions.
- To report a security issue, see [`SECURITY.md`](./SECURITY.md).

For questions or issues, **open an issue on this repository** or contact the maintainer:
**Allan Cao** (`allan.cao@databricks.com`). See [`NOTICE.md`](./NOTICE.md) for the full
disclaimer.
