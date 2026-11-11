"""Tests for the settings reuse on redeploy (scripts/deploy_settings.py) and the
upgrade-mode choices in scripts/post_deploy.py.

No network and no workspace: every CLI/SDK read is a fixture or a stub.
"""
import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import deploy_settings as ds  # noqa: E402
import post_deploy  # noqa: E402

TEMPLATE = (ROOT / "app" / "app.yml.template").read_text()

# A rendered app.yml as a customer's earlier release would have written it.
DEPLOYED_YML = """\
command:
  - python
env:
  - name: DATABRICKS_WAREHOUSE_ID
    valueFrom: sql-warehouse
  - name: ASSESS_CATALOGS
    value: "sales,finance"
  - name: GENIE_SPACE_ID
    value: "space-123"
  - name: BRAND_NAME
    value: 'Acme'
  - name: FORCE_SP
    value: "true"
  - name: USE_LAKEBASE
    value: "true"
  - name: LAKEBASE_HOST
    value: "old-host.example"
  - name: LAKEBASE_USER
    value: "old-sp-id"
  - name: LAKEBASE_DATABASE
    value: "history_db"
  - name: LAKEBASE_INSTANCE_NAME
    value: "shared-inst"
  - name: WORKSPACE_ID
    value: "111"
"""

LAKEBASE_APP = {
    "name": "genie-ontology-readiness-prod",
    "service_principal_client_id": "live-sp-id",
    "active_deployment": {"source_code_path": "/Workspace/Users/a@x/.genie/app"},
    "resources": [
        {"name": "sql-warehouse", "sql_warehouse": {"id": "wh-deployed", "permission": "CAN_USE"}},
        {"name": "lakebase-db", "database": {"instance_name": "shared-inst",
                                             "database_name": "history_db",
                                             "permission": "CAN_CONNECT_AND_CREATE"}},
    ],
}


def _live(app=LAKEBASE_APP, yml=DEPLOYED_YML, readable=True):
    def read_text(path):
        assert path == "/Workspace/Users/a@x/.genie/app/app.yml"
        return yml if readable else None
    return ds.read_live(app, read_text)


def _values(explicit=None, live=None, reset=False, forced=None):
    live = live if live is not None else _live()
    forced = forced if forced is not None else {"GOR_VERSION": "1.2.3", "LAKEBASE_USER": "live-sp-id",
                                                "LAKEBASE_HOST": ""}
    return ds.plan(explicit or {}, live, TEMPLATE, forced=forced,
                   extra_defaults={"WORKSPACE_ID": "999"}, reset=reset)


# --- parsing -----------------------------------------------------------------

def test_parse_env_reads_quoted_and_single_quoted_values():
    env = ds.parse_env(DEPLOYED_YML)
    assert env["ASSESS_CATALOGS"] == "sales,finance"
    assert env["BRAND_NAME"] == "Acme"
    assert env["LAKEBASE_DATABASE"] == "history_db"


def test_parse_env_skips_value_from_entries():
    assert "DATABRICKS_WAREHOUSE_ID" not in ds.parse_env(DEPLOYED_YML)


def test_parse_env_keeps_explicit_empty_value():
    assert ds.parse_env('env:\n  - name: ASSESS_CATALOGS\n    value: ""\n') == {"ASSESS_CATALOGS": ""}


def test_template_keys_match_known_keys():
    """Drift guard: every value key in the template must be a key this module resolves."""
    template_keys = set(ds.parse_env(TEMPLATE))
    assert template_keys == set(ds.KEYS) - {"WAREHOUSE_ID"}


def test_read_resources_warehouse_and_lakebase():
    res = ds.read_resources(LAKEBASE_APP)
    assert res == {"WAREHOUSE_ID": "wh-deployed",
                   "LAKEBASE_INSTANCE_NAME": "shared-inst",
                   "LAKEBASE_DATABASE": "history_db"}


def test_read_resources_absent_lakebase_has_no_keys():
    app = {"resources": [{"name": "sql-warehouse", "sql_warehouse": {"id": "w"}}]}
    assert ds.read_resources(app) == {"WAREHOUSE_ID": "w"}


# --- live app reads ----------------------------------------------------------

def test_read_live_no_app_is_first_install():
    live = ds.read_live(None, lambda p: pytest.fail("must not read a file"))
    assert live["exists"] is False and live["has_deployment"] is False
    assert live["env"] == {} and live["resources"] == {}


def test_read_live_app_without_deployment_does_not_read_files():
    app = {"name": "x", "resources": [], "service_principal_client_id": "sp"}
    live = ds.read_live(app, lambda p: pytest.fail("must not read a file"))
    assert live["exists"] and not live["has_deployment"] and live["yml_readable"]
    assert live["sp"] == "sp"


