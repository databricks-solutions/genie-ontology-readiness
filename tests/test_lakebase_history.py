"""Lakebase history: which schema holds the tables, and startup that never stops the app.

No database: a fake pool records the SQL the app would run.
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

from server import history_schema as hs  # noqa: E402
from server import lakebase_client as lc  # noqa: E402
from server import plans, runtime_config, snapshots  # noqa: E402
import server.config as cfg  # noqa: E402


# --- pure schema choice ---------------------------------------------------------------

def test_existing_public_tables_keep_public():
    schema, reason = hs.choose_history_schema(["ontology_assessment_snapshots"])
    assert schema == "public" and "existing tables in public" in reason


def test_new_database_gets_its_own_schema():
    schema, reason = hs.choose_history_schema([])
    assert schema == "genie_ontology_readiness" and "new database" in reason


def test_unrelated_public_tables_do_not_count():
    assert hs.choose_history_schema(["some_other_table"])[0] == "genie_ontology_readiness"


def test_qualified_table_quotes_names():
    assert hs.qualified_table("genie_ontology_readiness", "ontology_plans") == \
        '"genie_ontology_readiness"."ontology_plans"'
    assert hs.quote_ident('we"ird') == '"we""ird"'


def test_probe_checks_both_history_tables_in_public():
    sql = hs.legacy_probe_sql()
    assert "to_regclass('public.ontology_assessment_snapshots')" in sql
    assert "to_regclass('public.ontology_plans')" in sql


# --- fake pool ----------------------------------------------------------------------

class _Conn:
    def __init__(self, legacy_present):
        self.legacy_present = legacy_present
        self.executed = []

    async def fetchrow(self, sql, *args):
        return {"legacy_present": self.legacy_present}

    async def execute(self, sql, *args):
        self.executed.append(sql)


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Acquire(self.conn)


def _patch_pool(monkeypatch, conn):
    async def get_pool():
        return _Pool(conn)
    monkeypatch.setattr(lc, "get_pool", get_pool)


def test_existing_install_keeps_public_and_creates_no_schema(monkeypatch):
    conn = _Conn(legacy_present=True)
    _patch_pool(monkeypatch, conn)
    monkeypatch.setattr(lc, "_HISTORY_SCHEMA", "public")
    assert asyncio.run(lc.prepare_history_schema()) == "public"
    assert conn.executed == []
    assert lc.history_table("ontology_plans") == '"public"."ontology_plans"'


def test_new_database_creates_and_uses_its_own_schema(monkeypatch):
    conn = _Conn(legacy_present=False)
    _patch_pool(monkeypatch, conn)
    monkeypatch.setattr(lc, "_HISTORY_SCHEMA", "public")
    assert asyncio.run(lc.prepare_history_schema()) == "genie_ontology_readiness"
    assert conn.executed == ['CREATE SCHEMA IF NOT EXISTS "genie_ontology_readiness"']
    assert lc.history_table("ontology_plans") == '"genie_ontology_readiness"."ontology_plans"'


def test_snapshot_and_plan_tables_are_schema_qualified(monkeypatch):
    conn = _Conn(legacy_present=False)
    _patch_pool(monkeypatch, conn)
    monkeypatch.setattr(lc, "_HISTORY_SCHEMA", "genie_ontology_readiness")

    async def enabled():
        return True
    monkeypatch.setattr(snapshots, "_enabled", enabled)
    monkeypatch.setattr(plans, "_enabled", enabled)
    asyncio.run(snapshots.ensure_table())
    asyncio.run(plans.ensure_table())
    assert any('"genie_ontology_readiness"."ontology_assessment_snapshots"' in sql for sql in conn.executed)
    assert any('"genie_ontology_readiness"."ontology_plans"' in sql for sql in conn.executed)
    assert not any("CREATE TABLE IF NOT EXISTS ontology_" in sql for sql in conn.executed)


# --- startup must never stop the app -------------------------------------------------

def test_start_history_turns_off_on_pool_error_and_does_not_raise(monkeypatch):
    monkeypatch.setattr(cfg, "USE_LAKEBASE", True)

    async def boom():
        raise RuntimeError("permission denied for schema public")
    monkeypatch.setattr(lc, "init_pool", boom)
    assert asyncio.run(lc.start_history()) is False


def test_start_history_turns_off_when_table_setup_fails(monkeypatch):
    monkeypatch.setattr(cfg, "USE_LAKEBASE", True)

    async def init_pool():
        pass
    monkeypatch.setattr(lc, "init_pool", init_pool)
    monkeypatch.setattr(lc, "is_available", lambda: True)

    async def prepare():
        raise RuntimeError("InsufficientPrivilegeError")
    monkeypatch.setattr(lc, "prepare_history_schema", prepare)
    closed = []

    async def close_pool():
        closed.append(True)
    monkeypatch.setattr(lc, "close_pool", close_pool)
    assert asyncio.run(lc.start_history()) is False
    assert closed == [True]


def test_start_history_off_when_lakebase_is_off(monkeypatch):
    monkeypatch.setattr(cfg, "USE_LAKEBASE", False)
    assert asyncio.run(lc.start_history()) is False


def test_start_history_ready_on_success(monkeypatch):
    monkeypatch.setattr(cfg, "USE_LAKEBASE", True)

    async def init_pool():
        pass

    async def prepare():
        return "public"

    async def ensure():
        pass
    monkeypatch.setattr(lc, "init_pool", init_pool)
    monkeypatch.setattr(lc, "is_available", lambda: True)
    monkeypatch.setattr(lc, "prepare_history_schema", prepare)
    monkeypatch.setattr(snapshots, "ensure_table", ensure)
    monkeypatch.setattr(plans, "ensure_table", ensure)
    assert asyncio.run(lc.start_history()) is True


# --- log labels -------------------------------------------------------------------------

def test_database_label_says_resource_when_from_pg_env():
    value, source = runtime_config.resolve_database("db_from_pg", "", env_label="database resource")
    assert (value, source) == ("db_from_pg", "database resource")


def test_user_label_says_resource_when_from_pguser():
    value, source = runtime_config.resolve_lakebase_user("pg_user", "client", env_label="database resource")
    assert (value, source) == ("pg_user", "database resource")
    assert runtime_config.resolve_lakebase_user("", "client") == ("client", "runtime (client id)")



# --- scoped snapshots: every statement uses the schema chosen at startup ----------------

class _SnapConn(_Conn):
    """Records every statement (execute, fetchrow, fetch, fetchval) and the scope-check args."""

    def __init__(self, scope_columns=2):
        super().__init__(legacy_present=False)
        self.scope_columns = scope_columns
        self.count_args = []

    async def fetchrow(self, sql, *args):
        self.executed.append(sql)
        return {"id": 7}

    async def fetch(self, sql, *args):
        self.executed.append(sql)
        return []

    async def fetchval(self, sql, *args):
        self.executed.append(sql)
        self.count_args.append(args)
        return self.scope_columns


def _snapshot_conn(monkeypatch, schema, scope_columns=2):
    conn = _SnapConn(scope_columns)
    _patch_pool(monkeypatch, conn)
    monkeypatch.setattr(lc, "_HISTORY_SCHEMA", schema)

    async def enabled():
        return True
    monkeypatch.setattr(snapshots, "_enabled", enabled)
    monkeypatch.setattr(snapshots, "_scope_columns", True)
    return conn


def test_snapshot_statements_use_the_chosen_schema_in_both_schemas(monkeypatch):
    for schema in ("public", "genie_ontology_readiness"):
        conn = _snapshot_conn(monkeypatch, schema)
        table = f'"{schema}"."ontology_assessment_snapshots"'
        asyncio.run(snapshots.ensure_table())
        scope = {"catalogs": ["sales"], "schemas": [], "workspace_filter": None}
        assert asyncio.run(snapshots.save_snapshot({"overall": {"score": 1, "level": 1}}, "me", scope)) == 7
        asyncio.run(snapshots.list_snapshots("me"))
        asyncio.run(snapshots.get_snapshot(7, "me"))
        # CREATE, ALTER, INSERT, SELECT (list), SELECT (get)
        assert len(conn.executed) == 5, conn.executed
        for sql in conn.executed:
            assert table in sql, (schema, sql)
            assert "ontology_assessment_snapshots" not in sql.replace(table, ""), (schema, sql)


def test_scope_column_check_resolves_the_chosen_schema(monkeypatch):
    conn = _snapshot_conn(monkeypatch, "genie_ontology_readiness", scope_columns=2)

    async def failing_alter(sql, *args):
        if sql.startswith("ALTER TABLE"):
            raise RuntimeError("must be owner of table")
        conn.executed.append(sql)
    conn.execute = failing_alter
    asyncio.run(snapshots.ensure_table())
    assert conn.count_args == [('"genie_ontology_readiness"."ontology_assessment_snapshots"',)]
    assert snapshots._scope_columns is True


def test_failed_scope_migration_turns_scope_columns_off(monkeypatch):
    conn = _snapshot_conn(monkeypatch, "public", scope_columns=0)

    async def failing_alter(sql, *args):
        if sql.startswith("ALTER TABLE"):
            raise RuntimeError("must be owner of table")
        conn.executed.append(sql)
    conn.execute = failing_alter
    asyncio.run(snapshots.ensure_table())
    assert conn.count_args == [('"public"."ontology_assessment_snapshots"',)]
    assert snapshots._scope_columns is False
    asyncio.run(snapshots.save_snapshot({"overall": {}}, "me", None))
    insert = [s for s in conn.executed if s.startswith("INSERT")][-1]
    assert "scope" not in insert
    assert '"public"."ontology_assessment_snapshots"' in insert
