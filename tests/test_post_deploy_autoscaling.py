"""Unit tests for the Autoscaling Lakebase branch of scripts/post_deploy.py (issue #26).

Autoscaling is the default/primary Lakebase path; Provisioned is legacy (only when
LAKEBASE_INSTANCE_NAME is set). These tests pin the behaviour that matters and needs no
workspace: every `databricks` CLI read/write is monkeypatched (cli_json/cli + the postgres
helpers). They assert:

  - the attach payload uses the `postgres` resource shape with CAN_CONNECT_AND_CREATE;
  - ordering: `bundle deploy` runs before the attach;
  - idempotent project/branch/endpoint/database/role — create-if-missing AND reuse-if-exists;
  - the rendered LAKEBASE_DATABASE is the RESOLVED underscore name, never the hyphenated id.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import deploy_settings as ds  # noqa: E402
import post_deploy  # noqa: E402

TEMPLATE = (ROOT / "app" / "app.yaml").read_text()
APP = "genie-ontology-readiness-dev"
SP = "sp-uuid-123"
ROLE_ID = "gor_sp_uuid_123"  # _pg_role_id(SP): starts with a letter, UUID sanitized


def _reset_lakebase():
    post_deploy._LAKEBASE.update(host="", sp_client_id="", user_email="", endpoint="",
                                 branch="", database_resource="", database="")


@pytest.fixture(autouse=True)
def _clean():
    _reset_lakebase()
    yield
    _reset_lakebase()


def _ids():
    project = f"projects/{post_deploy._pg_id(APP)}"
    branch = f"{project}/branches/production"
    return {
        "project": project,
        "branch": branch,
        "endpoint": f"{branch}/endpoints/primary",
        "database": f"{branch}/databases/databricks-postgres",
        "role": f"{branch}/roles/{ROLE_ID}",
    }


class _PgFake:
    """A stand-in Databricks CLI: get-* returns a resource only when it already exists; each
    create-* registers its resource so a later get-* (host / resolved db name) succeeds — exactly
    as a real create would. Records every `cli` (write) call so a test can assert create-vs-reuse
    and ordering."""

    def __init__(self, existing=(), resolve_db=True):
        self.existing = set(existing)
        self.resolve_db = resolve_db  # False: get-database returns a status WITHOUT postgres_database
        self.cli_calls = []
        self.updates = []  # apps update payloads (the attach)

    def cli_json(self, *args):
        a = list(args)
        if a[:2] == ["current-user", "me"]:
            return {"userName": "me@example.com"}
        if a[:2] == ["apps", "get"]:
            return {"service_principal_client_id": SP,
                    "active_deployment": {"source_code_path": "/Workspace/Users/me/app"},
                    "resources": [{"name": "sql-warehouse", "sql_warehouse": {"id": "wh"}}]}
        if a[:2] == ["apps", "update"]:
            import json
            self.updates.append(json.loads(a[a.index("--json") + 1]))
            return {"name": APP}
        if a and a[0] == "postgres" and a[1].startswith("get-"):
            name = a[2]
            if name not in self.existing:
                return None
            if a[1] == "get-endpoint":
                return {"name": name, "status": {"hosts": {"host": "ep-x.database.cloud.databricks.com"}}}
            if a[1] == "get-database":
                # The resource id is hyphenated; the Postgres name is the underscore form.
                status = {"postgres_database": "databricks_postgres"} if self.resolve_db else {}
                return {"name": name, "status": status}
            return {"name": name}
        return None

    def cli(self, *args, check=True):
        self.cli_calls.append(args)
        verb = args[1] if len(args) > 1 else ""
        if verb == "create-project":
            self.existing.add(f"projects/{args[2]}")
        elif verb == "create-branch":
            self.existing.add(f"{args[2]}/branches/{args[3]}")
        elif verb == "create-endpoint":
            self.existing.add(f"{args[2]}/endpoints/{args[3]}")
        elif verb == "create-database":
            self.existing.add(f"{args[2]}/databases/{args[args.index('--database-id') + 1]}")
        elif verb == "create-role":
            self.existing.add(f"{args[2]}/roles/{args[args.index('--role-id') + 1]}")
        return 0


def _wire(monkeypatch, fake, *, endpoint_setting=""):
    monkeypatch.setattr(post_deploy, "APP_NAME", APP)
    monkeypatch.setattr(post_deploy, "LAKEBASE_ENDPOINT_NAME", endpoint_setting)
    monkeypatch.setattr(post_deploy, "LAKEBASE_INSTANCE", "")
    monkeypatch.setattr(post_deploy, "cli_json", fake.cli_json)
    monkeypatch.setattr(post_deploy, "cli", fake.cli)


# --- sanitizers --------------------------------------------------------------

def test_role_id_starts_with_a_letter_and_sanitizes_the_uuid():
    assert post_deploy._pg_role_id(SP) == ROLE_ID
    assert post_deploy._pg_role_id("123-abc")[0].isalpha()


def test_project_id_is_a_valid_resource_id():
    assert post_deploy._pg_id(APP) == APP  # already valid
    assert post_deploy._pg_id("9Weird Name!")[0].isalpha()
    assert post_deploy._pg_id("") == "gor"


def test_autoscaling_ids_parse_an_explicit_endpoint_else_derive_from_app_name():
    monkeypatch_app = APP
    ep = "projects/other/branches/dev/endpoints/e1"
    assert post_deploy._autoscaling_ids(ep) == ("other", "dev", "e1")
    # Derived from the app name when no explicit endpoint is set.
    post_deploy.APP_NAME = monkeypatch_app
    assert post_deploy._autoscaling_ids("") == (post_deploy._pg_id(APP), "production", "primary")


# --- idempotent create-if-missing AND reuse-if-exists ------------------------

def test_setup_autoscaling_creates_everything_when_missing(monkeypatch):
    fake = _PgFake(existing=[])
    _wire(monkeypatch, fake)
    post_deploy.setup_lakebase_autoscaling()

    verbs = [c[1] for c in fake.cli_calls]
    assert verbs == ["create-project", "create-branch", "create-endpoint",
                     "create-database", "create-role"]
    ids = _ids()
    assert post_deploy._LAKEBASE["endpoint"] == ids["endpoint"]
    assert post_deploy._LAKEBASE["branch"] == ids["branch"]
    assert post_deploy._LAKEBASE["database_resource"] == ids["database"]
    # resolved underscore name, host, and SP all captured
    assert post_deploy._LAKEBASE["database"] == "databricks_postgres"
    assert post_deploy._LAKEBASE["host"] == "ep-x.database.cloud.databricks.com"
    assert post_deploy._LAKEBASE["sp_client_id"] == SP


def test_setup_autoscaling_reuses_everything_when_present(monkeypatch):
    ids = _ids()
    fake = _PgFake(existing=list(ids.values()))
    _wire(monkeypatch, fake)
    post_deploy.setup_lakebase_autoscaling()

    assert fake.cli_calls == []  # nothing created; all reused
    assert post_deploy._LAKEBASE["database"] == "databricks_postgres"
    assert post_deploy._LAKEBASE["endpoint"] == ids["endpoint"]


def test_setup_autoscaling_creates_only_the_missing_role(monkeypatch):
    ids = _ids()
    present = [ids["project"], ids["branch"], ids["endpoint"], ids["database"]]  # role missing
    fake = _PgFake(existing=present)
    _wire(monkeypatch, fake)
    post_deploy.setup_lakebase_autoscaling()

    assert [c[1] for c in fake.cli_calls] == ["create-role"]
    role_call = fake.cli_calls[0]
    assert role_call[2] == ids["branch"] and role_call[role_call.index("--role-id") + 1] == ROLE_ID


def test_create_role_body_is_a_service_principal_role_with_the_uuid(monkeypatch):
    fake = _PgFake(existing=[])
    _wire(monkeypatch, fake)
    post_deploy.setup_lakebase_autoscaling()
    import json
    role_call = next(c for c in fake.cli_calls if c[1] == "create-role")
    body = json.loads(role_call[role_call.index("--json") + 1])
    assert body["spec"]["identity_type"] == "SERVICE_PRINCIPAL"
    assert body["spec"]["postgres_role"] == SP  # the raw SP client-id UUID
    assert body["spec"]["auth_method"] == "LAKEBASE_OAUTH_V1"


# --- the attach payload uses the `postgres` shape ----------------------------

def test_attach_payload_uses_the_postgres_shape_with_can_connect_and_create(monkeypatch):
    fake = _PgFake(existing=list(_ids().values()))
    _wire(monkeypatch, fake)
    monkeypatch.setattr(post_deploy, "USE_LAKEBASE", True)
    monkeypatch.setattr(post_deploy, "_grant_history", lambda: None)
    post_deploy.setup_lakebase_autoscaling()
    post_deploy.attach_lakebase_autoscaling()

    assert fake.updates, "apps update (attach) was not called"
    resources = fake.updates[-1]["resources"]
    lakebase = next(r for r in resources if r["name"] == "lakebase-db")
    assert "postgres" in lakebase and "database" not in lakebase  # Autoscaling shape, not Provisioned
    ids = _ids()
    assert lakebase["postgres"] == {
        "branch": ids["branch"],
        "database": ids["database"],  # the database RESOURCE name (hyphen) on the resource
        "permission": "CAN_CONNECT_AND_CREATE",
    }
    # The warehouse resource is preserved across the attach.
    assert any(r["name"] == "sql-warehouse" for r in resources)


# --- the rendered LAKEBASE_DATABASE is the resolved underscore name ----------

def test_render_writes_the_resolved_underscore_database_and_endpoint(monkeypatch, tmp_path):
    fake = _PgFake(existing=list(_ids().values()))
    _wire(monkeypatch, fake)
    monkeypatch.setattr(post_deploy, "USE_LAKEBASE", True)
    post_deploy.setup_lakebase_autoscaling()

    target = tmp_path / "app.yaml"
    target.write_text(TEMPLATE)
    monkeypatch.setattr(post_deploy, "APP_CONFIG", target)
    monkeypatch.setattr(post_deploy, "ORIGINAL_APP_CONFIG", TEMPLATE)

    live = ds.read_live(None, lambda p: None)
    values, _ = ds.plan({"USE_LAKEBASE": "true"}, live, TEMPLATE,
                        forced={"GOR_VERSION": "1", "LAKEBASE_USER": SP, "LAKEBASE_HOST": ""},
                        extra_defaults={"WORKSPACE_ID": "9"})
    post_deploy.render_app_yml(values)

    env = ds.parse_env(target.read_text())
    assert env["LAKEBASE_DATABASE"] == "databricks_postgres"       # underscore, resolved
    assert env["LAKEBASE_DATABASE"] != "databricks-postgres"       # NEVER the hyphenated id
    ids = _ids()
    assert env["LAKEBASE_ENDPOINT_NAME"] == ids["endpoint"]
    assert env["LAKEBASE_USER"] == SP
    assert env["USE_LAKEBASE"] == "true"


# --- ordering: bundle deploy before the attach -------------------------------

def _as_app(branch, db):
    """A Git-deployed app with an Autoscaling lakebase-db resource (live branch + db resource)."""
    return {"name": APP, "service_principal_client_id": SP,
            "active_deployment": {"git_source": {"commit": "abc"}, "source_code_path": None},
            "resources": [{"name": "sql-warehouse", "sql_warehouse": {"id": "wh"}},
                          {"name": "lakebase-db", "postgres": {"branch": branch, "database": db}}]}


def _drive_main(monkeypatch, *, get_app, env, order):
    """Drive post_deploy._main() with the workspace I/O stubbed, recording the step order.

    resolve_settings runs for real (so the repoint/detach guard fires); only its I/O is stubbed.
    `get_app` is the apps-get dict (None = first install). Lakebase setup/attach/grant/restart and
    the CLI are recorded. Returns the list of apps-update payloads (the attach)."""
    updates = []
    for k in ("WAREHOUSE_ID", "USE_LAKEBASE", "LAKEBASE_INSTANCE_NAME", "LAKEBASE_ENDPOINT_NAME",
              "LAKEBASE_DATABASE", "RESET_SETTINGS", "INFRA", "ASSESS_CATALOGS"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    monkeypatch.setattr(post_deploy, "PROFILE", "p")
    monkeypatch.setattr(post_deploy, "TARGET", "dev")
    monkeypatch.setattr(post_deploy, "APP_NAME", APP)
    monkeypatch.setattr(post_deploy, "ORIGINAL_APP_CONFIG", TEMPLATE)
    monkeypatch.setattr(post_deploy, "resolve_app_name", lambda: APP)
    monkeypatch.setattr(post_deploy, "git_version", lambda: "1.0")
    monkeypatch.setattr(post_deploy, "_workspace_id_default", lambda: "9")
    monkeypatch.setattr(post_deploy, "_get_app", lambda name: get_app)
    monkeypatch.setattr(post_deploy, "render_app_yml", lambda values: order.append("render"))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: order.append("grant-perms"))

    def _setup_as():
        order.append("setup-autoscaling")
        post_deploy._LAKEBASE.update(host="ep.host", sp_client_id=SP, endpoint=_ids()["endpoint"],
                                     branch=_ids()["branch"], database_resource=_ids()["database"],
                                     database="databricks_postgres")

    def _setup_prov():
        order.append("setup-provisioned")
        post_deploy._LAKEBASE.update(host="prov.host", sp_client_id=SP)

    monkeypatch.setattr(post_deploy, "setup_lakebase_autoscaling", _setup_as)
    monkeypatch.setattr(post_deploy, "setup_lakebase", _setup_prov)
    monkeypatch.setattr(post_deploy, "attach_lakebase_autoscaling",
                        lambda: order.append("attach-autoscaling"))
    monkeypatch.setattr(post_deploy, "attach_lakebase", lambda: order.append("attach-provisioned"))
    monkeypatch.setattr(post_deploy, "upgrade_app", lambda values, live: order.append("upgrade"))

    def _fake_cli(*args, check=True):
        if args[:2] == ("bundle", "deploy"):
            order.append("bundle-deploy")
        elif args[:2] == ("bundle", "run"):
            order.append("bundle-run")
        elif args[:2] == ("apps", "deploy"):
            order.append("apps-deploy")
        return 0
    monkeypatch.setattr(post_deploy, "cli", _fake_cli)
    monkeypatch.setattr(post_deploy, "cli_json",
                        lambda *a: {"active_deployment": {"source_code_path": "/W/app"}})
    post_deploy._main()
    return updates


def test_bundle_mode_sequence_render_publish_attach(monkeypatch):
    """First-install (bundle) ordering must be: render -> bundle deploy -> bundle run -> attach,
    so `bundle deploy` (which reconciles resources from databricks.yml) can't strip the freshly-
    attached lakebase-db. The attach is the Autoscaling path (no instance set)."""
    order = []
    _drive_main(monkeypatch, get_app=None, env={"WAREHOUSE_ID": "wh", "USE_LAKEBASE": "true"},
                order=order)
    seq = [s for s in order if s in ("render", "bundle-deploy", "bundle-run", "attach-autoscaling")]
    assert seq == ["render", "bundle-deploy", "bundle-run", "attach-autoscaling"]
    assert "attach-provisioned" not in order  # Autoscaling is the default path


def test_attach_sequence_update_then_grant_then_restart(monkeypatch):
    """attach_lakebase_autoscaling must update (attach) -> grant -> restart (apps deploy). Removing
    _restart_app or reordering grant/restart fails here."""
    order = []
    fake = _PgFake(existing=list(_ids().values()))
    monkeypatch.setattr(post_deploy, "APP_NAME", APP)
    monkeypatch.setattr(post_deploy, "LAKEBASE_INSTANCE", "")
    monkeypatch.setattr(post_deploy, "USE_LAKEBASE", True)
    monkeypatch.setattr(post_deploy, "cli_json", fake.cli_json)

    def _fake_cli(*args, check=True):
        if args[:2] == ("apps", "deploy"):
            order.append("restart")
        return 0
    monkeypatch.setattr(post_deploy, "cli", _fake_cli)
    monkeypatch.setattr(post_deploy, "_update_resources",
                        lambda **kw: order.append(("attach", kw)) or True)
    monkeypatch.setattr(post_deploy, "_grant_history", lambda: order.append("grant"))

    post_deploy.setup_lakebase_autoscaling()
    post_deploy.attach_lakebase_autoscaling()

    steps = [s if isinstance(s, str) else s[0] for s in order]
    assert steps == ["attach", "grant", "restart"]
    _, kw = next(s for s in order if isinstance(s, tuple))
    assert kw["postgres"]["database"] == _ids()["database"]
    assert kw.get("lakebase") is None  # not the Provisioned shape


def test_provisioned_path_is_taken_when_instance_name_is_set(monkeypatch):
    """Drive _main with resolved Provisioned settings (LAKEBASE_INSTANCE_NAME set): the Provisioned
    setup + attach run, Autoscaling does not."""
    order = []
    _drive_main(monkeypatch, get_app=None,
                env={"WAREHOUSE_ID": "wh", "USE_LAKEBASE": "true",
                     "LAKEBASE_INSTANCE_NAME": "shared-inst", "LAKEBASE_DATABASE": "ontology_readiness"},
                order=order)
    assert "setup-provisioned" in order and "setup-autoscaling" not in order
    assert "attach-provisioned" in order and "attach-autoscaling" not in order


# --- would-repoint / would-detach guards (blocking 1 & 2) --------------------

DERIVED_BRANCH = f"projects/{APP}/branches/production"
DERIVED_DB = f"{DERIVED_BRANCH}/databases/databricks-postgres"


def test_main_stops_before_any_change_when_live_is_another_project(monkeypatch):
    """BLOCKING 1: an upgrade with no explicit endpoint must NOT silently repoint a live app that is
    attached to another project to the app-name-derived one — it stops before any change."""
    order = []
    app = _as_app("projects/elsewhere/branches/production",
                  "projects/elsewhere/branches/production/databases/databricks-postgres")
    with pytest.raises(SystemExit):
        _drive_main(monkeypatch, get_app=app, env={"USE_LAKEBASE": "true"}, order=order)
    # stopped in resolve_settings, before setup / publish / attach
    assert not any(s in order for s in ("setup-autoscaling", "bundle-deploy", "attach-autoscaling",
                                        "apps-deploy"))


def test_main_stops_when_live_database_resource_differs_on_the_same_branch(monkeypatch):
    """BLOCKING 2: same branch but a different live database RESOURCE (the app uses /databases/d,
    the script would otherwise attach /databases/databricks-postgres) must stop, not switch DBs."""
    order = []
    app = _as_app(DERIVED_BRANCH, f"{DERIVED_BRANCH}/databases/d")
    with pytest.raises(SystemExit):
        _drive_main(monkeypatch, get_app=app, env={"USE_LAKEBASE": "true"}, order=order)
    assert not any(s in order for s in ("setup-autoscaling", "bundle-deploy", "attach-autoscaling"))


def test_main_proceeds_on_legitimate_reuse_same_project_branch_and_database(monkeypatch):
    """The legitimate reuse case (live branch + db == the derived destination) proceeds through
    setup and the in-place upgrade (no bundle deploy for an existing app)."""
    order = []
    app = _as_app(DERIVED_BRANCH, DERIVED_DB)
    _drive_main(monkeypatch, get_app=app, env={"USE_LAKEBASE": "true"}, order=order)
    assert "setup-autoscaling" in order  # not stopped


# --- blocking 3: unresolved postgres_database hard-fails before publish ------

def test_setup_hard_fails_when_postgres_database_cannot_be_resolved(monkeypatch):
    """BLOCKING 3: if get-database has no .status.postgres_database, setup must hard-error rather
    than fall open to the previous/hyphenated LAKEBASE_DATABASE."""
    fake = _PgFake(existing=list(_ids().values()), resolve_db=False)
    _wire(monkeypatch, fake)
    with pytest.raises(SystemExit):
        post_deploy.setup_lakebase_autoscaling()
    # host may resolve, but the DB name did not, so nothing is recorded for publish
    assert post_deploy._LAKEBASE["database"] == ""


def test_postgres_database_name_ignores_spec_and_uses_status_only(monkeypatch):
    """The resolved name must come from .status.postgres_database ONLY (no spec fallback)."""
    monkeypatch.setattr(post_deploy, "cli_json",
                        lambda *a: {"name": a[2], "status": {}, "spec": {"postgres_database": "from_spec"}})
    assert post_deploy._postgres_database_name("projects/p/branches/b/databases/x") == ""
    monkeypatch.setattr(post_deploy, "cli_json",
                        lambda *a: {"name": a[2], "status": {"postgres_database": "from_status"}})
    assert post_deploy._postgres_database_name("projects/p/branches/b/databases/x") == "from_status"
