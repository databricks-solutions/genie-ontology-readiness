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
ROLE_ID = "gor-sp-uuid-123"  # _pg_role_id(SP): a valid resource id (lowercase/hyphen, letter-prefixed)


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


class _Done:
    def __init__(self, rc, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = rc, stdout, stderr


class _PgFake:
    """A stand-in Databricks CLI.

    The create-gating reads go through _pg_get (subprocess.run): a get-* returns the resource only
    when it already exists, an explicit NOT_FOUND when missing, or (via `get_error`) a non-NOT_FOUND
    failure that must hard-stop. Each create-* registers its resource so a later read (host /
    resolved db name, via cli_json) succeeds — exactly as a real create would. Records every `cli`
    (write) call so a test can assert create-vs-reuse, ordering, and that --replace-existing is
    never passed for the history database/endpoint."""

    def __init__(self, existing=(), resolve_db=True, get_error=None, create_fail=None,
                 endpoint_host="ep-x.database.cloud.databricks.com", read_only_endpoints=()):
        self.existing = set(existing)
        self.resolve_db = resolve_db  # False: get-database returns a status WITHOUT postgres_database
        self.get_error = get_error    # (verb, stderr) -> a non-NOT_FOUND read failure on that verb
        self.create_fail = create_fail  # a create-* verb that returns non-zero (rc 1)
        self.endpoint_host = endpoint_host  # "" -> endpoint host never populates
        self.read_only_endpoints = set(read_only_endpoints)  # excluded from list-endpoints RW set
        self.cli_calls = []
        self.updates = []             # apps update payloads (the attach)

    def _resource_json(self, verb, name):
        if verb == "get-endpoint":
            return {"name": name, "status": {"hosts": {"host": self.endpoint_host}}}
        if verb == "get-database":
            status = {"postgres_database": "databricks_postgres"} if self.resolve_db else {}
            return {"name": name, "status": status}
        return {"name": name}

    def subprocess_run(self, cmd, capture_output=False, text=False, **k):
        # _pg_get: ["databricks", "postgres", <verb>, <name>, "--output", "json", ...]
        verb, name = cmd[2], cmd[3]
        if self.get_error and self.get_error[0] == verb:
            return _Done(1, stderr=self.get_error[1])
        if name in self.existing:
            return _Done(0, stdout=json.dumps(self._resource_json(verb, name)))
        return _Done(1, stderr=f"Error: {name} does not exist (RESOURCE_DOES_NOT_EXIST)")

    def cli_json(self, *args):
        a = list(args)
        if a[:2] == ["current-user", "me"]:
            return {"userName": "me@example.com"}
        if a[:2] == ["apps", "get"]:
            return {"service_principal_client_id": SP,
                    "active_deployment": {"source_code_path": "/Workspace/Users/me/app"},
                    "resources": [{"name": "sql-warehouse", "sql_warehouse": {"id": "wh"}}]}
        if a[:2] == ["apps", "update"]:
            self.updates.append(json.loads(a[a.index("--json") + 1]))
            return {"name": APP}
        if a[:2] == ["postgres", "list-endpoints"]:  # reuse: find the branch's read-write endpoint
            branch = a[2].rstrip("/")
            eps = [n for n in self.existing
                   if n.startswith(branch + "/endpoints/") and n not in self.read_only_endpoints]
            return {"endpoints": [{"name": n, "status": {"endpoint_type": "ENDPOINT_TYPE_READ_WRITE"}}
                                  for n in sorted(eps)]}
        if a and a[0] == "postgres" and a[1].startswith("get-"):  # host / db-name resolution reads
            name = a[2]
            return self._resource_json(a[1], name) if name in self.existing else None
        return None

    def cli(self, *args, check=True):
        self.cli_calls.append(args)
        verb = args[1] if len(args) > 1 else ""
        if self.create_fail and verb == self.create_fail:
            return 1  # create failed; _pg_create must _stop
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


FIRST_INSTALL = {"resources": {}}  # no deployed lakebase-db resource


def _wire(monkeypatch, fake, *, endpoint_setting=""):
    monkeypatch.setattr(post_deploy, "APP_NAME", APP)
    monkeypatch.setattr(post_deploy, "PROFILE", "")
    monkeypatch.setattr(post_deploy, "LAKEBASE_ENDPOINT_NAME", endpoint_setting)
    monkeypatch.setattr(post_deploy, "LAKEBASE_INSTANCE", "")
    monkeypatch.setattr(post_deploy, "cli_json", fake.cli_json)
    monkeypatch.setattr(post_deploy, "cli", fake.cli)
    monkeypatch.setattr(subprocess, "run", fake.subprocess_run)


# --- sanitizers / destination ------------------------------------------------

def test_role_id_is_a_valid_resource_id():
    import re
    rid = post_deploy._pg_role_id(SP)
    assert rid == ROLE_ID
    # valid Lakebase resource id: starts with a lowercase letter, only lowercase/digits/hyphens
    assert re.fullmatch(r"[a-z][a-z0-9-]*", rid) and len(rid) <= 63
    assert post_deploy._pg_role_id("123-ABC")[0].isalpha()
    assert post_deploy._pg_role_id("123-ABC") == post_deploy._pg_role_id("123-ABC").lower()


def test_project_id_is_a_valid_resource_id():
    assert post_deploy._pg_id(APP) == APP  # already valid
    assert post_deploy._pg_id("9Weird Name!")[0].isalpha()
    assert post_deploy._pg_id("") == "gor"


def test_destination_parses_explicit_endpoint_else_reuses_deployed_else_derives(monkeypatch):
    monkeypatch.setattr(post_deploy, "APP_NAME", APP)
    # 1. explicit endpoint names the branch/endpoint
    dest = post_deploy._autoscaling_destination("projects/other/branches/dev/endpoints/e1", {})
    assert (dest["branch"], dest["endpoint_id"], dest["database_id"]) == (
        "projects/other/branches/dev", "e1", "databricks-postgres")
    # 2. already attached, no explicit: reuse the DEPLOYED branch + database resource (non-default db).
    #    endpoint_id is None -> setup must find the branch's EXISTING read-write endpoint.
    deployed = {"LAKEBASE_BRANCH": "projects/p/branches/production",
                "LAKEBASE_DATABASE_RESOURCE": "projects/p/branches/production/databases/d"}
    dest = post_deploy._autoscaling_destination("", deployed)
    assert dest == {"branch": "projects/p/branches/production", "endpoint_id": None,
                    "database_resource": "projects/p/branches/production/databases/d", "database_id": "d"}
    # 3. first install: derive from the app name
    dest = post_deploy._autoscaling_destination("", {})
    assert dest["branch"] == f"projects/{APP}/branches/production"
    assert dest["database_id"] == "databricks-postgres"
    # explicit endpoint on the SAME branch as the deployed resource keeps the deployed database
    dest = post_deploy._autoscaling_destination("projects/p/branches/production/endpoints/x", deployed)
    assert dest["database_resource"] == "projects/p/branches/production/databases/d"


# --- _pg_get tri-state (fail-closed) -----------------------------------------

def test_pg_get_success_but_null_or_non_dict_stops(monkeypatch):
    """Exit 0 with a null/non-dict body is NOT 'missing' — STOP rather than let _ensure_* create."""
    monkeypatch.setattr(post_deploy, "PROFILE", "")
    for out in ("null", "[]", "\"x\"", ""):
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Done(0, stdout=out))
        with pytest.raises(SystemExit):
            post_deploy._pg_get("get-database", "projects/p/branches/b/databases/x")


