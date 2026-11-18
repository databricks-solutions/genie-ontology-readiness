"""Domains & stewardship probe: Discover domains (Domains API + `Domain/Subdomain` governed tags)."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from server.assessment import probes

SOURCES = {"system_ok": True, "catalogs": ["cp"], "schemas": None}


class _Resp:
    def __init__(self, status, body=None):
        self.status, self._body = status, body or {}

    async def json(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Session:
    """aiohttp.ClientSession stand-in: answers GETs from a {path: [responses]} script."""

    def __init__(self, script, calls):
        self.script, self.calls = script, calls

    def get(self, url, headers=None, params=None):
        path = url.split("example.cloud.databricks.com", 1)[1]
        self.calls.append((path, dict(params or {})))
        queue = self.script.get(path) or [_Resp(404)]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _run_native(script):
    calls = []
    with patch.object(probes, "get_workspace_host", return_value="https://example.cloud.databricks.com"), \
         patch.object(probes, "get_auth_headers", return_value={"Authorization": "Bearer x"}), \
         patch.object(probes, "record_rest_identity"), \
         patch.object(probes.aiohttp, "ClientSession", side_effect=lambda **kw: _Session(script, calls)):
        return asyncio.run(probes._native_domains()), calls


class NativeDomainsTest(unittest.TestCase):
    def test_lists_domains_across_pages(self):
        script = {"/api/2.0/domains": [
            _Resp(200, {"domains": [{"domain_id": "1", "tag_key": "CarePortal"}], "next_page_token": "p2"}),
            _Resp(200, {"domains": [{"domain_id": "2", "tag_key": "CarePortal/Referrals", "parent_domain_id": "1"}]}),
        ]}
        res, calls = _run_native(script)
        self.assertEqual(res["status"], "ok")
        self.assertEqual([d["tag_key"] for d in res["domains"]], ["CarePortal", "CarePortal/Referrals"])
        self.assertEqual(calls[1][1].get("page_token"), "p2")

    def test_forbidden_means_missing_manage_discover(self):
        res, _ = _run_native({"/api/2.0/domains": [_Resp(403)], "/api/discover/v1/domains": [_Resp(403)]})
        self.assertEqual(res, {"status": "forbidden"})

    def test_missing_endpoint_is_unavailable(self):
        res, calls = _run_native({})
        self.assertEqual(res, {"status": "unavailable"})
        self.assertEqual([c[0] for c in calls], ["/api/2.0/domains", "/api/discover/v1/domains"])

    def test_alternate_path_is_used(self):
        res, _ = _run_native({"/api/discover/v1/domains": [_Resp(200, {"domains": [{"tag_key": "Finance"}]})]})
        self.assertEqual(res["status"], "ok")
        self.assertEqual(len(res["domains"]), 1)


class _AuthSession(_Session):
    """Refuses the viewer token (403) and answers the SP token, like the Domains API with an
    `sql`-scoped OBO token."""

    def get(self, url, headers=None, params=None):
        self.calls.append((url.split("example.cloud.databricks.com", 1)[1], (headers or {}).get("Authorization")))
        if (headers or {}).get("Authorization") == "Bearer viewer":
            return _Resp(403)
        return _Resp(200, {"domains": [{"domain_id": "1", "tag_key": "Finance"}]})


def _run_native_obo(user_token):
    calls, identities = [], []
    headers = lambda force_sp=False: {"Authorization": "Bearer sp" if force_sp or not user_token else "Bearer viewer"}
    with patch.object(probes, "get_workspace_host", return_value="https://example.cloud.databricks.com"), \
         patch.object(probes, "get_auth_headers", side_effect=headers), \
         patch.object(probes, "get_user_token", return_value=user_token), \
         patch.object(probes, "record_rest_identity", side_effect=lambda: identities.append("rest")), \
         patch.object(probes, "record_identity", side_effect=identities.append), \
         patch.object(probes.aiohttp, "ClientSession", side_effect=lambda **kw: _AuthSession({}, calls)):
        return asyncio.run(probes._native_domains()), calls, identities


class NativeDomainsIdentityTest(unittest.TestCase):
    def test_refused_viewer_token_retries_as_the_sp(self):
        res, calls, identities = _run_native_obo("viewer")
        self.assertEqual(res["status"], "ok")
        self.assertEqual([c[1] for c in calls], ["Bearer viewer", "Bearer viewer", "Bearer sp"])
        self.assertEqual(identities, ["sp_fallback"])

    def test_sp_only_does_not_retry(self):
        res, calls, identities = _run_native_obo(None)
        self.assertEqual(res["status"], "ok")
        self.assertEqual([c[1] for c in calls], ["Bearer sp"])
        self.assertEqual(identities, ["rest"])


class DomainTagSqlTest(unittest.TestCase):
    def test_legacy_keys_only(self):
        pred, label = probes._domain_tag_sql([])
        self.assertIn("'domain'", pred)
        self.assertNotIn("careportal", pred)
        self.assertIn("split(tag_name, '/')[0]", label)

    def test_discover_keys_are_matched_case_insensitively(self):
        pred, _ = probes._domain_tag_sql(["CarePortal", "CarePortal/Referrals"])
        self.assertIn("'careportal'", pred)
        self.assertIn("'careportal/referrals'", pred)
        self.assertIn("'domain'", pred)  # legacy tags still count


def _fake_sql(rows_by_marker):
    """execute_sql stand-in: the first marker found in the statement picks the rows."""
    statements = []

    async def run(sql, **kw):
        statements.append(sql)
        for marker, rows in rows_by_marker:
            if marker in sql:
                return rows
        return []
    return run, statements


def _run_probe(api, rows_by_marker, scalar=5, scope=None, sources=SOURCES):
    run, statements = _fake_sql(rows_by_marker)
    with patch.object(probes, "_native_domains", AsyncMock(return_value=api)), \
         patch.object(probes, "_resolve_sources", AsyncMock(return_value=sources)), \
         patch.object(probes, "get_catalog_scope", return_value=scope), \
         patch.object(probes, "execute_sql", side_effect=run), \
         patch.object(probes, "_scalar", AsyncMock(return_value=scalar)):
        return asyncio.run(probes.probe_domains()), statements


class ProbeDomainsTest(unittest.TestCase):
    def test_discover_subdomain_tags_count_without_the_api(self):
        # The customer case: 49 tables tagged CarePortal/<Subdomain>, no parent tag, Domains API 404.
        res, statements = _run_probe(
            {"status": "unavailable"},
            [("instr(tag_name, '/') > 1", [{"tag_name": "CarePortal/Referrals"}, {"tag_name": "CarePortal/Intake"}]),
             ("distinct_domains", [{"distinct_domains": "1", "assignments": "49"}])])
        self.assertTrue(res["available"])
        self.assertEqual(res["metrics"]["distinct_domains"], 1)
        self.assertGreater(res["score"], 0)
        self.assertIn("'careportal/referrals'", next(s for s in statements if "distinct_domains" in s))
        self.assertEqual(res["metrics"]["source"], "tag_proxy")

    def test_api_domains_drive_the_count(self):
        api = {"status": "ok", "domains": [
            {"domain_id": "1", "tag_key": "CarePortal"},
            {"domain_id": "2", "tag_key": "Finance"},
            {"domain_id": "3", "tag_key": "CarePortal/Referrals", "parent_domain_id": "1"}]}
        res, statements = _run_probe(api, [("distinct_domains", [{"distinct_domains": "1", "assignments": "49"}])])
        self.assertEqual(res["metrics"]["source"], "native_api")
        self.assertEqual(res["metrics"]["distinct_domains"], 2)  # both top-level domains, even if Finance is untagged
        self.assertEqual(res["metrics"]["subdomains"], 1)
        self.assertFalse(any("instr(tag_name, '/') > 1" in s for s in statements))  # no key inference needed
        self.assertIn("Published domains read from the Discover Domains API", res["note"])

    def test_draft_domains_are_not_counted(self):
        api = {"status": "ok", "domains": [
            {"domain_id": "1", "tag_key": "Finance"},
            {"domain_id": "2", "tag_key": "Sales", "effective_draft": True},
            {"domain_id": "3", "tag_key": "Finance/Tax", "parent_domain_id": "1"},
            {"domain_id": "4", "tag_key": "Finance/Audit", "parent_domain_id": "1", "effective_draft": True}]}
        res, statements = _run_probe(api, [("distinct_domains", [{"distinct_domains": "1", "assignments": "3"}])])
        self.assertEqual(res["metrics"]["distinct_domains"], 1)
        self.assertEqual((res["metrics"]["subdomains"], res["metrics"]["draft_domains"]), (1, 2))
        pred = next(s for s in statements if "distinct_domains" in s)
        self.assertIn("'finance/tax'", pred)
        self.assertNotIn("'sales'", pred)  # a draft domain's tags don't place assets in a domain
        self.assertNotIn("'finance/audit'", pred)

    def test_scoped_run_counts_only_domains_with_assets_in_scope(self):
        # Domains are account-level: a run scoped to one catalog must not count every domain.
        api = {"status": "ok", "domains": [
            {"domain_id": "1", "tag_key": "CarePortal"}, {"domain_id": "2", "tag_key": "Finance"},
            {"domain_id": "3", "tag_key": "Sales"}]}
        res, _ = _run_probe(api, [("distinct_domains", [{"distinct_domains": "1", "assignments": "4"}])], scope=["cp"])
        self.assertEqual(res["metrics"]["distinct_domains"], 1)
        self.assertIn("with assets in this scope", res["signals"][0]["detail"])

    def test_untagged_scope_scores_zero_even_with_api_domains(self):
        api = {"status": "ok", "domains": [{"domain_id": str(i), "tag_key": f"D{i}"} for i in range(6)]}
        res, _ = _run_probe(api, [("distinct_domains", [{"distinct_domains": "0", "assignments": "0"}])],
                            scalar=0, scope=["cp"])
        self.assertEqual(res["metrics"]["distinct_domains"], 0)
        self.assertEqual(res["score"], 0.0)

    def test_api_count_only_when_tags_are_unreadable(self):
        api = {"status": "ok", "domains": [
            {"domain_id": "1", "tag_key": "Finance"}, {"domain_id": "2", "tag_key": "Sales"},
            {"domain_id": "3", "tag_key": "Finance/Tax", "parent_domain_id": "1"}]}
        no_catalogs = {"system_ok": False, "catalogs": [], "schemas": None}
        res, _ = _run_probe(api, [], sources=no_catalogs)
        self.assertTrue(res["available"])
        self.assertEqual(res["score"], 40 + 40 * 2 / 5)
        self.assertEqual((res["metrics"]["domains"], res["metrics"]["subdomains"]), (2, 1))
        scoped, _ = _run_probe(api, [], sources=no_catalogs, scope=["cp"])
        self.assertFalse(scoped["available"])  # account-wide domains say nothing about this scope

    def test_forbidden_api_flags_manage_discover(self):
        res, _ = _run_probe({"status": "forbidden"}, [("distinct_domains", [{"distinct_domains": "0", "assignments": "0"}])])
        self.assertTrue(any("MANAGE DISCOVER" in g for g in res["gaps"]))
        self.assertIn("needs MANAGE DISCOVER", res["note"])
        self.assertEqual(res["metrics"]["domains_api"], "forbidden")


if __name__ == "__main__":
    unittest.main()
