"""Post-deploy: render app.yaml, grant the app SP read access, provision Lakebase
(per-user history), and (re)deploy the app.

Two modes (see deploy_settings.choose_mode):

  upgrade  The app already exists and INFRA is not set. Reuses the deployed app's
           settings, syncs app/ to your own staging folder, and runs `apps deploy`.
           It never runs `bundle deploy`, so the original deployer's bundle state
           does not matter. The lakebase-db resource is changed only when it is
           missing or differs from the settings.

  bundle   A first install, or INFRA=true (for infrastructure changes such as a
           new databricks.yml). Runs `bundle deploy` with the warehouse id, then
           `bundle run`, then attaches lakebase-db.

Usage (first install):
    export DATABRICKS_PROFILE=<profile>
    export TARGET=dev            # bundle target: dev (default) | stg | prod.
                                 # The app name is fixed per target in
                                 # databricks.yml and read back from the bundle;
                                 # do NOT pass an app name. (An APP_NAME env is
                                 # IGNORED — only warned about — so it can never
                                 # redirect grants to another environment; the
                                 # target is the sole source of truth.)
    export WAREHOUSE_ID=<warehouse_id>           # required on a first install
    # optional:
    #   export ASSESS_CATALOGS="cat_a,cat_b"
    #   export GENIE_SPACE_ID=<id>
    #   export BRAND_NAME="Acme"
    #   export FORCE_SP=true                         # SP-only mode (never attempt OBO)
    #   export USE_LAKEBASE=true                     # enable per-user history/plans
    #   export LAKEBASE_INSTANCE_NAME=<instance>     # existing Lakebase instance to reuse
    #   export LAKEBASE_DATABASE=ontology_readiness
    python3 scripts/post_deploy.py

Usage (upgrade to a new release): set only DATABRICKS_PROFILE and TARGET. Each
setting that you do not set is reused from the deployed app. To change one
setting, export just that variable. To ignore the deployed settings, set
RESET_SETTINGS=true. Set INFRA=true only when the release changed databricks.yml.

Python 3.9+. Requires databricks-sdk>=0.36.0 (the SP-grant step,
setup_app_permissions.py, resolves the app SP via the SDK). The Lakebase
history steps additionally require asyncpg.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent / "app"
# The one app config file. The Git deploy uses it as committed. A CLI publish renders
# values into it and restores the committed text when the run ends (see main()).
APP_CONFIG = APP_DIR / "app.yaml"
ORIGINAL_APP_CONFIG = ""  # the committed text, read at the start of main()

# Reuse the app's git-tag version resolver (dependency-light — stdlib only) so
# the baked version matches what the running app reports.
sys.path.insert(0, str(APP_DIR))
from server.user_agent import git_version  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import deploy_settings  # noqa: E402

PROFILE = os.environ.get("DATABRICKS_PROFILE", "")
# Bundle target (dev|stg|prod). The app name is pinned per target in
# databricks.yml and resolved from the bundle in main(); TARGET — not an app
# name — is what selects the environment.
TARGET = os.environ.get("TARGET", "dev")
# The bundle targets defined in databricks.yml. Kept here to validate TARGET and
# to derive a deterministic fallback app name (prod -> bare, else -<target>) that
# matches the yaml, so a failed `bundle summary` never silently falls back to the
# live prod app name for a dev/stg deploy.
VALID_TARGETS = ("dev", "stg", "prod")
# Placeholder only — main() reassigns this via resolve_app_name(), which resolves
# the name from the bundle target. An explicit APP_NAME env is IGNORED there (only
# warned about), never honored, so it cannot redirect this run to another env.
APP_NAME = os.environ.get("APP_NAME", "genie-ontology-readiness")
# Set from the resolved settings in main(). The env values here are only the
# starting point for resolve_app_name(), which passes WAREHOUSE_ID to bundle summary.
WAREHOUSE_ID = os.environ.get("WAREHOUSE_ID", os.environ.get("DATABRICKS_WAREHOUSE_ID", ""))
USE_LAKEBASE = False
LAKEBASE_INSTANCE = ""
LAKEBASE_DATABASE = "ontology_readiness"
# An explicit Autoscaling endpoint resource name to reuse (set in _main from the resolved
# settings). Empty = derive the project/branch/endpoint ids from the app name.
LAKEBASE_ENDPOINT_NAME = ""
# These provisioning connections carry a live Lakebase credential over the public
# internet. `require` encrypts but verifies nothing, so it gives no protection
# against an in-path attacker — see app/server/lakebase_client.py for the runtime
# counterpart and the escape hatch.
LAKEBASE_SSL_MODE = "verify-full"
MAX_WAIT = 600


def _lakebase_ssl():
    """asyncpg ``ssl`` arg for LAKEBASE_SSL_MODE (mirrors lakebase_client._ssl_arg).

    Passing the bare ``verify-full``/``verify-ca`` string makes asyncpg look for a
    CA cert at ``~/.postgresql/root.crt`` (absent here), so the provisioning
    connections fail. The Lakebase endpoint is publicly-signed, so build an
    SSLContext from a trusted CA bundle and verify against it; non-verifying modes
    pass through as the plain string."""
    import ssl
    mode = (LAKEBASE_SSL_MODE or "").lower()
    if mode not in ("verify-ca", "verify-full"):
        return LAKEBASE_SSL_MODE
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()
    if mode == "verify-ca":
        ctx.check_hostname = False
    return ctx

# The Autoscaling database resource id. Its hyphenated resource id (databricks-postgres) is
# NOT the Postgres database name — the actual name (databricks_postgres, underscore) is read
# back from `postgres get-database ... .status.postgres_database` and rendered into
# LAKEBASE_DATABASE. Rendering the hyphenated id fails pool init with
# `database "databricks-postgres" does not exist`.
AUTOSCALING_DATABASE_ID = "databricks-postgres"
# Default branch/endpoint ids for a new Autoscaling project (reused when they already exist).
AUTOSCALING_BRANCH_ID = "production"
AUTOSCALING_ENDPOINT_ID = "primary"

# Populated by setup_lakebase()/setup_lakebase_autoscaling() when USE_LAKEBASE is on.
# Provisioned fills host/sp_client_id/user_email; Autoscaling additionally fills endpoint
# (credential target), branch + database_resource (the attach payload), and database (the
# resolved underscore Postgres name rendered into LAKEBASE_DATABASE).
_LAKEBASE = {"host": "", "sp_client_id": "", "user_email": "",
             "endpoint": "", "branch": "", "database_resource": "", "database": ""}


def _pg_id(value):
    """A valid Lakebase resource id from a name: 1-63 chars, starts with a lowercase letter,
    only lowercase letters, digits and hyphens."""
    out = re.sub(r"[^a-z0-9-]", "-", (value or "").lower()).strip("-") or "gor"
    if not out[0].isalpha():
        out = "gor-" + out
    return out[:63]


def _pg_role_id(sp_client_id):
    """A Postgres role_id for the app SP. Must start with a letter, so it is `gor_<sp>` with the
    SP client-id UUID sanitized. The role's `postgres_role` stays the raw SP client-id UUID."""
    return ("gor_" + re.sub(r"[^A-Za-z0-9]", "_", sp_client_id or ""))[:63]


