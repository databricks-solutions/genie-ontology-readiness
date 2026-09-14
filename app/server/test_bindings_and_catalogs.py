import unittest
from unittest.mock import AsyncMock, patch

from server.bindings import _select_accessible
from server.routes import catalogs as cat_route
from server.assessment import probes
from server.workspace_filter import set_workspace_filter, set_catalog_scope


CATS = [
    {"name": "open_cat", "isolation_mode": "OPEN"},
    {"name": "iso_bound", "isolation_mode": "ISOLATED"},
    {"name": "iso_other", "isolation_mode": "ISOLATED"},
    {"name": "system", "isolation_mode": "OPEN"},  # internal → excluded
]
BINDINGS = {
    "iso_bound": [{"binding_type": "BINDING_TYPE_READ_WRITE", "workspace_id": 111}],
    "iso_other": [{"binding_type": "BINDING_TYPE_READ_ONLY", "workspace_id": 999}],
}


class SelectAccessibleTest(unittest.TestCase):
    def test_open_always_in_isolated_only_if_bound(self):
        out = _select_accessible(CATS, BINDINGS, {"111"})
        names = {c["name"]: c["access"] for c in out}
        self.assertEqual(names, {"open_cat": "OPEN", "iso_bound": "READ_WRITE"})
        self.assertNotIn("system", names)   # internal excluded
        self.assertNotIn("iso_other", names)  # bound to a different workspace

    def test_read_only_binding_reported_as_read(self):
        out = _select_accessible(CATS, BINDINGS, {"999"})
        names = {c["name"]: c["access"] for c in out}
        self.assertEqual(names.get("iso_other"), "READ")


class CatalogsRouteTest(unittest.IsolatedAsyncioTestCase):
    async def test_include_uses_bindings(self):
        acc = AsyncMock(return_value=[{"name": "b", "access": "READ_WRITE", "isolation": "ISOLATED"},
                                      {"name": "a", "access": "OPEN", "isolation": "OPEN"}])
        with patch.object(cat_route, "accessible_catalogs", acc):
            resp = await cat_route.list_catalogs(workspace_ids="111", mode="include")
        self.assertTrue(resp["available"])
        self.assertEqual([c["name"] for c in resp["catalogs"]], ["a", "b"])  # sorted

    async def test_no_include_lists_all(self):
        allc = AsyncMock(return_value=[{"name": "x", "access": "ALL", "isolation": "OPEN"}])
        with patch.object(cat_route, "all_catalogs", allc):
            resp = await cat_route.list_catalogs(workspace_ids=None, mode="include")
        self.assertEqual([c["name"] for c in resp["catalogs"]], ["x"])

    async def test_sql_fallback_when_rest_unavailable(self):
        # REST bindings unreadable → SQL enumeration fallback still populates the
        # filter, with available=False to signal binding info is missing.
        fb = AsyncMock(return_value=[{"name": "cat_sql", "access": "ALL", "isolation": "UNKNOWN"}])
        with patch.object(cat_route, "accessible_catalogs", AsyncMock(return_value=None)), \
             patch.object(cat_route, "sql_enumerate_catalogs", fb):
            resp = await cat_route.list_catalogs(workspace_ids="111", mode="include")
        self.assertFalse(resp["available"])
        self.assertEqual([c["name"] for c in resp["catalogs"]], ["cat_sql"])


class ScopedCatalogsPrecedenceTest(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        set_workspace_filter(None)
        set_catalog_scope(None)

    async def test_explicit_override_wins(self):
        set_catalog_scope(["c1", "c2"])
        set_workspace_filter({"mode": "include", "workspace_ids": ["111"]})
        self.assertEqual(await probes._scoped_catalogs(), ["c1", "c2"])

    async def test_bindings_when_workspace_include(self):
        set_catalog_scope(None)
        set_workspace_filter({"mode": "include", "workspace_ids": ["111"]})
        acc = AsyncMock(return_value=[{"name": "bound_a", "access": "READ_WRITE", "isolation": "ISOLATED"}])
        with patch.object(probes, "accessible_catalogs", acc):
            self.assertEqual(await probes._scoped_catalogs(), ["bound_a"])

    async def test_falls_back_to_env_when_bindings_unreadable(self):
        set_catalog_scope(None)
        set_workspace_filter({"mode": "include", "workspace_ids": ["111"]})
        with patch.object(probes, "accessible_catalogs", AsyncMock(return_value=None)), \
             patch.object(probes, "ASSESS_CATALOGS", ["env_cat"]):
            self.assertEqual(await probes._scoped_catalogs(), ["env_cat"])

    async def test_none_when_nothing_scoped(self):
        set_catalog_scope(None)
        set_workspace_filter(None)
        with patch.object(probes, "ASSESS_CATALOGS", []):
            self.assertIsNone(await probes._scoped_catalogs())


if __name__ == "__main__":
    unittest.main()
