"""Assessment scope is saved with each snapshot and compared like-for-like (#89)."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from server import snapshots
from server.assessment.compare import compare_snapshots
from server.scope import normalize_scope, same_scope, scope_hash

WS = {"mode": "include", "workspace_ids": ["123"]}


class NormalizeScopeTest(unittest.TestCase):
    def test_order_case_and_duplicates_do_not_matter(self):
        a = normalize_scope(["Sales", "finance", "sales"], None, WS)
        b = normalize_scope(["FINANCE", "sales"], [], {"mode": "include", "workspace_ids": ["123", "123"]})
        self.assertEqual(a, b)
        self.assertEqual(a["catalogs"], ["finance", "sales"])
        self.assertEqual(scope_hash(a), scope_hash(b))

    def test_different_catalogs_are_different_scopes(self):
        self.assertNotEqual(scope_hash(normalize_scope(["a"], None, WS)),
                            scope_hash(normalize_scope(["a", "b"], None, WS)))
        self.assertNotEqual(scope_hash(normalize_scope(["a"], None, WS)),
                            scope_hash(normalize_scope([], None, WS)))  # [] = all catalogs

    def test_schemas_count_only_with_one_catalog(self):
        self.assertEqual(normalize_scope(["a"], ["Gold"], WS)["schemas"], ["gold"])
        self.assertEqual(normalize_scope(["a", "b"], ["gold"], WS)["schemas"], [])
        self.assertNotEqual(scope_hash(normalize_scope(["a"], ["gold"], WS)),
                            scope_hash(normalize_scope(["a"], None, WS)))

    def test_workspace_filter(self):
        self.assertEqual(normalize_scope([], None, None)["workspace_mode"], None)
        self.assertEqual(normalize_scope([], None, {"mode": "exclude", "workspace_ids": []})["workspace_mode"], None)
        self.assertNotEqual(scope_hash(normalize_scope([], None, WS)),
                            scope_hash(normalize_scope([], None, {"mode": "exclude", "workspace_ids": ["123"]})))

    def test_mode_is_normalized_like_the_assessment(self):
        # set_workspace_filter treats anything but exactly "exclude" as "include".
        a = normalize_scope([], None, {"mode": "Include", "workspace_ids": ["1"]})
        b = normalize_scope([], None, {"mode": "include", "workspace_ids": ["1"]})
        self.assertEqual(scope_hash(a), scope_hash(b))
        self.assertEqual(a["workspace_mode"], "include")

    def test_assess_catalogs_env_is_part_of_the_scope(self):
        # No catalogs picked and no workspace include-filter: ASSESS_CATALOGS decides.
        self.assertEqual(normalize_scope([], None, None, env_catalogs=["Sales"])["catalogs"], ["sales"])
        self.assertNotEqual(scope_hash(normalize_scope([], None, None, env_catalogs=["sales"])),
                            scope_hash(normalize_scope([], None, None, env_catalogs=["sales", "finance"])))
        # Picked catalogs win; an include-filter picks catalogs through bindings instead.
        self.assertEqual(normalize_scope(["a"], None, None, env_catalogs=["sales"])["catalogs"], ["a"])
        self.assertEqual(normalize_scope([], None, WS, env_catalogs=["sales"])["catalogs"], [])
        self.assertEqual(normalize_scope([], None, {"mode": "exclude", "workspace_ids": ["1"]},
                                         env_catalogs=["sales"])["catalogs"], ["sales"])

    def test_unknown_scope(self):
        self.assertIsNone(scope_hash(None))
        self.assertIsNone(same_scope(None, "abc"))
        self.assertTrue(same_scope("abc", "abc"))
        self.assertFalse(same_scope("abc", "def"))


class CompareScopeTest(unittest.TestCase):
    def _snap(self, i, h):
        return {"id": i, "created_at": "2026-11-05", "scope_hash": h,
                "scope": None if h is None else {"catalogs": [h]},
                "scorecard": {"overall": {"score": 50, "level": 2}, "pillars": [], "top_gaps": []}}

    def test_same_scope_flag(self):
        self.assertTrue(compare_snapshots(self._snap(1, "x"), self._snap(2, "x"))["same_scope"])
        self.assertFalse(compare_snapshots(self._snap(1, "x"), self._snap(2, "y"))["same_scope"])
        self.assertIsNone(compare_snapshots(self._snap(1, None), self._snap(2, "y"))["same_scope"])
        self.assertEqual(compare_snapshots(self._snap(1, "x"), self._snap(2, "y"))["current"]["scope"],
                         {"catalogs": ["y"]})


class _Conn:
    def __init__(self, fail_alter=False, rows=(), row=None, existing_scope_columns=0):
        self.executed, self.fetchrow_args, self.fetch_sql = [], None, None
        self.fail_alter, self.rows, self.row = fail_alter, list(rows), row or {"id": 7}
        self.existing_scope_columns = existing_scope_columns

    async def execute(self, sql, *args):
        if self.fail_alter and sql.startswith("ALTER"):
            raise RuntimeError("must be owner of table")
        self.executed.append(sql)

    async def fetchval(self, sql, *args):
        return self.existing_scope_columns

    async def fetchrow(self, sql, *args):
        self.fetchrow_args = (sql, args)
        return self.row

    async def fetch(self, sql, *args):
        self.fetch_sql = sql
        return self.rows


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool.conn

            async def __aexit__(self, *a):
                return False
        return _Ctx()


class SnapshotScopeStorageTest(unittest.TestCase):
    def setUp(self):
        snapshots._scope_columns = True

    tearDown = setUp

    def _with_pool(self, conn):
        async def get_pool():
            return _Pool(conn)
        return (patch.object(snapshots, "_enabled", AsyncMock(return_value=True)),
                patch("server.lakebase_client.get_pool", get_pool))

    def test_migration_adds_scope_columns(self):
        conn = _Conn()
        p1, p2 = self._with_pool(conn)
        with p1, p2:
            asyncio.run(snapshots.ensure_table())
        self.assertTrue(any("ADD COLUMN IF NOT EXISTS scope JSONB" in s for s in conn.executed))

    def test_failed_migration_keeps_history_readable(self):
        """If the scope columns can't be added, history still loads, without scopes."""
        conn = _Conn(fail_alter=True, rows=[_row(1, None, None)])
        p1, p2 = self._with_pool(conn)
        with p1, p2:
            asyncio.run(snapshots.ensure_table())  # no exception
            self.assertFalse(snapshots._scope_columns)
            out = asyncio.run(snapshots.list_snapshots("u"))
            asyncio.run(snapshots.save_snapshot({"overall": {}}, "u", scope=normalize_scope(["a"], None, WS)))
        self.assertNotIn("scope", conn.fetch_sql)
        self.assertEqual((out[0]["scope"], out[0]["scope_hash"]), (None, None))
        sql, args = conn.fetchrow_args
        self.assertNotIn("scope", sql)
        self.assertEqual(len(args), 4)

    def test_failed_alter_with_existing_columns_keeps_scopes(self):
        conn = _Conn(fail_alter=True, existing_scope_columns=2)
        p1, p2 = self._with_pool(conn)
        with p1, p2:
            asyncio.run(snapshots.ensure_table())
        self.assertTrue(snapshots._scope_columns)

    def test_get_snapshot_returns_scope(self):
        from datetime import datetime, timezone
        row = {"id": 3, "created_at": datetime(2026, 11, 5, tzinfo=timezone.utc), "created_by": "u",
               "scorecard": json.dumps({"overall": {}}), "scope": json.dumps({"catalogs": ["a"]}),
               "scope_hash": "abc"}
        p1, p2 = self._with_pool(_Conn(row=row))
        with p1, p2:
            snap = asyncio.run(snapshots.get_snapshot(3, "u"))
        self.assertEqual((snap["scope"], snap["scope_hash"]), ({"catalogs": ["a"]}, "abc"))

    def test_save_stores_scope_and_hash(self):
        conn = _Conn()
        scope = normalize_scope(["a"], None, WS)
        p1, p2 = self._with_pool(conn)
        with p1, p2:
            sid = asyncio.run(snapshots.save_snapshot({"overall": {"score": 1, "level": 1}}, "u@x", scope=scope))
        self.assertEqual(sid, 7)
        sql, args = conn.fetchrow_args
        self.assertIn("scope, scope_hash", sql)
        self.assertEqual(json.loads(args[4]), scope)
        self.assertEqual(args[5], scope_hash(scope))

    def test_list_returns_scope_and_null_for_old_rows(self):
        from datetime import datetime, timezone
        now = datetime(2026, 11, 5, tzinfo=timezone.utc)
        rows = [_row(2, json.dumps({"catalogs": ["a"]}), "abc", now), _row(1, None, None, now)]
        p1, p2 = self._with_pool(_Conn(rows=rows))
        with p1, p2:
            out = asyncio.run(snapshots.list_snapshots("u"))
        self.assertEqual([(r["scope"], r["scope_hash"]) for r in out],
                         [({"catalogs": ["a"]}, "abc"), (None, None)])