def _stop(lines):
    """Print plain-text reasons and exit non-zero. Nothing has been changed yet
    when this is called from the settings step."""
    for line in lines:
        print(f"ERROR: {line}")
    sys.exit(1)


def explicit_from_env():
    """The settings the customer set for this run: env vars that are SET, even when
    empty. An empty value is a real value (for example ASSESS_CATALOGS= clears the
    list), so it is kept."""
    out = {k: os.environ[k].strip() for k in deploy_settings.KEYS if k in os.environ}
    if "WAREHOUSE_ID" not in out and "DATABRICKS_WAREHOUSE_ID" in os.environ:
        out["WAREHOUSE_ID"] = os.environ["DATABRICKS_WAREHOUSE_ID"].strip()
    return out


def _flag_from_env(name):
    """A strict true/false env flag. Anything else stops the run (a typo must not be ignored)."""
    raw = os.environ.get(name, "").strip().lower()
    if raw in ("", "false"):
        return False
    if raw == "true":
        return True
    _stop([f"{name}={raw!r} is not valid. Use {name}=true, or leave it unset."])


def reset_requested():
    """RESET_SETTINGS=true ignores the deployed settings (a deliberate fresh start)."""
    return _flag_from_env("RESET_SETTINGS")


def infra_requested():
    """INFRA=true forces the bundle path (`bundle deploy`) even for an existing app."""
    return _flag_from_env("INFRA")


def render_app_yml(values):
    """Write the resolved settings into app/app.yaml (rendered from the committed text).

    LAKEBASE_HOST is not written: the lakebase-db resource supplies it (valueFrom). The
    USE_LAKEBASE flag is on only when the history database was reached."""
    rendered = {name: setting.value for name, setting in values.items()}
    rendered["USE_LAKEBASE"] = "true" if (USE_LAKEBASE and _LAKEBASE["host"]) else "false"
    rendered["LAKEBASE_USER"] = _LAKEBASE["sp_client_id"] or values["LAKEBASE_USER"].value
    # Autoscaling: pin the credential endpoint and the RESOLVED underscore database name
    # (never the hyphenated resource id) so pool init connects to the real Postgres database.
    if _LAKEBASE.get("endpoint"):
        rendered["LAKEBASE_ENDPOINT_NAME"] = _LAKEBASE["endpoint"]
    if _LAKEBASE.get("database"):
        rendered["LAKEBASE_DATABASE"] = _LAKEBASE["database"]
    text, missing = deploy_settings.render(ORIGINAL_APP_CONFIG, rendered)
    for name in missing:
        print(f"  (warning) could not set {name} in app.yaml — leaving the committed value")
    APP_CONFIG.write_text(text)
    print(f"  Rendered {APP_CONFIG} (restored to the committed text when this run ends)")


