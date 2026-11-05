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

Then build and deploy:

```bash
cd app/frontend && npm install && npm run build && cd ../..
databricks bundle deploy -t dev --profile <p> --var="warehouse_id=<id>"
DATABRICKS_PROFILE=<p> TARGET=dev WAREHOUSE_ID=<id> python3 scripts/post_deploy.py
```

The `-t <target>` selects the environment and fixes the app name (`dev`, the
default → `genie-ontology-readiness-dev`, `stg` → `…-stg`, `prod` → the bare
`genie-ontology-readiness`). There is no `--var app_name` — the name is pinned
per target so a deploy can never rename or delete another environment's app. Keep
`TARGET` in step 2 matching the `-t` in step 1. **For a production install**, use
`-t prod` (with `TARGET=prod`) to get the unsuffixed `genie-ontology-readiness`.

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

### Run the assessment without the app (Genie Code skill)

If your workspace can't install Databricks Apps (security review, deployment, or
service-principal constraints), use the standalone skill in
[`skills/genie-ontology-readiness/`](skills/genie-ontology-readiness/) instead. The
Genie Code agent runs the same read-only `information_schema` and system-table SQL as
the app, using **your own** Unity Catalog permissions. It produces the same seven-pillar
scorecard, maturity level, readiness stage, and prioritized gaps, with no app, app
service principal, or Lakebase.

There are two ways to install it. **Start with the workspace folder:** it needs no
preview. Publish it as a Unity Catalog skill only if you need UC grants, tags and
audit for sharing, and your workspace can turn on the Beta previews it needs. A UC
skill loads in **Genie One** only: Genie Code reads skills from workspace folders, not
from Unity Catalog.

#### 1. Install it in a workspace skills folder (recommended, no preview needed)