def _row(i, scope, h, when=None):
    from datetime import datetime, timezone
    return {"id": i, "created_at": when or datetime(2026, 11, 5, tzinfo=timezone.utc), "created_by": "u",
            "overall_score": 50.0, "overall_level": 2, "scope": scope, "scope_hash": h}


class StreamSavesScopeTest(unittest.TestCase):
    """/assess/stream saves the normalized scope and returns it on the complete event."""

    def test_scope_is_saved_and_returned(self):
        from server.routes import assess

        async def fake_stream():
            yield {"type": "complete", "overall": {"score": 50}, "pillars": [], "top_gaps": []}

        req = assess.AssessRequest(catalogs=["B", "a"],
                                   workspace_filter=assess.WorkspaceFilterModel(mode="include",
                                                                                workspace_ids=["9"]))
        save = AsyncMock(return_value=11)
        with patch.object(assess, "run_assessment_stream", fake_stream), \
             patch.object(assess.snapshots, "save_snapshot", save), \
             patch.object(assess, "ASSESS_CATALOGS", ["ignored"]):
            resp = asyncio.run(assess.assess_stream(req=req, principal="u@x", x_forwarded_access_token=None))

            async def body():
                return [chunk async for chunk in resp.body_iterator]
            chunks = asyncio.run(body())
        expected = normalize_scope(["B", "a"], None, {"mode": "include", "workspace_ids": ["9"]})
        self.assertEqual(save.call_args.kwargs["scope"], expected)
        event = json.loads(chunks[0][len("data: "):])
        self.assertEqual(event["scope"], expected)
        self.assertEqual(event["scope_hash"], scope_hash(expected))
        self.assertEqual(event["snapshot_id"], 11)


if __name__ == "__main__":
    unittest.main()