def test_read_live_unreadable_app_yml_is_flagged():
    live = _live(readable=False)
    assert live["has_deployment"] and not live["yml_readable"] and live["env"] == {}


# --- precedence ----------------------------------------------------------------

def test_precedence_you_set_beats_deployed_beats_default():
    values, problems = _values(explicit={"BRAND_NAME": "Globex"})
    assert values["BRAND_NAME"] == ("Globex", ds.SOURCE_YOU)
    assert values["GENIE_SPACE_ID"] == ("space-123", ds.SOURCE_DEPLOYED)
    assert values["MAX_REQUEST_BYTES"] == ("2097152", ds.SOURCE_DEFAULT)
    assert problems == []


def test_explicit_empty_clears_deployed_value():
    values, _ = _values(explicit={"ASSESS_CATALOGS": ""})
    assert values["ASSESS_CATALOGS"] == ("", ds.SOURCE_YOU)


def test_warehouse_comes_from_resource_not_env():
    values, _ = _values()
    assert values["WAREHOUSE_ID"] == ("wh-deployed", ds.SOURCE_DEPLOYED)


def test_explicit_warehouse_wins_over_deployed():
    values, _ = _values(explicit={"WAREHOUSE_ID": "wh-new"})
    assert values["WAREHOUSE_ID"] == ("wh-new", ds.SOURCE_YOU)


def test_new_template_key_missing_from_deployed_gets_template_default():
    """A key the deployed app.yml never had keeps the new release's default (secure default)."""
    values, _ = _values()
    assert values["GENIE_ALLOW_SP_FALLBACK"] == ("false", ds.SOURCE_DEFAULT)
    assert values["LAKEBASE_ENDPOINT_NAME"] == ("", ds.SOURCE_DEFAULT)


def test_workspace_id_extra_default_used_when_nothing_else_set():
    live = ds.read_live(None, lambda p: None)
    values, _ = _values(live=live)
    assert values["WORKSPACE_ID"] == ("999", ds.SOURCE_DEFAULT)


def test_lakebase_user_and_host_forced_to_live_values():
    values, _ = _values(explicit={"LAKEBASE_USER": "typed-by-customer", "LAKEBASE_HOST": "typed.example"})
    assert values["LAKEBASE_USER"] == ("live-sp-id", ds.SOURCE_LIVE)
    assert values["LAKEBASE_HOST"] == ("", ds.SOURCE_LIVE)


def test_gor_version_always_from_new_release():
    values, _ = _values(explicit={"GOR_VERSION": "0.0.1"})
    assert values["GOR_VERSION"] == ("1.2.3", ds.SOURCE_RELEASE)


# --- safety stops ----------------------------------------------------------------

def test_keeps_lakebase_when_nothing_is_set():
    values, problems = _values()
    assert values["USE_LAKEBASE"] == ("true", ds.SOURCE_DEPLOYED)
    assert problems == []


def test_turning_lakebase_off_stops():
    _, problems = _values(explicit={"USE_LAKEBASE": "false"})
    assert len(problems) == 1 and "turns Lakebase off" in problems[0]


def test_different_instance_stops():
    _, problems = _values(explicit={"LAKEBASE_INSTANCE_NAME": "other-inst"})
    assert len(problems) == 1 and "points the app at Lakebase instance" in problems[0]


def test_different_database_stops():
    _, problems = _values(explicit={"LAKEBASE_DATABASE": "fresh_db"})
    assert len(problems) == 1 and "points the app at Lakebase database" in problems[0]


def test_same_instance_and_database_explicit_is_fine():
    _, problems = _values(explicit={"LAKEBASE_INSTANCE_NAME": "shared-inst",
                                    "LAKEBASE_DATABASE": "history_db"})
    assert problems == []


def test_deployed_lakebase_detected_from_resource_alone():
    """A detached app.yml (USE_LAKEBASE false) with the lakebase-db resource still counts as Lakebase."""
    live = _live(yml='env:\n  - name: USE_LAKEBASE\n    value: "false"\n')
    assert ds.uses_lakebase(live["env"], live["resources"])
    values, problems = _values(live=live)
    assert values["USE_LAKEBASE"].value == "false"
    assert len(problems) == 1 and "turns Lakebase off" in problems[0]


def test_no_lakebase_deployed_means_no_lakebase_stop():
    app = {"resources": [{"name": "sql-warehouse", "sql_warehouse": {"id": "w"}}]}
    live = ds.read_live(app, lambda p: None)
    values, problems = _values(explicit={"USE_LAKEBASE": "true", "LAKEBASE_INSTANCE_NAME": "new"},
                               live=live)
    assert problems == []
    assert values["USE_LAKEBASE"].value == "true"