def test_pg_get_auth_error_mentioning_not_found_stops(monkeypatch):
    """An auth/token error whose text happens to contain 'not found' must STOP, not be read as
    resource-absent (which would create/replace a live resource)."""
    monkeypatch.setattr(post_deploy, "PROFILE", "")
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: _Done(1, stderr="401 Unauthorized: token not found"))
    with pytest.raises(SystemExit):
        post_deploy._pg_get("get-database", "projects/p/branches/b/databases/x")


def test_pg_get_explicit_resource_not_found_returns_none(monkeypatch):
    monkeypatch.setattr(post_deploy, "PROFILE", "")
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: _Done(1, stderr="RESOURCE_DOES_NOT_EXIST: no such database"))
    assert post_deploy._pg_get("get-database", "projects/p/branches/b/databases/x") is None


def test_pg_get_unrecognised_error_stops(monkeypatch):
    monkeypatch.setattr(post_deploy, "PROFILE", "")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Done(1, stderr="weird backend hiccup"))
    with pytest.raises(SystemExit):
        post_deploy._pg_get("get-database", "projects/p/branches/b/databases/x")


# --- idempotent create-if-missing AND reuse-if-exists ------------------------

def test_setup_autoscaling_creates_everything_when_missing(monkeypatch):
    fake = _PgFake(existing=[])
    _wire(monkeypatch, fake)
    post_deploy.setup_lakebase_autoscaling(FIRST_INSTALL)

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
    post_deploy.setup_lakebase_autoscaling(FIRST_INSTALL)

    assert fake.cli_calls == []  # nothing created; all reused
    assert post_deploy._LAKEBASE["database"] == "databricks_postgres"
    assert post_deploy._LAKEBASE["endpoint"] == ids["endpoint"]


