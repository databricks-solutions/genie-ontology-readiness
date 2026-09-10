import unittest
from unittest.mock import AsyncMock, patch

from server.assessment import probes


class GenieAuditCountsTest(unittest.IsolatedAsyncioTestCase):
    async def test_scopes_audit_scan_to_workspace_and_lookback(self):
        execute = AsyncMock(return_value=[{"total": "3", "active_30d": "2"}])

        with patch.object(probes, "execute_sql", execute), patch.object(
            probes, "WORKSPACE_ID", "1234567890"
        ):
            result = await probes._genie_audit_counts()

        self.assertEqual(result, {"total": 3, "active_30d": 2})
        query = execute.await_args.args[0]
        self.assertIn("workspace_id = CAST(:workspace_id AS BIGINT)", query)
        self.assertIn("event_date >= current_date() - INTERVAL 30 DAYS", query)
        self.assertEqual(
            execute.await_args.kwargs["parameters"],
            {"workspace_id": "1234567890"},
        )

    async def test_omits_workspace_predicate_when_id_is_unset(self):
        execute = AsyncMock(return_value=[{"total": 0, "active_30d": 0}])

        with patch.object(probes, "execute_sql", execute), patch.object(
            probes, "WORKSPACE_ID", ""
        ):
            await probes._genie_audit_counts()

        self.assertNotIn("workspace_id = CAST(:workspace_id AS BIGINT)", execute.await_args.args[0])
        self.assertIsNone(execute.await_args.kwargs["parameters"])

    async def test_degrades_gracefully_when_audit_query_fails(self):
        execute = AsyncMock(side_effect=TimeoutError("audit query timed out"))

        with patch.object(probes, "execute_sql", execute):
            result = await probes._genie_audit_counts()

        self.assertEqual(result, {"total": None, "active_30d": None})


if __name__ == "__main__":
    unittest.main()
