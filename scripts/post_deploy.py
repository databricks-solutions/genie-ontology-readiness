"""Post-deploy: render app.yml, grant the app SP read access, provision Lakebase
(per-user history), and (re)deploy the app.

Run AFTER `databricks bundle deploy`.

Usage:
    export DATABRICKS_PROFILE=<profile>
    export TARGET=dev            # bundle target: dev (default) | stg | prod.
                                 # The app name is fixed per target in
                                 # databricks.yml and read back from the bundle;
                                 # do NOT pass an app name. (An APP_NAME env is
                                 # IGNORED — only warned about — so it can never
                                 # redirect grants to another environment; the
                                 # target is the sole source of truth.)
    export WAREHOUSE_ID=<warehouse_id>
    # optional:
    #   export ASSESS_CATALOGS="cat_a,cat_b"
    #   export GENIE_SPACE_ID=<id>
    #   export BRAND_NAME="Acme"
    #   export FORCE_SP=true                         # SP-only mode (never attempt OBO)
    #   export USE_LAKEBASE=true                     # enable per-user history/plans
    #   export LAKEBASE_INSTANCE_NAME=<instance>     # existing Lakebase instance to reuse
    #   export LAKEBASE_DATABASE=ontology_readiness
    python3 scripts/post_deploy.py

Python 3.9+. Lakebase steps require: databricks-sdk>=0.36.0, asyncpg.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent / "app"
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
WAREHOUSE_ID = os.environ.get("WAREHOUSE_ID", os.environ.get("DATABRICKS_WAREHOUSE_ID", ""))
USE_LAKEBASE = os.environ.get("USE_LAKEBASE", "false").lower() == "true"
LAKEBASE_INSTANCE = os.environ.get("LAKEBASE_INSTANCE_NAME", "")
LAKEBASE_DATABASE = os.environ.get("LAKEBASE_DATABASE", "ontology_readiness")
# These provisioning connections carry a live Lakebase credential over the public
# internet. `require` encrypts but verifies nothing, so it gives no protection
# against an in-path attacker — see app/server/lakebase_client.py for the runtime
# counterpart and the escape hatch.
LAKEBASE_SSL_MODE = os.environ.get("LAKEBASE_SSL_MODE", "verify-full")
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

# Populated by setup_lakebase() when USE_LAKEBASE is on.
_LAKEBASE = {"host": "", "sp_client_id": ""}


def render_app_yml():
    """Write app/app.yml from the template with env-driven values."""
    template = APP_DIR / "app.yml.template"
    out = APP_DIR / "app.yml"
    text = template.read_text()

    def set_env(name: str, value: str) -> None:
        nonlocal text
        import re
        pattern = rf'(- name: {name}\n\s+value: ")[^"]*(")'
        text, n = re.subn(pattern, rf"\g<1>{value}\g<2>", text)
        if n == 0:
            print(f"  (warning) could not set {name} in app.yml — leaving template default")

    set_env("ASSESS_CATALOGS", os.environ.get("ASSESS_CATALOGS", ""))
    set_env("GENIE_SPACE_ID", os.environ.get("GENIE_SPACE_ID", ""))
    set_env("WORKSPACE_ID", _resolve_workspace_id())
    set_env("BRAND_NAME", os.environ.get("BRAND_NAME", "Databricks"))
    set_env("FORCE_SP", os.environ.get("FORCE_SP", "false"))
    set_env("USE_LAKEBASE", "true" if (USE_LAKEBASE and _LAKEBASE["host"]) else "false")
    set_env("LAKEBASE_HOST", _LAKEBASE["host"])
    set_env("LAKEBASE_USER", _LAKEBASE["sp_client_id"])
    set_env("LAKEBASE_DATABASE", LAKEBASE_DATABASE)
    set_env("LAKEBASE_SSL_MODE", LAKEBASE_SSL_MODE)
    set_env("LAKEBASE_INSTANCE_NAME", LAKEBASE_INSTANCE)

    out.write_text(text)
    print(f"  Rendered {out}")


def _resolve_workspace_id() -> str:
    """The deployed workspace's id, to seed the app's default workspace filter.

    Honors an explicit WORKSPACE_ID env if set; otherwise resolves it from the CLI
    (``databricks metastores current`` returns the current workspace_id). Falls back
    to empty (the app then defaults the filter to all workspaces) if unavailable."""
    explicit = os.environ.get("WORKSPACE_ID", "")
    if explicit:
        return explicit
    ms = cli_json("metastores", "current")
    if isinstance(ms, dict) and ms.get("workspace_id"):
        return str(ms["workspace_id"])
    print("  (warning) could not resolve WORKSPACE_ID; workspace filter will default to all workspaces")
    return ""


def cli_json(*args):
    cmd = ["databricks"] + list(args) + ["--output", "json"]
    if PROFILE:
        cmd += ["--profile", PROFILE]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return r.stdout.strip()


def cli(*args, check=True):
    cmd = ["databricks"] + list(args)
    if PROFILE:
        cmd += ["--profile", PROFILE]
    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd, text=True)
    if check and result.returncode != 0:
        print(f"  command failed ({result.returncode})")
    return result.returncode


def _fallback_app_name():
    """Deterministic name for TARGET matching the databricks.yml convention.

    Used only when `bundle summary` can't be read; it mirrors the yaml (prod ->
    bare, dev/stg -> suffixed) so a transient failure never resolves a dev/stg
    deploy to the live prod app name."""
    return "genie-ontology-readiness" if TARGET == "prod" else f"genie-ontology-readiness-{TARGET}"


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


def _instance(name):
    for i in (cli_json("database", "list-database-instances") or []):
        if i.get("name") == name:
            return i
    return None


def _credential(instance):
    cred = cli_json("database", "generate-database-credential", "--json",
                    json.dumps({"request_id": "ontology_post_deploy", "instance_names": [instance]}))
    return cred.get("token") if isinstance(cred, dict) else None


def setup_lakebase():
    """Create the app's database on the shared instance, attach the lakebase-db
    resource, and record host + SP client id for app.yml rendering."""
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


def attach_lakebase():
    """Attach the lakebase-db resource, grant the app SP, and restart the app.

    Runs AFTER `bundle deploy`/`run`, because `bundle deploy` reconciles the app's
    resources from databricks.yml (which intentionally omits lakebase-db) and would
    otherwise strip it. Pinned LAKEBASE_* env stays in app.yml; the resource
    provides the SP's Postgres role + network path, so we restart to reconnect.
    """
    import asyncio
    host = _LAKEBASE.get("host")
    sp = _LAKEBASE.get("sp_client_id")
    user_email = _LAKEBASE.get("user_email", "")
    if not host:
        return
    print("\n[Lakebase] Attaching lakebase-db resource (post-publish)")
    app = cli_json("apps", "get", APP_NAME)
    resources = []
    for r in (app.get("resources", []) if isinstance(app, dict) else []):
        item = {"name": r["name"]}
        for k in ("sql_warehouse", "database", "secret", "serving_endpoint", "job"):
            if r.get(k) is not None:
                item[k] = r[k]
        resources.append(item)
    if not any(r["name"] == "lakebase-db" for r in resources):
        resources.append({
            "name": "lakebase-db",
            "database": {"instance_name": LAKEBASE_INSTANCE,
                         "database_name": LAKEBASE_DATABASE,
                         "permission": "CAN_CONNECT_AND_CREATE"},
        })
    # Retry: `apps update` can transiently conflict right after `bundle run`.
    # Also (re)assert user_api_scopes so on-behalf-of-user (OBO) auth is enabled on
    # an already-created app — user_api_scopes is otherwise only applied on create.
    payload = json.dumps({"resources": resources, "user_api_scopes": ["sql"]})
    res = None
    for attempt in range(6):
        res = cli_json("apps", "update", APP_NAME, "--json", payload)
        if res:
            break
        time.sleep(10)
    print(f"  resources: {', '.join(r['name'] for r in resources)}" if res else "  WARNING: apps update failed after retries")

    # Grant the app SP full DML on the history objects. Schema USAGE/CREATE alone
    # is NOT enough when the tables/sequences already exist owned by another role
    # (e.g. the deploying user) — the SP then gets "permission denied". Granting
    # ALL on existing tables + sequences plus default privileges covers both the
    # pre-existing case and anything created later. Run as the owning user.
    if sp:
        grants = [
            f'GRANT USAGE, CREATE ON SCHEMA public TO "{sp}"',
            f'GRANT ALL ON ALL TABLES IN SCHEMA public TO "{sp}"',
            f'GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO "{sp}"',
            f'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO "{sp}"',
            f'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO "{sp}"',
        ]
        async def _grant():
            import asyncpg
            token = _credential(LAKEBASE_INSTANCE)
            conn = await asyncpg.connect(host=host, port=5432, database=LAKEBASE_DATABASE,
                                         user=user_email, password=token, ssl=_lakebase_ssl())
            try:
                for g in grants:
                    await conn.execute(g)
                print(f"  granted schema + table + sequence privileges to app SP ({sp[:12]}...)")
            except Exception as e:
                print(f"  WARNING: grant skipped ({str(e)[:120]})")
            finally:
                await conn.close()
        try:
            asyncio.run(_grant())
        except Exception as e:
            print(f"  WARNING: grant step failed: {str(e)[:120]}")

    # Restart the app so its lifespan re-runs init_pool with the resource present.
    app = cli_json("apps", "get", APP_NAME)
    app_path = ""
    if isinstance(app, dict):
        app_path = (app.get("active_deployment") or {}).get("source_code_path", "")
    if app_path:
        cli("apps", "deploy", APP_NAME, "--source-code-path", app_path, check=False)
    else:
        print("  WARNING: could not resolve app source path to restart; the app may need a manual redeploy.")


def main():
    global APP_NAME
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

    if USE_LAKEBASE:
        setup_lakebase()

    print("\n[1/3] Rendering app.yml...")
    render_app_yml()

    print("\n[2/3] Granting app SP read access...")
    env = os.environ.copy()
    # Force-set (not setdefault): a stale APP_NAME already in the environment must
    # NOT win over the target-resolved name, or the child would grant the SP on the
    # wrong environment's app.
    env["APP_NAME"] = APP_NAME
    if WAREHOUSE_ID:
        env["WAREHOUSE_ID"] = WAREHOUSE_ID
    subprocess.run([sys.executable, str(Path(__file__).parent / "setup_app_permissions.py")], env=env)

    print(f"\n[3/3] Publishing the app to target '{TARGET}' (re-sync app.yml + dist, start compute, deploy)...")
    # The app name is fixed by the target in databricks.yml — do NOT pass
    # --var app_name (the variable no longer exists; passing it would fail and,
    # historically, sharing one target's state across app names is the delete
    # footgun this hardening removes). Only warehouse_id is a real per-deploy var.
    var_args = [f"--var=warehouse_id={WAREHOUSE_ID}"] if WAREHOUSE_ID else []
    cli("bundle", "deploy", "-t", TARGET, *var_args, check=False)
    rc = cli("bundle", "run", "ontology_readiness", "-t", TARGET, *var_args, check=False)
    if rc != 0:
        print(f"  (bundle run failed — ensure --var warehouse_id is set. If the app already "
              f"exists outside target '{TARGET}' state, redeploy code with `databricks apps deploy {APP_NAME}`.)")

    # Attach Lakebase AFTER publishing so `bundle deploy` doesn't strip the resource.
    if USE_LAKEBASE and _LAKEBASE["host"]:
        attach_lakebase()

    print("\nDone. App URL:")
    cli("apps", "get", APP_NAME, check=False)


if __name__ == "__main__":
    main()