def test_setup_autoscaling_creates_only_the_missing_role(monkeypatch):
    ids = _ids()
    present = [ids["project"], ids["branch"], ids["endpoint"], ids["database"]]  # role missing
    fake = _PgFake(existing=present)
    _wire(monkeypatch, fake)
    post_deploy.setup_lakebase_autoscaling(FIRST_INSTALL)

    assert [c[1] for c in fake.cli_calls] == ["create-role"]
    role_call = fake.cli_calls[0]
    assert role_call[2] == ids["branch"] and role_call[role_call.index("--role-id") + 1] == ROLE_ID


def test_create_role_body_is_a_service_principal_role_with_the_uuid(monkeypatch):
    fake = _PgFake(existing=[])
    _wire(monkeypatch, fake)
    post_deploy.setup_lakebase_autoscaling(FIRST_INSTALL)
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
    post_deploy.setup_lakebase_autoscaling(FIRST_INSTALL)
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
    post_deploy.setup_lakebase_autoscaling(FIRST_INSTALL)

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

    def _setup_as(live_arg):
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
    monkeypatch.setattr(post_deploy, "PROFILE", "")
    monkeypatch.setattr(post_deploy, "LAKEBASE_INSTANCE", "")
    monkeypatch.setattr(post_deploy, "LAKEBASE_ENDPOINT_NAME", "")
    monkeypatch.setattr(post_deploy, "USE_LAKEBASE", True)
    monkeypatch.setattr(post_deploy, "cli_json", fake.cli_json)
    monkeypatch.setattr(subprocess, "run", fake.subprocess_run)

    def _fake_cli(*args, check=True):
        if args[:2] == ("apps", "deploy"):
            order.append("restart")
        return 0
    monkeypatch.setattr(post_deploy, "cli", _fake_cli)
    monkeypatch.setattr(post_deploy, "_update_resources",
                        lambda **kw: order.append(("attach", kw)) or True)
    monkeypatch.setattr(post_deploy, "_grant_history", lambda: order.append("grant"))

    post_deploy.setup_lakebase_autoscaling(FIRST_INSTALL)
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


# --- read-error fail-closed (critical + major 1): never recreate/replace on a failed read ----

def test_ensure_database_read_error_stops_and_never_creates(monkeypatch):
    """CRITICAL: a non-NOT_FOUND read of the history database (auth/permission/transient) must STOP,
    never fall through to create (which with --replace-existing would REPLACE live history)."""
    ids = _ids()
    present = [ids["project"], ids["branch"], ids["endpoint"]]  # db read will error, not 404
    fake = _PgFake(existing=present, get_error=("get-database", "403 PERMISSION_DENIED"))
    _wire(monkeypatch, fake)
    with pytest.raises(SystemExit):
        post_deploy.setup_lakebase_autoscaling(FIRST_INSTALL)
    assert not any(c[1] == "create-database" for c in fake.cli_calls)


def test_ensure_endpoint_read_error_stops_and_never_creates(monkeypatch):
    """MAJOR: a non-NOT_FOUND read of the live compute endpoint must STOP, not recreate it."""
    ids = _ids()
    fake = _PgFake(existing=[ids["project"], ids["branch"]],
                   get_error=("get-endpoint", "503 temporarily unavailable"))
    _wire(monkeypatch, fake)
    with pytest.raises(SystemExit):
        post_deploy.setup_lakebase_autoscaling(FIRST_INSTALL)
    assert not any(c[1] == "create-endpoint" for c in fake.cli_calls)


def test_creates_on_not_found_never_pass_replace_existing(monkeypatch):
    """NOT_FOUND still creates — but the history database and endpoint are created WITHOUT
    --replace-existing (replacing them would destroy/disrupt history)."""
    fake = _PgFake(existing=[])
    _wire(monkeypatch, fake)
    post_deploy.setup_lakebase_autoscaling(FIRST_INSTALL)
    for c in fake.cli_calls:
        if c[1] in ("create-database", "create-endpoint"):
            assert "--replace-existing" not in c


