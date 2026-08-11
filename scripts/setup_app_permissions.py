"""Grant the Databricks App service principal READ access for the assessment.

The app reads:
  - system.information_schema (catalogs/schemas/tables/columns/constraints/tags)
  - system.access / system.query (adoption signals — optional)
  - each catalog being assessed (so those objects appear in information_schema)

Because Databricks Apps run as a service principal (no end-user token
forwarding), the SP needs these grants or the assessment degrades to
"not available" for the affected pillars.

Usage:
    export DATABRICKS_PROFILE=<profile>
    export APP_NAME=genie-ontology-readiness
    export WAREHOUSE_ID=<warehouse_id>
    # optional: export ASSESS_CATALOGS="cat_a,cat_b"  (else grants on all non-system catalogs)
    python3 scripts/setup_app_permissions.py
"""

import json
import os
import subprocess
import sys

PROFILE = os.environ.get("DATABRICKS_PROFILE", "")
APP_NAME = os.environ.get("APP_NAME", "genie-ontology-readiness")
WAREHOUSE_ID = os.environ.get("WAREHOUSE_ID", os.environ.get("DATABRICKS_WAREHOUSE_ID", ""))
ASSESS_CATALOGS = [c.strip() for c in os.environ.get("ASSESS_CATALOGS", "").split(",") if c.strip()]

_INTERNAL = {"system", "__databricks_internal", "samples", "information_schema"}


def run_cli(*args):
    cmd = ["databricks"] + list(args)
    if PROFILE:
        cmd += ["--profile", PROFILE]
    result = subprocess.run(cmd + ["--output", "json"], capture_output=True, text=True)
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return result.stdout.strip()


def main():
    if not PROFILE:
        print("ERROR: DATABRICKS_PROFILE not set.")
        sys.exit(1)

    app_info = run_cli("apps", "get", APP_NAME)
    if not app_info or not isinstance(app_info, dict):
        print(f"ERROR: Could not get app '{APP_NAME}'. Deploy the bundle first.")
        sys.exit(1)
    sp = app_info.get("service_principal_client_id", "")
    if not sp:
        print("ERROR: App has no service principal yet.")
        sys.exit(1)
    print(f"App SP: {sp}")

    wh_id = WAREHOUSE_ID
    if not wh_id:
        wh = run_cli("api", "get", "/api/2.0/sql/warehouses")
        if wh and isinstance(wh, dict) and wh.get("warehouses"):
            wh_id = wh["warehouses"][0]["id"]
    if not wh_id:
        print("ERROR: No warehouse available to run GRANT statements. Set WAREHOUSE_ID.")
        sys.exit(1)

    from databricks.sdk import WorkspaceClient
    w = WorkspaceClient(profile=PROFILE)

    def grant(stmt: str):
        label = stmt.split(" ON ")[0].replace("GRANT ", "")
        target = stmt.split(" ON ", 1)[1].split(" TO ")[0] if " ON " in stmt else ""
        try:
            res = w.statement_execution.execute_statement(warehouse_id=wh_id, statement=stmt, wait_timeout="30s")
            state = res.status.state.value
            if state == "SUCCEEDED":
                print(f"  OK   {label} ON {target}")
            else:
                err = res.status.error.message[:120] if res.status.error else "unknown"
                print(f"  FAIL {label} ON {target} — {err}")
        except Exception as e:
            print(f"  ERR  {label} ON {target} — {str(e)[:120]}")

    # 1) System schemas (information_schema is the core; access/query are optional)
    print("\n[1/2] Granting read on system schemas...")
    grant(f"GRANT USE CATALOG ON CATALOG system TO `{sp}`")
    for schema in ("information_schema", "access", "query"):
        grant(f"GRANT USE SCHEMA ON SCHEMA system.{schema} TO `{sp}`")
        grant(f"GRANT SELECT ON SCHEMA system.{schema} TO `{sp}`")

    # 2) Target catalogs
    print("\n[2/2] Granting read on assessed catalogs...")
    catalogs = ASSESS_CATALOGS
    if not catalogs:
        cats = run_cli("api", "get", "/api/2.1/unity-catalog/catalogs")
        if cats and isinstance(cats, dict):
            catalogs = [
                c["name"] for c in cats.get("catalogs", [])
                if c.get("name") and c["name"] not in _INTERNAL and not c["name"].startswith("__")
            ]
        print(f"  Discovered {len(catalogs)} non-system catalogs.")
    for cat in catalogs:
        # Back-quote the catalog identifier so names with hyphens or other
        # special characters (e.g. "cardiac-intelligence-lakebase") are valid.
        cat_id = "`" + cat.replace("`", "``") + "`"
        grant(f"GRANT USE CATALOG ON CATALOG {cat_id} TO `{sp}`")
        grant(f"GRANT USE SCHEMA ON CATALOG {cat_id} TO `{sp}`")
        grant(f"GRANT SELECT ON CATALOG {cat_id} TO `{sp}`")

    print("\nPermissions setup complete.")
    print("Note: SELECT on catalogs lets the SP see those objects in information_schema.")
    print("For org-wide assessment, an account admin can instead grant at the metastore level.")


if __name__ == "__main__":
    main()