def _workspace_id_default():
    """The deployed workspace's id, used only when no setting is given (see WORKSPACE_ID).

    Resolved from the CLI (``databricks metastores current`` returns the current
    workspace_id). Empty if unavailable (the app then defaults the filter to all
    workspaces)."""
    ms = cli_json("metastores", "current")
    if isinstance(ms, dict) and ms.get("workspace_id"):
        return str(ms["workspace_id"])
    print("  (warning) could not resolve WORKSPACE_ID; workspace filter will default to all workspaces")
    return ""


def _cli_cmd(*args):
    cmd = ["databricks", *args]
    if PROFILE:
        cmd += ["--profile", PROFILE]
    return cmd


def cli_json(*args):
    r = subprocess.run(_cli_cmd(*args, "--output", "json"), capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return r.stdout.strip()


def cli(*args, check=True):
    cmd = _cli_cmd(*args)
    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd, text=True)
    if check and result.returncode != 0:
        print(f"  command failed ({result.returncode})")
    return result.returncode


def _get_app(name):
    """The app's apps-get JSON, or None if the app does not exist.

    Any other error stops the run. Treating a failed read as "no app" would skip
    the safety checks and could turn a live app's history off."""
    r = subprocess.run(_cli_cmd("apps", "get", name, "--output", "json"), capture_output=True, text=True)
    if r.returncode == 0:
        try:
            return json.loads(r.stdout)
        except json.JSONDecodeError:
            _stop([f"Could not read app '{name}': the CLI returned unexpected output."])
    text = (r.stdout + r.stderr).lower()
    if "does not exist" in text or "not found" in text or "resource_does_not_exist" in text:
        return None
    _stop([f"Could not read app '{name}' from the workspace: {r.stderr.strip()[:200]}",
           "Fix the CLI profile or your access, then run again. Nothing was changed."])


def _read_workspace_text(path):
    """Text of a workspace file, or None if it cannot be read."""
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "app.yaml"
        r = subprocess.run(_cli_cmd("workspace", "export", path, "--format", "RAW", "--file", str(dest)),
                           capture_output=True, text=True)
        if r.returncode != 0 or not dest.exists():
            return None
        return dest.read_text()


def resolve_settings(live):
    """Resolve every setting for this run. Prints the table; stops on a safety problem."""
    reset = reset_requested()
    template_text = ORIGINAL_APP_CONFIG
    forced = {"GOR_VERSION": git_version() or "", "LAKEBASE_USER": live["sp"], "LAKEBASE_HOST": ""}
    extra = {"WORKSPACE_ID": _workspace_id_default()}
    values, problems = deploy_settings.plan(
        explicit_from_env(), live, template_text, forced=forced, extra_defaults=extra, reset=reset)
    if not values["WAREHOUSE_ID"].value:
        problems.insert(0, "No SQL warehouse id. Set WAREHOUSE_ID to a SQL warehouse you can use. "
                           "List warehouses with: databricks warehouses list.")
    if not (APP_DIR / "frontend" / "dist").is_dir():
        problems.insert(0, "The frontend is not built. Run: cd app/frontend && npm install && "
                           "npm run build && cd ../.. — then run this script again.")
    if reset:
        print("RESET_SETTINGS=true: ignoring the deployed app's settings. "
              "Each setting you did not set uses the release default.")
    if not live["exists"]:
        print("The app does not exist yet (first install). Settings you did not set use the release defaults.")
    print("\nSettings for this run (you set > deployed app > default):")
    print("\n".join(deploy_settings.format_table(values)))
    if problems:
        _stop(problems)
    return values


def _instance(name):
    for i in (cli_json("database", "list-database-instances") or []):
        if i.get("name") == name:
            return i
    return None


def _credential(instance):
    cred = cli_json("database", "generate-database-credential", "--json",
                    json.dumps({"request_id": "ontology_post_deploy", "instance_names": [instance]}))
    return cred.get("token") if isinstance(cred, dict) else None


def _credential_autoscaling(endpoint):
    """An OAuth credential for a Lakebase Autoscaling compute endpoint (mirrors the runtime
    POST /api/2.0/postgres/credentials). None if it cannot be minted."""
    cred = cli_json("postgres", "generate-database-credential", endpoint)
    return cred.get("token") if isinstance(cred, dict) else None


def _pg_exists(got):
    """True when a `postgres get-*` read returned a real resource (has a `name`)."""
    return isinstance(got, dict) and bool(got.get("name"))


def _ensure_project(project_id):
    """Idempotently ensure an Autoscaling project. Returns its resource name."""
    name = f"projects/{project_id}"
    if _pg_exists(cli_json("postgres", "get-project", name)):
        print(f"  reuse project {name}")
    else:
        print(f"  create project {name}")
        cli("postgres", "create-project", project_id, check=False)
    return name


def _ensure_branch(project_name, branch_id):
    """Idempotently ensure a branch of the project. Returns its resource name."""
    name = f"{project_name}/branches/{branch_id}"
    if _pg_exists(cli_json("postgres", "get-branch", name)):
        print(f"  reuse branch {name}")
    else:
        print(f"  create branch {name}")
        cli("postgres", "create-branch", project_name, branch_id, "--no-expiry", check=False)
    return name