# --- major 3: reuse the DEPLOYED database (no hardcoded databricks-postgres) ------------------

DERIVED_BRANCH = f"projects/{APP}/branches/production"
DERIVED_DB = f"{DERIVED_BRANCH}/databases/databricks-postgres"


def _attached_live(branch, db_resource):
    return {"resources": {"LAKEBASE_BRANCH": branch, "LAKEBASE_DATABASE_RESOURCE": db_resource}}


def test_setup_reuses_the_deployed_non_default_db_and_existing_endpoint(monkeypatch):
    """An app attached to a non-default database resource with a read-write endpoint whose id is NOT
    'primary' (a typical Apps-UI deploy) must REUSE both — not switch to databricks-postgres and not
    create a second 'primary' endpoint. Nothing is created."""
    branch = DERIVED_BRANCH
    db = f"{branch}/databases/custom-db"
    ep = f"{branch}/endpoints/rw-abc"  # NOT 'primary'
    present = [f"projects/{APP}", branch, ep, db, f"{branch}/roles/{ROLE_ID}"]
    fake = _PgFake(existing=present)
    _wire(monkeypatch, fake)
    post_deploy.setup_lakebase_autoscaling(_attached_live(branch, db))
    assert fake.cli_calls == []  # nothing created
    assert post_deploy._LAKEBASE["endpoint"] == ep  # reused the existing RW endpoint, not a new 'primary'
    assert post_deploy._LAKEBASE["database_resource"] == db  # the deployed db, not databricks-postgres


def test_setup_stops_when_attached_branch_has_no_read_write_endpoint(monkeypatch):
    """An attached branch with no read-write endpoint this script can find must STOP and ask for
    LAKEBASE_ENDPOINT_NAME — never create a second endpoint on a live history branch."""
    branch = DERIVED_BRANCH
    db = f"{branch}/databases/custom-db"
    present = [f"projects/{APP}", branch, db, f"{branch}/roles/{ROLE_ID}"]  # no endpoint
    fake = _PgFake(existing=present)
    _wire(monkeypatch, fake)
    with pytest.raises(SystemExit):
        post_deploy.setup_lakebase_autoscaling(_attached_live(branch, db))
    assert not any(c[1] == "create-endpoint" for c in fake.cli_calls)


def test_setup_retains_another_projects_deployed_branch_and_database(monkeypatch):
    """Real-setup check for the other-project reuse: an app attached to a DIFFERENT project retains
    that project's deployed branch/database/endpoint and creates nothing (no repoint to the derived
    app-name project)."""
    branch = "projects/elsewhere/branches/production"
    db = f"{branch}/databases/d"
    ep = f"{branch}/endpoints/rw"
    present = ["projects/elsewhere", branch, ep, db, f"{branch}/roles/{ROLE_ID}"]
    fake = _PgFake(existing=present)
    _wire(monkeypatch, fake)
    post_deploy.setup_lakebase_autoscaling(_attached_live(branch, db))
    assert fake.cli_calls == []  # nothing created; the other project is retained
    assert post_deploy._LAKEBASE["branch"] == branch
    assert post_deploy._LAKEBASE["database_resource"] == db
    assert post_deploy._LAKEBASE["endpoint"] == ep


def test_main_reuses_when_attached_to_another_project_without_an_override(monkeypatch):
    """MAJOR 3: an upgrade with no explicit override of an app attached elsewhere PROCEEDS by reusing
    the deployed resource (it does not stop, and does not repoint to the app-name-derived project)."""
    order = []
    app = _as_app("projects/elsewhere/branches/production",
                  "projects/elsewhere/branches/production/databases/d")
    _drive_main(monkeypatch, get_app=app, env={"USE_LAKEBASE": "true"}, order=order)
    assert "setup-autoscaling" in order  # reused, not stopped


def test_main_proceeds_on_legitimate_reuse_same_project_branch_and_database(monkeypatch):
    """The legitimate reuse case (live branch + db == the derived destination) proceeds through
    setup and the in-place upgrade (no bundle deploy for an existing app)."""
    order = []
    app = _as_app(DERIVED_BRANCH, DERIVED_DB)
    _drive_main(monkeypatch, get_app=app, env={"USE_LAKEBASE": "true"}, order=order)
    assert "setup-autoscaling" in order  # not stopped


