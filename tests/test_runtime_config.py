"""Runtime fallbacks for the app, the GRANT statements, and the version file.

No workspace: the SDK-backed lookup of the lakebase-db resource is replaced with a stub.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

from server import grants, lakebase_client, runtime_config as rc  # noqa: E402
from server import user_agent  # noqa: E402


def test_lakebase_user_falls_back_to_app_client_id():
    assert rc.resolve_lakebase_user("", "sp-client") == ("sp-client", "runtime (client id)")
    assert rc.resolve_lakebase_user("explicit", "sp-client") == ("explicit", "app setting")


def test_workspace_id_falls_back_to_injected_id():
    assert rc.resolve_workspace_id("", "123") == ("123", "runtime")
    assert rc.resolve_workspace_id("9", "123") == ("9", "app setting")


def test_database_order_setting_then_resource_then_default():
    assert rc.resolve_database("mine", "from_resource") == ("mine", "app setting")
    assert rc.resolve_database("", "from_resource") == ("from_resource", "runtime")
    assert rc.resolve_database("", "") == ("ontology_readiness", "default")


def test_instance_order_setting_then_resource():
    assert rc.resolve_instance("", "inst") == ("inst", "runtime")
    assert rc.resolve_instance("", "") == ("", "default")


@pytest.mark.parametrize("raw,host,expected", [
    ("auto", "h.example", True), ("auto", "", False), ("", "h", True), ("", "", False),
    ("true", "", True), ("false", "h", False), ("TRUE", "", True),
])
def test_use_lakebase_auto(raw, host, expected):
    assert rc.resolve_use_lakebase(raw, host) is expected


def test_grant_statements_with_sp_id_and_catalogs():
    stmts = grants.grant_statements("abc-123", ["sales"])
    assert stmts[0] == "GRANT USE CATALOG ON CATALOG system TO `abc-123`"
    assert "GRANT USE SCHEMA ON SCHEMA system.access TO `abc-123`" in stmts
    assert "GRANT SELECT ON SCHEMA system.access TO `abc-123`" in stmts
    assert "GRANT USE CATALOG ON CATALOG `sales` TO `abc-123`" in stmts
    assert "GRANT SELECT ON CATALOG `sales` TO `abc-123`" in stmts


def test_grant_statements_placeholder_when_no_sp_id():
    stmts = grants.grant_statements("")
    assert all("<app-service-principal-id>" in s for s in stmts)


def test_grant_statements_escape_backticks():
    assert grants._ident("we`ird") == "`we``ird`"


def test_grant_statements_match_setup_script():
    """The statements must stay in step with scripts/setup_app_permissions.py."""
    script = (ROOT / "scripts" / "setup_app_permissions.py").read_text()
    for needle in ("GRANT USE CATALOG ON CATALOG system", "GRANT USE SCHEMA ON SCHEMA system.",
                   "GRANT SELECT ON SCHEMA system.", "GRANT USE SCHEMA ON CATALOG {cat_id}",
                   "GRANT SELECT ON CATALOG {cat_id}"):
        assert needle in script


def test_version_file_is_normalized(tmp_path):
    f = tmp_path / "VERSION"
    f.write_text("v1.3.0\n")
    assert user_agent.version_file_version(f) == "1.3.0"


def test_version_file_missing_or_empty(tmp_path):
    assert user_agent.version_file_version(tmp_path / "nope") is None
    empty = tmp_path / "VERSION"
    empty.write_text("  \n")
    assert user_agent.version_file_version(empty) is None


def test_env_version_wins_over_version_file(monkeypatch):
    monkeypatch.setenv("GOR_VERSION", "v2.0.0")
    assert user_agent.resolve_version() == "2.0.0"


def test_lakebase_database_read_from_resource_when_env_empty(monkeypatch):
    monkeypatch.setattr(lakebase_client, "_lakebase_resource",
                        lambda: {"instance": "inst-x", "database": "db-from-resource"})
    monkeypatch.delenv("LAKEBASE_DATABASE", raising=False)
    monkeypatch.delenv("PGDATABASE", raising=False)
    monkeypatch.setenv("LAKEBASE_HOST", "h.example")
    monkeypatch.delenv("LAKEBASE_USER", raising=False)
    monkeypatch.delenv("PGUSER", raising=False)
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "app-sp")
    cfg = lakebase_client._get_connection_config()
    assert cfg["database"] == "db-from-resource"
    assert cfg["user"] == "app-sp"


def test_lakebase_database_env_wins_over_resource(monkeypatch):
    monkeypatch.setattr(lakebase_client, "_lakebase_resource",
                        lambda: {"instance": "", "database": "db-from-resource"})
    monkeypatch.setenv("LAKEBASE_DATABASE", "explicit_db")
    assert lakebase_client._get_connection_config()["database"] == "explicit_db"


def test_lakebase_database_defaults_without_resource(monkeypatch):
    monkeypatch.setattr(lakebase_client, "_lakebase_resource", lambda: {"instance": "", "database": ""})
    monkeypatch.delenv("LAKEBASE_DATABASE", raising=False)
    monkeypatch.delenv("PGDATABASE", raising=False)
    assert lakebase_client._get_connection_config()["database"] == "ontology_readiness"


def test_lakebase_resource_read_failure_is_not_fatal(monkeypatch):
    """Outside a Databricks App there is no resource read at all."""
    monkeypatch.setattr(lakebase_client, "_RESOURCE", None)
    monkeypatch.delenv("DATABRICKS_APP_NAME", raising=False)
    assert lakebase_client._lakebase_resource() == {"instance": "", "database": ""}
    monkeypatch.setattr(lakebase_client, "_RESOURCE", None)
    monkeypatch.setenv("DATABRICKS_APP_NAME", "x")

    class Boom:
        @property
        def apps(self):
            raise RuntimeError("no sdk")
    import server.config as cfg
    monkeypatch.setattr(cfg, "_get_workspace_client", lambda: Boom())
    assert lakebase_client._lakebase_resource() == {"instance": "", "database": ""}
    monkeypatch.setattr(lakebase_client, "_RESOURCE", None)