def _ensure_endpoint(branch_name, endpoint_id):
    """Idempotently ensure a read-write compute endpoint of the branch. Returns its resource name."""
    name = f"{branch_name}/endpoints/{endpoint_id}"
    if _pg_exists(cli_json("postgres", "get-endpoint", name)):
        print(f"  reuse endpoint {name}")
    else:
        print(f"  create endpoint {name}")
        cli("postgres", "create-endpoint", branch_name, endpoint_id, "--replace-existing", check=False)
    return name


def _ensure_database(branch_name, database_id):
    """Idempotently ensure a database of the branch. Returns its resource name."""
    name = f"{branch_name}/databases/{database_id}"
    if _pg_exists(cli_json("postgres", "get-database", name)):
        print(f"  reuse database {name}")
    else:
        print(f"  create database {name}")
        cli("postgres", "create-database", branch_name, "--database-id", database_id,
            "--replace-existing", check=False)
    return name


def _ensure_role(branch_name, role_id, sp_client_id):
    """Idempotently ensure the app SP's Postgres role on the branch. Returns its resource name.

    role_id starts with a letter (`gor_<sp>`); postgres_role is the raw SP client-id UUID. The
    role is created with default privileges only — schema grants follow via _grant_history."""
    name = f"{branch_name}/roles/{role_id}"
    if _pg_exists(cli_json("postgres", "get-role", name)):
        print(f"  reuse SP Postgres role {name}")
    else:
        print(f"  create SP Postgres role {name}")
        body = json.dumps({"spec": {"identity_type": "SERVICE_PRINCIPAL",
                                    "postgres_role": sp_client_id,
                                    "auth_method": "LAKEBASE_OAUTH_V1"}})
        cli("postgres", "create-role", branch_name, "--role-id", role_id, "--json", body, check=False)
    return name


def _endpoint_host(endpoint):
    """The host name of an Autoscaling endpoint, or "" if it cannot be read."""
    ep = cli_json("postgres", "get-endpoint", endpoint)
    if isinstance(ep, dict):
        return (((ep.get("status") or {}).get("hosts") or {}).get("host")) or ""
    return ""


def _postgres_database_name(database_resource):
    """The resolved Postgres database name (underscore) of an Autoscaling database resource.

    Read from `postgres get-database <resource> .status.postgres_database` — NEVER the
    hyphenated resource id. "" if it cannot be read."""
    db = cli_json("postgres", "get-database", database_resource)
    if isinstance(db, dict):
        for part in (db.get("status") or {}, db.get("spec") or {}):
            name = part.get("postgres_database")
            if name:
                return name
    return ""


def _autoscaling_ids(endpoint_setting):
    """(project_id, branch_id, endpoint_id) for the Autoscaling branch.

    An explicit LAKEBASE_ENDPOINT_NAME (projects/<p>/branches/<b>/endpoints/<e>) names an
    existing project/branch/endpoint to reuse; otherwise ids are derived from the app name."""
    ep = (endpoint_setting or "").strip()
    if ep.startswith("projects/") and "/endpoints/" in ep:
        parts = ep.split("/")
        if len(parts) >= 6:
            return parts[1], parts[3], parts[5]
    return _pg_id(APP_NAME), AUTOSCALING_BRANCH_ID, AUTOSCALING_ENDPOINT_ID


def setup_lakebase():
    """Create the app's database on the shared instance and record host + SP client id
    for app.yaml rendering. Idempotent: an existing database is reused."""
    import asyncio
    if not LAKEBASE_INSTANCE:
        print("  USE_LAKEBASE=true but LAKEBASE_INSTANCE_NAME is unset. Set it to an existing "
              "Lakebase instance to enable per-user history. Skipping — history disabled.")
        return
    try:
        import asyncpg  # noqa: F401
    except ImportError:
        print("  ERROR: asyncpg not installed (pip install asyncpg). Skipping Lakebase — history disabled.")
        return

    print(f"\n[Lakebase] Reusing instance '{LAKEBASE_INSTANCE}', database '{LAKEBASE_DATABASE}'")
    inst = _instance(LAKEBASE_INSTANCE)
    waited = 0
    while (not inst or inst.get("state") != "AVAILABLE") and waited < MAX_WAIT:
        print(f"  waiting for instance AVAILABLE (state={inst.get('state') if inst else 'missing'})...")
        time.sleep(15); waited += 15
        inst = _instance(LAKEBASE_INSTANCE)
    if not inst or inst.get("state") != "AVAILABLE":
        print("  ERROR: Lakebase instance not AVAILABLE — skipping (history disabled).")
        return
    host = inst["read_write_dns"]
    _LAKEBASE["host"] = host
    print(f"  host: {host}")

    me = cli_json("current-user", "me")
    user_email = me.get("userName", "") if isinstance(me, dict) else ""
    app = cli_json("apps", "get", APP_NAME)
    sp = app.get("service_principal_client_id", "") if isinstance(app, dict) else ""
    _LAKEBASE["sp_client_id"] = sp
    if not sp:
        print("  WARNING: could not resolve app service principal client id yet.")

    async def _provision():
        import asyncpg
        token = _credential(LAKEBASE_INSTANCE)
        # 1. create the database (idempotent)
        conn = await asyncpg.connect(host=host, port=5432, database="postgres",
                                     user=user_email, password=token, ssl=_lakebase_ssl())
        try:
            await conn.execute(f'CREATE DATABASE {LAKEBASE_DATABASE}')
            print(f"  created database {LAKEBASE_DATABASE}")
        except Exception as e:
            print(f"  database {LAKEBASE_DATABASE}: {'exists' if 'already exists' in str(e) else e}")
        finally:
            await conn.close()

    asyncio.run(_provision())
    _LAKEBASE["user_email"] = user_email