def test_main_stops_on_explicit_endpoint_override_to_a_different_branch(monkeypatch):
    """A genuine override — an explicit LAKEBASE_ENDPOINT_NAME on a different branch than the
    deployed one — still STOPS before any change (would repoint history)."""
    order = []
    app = _as_app(DERIVED_BRANCH, DERIVED_DB)
    with pytest.raises(SystemExit):
        _drive_main(monkeypatch, get_app=app,
                    env={"USE_LAKEBASE": "true",
                         "LAKEBASE_ENDPOINT_NAME": "projects/elsewhere/branches/production/endpoints/primary"},
                    order=order)
    assert not any(s in order for s in ("setup-autoscaling", "attach-autoscaling", "upgrade"))


# --- major 4: RESET_SETTINGS must NOT bypass detach/repoint ------------------------------------

def test_reset_settings_still_stops_detach(monkeypatch):
    """RESET_SETTINGS=true with USE_LAKEBASE=false must still STOP (never silently detach history)."""
    order = []
    app = _as_app(DERIVED_BRANCH, DERIVED_DB)
    with pytest.raises(SystemExit):
        _drive_main(monkeypatch, get_app=app,
                    env={"RESET_SETTINGS": "true", "USE_LAKEBASE": "false", "WAREHOUSE_ID": "wh"},
                    order=order)
    assert not any(s in order for s in ("setup-autoscaling", "attach-autoscaling", "upgrade"))


def test_reset_settings_still_stops_repoint_override(monkeypatch):
    """RESET_SETTINGS=true with an explicit endpoint on another project must still STOP (no repoint)."""
    order = []
    app = _as_app(DERIVED_BRANCH, DERIVED_DB)
    with pytest.raises(SystemExit):
        _drive_main(monkeypatch, get_app=app,
                    env={"RESET_SETTINGS": "true", "WAREHOUSE_ID": "wh",
                         "LAKEBASE_ENDPOINT_NAME": "projects/elsewhere/branches/production/endpoints/primary"},
                    order=order)
    assert not any(s in order for s in ("setup-autoscaling", "attach-autoscaling", "upgrade"))


# --- blocking 3: unresolved postgres_database hard-fails before publish ------

def test_setup_hard_fails_when_postgres_database_cannot_be_resolved(monkeypatch):
    """BLOCKING 3: if get-database has no .status.postgres_database, setup must hard-error rather
    than fall open to the previous/hyphenated LAKEBASE_DATABASE."""
    fake = _PgFake(existing=list(_ids().values()), resolve_db=False)
    _wire(monkeypatch, fake)
    with pytest.raises(SystemExit):
        post_deploy.setup_lakebase_autoscaling(FIRST_INSTALL)
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


# --- bug 3: a failed create stops the run ------------------------------------

def test_a_failed_create_role_stops_the_run(monkeypatch):
    """A failed create-* (e.g. a rejected role id or a quota/permission error) must STOP — not
    leave the SP without a Postgres role while the run attaches and reports success."""
    fake = _PgFake(existing=[], create_fail="create-role")
    _wire(monkeypatch, fake)
    with pytest.raises(SystemExit):
        post_deploy.setup_lakebase_autoscaling(FIRST_INSTALL)
    assert any(c[1] == "create-role" for c in fake.cli_calls)  # it was attempted, and it stopped


# --- bug 4: a freshly-created endpoint's host must be populated before publish ----------------

def test_setup_stops_when_endpoint_host_never_populates(monkeypatch):
    """If the endpoint host stays empty, setup must STOP (not render history OFF and fall through)."""
    fake = _PgFake(existing=list(_ids().values()), endpoint_host="")
    _wire(monkeypatch, fake)
    monkeypatch.setattr(post_deploy, "AUTOSCALING_HOST_WAIT", 0)  # don't actually sleep
    with pytest.raises(SystemExit):
        post_deploy.setup_lakebase_autoscaling(FIRST_INSTALL)
    assert post_deploy._LAKEBASE["host"] == ""  # nothing recorded for publish


# --- bug 6: an attached-but-empty resource must not be treated as first install ---------------

def test_main_stops_when_attached_autoscaling_resource_is_empty(monkeypatch):
    """postgres={} (attached, but no branch/database) must STOP — never assume a first install and
    create a NEW project that orphans the existing history."""
    order = []
    app = _as_app("", "")  # empty branch + database resource
    with pytest.raises(SystemExit):
        _drive_main(monkeypatch, get_app=app, env={"USE_LAKEBASE": "true"}, order=order)
    assert not any(s in order for s in ("setup-autoscaling", "bundle-deploy", "attach-autoscaling",
                                        "upgrade"))
