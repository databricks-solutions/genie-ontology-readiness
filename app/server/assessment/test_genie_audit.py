import unittest
from unittest.mock import AsyncMock, patch

from server.assessment import probes
from server.workspace_filter import set_workspace_filter


class GenieAuditCountsTest(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        set_workspace_filter(None)  # don't leak filter state across tests

    async def test_scopes_audit_scan_to_selected_workspaces_and_lookback(self):
        execute = AsyncMock(return_value=[{"total": "3", "active_30d": "2"}])
        set_workspace_filter({"mode": "include", "workspace_ids": ["1444828305810485"]})

        with patch.object(probes, "execute_sql", execute):
            result = await probes._genie_audit_counts()

        self.assertEqual(result, {"total": 3, "active_30d": 2})
        query = execute.await_args.args[0]
        self.assertIn("CAST(workspace_id AS STRING) IN (:wsf_0)", query)
        self.assertIn("event_date >= current_date() - INTERVAL 30 DAYS", query)
        self.assertEqual(execute.await_args.kwargs["parameters"], {"wsf_0": "1444828305810485"})

    async def test_exclude_mode_uses_not_in(self):
        execute = AsyncMock(return_value=[{"total": "1", "active_30d": "0"}])
        set_workspace_filter({"mode": "exclude", "workspace_ids": ["111", "222"]})

        with patch.object(probes, "execute_sql", execute):
            await probes._genie_audit_counts()

        query = execute.await_args.args[0]
        self.assertIn("CAST(workspace_id AS STRING) NOT IN (:wsf_0, :wsf_1)", query)
        self.assertEqual(execute.await_args.kwargs["parameters"], {"wsf_0": "111", "wsf_1": "222"})

    async def test_omits_workspace_predicate_when_no_filter(self):
        execute = AsyncMock(return_value=[{"total": 0, "active_30d": 0}])
        set_workspace_filter(None)

        with patch.object(probes, "execute_sql", execute):
            await probes._genie_audit_counts()

        self.assertNotIn("wsf_", execute.await_args.args[0])
        self.assertIsNone(execute.await_args.kwargs["parameters"])

    async def test_degrades_gracefully_when_audit_query_fails(self):
        execute = AsyncMock(side_effect=TimeoutError("audit query timed out"))

        with patch.object(probes, "execute_sql", execute):
            result = await probes._genie_audit_counts()

        self.assertEqual(result, {"total": None, "active_30d": None})


if __name__ == "__main__":
    unittest.main()