def setup_lakebase_autoscaling():
    """Default Lakebase path: idempotently ensure the Autoscaling project / branch / endpoint /
    database / SP Postgres role, then record the host, credential endpoint, database resource,
    and resolved underscore database name for the attach + app.yaml rendering.

    Idempotent create-or-reuse: a get-then-create for each resource works whether it already
    exists (reuse) or is missing (create); none fails on "already exists". The app may not exist
    yet on a first install, so the SP role is created on the follow-up upgrade run (as the README
    notes for Lakebase)."""
    me = cli_json("current-user", "me")
    user_email = me.get("userName", "") if isinstance(me, dict) else ""
    app = cli_json("apps", "get", APP_NAME)
    sp = app.get("service_principal_client_id", "") if isinstance(app, dict) else ""

    project_id, branch_id, endpoint_id = _autoscaling_ids(LAKEBASE_ENDPOINT_NAME)
    print(f"\n[Lakebase] Autoscaling (default path): project '{project_id}', branch '{branch_id}', "
          f"endpoint '{endpoint_id}'")
    project = _ensure_project(project_id)
    branch = _ensure_branch(project, branch_id)
    endpoint = _ensure_endpoint(branch, endpoint_id)
    database_resource = _ensure_database(branch, AUTOSCALING_DATABASE_ID)
    if sp:
        _ensure_role(branch, _pg_role_id(sp), sp)
    else:
        print("  WARNING: the app's service principal id is not known yet (first install). "
              "The SP Postgres role is created on the follow-up upgrade run.")

    host = _endpoint_host(endpoint)
    database = _postgres_database_name(database_resource)
    _LAKEBASE.update(host=host, sp_client_id=sp, user_email=user_email, endpoint=endpoint,
                     branch=branch, database_resource=database_resource, database=database)
    if not host:
        print("  WARNING: could not resolve the endpoint host — history will stay off this run.")
    if database:
        print(f"  database: resource '{database_resource}' -> Postgres name '{database}'")
    else:
        print(f"  WARNING: could not resolve the Postgres name of '{database_resource}'.")


def _grant_history():
    """Grant the app SP full DML on the history objects. Idempotent, so it runs on every
    publish.

    Schema USAGE/CREATE alone is NOT enough when the tables/sequences already exist
    owned by another role (e.g. the deploying user) — the SP then gets "permission
    denied". Granting ALL on existing tables + sequences plus default privileges covers
    both the pre-existing case and anything created later. Run as the owning user."""
    import asyncio
    host = _LAKEBASE.get("host")
    sp = _LAKEBASE.get("sp_client_id")
    user_email = _LAKEBASE.get("user_email", "")
    if not (host and sp):
        return
    # public: existing installs keep their tables there. genie_ontology_readiness: a new
    # database gets its own schema (the app creates it). Each statement runs on its own, so a
    # schema that does not exist yet does not stop the others.
    grants = []
    for schema in ("public", "genie_ontology_readiness"):
        grants += [
            f'GRANT USAGE, CREATE ON SCHEMA {schema} TO "{sp}"',
            f'GRANT ALL ON ALL TABLES IN SCHEMA {schema} TO "{sp}"',
            f'GRANT ALL ON ALL SEQUENCES IN SCHEMA {schema} TO "{sp}"',
            f'ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT ALL ON TABLES TO "{sp}"',
            f'ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT ALL ON SEQUENCES TO "{sp}"',
        ]

    endpoint = _LAKEBASE.get("endpoint")
    # Autoscaling connects to the resolved underscore Postgres name and mints an endpoint
    # credential; Provisioned uses the instance credential and LAKEBASE_DATABASE.
    database = (_LAKEBASE.get("database") or LAKEBASE_DATABASE) if endpoint else LAKEBASE_DATABASE

    async def _grant():
        import asyncpg
        token = _credential_autoscaling(endpoint) if endpoint else _credential(LAKEBASE_INSTANCE)
        conn = await asyncpg.connect(host=host, port=5432, database=database,
                                     user=user_email, password=token, ssl=_lakebase_ssl())
        done = 0
        try:
            for g in grants:
                try:
                    await conn.execute(g)
                    done += 1
                except Exception as e:
                    print(f"  skipped: {g.split(' TO ')[0]} ({str(e)[:80]})")
            print(f"  granted {done} of {len(grants)} history privileges to app SP ({sp[:12]}...)")
        finally:
            await conn.close()
    try:
        asyncio.run(_grant())
    except Exception as e:
        print(f"  WARNING: grant step failed: {str(e)[:120]}")


