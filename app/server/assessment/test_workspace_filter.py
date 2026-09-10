import unittest
from unittest.mock import AsyncMock, patch

from server.assessment import probes
from server import workspace_filter as wf
from server.workspace_filter import set_workspace_filter, workspace_predicate, is_multi_workspace


class WorkspacePredicateTest(unittest.TestCase):
    def tearDown(self):
        set_workspace_filter(None)

    def test_no_filter_is_noop(self):
        set_workspace_filter(None)
        frag, params = workspace_predicate()
        self.assertEqual(frag, "")
        self.assertEqual(params, {})

    def test_empty_ids_normalize_to_no_filter(self):
        set_workspace_filter({"mode": "include", "workspace_ids": []})
        self.assertIsNone(wf.get_workspace_filter())
        self.assertEqual(workspace_predicate(), ("", {}))

    def test_include_builds_in_clause(self):
        set_workspace_filter({"mode": "include", "workspace_ids": ["1", "2"]})
        frag, params = workspace_predicate()
        self.assertIn("CAST(workspace_id AS STRING) IN (:wsf_0, :wsf_1)", frag)
        self.assertEqual(params, {"wsf_0": "1", "wsf_1": "2"})

    def test_exclude_builds_not_in_clause(self):
        set_workspace_filter({"mode": "exclude", "workspace_ids": ["9"]})
        frag, _ = workspace_predicate()
        self.assertIn("NOT IN (:wsf_0)", frag)

    def test_custom_column(self):
        set_workspace_filter({"mode": "include", "workspace_ids": ["1"]})
        frag, _ = workspace_predicate(column="a.workspace_id", prefix="p")
        self.assertIn("CAST(a.workspace_id AS STRING) IN (:p_0)", frag)

    def test_is_multi_workspace(self):
        set_workspace_filter(None)
        self.assertTrue(is_multi_workspace())  # no filter → account-wide
        set_workspace_filter({"mode": "include", "workspace_ids": ["1"]})
        self.assertFalse(is_multi_workspace())
        set_workspace_filter({"mode": "include", "workspace_ids": ["1", "2"]})
        self.assertTrue(is_multi_workspace())
        set_workspace_filter({"mode": "exclude", "workspace_ids": ["1"]})
        self.assertTrue(is_multi_workspace())  # exclude is open-ended


class ReasonClassificationTest(unittest.TestCase):
    def test_authz_error_is_insufficient_permission(self):
        self.assertEqual(probes._reason_for(Exception("PERMISSION_DENIED: no SELECT")), "insufficient_permission")
        self.assertEqual(probes._reason_for(Exception("SQL Warehouse error (403): forbidden")), "insufficient_permission")

    def test_other_error_is_scan_failed(self):
        self.assertEqual(probes._reason_for(TimeoutError("timed out")), "scan_failed")

    def test_empty_threads_reason(self):
        r = probes._empty("nope", reason="not_enabled")
        self.assertFalse(r["available"])
        self.assertEqual(r["unavailable_reason"], "not_enabled")


class AdoptionFilterTest(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        set_workspace_filter(None)

    async def test_adoption_applies_workspace_filter(self):
        # First two calls: active users + queries. Return scalars.
        execute = AsyncMock(side_effect=[[{"c": "5"}], [{"c": "50"}]])
        set_workspace_filter({"mode": "include", "workspace_ids": ["77"]})
        with patch.object(probes, "execute_sql", execute):
            result = await probes.probe_adoption()
        self.assertTrue(result["available"])
        users_query = execute.await_args_list[0].args[0]
        self.assertIn("CAST(workspace_id AS STRING) IN (:wsf_0)", users_query)
        self.assertEqual(execute.await_args_list[0].kwargs["parameters"], {"wsf_0": "77"})


class GenieDrillDownTest(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        set_workspace_filter(None)

    async def test_genie_pillar_emits_per_agent_drill_down(self):
        set_workspace_filter({"mode": "include", "workspace_ids": ["1"]})  # single ws → no workspace column
        counts = AsyncMock(return_value={"total": 2, "active_30d": 1})
        rows = AsyncMock(return_value=[
            {"agent": "space-a", "events": "40", "active_30d": "1"},
            {"agent": "space-b", "events": "3", "active_30d": "0"},
        ])
        with patch.object(probes, "_genie_audit_counts", counts), patch.object(probes, "_genie_audit_rows", rows):
            result = await probes.probe_genie_agents()
        dd = result["drill_down"]
        self.assertIsNotNone(dd)
        self.assertEqual(dd["rows"][0]["agent"], "space-a")
        self.assertNotIn("workspace", [c["key"] for c in dd["columns"]])


if __name__ == "__main__":
    unittest.main()
