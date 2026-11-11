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

**Get the code.** For a **stable build**, clone the latest tagged
[release](https://github.com/databricks-solutions/genie-ontology-readiness/releases)
— this is the recommended source for customer deployments. For a **staging build**
with the newest, unreleased changes, clone `main` directly.

```bash
# Stable — latest release (recommended)
tag=$(gh release view --repo databricks-solutions/genie-ontology-readiness --json tagName -q .tagName)
git clone --branch "$tag" https://github.com/databricks-solutions/genie-ontology-readiness.git
# (no gh? browse the Releases page above and: git clone --branch <tag> <repo-url>)

# Staging — latest main (newest, unreleased)
git clone https://github.com/databricks-solutions/genie-ontology-readiness.git
```

### First install

Clone the release (see **Get the code** above). Run these steps from the repo root. Use the
same target (`prod` here) for every release. `-t prod` gives the app the name
`genie-ontology-readiness`.

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

If you use Lakebase, run the upgrade command below once more after the first install. The
app's service principal id is known only after step 2 creates the app. The second run
finishes the Lakebase grants.

### Upgrade to a new release

An upgrade keeps your settings and your history. Run these steps from the repo folder you
cloned for the first install.

```bash
# 1. Get the new release
git fetch --tags origin
git checkout "$(gh release view --repo databricks-solutions/genie-ontology-readiness --json tagName -q .tagName)"

# 2. Build the frontend
cd app/frontend && npm install && npm run build && cd ../..

# 3. Upgrade the app in place
DATABRICKS_PROFILE=<p> TARGET=prod python3 scripts/post_deploy.py
```

Use the same `TARGET` as the first install. Do not set `WAREHOUSE_ID` or any other
setting. The script reuses each deployed value. It prints a table. The table shows each
setting and its source: `you set`, `deployed app`, `new release`, `live`, or `default`.

The upgrade does not run `bundle deploy`. It copies `app/` to your own folder,
`/Workspace/Users/<you>/.genie-ontology-readiness/<app>`, and deploys it there. You do not
need the bundle state of whoever ran the first install. You do need to read the deployed
app's source folder, because that is where its settings are. If another user ran the first
install, ask that user or an admin to run the upgrade. If you cannot read the folder, the
script stops before any change.

**What is kept**

- The history database on Lakebase, with past runs and plans. The upgrade does not drop
  it. A schema change only adds columns.
- Every setting you did not set. This includes the warehouse, the catalogs, the Genie space,
  the brand, `FORCE_SP`, `GENIE_ALLOW_SP_FALLBACK`, `MAX_REQUEST_BYTES`, and the Lakebase
  instance and database.
- A key that the new release adds takes the release default.

**Change one setting.** Set only that variable. Set it to an empty value to clear it.

```bash
DATABRICKS_PROFILE=<p> TARGET=prod BRAND_NAME="Acme" python3 scripts/post_deploy.py
DATABRICKS_PROFILE=<p> TARGET=prod ASSESS_CATALOGS= python3 scripts/post_deploy.py
```

**Start fresh.** Set `RESET_SETTINGS=true`. The script then ignores the deployed settings.
Each setting you do not set uses the release default. If you do not set Lakebase again, the app
stops using its history. The old history stays in Lakebase.

```bash
DATABRICKS_PROFILE=<p> TARGET=prod RESET_SETTINGS=true WAREHOUSE_ID=<id> python3 scripts/post_deploy.py
```

**The script stops before it changes anything if:**

- The deployed `app.yml` cannot be read.
- This run would turn Lakebase history off.
- This run would point the app at another Lakebase instance or database.

Fix the setting, or set `RESET_SETTINGS=true` if you want that change on purpose.

**Infrastructure changes.** If the release notes say `databricks.yml` changed, run the
upgrade with `INFRA=true`. This runs `bundle deploy` with the reused warehouse id.

```bash
INFRA=true DATABRICKS_PROFILE=<p> TARGET=prod python3 scripts/post_deploy.py
```

`INFRA=true` needs the bundle state of the first install. If you are not the user who ran
it, bind the app to your bundle first:

```bash
databricks bundle deployment bind ontology_readiness <app-name> -t prod
```

**Roll back.** Check out the previous release tag. Then run the upgrade command again. A
schema change only adds columns, so the previous release can still read the history.

**Do not:**

- Run `bundle destroy`.
- Drop the history database.
- Run `bundle deploy` by hand for an upgrade without `INFRA=true`.

The `-t <target>` selects the environment and fixes the app name (`dev`, the
default → `genie-ontology-readiness-dev`, `stg` → `…-stg`, `prod` → the bare
`genie-ontology-readiness`). There is no `--var app_name`. The name is pinned
per target, so a deploy can never rename or delete another environment's app.

See **[CLAUDE.md](./CLAUDE.md)** for prerequisites, the service-principal grants the
assessment needs, local development, optional Lakebase history, and branding.

### Install from a notebook (no local tooling)

If you'd rather not install Node.js or the Databricks CLI, install the app from a
notebook inside the workspace:

1. In the workspace, **Create → Git folder** and paste this repo's URL (pick the
   latest release tag as the branch for a stable build).
2. Open [`notebooks/install.py`](notebooks/install.py) from the Git folder and attach
   it to **Serverless** compute.
3. Set the widgets (`warehouse_id` defaults to the first warehouse you can see; set
   `lakebase_instance_name` to an existing Lakebase instance for per-user history)
   and click **Run all**.

The notebook creates or reuses the app, applies the same service-principal grants as
`setup_app_permissions.py`, renders `app.yml`, and deploys. The frontend is built by the
Databricks Apps runtime during deployment, so nothing is built on your machine. The
staged source lives in `/Workspace/Users/<you>/.genie-ontology-readiness/<app_name>`.
The app name is a widget (default `genie-ontology-readiness`), so use a different name
than any app you manage with `bundle deploy` to keep the two flows separate.

**Upgrade:** pull the Git folder to the new release tag, leave the widgets empty, and click
**Run all**. The notebook reads the deployed app's settings and keeps them. Fill in one widget
to change one setting. Set `reset_settings` to `true` only to start fresh.

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
> env (in `app.yml`, or `export FORCE_SP=true` before `post_deploy.py`). Every read
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