def _update_resources(warehouse_id=None, lakebase=None, postgres=None):
    """Set the app's resources. Keeps every resource this script does not manage.

    warehouse_id  new sql-warehouse id, or None to keep the current resource
    lakebase      {"instance": ..., "database": ...} to attach a Provisioned lakebase-db, or None
    postgres      {"branch": ..., "database": ...} to attach an Autoscaling lakebase-db, or None
                  (branch/database are resource names; mutually exclusive with `lakebase`)
    """
    app = cli_json("apps", "get", APP_NAME)
    resources = []
    for r in (app.get("resources", []) if isinstance(app, dict) else []):
        if r["name"] == "lakebase-db" or (r["name"] == "sql-warehouse" and warehouse_id):
            continue
        item = {"name": r["name"]}
        for k in ("sql_warehouse", "database", "postgres", "secret", "serving_endpoint", "job"):
            if r.get(k) is not None:
                item[k] = r[k]
        resources.append(item)
    if warehouse_id:
        resources.append({"name": "sql-warehouse",
                          "sql_warehouse": {"id": warehouse_id, "permission": "CAN_USE"}})
    if lakebase:
        resources.append({
            "name": "lakebase-db",
            "database": {"instance_name": lakebase["instance"],
                         "database_name": lakebase["database"],
                         "permission": "CAN_CONNECT_AND_CREATE"},
        })
    if postgres:
        resources.append({
            "name": "lakebase-db",
            "postgres": {"branch": postgres["branch"],
                         "database": postgres["database"],
                         "permission": "CAN_CONNECT_AND_CREATE"},
        })
    # Retry: `apps update` can transiently conflict right after a deploy.
    # Also (re)assert user_api_scopes so on-behalf-of-user (OBO) auth is enabled on
    # an already-created app — user_api_scopes is otherwise only applied on create.
    payload = json.dumps({"resources": resources, "user_api_scopes": ["sql"]})
    for _ in range(6):
        if cli_json("apps", "update", APP_NAME, "--json", payload):
            print(f"  resources: {', '.join(r['name'] for r in resources)}")
            return True
        time.sleep(10)
    print("  WARNING: apps update failed after retries")
    return False


def _restart_app():
    """Restart the app so its lifespan re-runs init_pool with the lakebase-db resource present."""
    app = cli_json("apps", "get", APP_NAME)
    app_path = ""
    if isinstance(app, dict):
        app_path = (app.get("active_deployment") or {}).get("source_code_path", "")
    if app_path:
        cli("apps", "deploy", APP_NAME, "--source-code-path", app_path, check=False)
    else:
        print("  WARNING: could not resolve app source path to restart; the app may need a manual redeploy.")


def attach_lakebase():
    """Bundle path (Provisioned, legacy): attach the lakebase-db resource, grant the app SP, and
    restart.

    Runs AFTER `bundle deploy`/`run`, because `bundle deploy` reconciles the app's
    resources from databricks.yml (which intentionally omits lakebase-db) and would
    otherwise strip it. Pinned LAKEBASE_* env stays in app.yaml; the resource
    provides the SP's Postgres role + network path, so we restart to reconnect.
    """
    if not _LAKEBASE.get("host"):
        return
    print("\n[Lakebase] Attaching lakebase-db resource (Provisioned, post-publish)")
    _update_resources(lakebase={"instance": LAKEBASE_INSTANCE, "database": LAKEBASE_DATABASE})
    _grant_history()
    _restart_app()


def attach_lakebase_autoscaling():
    """Bundle path (Autoscaling, default): attach the `postgres` lakebase-db resource, grant the
    app SP, and restart.

    Mirrors attach_lakebase and runs AFTER `bundle deploy`/`run` for the same reason (bundle
    deploy strips lakebase-db). The resource carries the branch + database resource names so NO
    manual `apps update` is needed; the rendered app.yaml pins the endpoint + underscore database."""
    if not _LAKEBASE.get("host"):
        return
    print("\n[Lakebase] Attaching lakebase-db resource (Autoscaling, post-publish)")
    _update_resources(postgres={"branch": _LAKEBASE["branch"],
                                "database": _LAKEBASE["database_resource"]})
    _grant_history()
    _restart_app()