def test_unreadable_app_yml_with_deployment_stops():
    _, problems = _values(live=_live(readable=False))
    assert len(problems) == 1 and "cannot be read" in problems[0]


def test_unreadable_app_yml_without_deployment_does_not_stop():
    app = {"resources": [], "active_deployment": None}
    _, problems = _values(live=ds.read_live(app, lambda p: None))
    assert problems == []


def test_reset_ignores_deployed_and_skips_stops():
    values, problems = _values(explicit={"WAREHOUSE_ID": "wh-new"}, reset=True)
    assert problems == []
    assert values["USE_LAKEBASE"] == ("false", ds.SOURCE_DEFAULT)
    assert values["GENIE_SPACE_ID"] == ("", ds.SOURCE_DEFAULT)
    assert values["WAREHOUSE_ID"] == ("wh-new", ds.SOURCE_YOU)


def test_reset_skips_unreadable_yml_stop():
    _, problems = _values(live=_live(readable=False), reset=True)
    assert problems == []


def test_first_install_uses_explicit_and_defaults():
    live = ds.read_live(None, lambda p: None)
    values, problems = _values(explicit={"WAREHOUSE_ID": "wh-1", "USE_LAKEBASE": "true",
                                         "LAKEBASE_INSTANCE_NAME": "inst"}, live=live)
    assert problems == []
    assert values["WAREHOUSE_ID"] == ("wh-1", ds.SOURCE_YOU)
    assert values["LAKEBASE_DATABASE"] == ("ontology_readiness", ds.SOURCE_DEFAULT)


def test_choose_mode():
    assert ds.choose_mode(app_exists=True, infra=False) == "upgrade"
    assert ds.choose_mode(app_exists=True, infra=True) == "bundle"
    assert ds.choose_mode(app_exists=False, infra=False) == "bundle"
    assert ds.choose_mode(app_exists=False, infra=True) == "bundle"


# --- rendering and display -----------------------------------------------------

def test_render_sets_resolved_values_and_reports_missing():
    values, _ = _values(explicit={"BRAND_NAME": 'Ac"me'})
    rendered = {k: v.value for k, v in values.items()}
    text, missing = ds.render(TEMPLATE, rendered)
    assert missing == []
    assert 'value: "2097152"' in text
    assert 'value: "Acme"' in text  # quote removed, not breaking the YAML
    assert ds.parse_env(text)["GENIE_SPACE_ID"] == "space-123"
    _, missing = ds.render(TEMPLATE.replace("- name: BRAND_NAME", "- name: OTHER"), {"BRAND_NAME": "x"})
    assert missing == []  # not a template key, so skipped silently


def test_render_warehouse_is_not_written_into_app_yml():
    text, missing = ds.render(TEMPLATE, {"WAREHOUSE_ID": "wh"})
    assert text == TEMPLATE and missing == []


def test_format_table_lists_every_key_with_source_and_no_blank_host():
    values, _ = _values()
    table = "\n".join(ds.format_table(values))
    for key in ds.KEYS:
        assert key in table
    assert "(from the instance)" in table
    assert "deployed app" in table and "new release" in table


def test_settings_module_is_stdlib_only():
    tree = ast.parse((ROOT / "scripts" / "deploy_settings.py").read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module.split(".")[0])
    assert names <= set(sys.stdlib_module_names)


# --- post_deploy: flags, app reads, upgrade mode ---------------------------------

def test_reset_flag_rejects_typos(monkeypatch):
    monkeypatch.setenv("RESET_SETTINGS", "ture")
    with pytest.raises(SystemExit):
        post_deploy.reset_requested()
    monkeypatch.setenv("RESET_SETTINGS", "true")
    assert post_deploy.reset_requested() is True
    monkeypatch.delenv("RESET_SETTINGS")
    assert post_deploy.reset_requested() is False


def test_infra_flag(monkeypatch):
    monkeypatch.setenv("INFRA", "true")
    assert post_deploy.infra_requested() is True
    monkeypatch.delenv("INFRA")
    assert post_deploy.infra_requested() is False


def test_explicit_from_env_keeps_set_empty_and_skips_unset(monkeypatch):
    monkeypatch.setenv("ASSESS_CATALOGS", "")
    monkeypatch.delenv("GENIE_SPACE_ID", raising=False)
    monkeypatch.delenv("WAREHOUSE_ID", raising=False)
    monkeypatch.delenv("DATABRICKS_WAREHOUSE_ID", raising=False)
    out = post_deploy.explicit_from_env()
    assert out.get("ASSESS_CATALOGS") == ""
    assert "GENIE_SPACE_ID" not in out and "WAREHOUSE_ID" not in out


def test_explicit_from_env_legacy_warehouse_var(monkeypatch):
    monkeypatch.delenv("WAREHOUSE_ID", raising=False)
    monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "legacy-wh")
    assert post_deploy.explicit_from_env()["WAREHOUSE_ID"] == "legacy-wh"


