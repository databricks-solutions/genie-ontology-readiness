import unittest
from unittest.mock import AsyncMock, patch

from server import sql_client
from server.routes import workspaces as ws_route


class QueryCaptureTest(unittest.TestCase):
    def test_capture_records_normalized_sql_and_params(self):
        sql_client.start_query_capture()
        sql_client.record_query("SELECT  1\n   FROM   t", {"a": 5})
        sql_client.record_query("SELECT 2")
        caps = sql_client.captured_queries()
        self.assertEqual(len(caps), 2)
        self.assertEqual(caps[0]["sql"], "SELECT 1 FROM t")  # whitespace collapsed
        self.assertEqual(caps[0]["parameters"], {"a": "5"})
        self.assertNotIn("parameters", caps[1])

    def test_capture_noop_without_start(self):
        # Fresh context with no start_query_capture → recording is a no-op, not an error.
        import contextvars

        def _run():
            sql_client.record_query("SELECT 1")
            return sql_client.captured_queries()

        self.assertEqual(contextvars.copy_context().run(_run), [])


class WorkspacesRouteTest(unittest.IsolatedAsyncioTestCase):
    async def test_lists_workspaces_and_marks_current(self):
        rows = [
            {"workspace_id": "111", "workspace_name": "alpha", "workspace_url": "u1", "status": "RUNNING"},
            {"workspace_id": "222", "workspace_name": "beta", "workspace_url": "u2", "status": "RUNNING"},
        ]
        execute = AsyncMock(return_value=rows)
        with patch.object(ws_route, "execute_sql", execute), patch.object(ws_route, "WORKSPACE_ID", "222"):
            resp = await ws_route.list_workspaces(x_forwarded_access_token=None)
        self.assertTrue(resp["available"])
        self.assertEqual(len(resp["workspaces"]), 2)
        current = [w for w in resp["workspaces"] if w["is_current"]]
        self.assertEqual([w["id"] for w in current], ["222"])

    async def test_falls_back_to_current_workspace_on_read_failure(self):
        execute = AsyncMock(side_effect=Exception("no SELECT on system.access"))
        with patch.object(ws_route, "execute_sql", execute), patch.object(ws_route, "WORKSPACE_ID", "999"):
            resp = await ws_route.list_workspaces(x_forwarded_access_token=None)
        self.assertFalse(resp["available"])
        self.assertEqual([w["id"] for w in resp["workspaces"]], ["999"])


if __name__ == "__main__":
    unittest.main()