def stage_source():
    """Copy app/ (with the built frontend and the rendered app.yaml) to your own
    staging folder and sync it there. Returns the workspace path.

    The folder is under your home directory. The deployed app's source folder may belong to
    whoever first deployed it, so an upgrade by someone else cannot write there."""
    me = cli_json("current-user", "me")
    user = me.get("userName", "") if isinstance(me, dict) else ""
    if not user:
        _stop(["Could not read your user name. Check the CLI profile, then run again."])
    remote = f"/Workspace/Users/{user}/.genie-ontology-readiness/{APP_NAME}"
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / "app"
        shutil.copytree(APP_DIR, local, ignore=shutil.ignore_patterns(
            "node_modules", "__pycache__", ".pytest_cache", "*.pyc"))
        # Do not delete the folder first. The deployed app's settings live in the app.yaml
        # in this folder, and a failed sync must not leave the next run without them.
        rc = cli("sync", "--full", str(local), remote, check=False)
    if rc != 0:
        _stop([f"Could not copy the app source to {remote}. Fix the error above. Nothing was deployed."])
    return remote


def upgrade_app(values, live):
    """Upgrade mode: update resources only if they changed, grant, sync and deploy.

    Lakebase is attached as a Provisioned (`database`) or an Autoscaling (`postgres`) resource,
    the same shapes the bundle-path attach uses."""
    warehouse = values["WAREHOUSE_ID"].value
    on = bool(USE_LAKEBASE and _LAKEBASE["host"])
    autoscaling = bool(on and not LAKEBASE_INSTANCE)
    want_lb = {"instance": LAKEBASE_INSTANCE, "database": LAKEBASE_DATABASE} if (on and not autoscaling) else None
    want_pg = {"branch": _LAKEBASE["branch"], "database": _LAKEBASE["database_resource"]} if autoscaling else None
    have_wh = live["resources"].get("WAREHOUSE_ID")
    have_lb = ({"instance": live["resources"]["LAKEBASE_INSTANCE_NAME"],
                "database": live["resources"].get("LAKEBASE_DATABASE", "")}
               if "LAKEBASE_INSTANCE_NAME" in live["resources"] else None)
    have_pg = ({"branch": live["resources"].get("LAKEBASE_BRANCH", ""),
                "database": live["resources"].get("LAKEBASE_DATABASE_RESOURCE", "")}
               if live.get("autoscaling") else None)
    if have_wh != warehouse or have_lb != want_lb or have_pg != want_pg:
        print("  the warehouse or Lakebase resource changed; updating the app's resources")
        kwargs = {"warehouse_id": warehouse if have_wh != warehouse else None, "lakebase": want_lb}
        if want_pg is not None or have_pg is not None:
            kwargs["postgres"] = want_pg
        _update_resources(**kwargs)
    else:
        print("  app resources already match the settings; not changed")
    if want_lb or want_pg:
        _grant_history()

    remote = stage_source()

    app = cli_json("apps", "get", APP_NAME)
    state = ((app or {}).get("compute_status") or {}).get("state", "") if isinstance(app, dict) else ""
    if state != "ACTIVE":
        cli("apps", "start", APP_NAME, check=False)
    rc = cli("apps", "deploy", APP_NAME, "--source-code-path", remote, "--mode", "SNAPSHOT",
             "--auto-approve", check=False)
    if rc != 0:
        _stop([f"apps deploy failed. The app can still run the previous release. Fix the error above, "
               f"then run this script again. Staged source: {remote}"])


def resolve_app_name():
    """Read the target's pinned app name back from the bundle.

    The name lives ONLY in databricks.yml (per-target resource override), so the
    bundle target — never a caller-supplied APP_NAME — is the source of truth and
    this script can't disagree with the actual deploy. If `bundle summary` can't
    be read, fall back to the target-derived name (not the bare prod name). An
    explicit APP_NAME env is ignored (with a warning) — it exists only as a last
    resort and must never silently redirect grants to another environment."""
    args = ["bundle", "summary", "-t", TARGET]
    if WAREHOUSE_ID:
        args += ["--var", f"warehouse_id={WAREHOUSE_ID}"]
    summ = cli_json(*args)
    try:
        name = summ["resources"]["apps"]["ontology_readiness"]["name"]
    except (TypeError, KeyError):
        name = None
    if not name:
        name = _fallback_app_name()
    explicit = os.environ.get("APP_NAME")
    if explicit and explicit != name:
        print(f"  (warning) APP_NAME={explicit!r} ignored — target '{TARGET}' resolves the "
              f"app name to {name!r}; the target is the source of truth.")
    return name


def _fallback_app_name():
    """Deterministic name for TARGET matching the databricks.yml convention.

    Used only when `bundle summary` can't be read; it mirrors the yaml (prod ->
    bare, dev/stg -> suffixed) so a transient failure never resolves a dev/stg
    deploy to the live prod app name."""
    return "genie-ontology-readiness" if TARGET == "prod" else f"genie-ontology-readiness-{TARGET}"


def main():
    """Run the publish, then put the committed app.yaml back, even when a stop exits early."""
    global ORIGINAL_APP_CONFIG
    ORIGINAL_APP_CONFIG = APP_CONFIG.read_text()
    try:
        _main()
    finally:
        APP_CONFIG.write_text(ORIGINAL_APP_CONFIG)