class _Done:
    def __init__(self, rc, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = rc, stdout, stderr


def test_get_app_not_found_is_none(monkeypatch):
    monkeypatch.setattr(post_deploy, "PROFILE", "p")
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: _Done(1, stderr="Error: App 'x' does not exist"))
    assert post_deploy._get_app("x") is None


def test_get_app_other_error_stops(monkeypatch):
    """A failed read must never look like 'no app': that would skip every safety stop."""
    monkeypatch.setattr(post_deploy, "PROFILE", "p")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Done(1, stderr="401 unauthorized"))
    with pytest.raises(SystemExit):
        post_deploy._get_app("x")


def test_get_app_success_returns_json(monkeypatch):
    monkeypatch.setattr(post_deploy, "PROFILE", "p")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Done(0, stdout='{"name": "x"}'))
    assert post_deploy._get_app("x") == {"name": "x"}


def _spy_upgrade(monkeypatch, *, warehouse, have_wh, have_lb):
    """Run upgrade_app with every CLI/SDK call stubbed. Returns the calls it made.

    warehouse  the warehouse id this run resolves to
    have_wh    the warehouse id the deployed app has (None = no sql-warehouse resource)
    have_lb    whether the deployed app already has lakebase-db (shared-inst / history_db)
    """
    calls = []
    monkeypatch.setattr(post_deploy, "cli", lambda *a, **k: (calls.append(("cli", a)), 0)[1])
    monkeypatch.setattr(post_deploy, "cli_json",
                        lambda *a: {"name": "x", "compute_status": {"state": "ACTIVE"}})
    monkeypatch.setattr(post_deploy, "_update_resources",
                        lambda **kw: (calls.append(("update", kw)), True)[1])
    monkeypatch.setattr(post_deploy, "_grant_history", lambda: calls.append(("grant", None)))
    monkeypatch.setattr(post_deploy, "stage_source", lambda: "/Workspace/Users/me/.genie/x")
    monkeypatch.setattr(post_deploy, "USE_LAKEBASE", True)
    monkeypatch.setattr(post_deploy, "LAKEBASE_INSTANCE", "shared-inst")
    monkeypatch.setattr(post_deploy, "LAKEBASE_DATABASE", "history_db")
    monkeypatch.setitem(post_deploy._LAKEBASE, "host", "h")
    resources = {}
    if have_wh:
        resources["WAREHOUSE_ID"] = have_wh
    if have_lb:
        resources.update({"LAKEBASE_INSTANCE_NAME": "shared-inst", "LAKEBASE_DATABASE": "history_db"})
    live = {"resources": resources, "env": {}, "exists": True, "has_deployment": True,
            "yml_readable": True, "sp": "sp"}
    values, _ = _values(explicit={"WAREHOUSE_ID": warehouse}, live=ds.read_live(None, lambda p: None))
    post_deploy.upgrade_app(values, live)
    return calls


def test_upgrade_never_runs_bundle_deploy_and_deploys_staged_source(monkeypatch):
    calls = _spy_upgrade(monkeypatch, warehouse="wh-1", have_wh="wh-1", have_lb=True)
    assert all(c[0] != "cli" or c[1][0] != "bundle" for c in calls)
    deploys = [c[1] for c in calls if c[0] == "cli" and c[1][:2] == ("apps", "deploy")]
    assert len(deploys) == 1
    args = list(deploys[0])
    assert args[args.index("--source-code-path") + 1] == "/Workspace/Users/me/.genie/x"
    assert "--mode" in args and args[args.index("--mode") + 1] == "SNAPSHOT"


def test_upgrade_leaves_resources_alone_when_they_match(monkeypatch):
    calls = _spy_upgrade(monkeypatch, warehouse="wh-1", have_wh="wh-1", have_lb=True)
    assert not [c for c in calls if c[0] == "update"]
    assert [c for c in calls if c[0] == "grant"]


def test_upgrade_updates_only_lakebase_when_lakebase_missing(monkeypatch):
    calls = _spy_upgrade(monkeypatch, warehouse="wh-1", have_wh="wh-1", have_lb=False)
    updates = [c[1] for c in calls if c[0] == "update"]
    assert updates == [{"warehouse_id": None,
                        "lakebase": {"instance": "shared-inst", "database": "history_db"}}]


def test_upgrade_updates_warehouse_when_it_changed(monkeypatch):
    calls = _spy_upgrade(monkeypatch, warehouse="wh-new", have_wh="wh-old", have_lb=True)
    updates = [c[1] for c in calls if c[0] == "update"]
    assert updates == [{"warehouse_id": "wh-new",
                        "lakebase": {"instance": "shared-inst", "database": "history_db"}}]
