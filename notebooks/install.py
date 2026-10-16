# Databricks notebook source
# MAGIC %md
# MAGIC # Genie Ontology Readiness — Notebook Installer
# MAGIC
# MAGIC Installs the Genie Ontology Readiness app into **this workspace** without a local
# MAGIC terminal, Node.js, or the Databricks CLI. It does the same work as the
# MAGIC `bundle deploy` + `scripts/post_deploy.py` flow in the README:
# MAGIC
# MAGIC 1. Creates (or reuses) the Databricks App with the SQL warehouse resource and the `sql` user scope.
# MAGIC 2. Stages the `app/` source into your workspace folder and renders `app.yml`.
# MAGIC 3. Grants the app service principal read access (system schemas + assessed catalogs).
# MAGIC 4. Optionally provisions per-user history on an existing Lakebase instance.
# MAGIC 5. Deploys the app. The **frontend is built by the Databricks Apps runtime** (`npm ci && npm run build`), so nothing is built locally.
# MAGIC
# MAGIC **How to use**
# MAGIC 1. Clone this repo into the workspace as a **Git folder** (Workspace → Create → Git folder), ideally at the latest [release tag](https://github.com/databricks-solutions/genie-ontology-readiness/releases).
# MAGIC 2. Open this notebook from the Git folder and attach it to **Serverless** compute.
# MAGIC 3. Fill in the widgets at the top (only `warehouse_id` matters; it defaults to the first warehouse you can see) and click **Run all**.
# MAGIC
# MAGIC **Prerequisites**
# MAGIC - [ ] **Databricks Apps** is enabled in this workspace.
# MAGIC - [ ] A SQL warehouse you have `CAN USE` on.
# MAGIC - [ ] To grant the app service principal read access you must be able to `GRANT` on the `system` schemas and the assessed catalogs (metastore admin, or catalog owner). Failed grants are reported, not fatal; see the README "Permissions required" table.
# MAGIC - [ ] Optional: **user authorization** (on-behalf-of-user, Public Preview) enabled by a workspace admin, so assessments run as the viewing user.
# MAGIC - [ ] Optional: an existing **Lakebase** instance for per-user assessment/plan history.
# MAGIC
# MAGIC Re-running the notebook is safe: it reuses the app, re-stages the source, re-applies grants and redeploys.

# COMMAND ----------

# MAGIC %pip install --quiet "databricks-sdk~=0.138.0" "asyncpg~=0.31.0"

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

dbutils.widgets.text("app_name", "genie-ontology-readiness", "App name")
dbutils.widgets.text("warehouse_id", "", "SQL warehouse ID (empty = first available)")
dbutils.widgets.text("assess_catalogs", "", "Catalogs to assess (comma-separated, empty = all)")
dbutils.widgets.text("genie_space_id", "", "Genie Agent ID for the answer test (optional)")
dbutils.widgets.text("brand_name", "Databricks", "Brand name")
dbutils.widgets.dropdown("force_sp", "false", ["false", "true"], "SP-only mode (FORCE_SP)")
dbutils.widgets.text("lakebase_instance_name", "", "Existing Lakebase instance (optional)")
dbutils.widgets.text("lakebase_database", "ontology_readiness", "Lakebase database")

# COMMAND ----------

import re
import shutil
import sys
from datetime import timedelta
from pathlib import Path

from databricks.sdk import WorkspaceClient
from databricks.sdk.service import apps as apps_svc


def find_repo_root(start: Path) -> Path:
    for candidate in [start, *start.parents]:
        if (candidate / "databricks.yml").exists() and (candidate / "app" / "app.yml.template").exists():
            return candidate
    raise RuntimeError(
        "Could not find the repo root (databricks.yml + app/app.yml.template). "
        "Run this notebook from inside the cloned genie-ontology-readiness Git folder."
    )


REPO_ROOT = find_repo_root(Path.cwd())
APP_SRC = REPO_ROOT / "app"

# Reuse the app's version resolver so deploy-time API calls carry the same
# User-Agent as the running app. Workspace Git folders have no `git` checkout,
# so this usually resolves to the semver-valid fallback.
sys.dont_write_bytecode = True  # Git folders reject __pycache__ writes
sys.path.insert(0, str(APP_SRC))
from server.user_agent import PRODUCT_NAME, git_version, resolve_version  # noqa: E402

