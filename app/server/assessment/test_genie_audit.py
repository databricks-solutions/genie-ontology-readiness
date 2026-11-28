import datetime
import unittest
from unittest.mock import AsyncMock, patch

from server.assessment import probes
from server.workspace_filter import set_workspace_filter


def _counts_row(total=3, active=2, users=5, questions=40, dashboard=1, last_active_date=None):
    return [{"total": str(total), "active_30d": str(active), "distinct_users_30d": str(users),
             "questions_30d": str(questions), "dashboard_embedded": str(dashboard),
             "last_active_date": last_active_date}]


class GenieAuditCountsTest(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        set_workspace_filter(None)  # don't leak filter state across tests

    async def test_scopes_audit_scan_to_selected_workspaces_and_lookback(self):
        execute = AsyncMock(return_value=_counts_row(total=3, active=2, users=5, questions=40, dashboard=1))
        set_workspace_filter({"mode": "include", "workspace_ids": ["1444828305810485"]})

        with patch.object(probes, "execute_sql", execute):
            result = await probes._genie_audit_counts()

        # Full graded-usage shape, with the derived recency (no last_active_date → None).
        self.assertEqual(result, {"total": 3, "active_30d": 2, "questions_30d": 40,
                                  "distinct_users_30d": 5, "dashboard_embedded": 1,
                                  "last_active_date": None, "recency_days": None})
        query = execute.await_args.args[0]
        # The workspace predicate scopes both the genie-events scan and the dashboard-refs scan.
        self.assertEqual(query.count("CAST(workspace_id AS STRING) IN (:wsf_0)"), 2)
        self.assertIn("event_date >= current_date() - INTERVAL 30 DAYS", query)
        self.assertIn("dashboard_embedded = 1", query)
        self.assertEqual(execute.await_args.kwargs["parameters"], {"wsf_0": "1444828305810485"})

    async def test_derives_recency_days_from_last_active_date(self):
        three_days_ago = (datetime.date.today() - datetime.timedelta(days=3)).isoformat()
        execute = AsyncMock(return_value=_counts_row(last_active_date=three_days_ago))

        with patch.object(probes, "execute_sql", execute):
            result = await probes._genie_audit_counts()

        self.assertEqual(result["last_active_date"], three_days_ago)
        self.assertEqual(result["recency_days"], 3)

    async def test_exclude_mode_uses_not_in(self):
        execute = AsyncMock(return_value=_counts_row(total=1, active=0))
        set_workspace_filter({"mode": "exclude", "workspace_ids": ["111", "222"]})

        with patch.object(probes, "execute_sql", execute):
            await probes._genie_audit_counts()

        query = execute.await_args.args[0]
        self.assertIn("CAST(workspace_id AS STRING) NOT IN (:wsf_0, :wsf_1)", query)
        self.assertEqual(execute.await_args.kwargs["parameters"], {"wsf_0": "111", "wsf_1": "222"})

    async def test_omits_workspace_predicate_when_no_filter(self):
        execute = AsyncMock(return_value=_counts_row(total=0, active=0))
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


class GenieAuditRowsTest(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        set_workspace_filter(None)

    async def test_resolves_display_name_classifies_type_and_keeps_space_id(self):
        # The query COALESCEs the resolved name over the id; a space with no name in
        # the window falls back to its id (the row's `agent` == `space_id`). Each row
        # carries its agent type and 30-day conversation usage.
        set_workspace_filter({"mode": "include", "workspace_ids": ["7474644235756678"]})  # single ws
        execute = AsyncMock(return_value=[
            {"agent": "TD US Portfolio Assistant", "space_id": "01f1a", "events": "1158",
             "questions": "42", "users": "7", "active_30d": 1, "dashboard_embedded": 0},
            {"agent": "01f1b", "space_id": "01f1b", "events": "5",
             "questions": "0", "users": "0", "active_30d": 0, "dashboard_embedded": 1},
        ])
        with patch.object(probes, "execute_sql", execute):
            rows = await probes._genie_audit_rows()

        query = execute.await_args.args[0]
        self.assertIn("max_by(request_params.display_name, event_time)", query)
        self.assertIn("COALESCE(nm.space_name, a.space_id) AS agent", query)
        self.assertIn("request_params.space_id <> 'new'", query)  # drop the placeholder
        self.assertIn("dashboard_refs", query)  # classification source
        self.assertEqual(rows[0], {"agent": "TD US Portfolio Assistant", "space_id": "01f1a",
                                   "type": "Standalone", "questions": 42, "users": 7,
                                   "events": 1158, "active_30d": "Yes"})
        self.assertEqual(rows[1]["agent"], rows[1]["space_id"])    # fallback to id
        self.assertEqual(rows[1]["type"], "Dashboard-embedded")    # seen in a dashboard-genie response

    async def test_scopes_name_window_to_selected_workspaces(self):
        set_workspace_filter({"mode": "include", "workspace_ids": ["7474644235756678"]})
        execute = AsyncMock(return_value=[])
        with patch.object(probes, "execute_sql", execute):
            await probes._genie_audit_rows()

        query = execute.await_args.args[0]
        # The workspace predicate is applied to the names CTE, the dashboard-refs CTE,
        # and the activity scan.
        self.assertEqual(query.count("CAST(workspace_id AS STRING) IN (:wsf_0)"), 3)
        self.assertEqual(execute.await_args.kwargs["parameters"], {"wsf_0": "7474644235756678"})


if __name__ == "__main__":
    unittest.main()