def _main():
    global APP_NAME, WAREHOUSE_ID, USE_LAKEBASE, LAKEBASE_INSTANCE, LAKEBASE_DATABASE
    global LAKEBASE_SSL_MODE, LAKEBASE_ENDPOINT_NAME
    if not PROFILE:
        print("ERROR: DATABRICKS_PROFILE not set.")
        sys.exit(1)
    if TARGET not in VALID_TARGETS:
        print(f"ERROR: TARGET={TARGET!r} is not a known bundle target {VALID_TARGETS}.")
        sys.exit(1)

    print("=" * 60)
    print("Genie Ontology Readiness — post-deploy")
    print("=" * 60)

    APP_NAME = resolve_app_name()
    print(f"Target: {TARGET}   App: {APP_NAME}")

    # Read the deployed app BEFORE any change, then decide every setting for this run.
    live = deploy_settings.read_live(_get_app(APP_NAME), _read_workspace_text)
    values = resolve_settings(live)
    mode = deploy_settings.choose_mode(live["exists"], infra_requested())
    if mode == "upgrade":
        print("Mode: upgrade (reuses the deployed app; no bundle deploy)")
    else:
        print("Mode: bundle deploy (first install, or INFRA=true)")

    WAREHOUSE_ID = values["WAREHOUSE_ID"].value
    USE_LAKEBASE = deploy_settings.effective_lakebase(
        values["USE_LAKEBASE"].value, values["LAKEBASE_INSTANCE_NAME"].value,
        autoscaling=live.get("autoscaling"))
    LAKEBASE_INSTANCE = values["LAKEBASE_INSTANCE_NAME"].value
    LAKEBASE_DATABASE = values["LAKEBASE_DATABASE"].value
    LAKEBASE_SSL_MODE = values["LAKEBASE_SSL_MODE"].value
    LAKEBASE_ENDPOINT_NAME = values["LAKEBASE_ENDPOINT_NAME"].value

    if USE_LAKEBASE:
        # Autoscaling is the default; Provisioned (legacy) runs only when an instance name is set.
        if LAKEBASE_INSTANCE:
            setup_lakebase()
        else:
            setup_lakebase_autoscaling()
    if (USE_LAKEBASE and not _LAKEBASE["host"]
            and deploy_settings.uses_lakebase(live["env"], live["resources"], live.get("autoscaling"))):
        _stop(["Lakebase is not available. This run would turn history off. Nothing was deployed. "
               "Make the instance/endpoint AVAILABLE, then run again."])

    print("\n[1/3] Rendering app.yaml...")
    render_app_yml(values)

    print("\n[2/3] Granting app SP read access...")
    env = os.environ.copy()
    # Force-set (not setdefault): a stale APP_NAME already in the environment must
    # NOT win over the target-resolved name, or the child would grant the SP on the
    # wrong environment's app.
    env["APP_NAME"] = APP_NAME
    env["WAREHOUSE_ID"] = WAREHOUSE_ID
    # The grants must cover the catalogs this run deploys, which may come from the
    # deployed app rather than from this shell.
    env["ASSESS_CATALOGS"] = values["ASSESS_CATALOGS"].value
    subprocess.run([sys.executable, str(Path(__file__).parent / "setup_app_permissions.py")], env=env)

    if mode == "upgrade":
        print("\n[3/3] Upgrading the app in place (sync source, apps deploy)...")
        upgrade_app(values, live)
        print("\nDone. App URL:")
        cli("apps", "get", APP_NAME, check=False)
        return

    print(f"\n[3/3] Publishing the app to target '{TARGET}' (re-sync app.yaml + dist, start compute, deploy)...")
    # The app name is fixed by the target in databricks.yml — do NOT pass
    # --var app_name (the variable no longer exists; passing it would fail and,
    # historically, sharing one target's state across app names is the delete
    # footgun this hardening removes). Only warehouse_id is a real per-deploy var,
    # and it is always the resolved value, never an empty one.
    var_args = [f"--var=warehouse_id={WAREHOUSE_ID}"]
    if cli("bundle", "deploy", "-t", TARGET, *var_args, check=False) != 0:
        _stop(["bundle deploy failed. The app was not updated. Fix the error above, then run this script again."])
    rc = cli("bundle", "run", "ontology_readiness", "-t", TARGET, *var_args, check=False)
    if rc != 0:
        print(f"  (bundle run failed — ensure --var warehouse_id is set. If the app already "
              f"exists outside target '{TARGET}' state, redeploy code with `databricks apps deploy {APP_NAME}`.)")

    # Attach Lakebase AFTER publishing so `bundle deploy` doesn't strip the resource.
    if USE_LAKEBASE and _LAKEBASE["host"]:
        if LAKEBASE_INSTANCE:
            attach_lakebase()
        else:
            attach_lakebase_autoscaling()
        if not _LAKEBASE["sp_client_id"]:
            print("\nLakebase: this was the first install, so the app's service principal id was not known.\n"
                  "Run the upgrade command once more to finish the Lakebase setup. See the README.")

    print("\nDone. App URL:")
    cli("apps", "get", APP_NAME, check=False)


if __name__ == "__main__":
    main()