Genie Code loads every skill in `/Workspace/.assistant/skills/` (all users) and
`/Users/<you>/.assistant/skills/` (just you)
([docs](https://docs.databricks.com/aws/en/genie-code/skills)). Genie One's
*Customizations → Skills* lists the same folders. From the repository root:

```bash
P=<your-profile>
# Everyone in the workspace:
databricks workspace import-dir skills/genie-ontology-readiness \
  /Workspace/.assistant/skills/genie-ontology-readiness --overwrite -p $P
# ...or only you:
# databricks workspace import-dir skills/genie-ontology-readiness \
#   "/Users/<you>/.assistant/skills/genie-ontology-readiness" --overwrite -p $P
```

To update it, run the same command with the new release. Access follows the folder's
workspace permissions.

#### 2. Optional: publish it as a Unity Catalog skill (Beta, Genie One only)

A [UC skill](https://docs.databricks.com/aws/en/ai-gateway/govern-skills) is a securable
(`<catalog>.<schema>.genie-ontology-readiness`), so access is managed with grants, tags
and audit like any other UC object. **This path is Beta** and needs two previews
([docs](https://docs.databricks.com/aws/en/genie-one/chat#share-a-skill-with-your-team)):

- **Enhanced Unity AI Gateway**: account console → *Previews* (account admin).
- **Unity Gateway skills in Genie**: workspace → *Previews* (workspace admin,
  [how](https://docs.databricks.com/aws/en/admin/workspace-settings/manage-previews)).

Genie Code doesn't load UC skills
([Genie Code skills docs](https://docs.databricks.com/aws/en/genie-code/skills) cover
workspace folders only), so keep the step 1 install for Genie Code users.

Use a schema in a standard (not foreign) catalog with managed storage. Publishing needs
`USE CATALOG`, `USE SCHEMA` and `CREATE VOLUME`; updating needs `WRITE VOLUME` and
`READ VOLUME`; people who run the skill need `USE CATALOG`, `USE SCHEMA` and
`READ VOLUME`. From the repository root:

```bash
P=<your-profile>; CAT=<catalog>; SCH=<schema>; NAME=genie-ontology-readiness
HOST=$(databricks auth describe -p $P -o json | jq -r .details.host)
TOKEN=$(databricks auth token -p $P | jq -r .access_token)

# a. Create the skill securable (first publish only)
databricks api post "/api/2.1/unity-catalog/skills?parent=schemas/$CAT.$SCH&skill_id=$NAME" \
  --json '{}' -p $P

# b. Upload the bundle (raw bytes, one PUT per file)
for f in SKILL.md references/pillar-signals.md references/scoring-and-output.md; do
  curl -sf -X PUT -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/octet-stream" \
    --data-binary @skills/$NAME/$f "$HOST/api/2.0/fs/files/Skills/$CAT/$SCH/$NAME/$f?overwrite=true"
done

# c. Finalize: the server validates SKILL.md's frontmatter and serves the new bundle
databricks api post "/api/2.1/unity-catalog/skills/$CAT.$SCH.$NAME/finalize" -p $P
```

To **update** a published skill, rerun steps b and c; grants, tags and the owner are
kept. The upload overwrites and adds files but never deletes them, so if a release
removes a file, delete it from `/Skills/$CAT/$SCH/$NAME/` as well.

> `databricks fs cp <dir> dbfs:/Skills/...` also uploads the bundle, but it fails with
> *"Public DBFS root is disabled"* on workspaces that disable the DBFS root. The Files
> API `PUT` above works either way.

You can also publish from the UI instead: install the skill in your workspace folder
(step 1), then in Genie One open *Customizations → Skills*, select the skill, click
**Share**, and pick a catalog and schema under **Publish to**.

**Share it.** Sharing is a UC grant, not a copy: users read the live skill under your
grants and audit. Grant the people who run it:

```sql
GRANT USE CATALOG ON CATALOG <catalog> TO `<group>`;
GRANT USE SCHEMA, READ VOLUME ON SCHEMA <catalog>.<schema> TO `<group>`;
```

Or, in Genie One, open *Customizations → Skills*, select the skill, click **Share**, add
users or groups, and click **Done**. Only the skill's owner, or a principal with
`MANAGE` on it, can change its grants.

**Enable or install it.** In Genie One, *Customizations → Skills* lists shared skills
next to your own (or use **+** → *Skills* → *Browse skills* in a chat). A skill you
published yourself shows an **Enable skill** switch, off by default; turn it on. A
skill a teammate shared with you shows **Install**. Enabled and installed skills also
run in Genie One scheduled tasks. The Genie One UI can't add files to a published skill, so update it
with steps b and c above (or Catalog Explorer).

**Use it from a local coding agent (optional).** The
[Unity Gateway CLI](https://docs.databricks.com/aws/en/agents/uc-skills/use-uc-skills)
downloads the published skill into Claude Code, Codex, Gemini, OpenCode, or Copilot:

```bash
uv tool install git+https://github.com/databricks/unity-gateway
ug configure --agents claude --workspaces https://<workspace-host>
ug skills add --names <catalog>.<schema>.genie-ontology-readiness
```

A download is a point-in-time copy; run `ug skills add` again after each release.

More: [create and share UC skills](https://docs.databricks.com/aws/en/agents/uc-skills/create-share-uc-skills)
· [Skills API reference](https://docs.databricks.com/aws/en/ai-gateway/skill-reference).

After installing or updating by either path, **start a new chat**; chats already open
keep the old version.

#### 3. Run it

1. In Genie Code or Genie One, ask *"Assess this workspace's Genie Ontology
   readiness"* (or type `/genie-ontology-readiness`).
2. You need `USE CATALOG` + `SELECT` on the catalogs to assess. `SELECT` on
   `system.information_schema`, `system.access.audit`, `system.query.history`, and
   `system.access.table_lineage` makes the result richer. A pillar whose reads are
   denied is reported as *unavailable*; it doesn't fail the run.

#### Keeping the skill in sync with the app

The skill and the app share one signal spec: weights, levels, stages, the roll-up
rules, and each migrated pillar's SQL, score formula, and gap text live in
`app/server/pillars.py` and `app/server/signal_spec.py`. The matching sections of the
skill's docs are generated from them. To change a signal, edit the spec, run
`python3 scripts/render_skill.py`, and commit both. `scripts/check.sh` fails if the
skill docs are stale. Pillars not yet migrated to `signal_spec.py` are still
transcribed by hand and must be updated in both places
([#108](https://github.com/databricks-solutions/genie-ontology-readiness/issues/108)).

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