cfg = {name: dbutils.widgets.get(name).strip() for name in (
    "app_name", "warehouse_id", "assess_catalogs", "genie_space_id",
    "brand_name", "force_sp", "lakebase_instance_name", "lakebase_database",
)}
if not re.fullmatch(r"[a-z0-9-]{2,30}", cfg["app_name"]):
    raise ValueError("app_name must be 2-30 characters of lowercase letters, numbers and dashes.")

w = WorkspaceClient(product=PRODUCT_NAME, product_version=resolve_version())
me = w.current_user.me().user_name
STAGE_DIR = f"/Workspace/Users/{me}/.genie-ontology-readiness/{cfg['app_name']}"


def step(message: str) -> None:
    print(f"\n==> {message}", flush=True)


print(f"Repo root:    {REPO_ROOT}")
print(f"Deploying as: {me}")
print(f"App name:     {cfg['app_name']}")
print(f"Staging dir:  {STAGE_DIR}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Resolve the SQL warehouse and create (or reuse) the app

# COMMAND ----------

if not cfg["warehouse_id"]:
    warehouses = list(w.warehouses.list())
    if not warehouses:
        raise RuntimeError("No SQL warehouse found. Create one (or set the warehouse_id widget) and re-run.")
    cfg["warehouse_id"] = warehouses[0].id
    print(f"warehouse_id not set; using '{warehouses[0].name}' ({warehouses[0].id})")
w.warehouses.get(cfg["warehouse_id"])  # fail fast on a wrong id / missing CAN USE

WAREHOUSE_RESOURCE = apps_svc.AppResource(
    name="sql-warehouse",
    sql_warehouse=apps_svc.AppResourceSqlWarehouse(
        id=cfg["warehouse_id"],
        permission=apps_svc.AppResourceSqlWarehouseSqlWarehousePermission.CAN_USE,
    ),
)
APP_DESCRIPTION = "Genie Ontology Readiness — assess, learn, and plan"


def get_app_or_none(name: str):
    try:
        return w.apps.get(name)
    except Exception as e:
        if "does not exist" in str(e).lower() or "not found" in str(e).lower():
            return None
        raise


def set_app_resources(extra: list | None = None) -> apps_svc.App:
    """Point the app at the warehouse (+ any extra resources) and (re)assert the
    `sql` user scope. Other existing resources are preserved."""
    current = w.apps.get(cfg["app_name"])
    keep_names = {"sql-warehouse", *(r.name for r in (extra or []))}
    resources = [r for r in (current.resources or []) if r.name not in keep_names]
    resources += [WAREHOUSE_RESOURCE, *(extra or [])]
    return w.apps.update(
        name=cfg["app_name"],
        app=apps_svc.App(
            name=cfg["app_name"],
            description=current.description or APP_DESCRIPTION,
            resources=resources,
            user_api_scopes=["sql"],
        ),
    )


step(f"Creating or reusing app '{cfg['app_name']}'")
app = get_app_or_none(cfg["app_name"])
if app is None:
    app = w.apps.create_and_wait(
        app=apps_svc.App(
            name=cfg["app_name"],
            description=APP_DESCRIPTION,
            resources=[WAREHOUSE_RESOURCE],
            user_api_scopes=["sql"],
        ),
    )
    print("  created")
else:
    print("  exists, reusing it")
app = set_app_resources()
SP_CLIENT_ID = app.service_principal_client_id
if not SP_CLIENT_ID:
    raise RuntimeError("The app has no service principal yet. Wait a minute and re-run.")
print(f"  app service principal: {SP_CLIENT_ID}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Grant the app service principal read access
# MAGIC
# MAGIC Same grants as `scripts/setup_app_permissions.py`. A failed grant (for example
# MAGIC because you are not a metastore admin) is reported and the corresponding
# MAGIC pillar degrades to "not available"; an admin can apply it later.

# COMMAND ----------

_INTERNAL_CATALOGS = {"system", "__databricks_internal", "samples", "information_schema"}


def _ident(name: str) -> str:
    """Back-quote a SQL identifier, doubling any embedded backtick (CWE-89)."""
    return "`" + str(name).replace("`", "``") + "`"


def run_sql(statement: str) -> str | None:
    """Run a statement on the warehouse; return None on success or the error."""
    try:
        res = w.statement_execution.execute_statement(
            warehouse_id=cfg["warehouse_id"], statement=statement, wait_timeout="50s"
        )
        if res.status.state.value == "SUCCEEDED":
            return None
        return res.status.error.message if res.status.error else res.status.state.value
    except Exception as e:
        return str(e)


def grant(statement: str) -> bool:
    error = run_sql(statement)
    print(f"  {'OK  ' if error is None else 'FAIL'} {statement}" + (f" — {error[:150]}" if error else ""))
    return error is None


step("Granting read access on system schemas")
sp = _ident(SP_CLIENT_ID)
grant_results = [grant(f"GRANT USE CATALOG ON CATALOG system TO {sp}")]
for schema in ("information_schema", "access", "query"):
    grant_results.append(grant(f"GRANT USE SCHEMA ON SCHEMA system.{schema} TO {sp}"))
    grant_results.append(grant(f"GRANT SELECT ON SCHEMA system.{schema} TO {sp}"))

step("Granting read access on assessed catalogs")
catalogs = [c.strip() for c in cfg["assess_catalogs"].split(",") if c.strip()]
if not catalogs:
    catalogs = [
        c.name for c in w.catalogs.list()
        if c.name and c.name not in _INTERNAL_CATALOGS and not c.name.startswith("__")
    ]
    print(f"  discovered {len(catalogs)} non-system catalogs")
for cat in catalogs:
    for privilege in ("USE CATALOG", "USE SCHEMA", "SELECT"):
        grant_results.append(grant(f"GRANT {privilege} ON CATALOG {_ident(cat)} TO {sp}"))

failed_grants = grant_results.count(False)
print(f"\n  {len(grant_results) - failed_grants} grants applied, {failed_grants} failed")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. (Optional) Per-user history on Lakebase
# MAGIC
# MAGIC Runs only when `lakebase_instance_name` is set. Creates a dedicated database on
# MAGIC that **existing** instance, attaches it to the app as the `lakebase-db`
# MAGIC resource, and grants the app service principal on it (mirrors
# MAGIC `post_deploy.py`).

# COMMAND ----------

import asyncio
import ssl
import threading

LAKEBASE = {"host": "", "user": ""}


def run_async(coro):
    """asyncio.run() in a worker thread (the notebook already runs an event loop)."""
    out = {}

    def _target():
        try:
            out["result"] = asyncio.run(coro)
        except BaseException as e:  # surfaced in the caller's thread
            out["error"] = e

    thread = threading.Thread(target=_target)
    thread.start()
    thread.join()
    if "error" in out:
        raise out["error"]
    return out.get("result")


async def lakebase_exec(host: str, database: str, statements: list[str]) -> None:
    import asyncpg

    token = w.database.generate_database_credential(
        request_id="ontology_notebook_install", instance_names=[cfg["lakebase_instance_name"]]
    ).token
    conn = await asyncpg.connect(
        host=host, port=5432, database=database, user=me, password=token,
        ssl=ssl.create_default_context(),
    )
    try:
        for statement in statements:
            try:
                await conn.execute(statement)
                print(f"  OK   {statement}")
            except Exception as e:
                print(f"  SKIP {statement} — {str(e)[:150]}")
    finally:
        await conn.close()


if cfg["lakebase_instance_name"]:
    instance_name, database = cfg["lakebase_instance_name"], cfg["lakebase_database"]
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", database):
        raise ValueError("lakebase_database must be a lowercase Postgres identifier (letters, digits, _).")
    step(f"Provisioning database '{database}' on Lakebase instance '{instance_name}'")
    instance = w.database.get_database_instance(instance_name)
    if instance.state is None or instance.state.value != "AVAILABLE":
        raise RuntimeError(f"Lakebase instance '{instance_name}' is not AVAILABLE (state={instance.state}).")
    host = instance.read_write_dns
    run_async(lakebase_exec(host, "postgres", [f"CREATE DATABASE {database}"]))

    step("Attaching the lakebase-db resource to the app")
    set_app_resources(extra=[
        apps_svc.AppResource(
            name="lakebase-db",
            database=apps_svc.AppResourceDatabase(
                instance_name=instance_name,
                database_name=database,
                permission=apps_svc.AppResourceDatabaseDatabasePermission.CAN_CONNECT_AND_CREATE,
            ),
        )
    ])

    step("Granting the app service principal on the history database")
    # The resource above creates the SP's Postgres role. Schema + table grants
    # cover tables a previous install (or another user) already created.
    sp_role = '"' + SP_CLIENT_ID.replace('"', '""') + '"'
    run_async(lakebase_exec(host, database, [
        f"GRANT USAGE, CREATE ON SCHEMA public TO {sp_role}",
        f"GRANT ALL ON ALL TABLES IN SCHEMA public TO {sp_role}",
        f"GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO {sp_role}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO {sp_role}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO {sp_role}",
    ]))
    LAKEBASE.update(host=host, user=SP_CLIENT_ID)
else:
    print("lakebase_instance_name is empty; skipping Lakebase (the app runs stateless, no saved history).")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Stage the app source and render `app.yml`

# COMMAND ----------

step(f"Staging app source to {STAGE_DIR}")
shutil.rmtree(STAGE_DIR, ignore_errors=True)
shutil.copytree(
    APP_SRC,
    STAGE_DIR,
    ignore=shutil.ignore_patterns(
        "node_modules", "dist", "__pycache__", "__tests__", ".pytest_cache", "test_*.py", "app.yml",
    ),
)

# The Apps runtime runs `npm install` + `npm run build` when the source root has
# a package.json, so the React frontend is built server-side, not on a laptop.
Path(STAGE_DIR, "package.json").write_text(
    '{\n'
    '  "name": "genie-ontology-readiness-app",\n'
    '  "private": true,\n'
    '  "scripts": {\n'
    '    "build": "cd frontend && npm ci --no-audit --no-fund && npm run build"\n'
    '  }\n'
    '}\n'
)


def set_env(text: str, name: str, value: str) -> str:
    """Set `- name: <name>` / `value: "..."` in the app.yml template (as post_deploy.py)."""
    pattern = rf'(- name: {name}\n\s+value: ")[^"]*(")'
    new_text, n = re.subn(pattern, lambda m: m.group(1) + value.replace('"', '') + m.group(2), text)
    if n == 0:
        print(f"  (warning) could not set {name} in app.yml; leaving template default")
    return new_text


use_lakebase = bool(LAKEBASE["host"])
app_yml = (APP_SRC / "app.yml.template").read_text()
for name, value in {
    "ASSESS_CATALOGS": cfg["assess_catalogs"],
    "GENIE_SPACE_ID": cfg["genie_space_id"],
    "WORKSPACE_ID": str(w.get_workspace_id()),
    "GOR_VERSION": git_version() or "",
    "BRAND_NAME": cfg["brand_name"] or "Databricks",
    "FORCE_SP": cfg["force_sp"],
    "USE_LAKEBASE": "true" if use_lakebase else "false",
    "LAKEBASE_HOST": LAKEBASE["host"],
    "LAKEBASE_USER": LAKEBASE["user"],
    "LAKEBASE_DATABASE": cfg["lakebase_database"],
    "LAKEBASE_INSTANCE_NAME": cfg["lakebase_instance_name"] if use_lakebase else "",
}.items():
    app_yml = set_env(app_yml, name, value)
Path(STAGE_DIR, "app.yml").write_text(app_yml)
print("  staged source and rendered app.yml")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Deploy
# MAGIC
# MAGIC The first deployment takes a few minutes: the Apps runtime installs the Python
# MAGIC requirements and builds the frontend.

# COMMAND ----------

app = w.apps.get(cfg["app_name"])
if app.compute_status is None or app.compute_status.state.value != "ACTIVE":
    step("Starting app compute")
    w.apps.start(cfg["app_name"]).result(timeout=timedelta(minutes=20))

step("Deploying (building the frontend in the Apps runtime)")
deployment = w.apps.deploy_and_wait(
    app_name=cfg["app_name"],
    app_deployment=apps_svc.AppDeployment(
        source_code_path=STAGE_DIR, mode=apps_svc.AppDeploymentMode.SNAPSHOT
    ),
    timeout=timedelta(minutes=20),
)
print(f"  deployment {deployment.deployment_id}: {deployment.status.state.value} — {deployment.status.message}")

app = w.apps.get(cfg["app_name"])
displayHTML(
    f"<h3>Genie Ontology Readiness is deployed</h3>"
    f"<p>Open the app: <a href='{app.url}' target='_blank'>{app.url}</a></p>"
    f"<p>Service principal grants: {len(grant_results) - failed_grants} applied, {failed_grants} failed. "
    f"Per-user history: {'enabled' if use_lakebase else 'disabled'}.</p>"
)
