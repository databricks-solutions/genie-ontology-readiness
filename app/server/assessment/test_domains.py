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
    def test_keys_are_matched_case_insensitively(self):
        pred, label = probes._domain_tag_sql(["CarePortal", "CarePortal/Referrals"])
        self.assertIn("'careportal'", pred)
        self.assertIn("'careportal/referrals'", pred)
        self.assertNotIn("'domain'", pred)  # no legacy `domain`-style keys
        self.assertEqual(label, "split(tag_name, '/')[0]")

    def test_no_domains_matches_nothing(self):
        self.assertEqual(probes._domain_tag_sql([])[0], "FALSE")


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


API = {"status": "ok", "domains": [
    {"domain_id": "1", "tag_key": "CarePortal"},
    {"domain_id": "2", "tag_key": "Finance"},
    {"domain_id": "3", "tag_key": "CarePortal/Referrals", "parent_domain_id": "1"},
    {"domain_id": "4", "tag_key": "Sales", "effective_draft": True},
    {"domain_id": "5", "tag_key": "Finance/Audit", "parent_domain_id": "2", "effective_draft": True}]}
USED = ("used_domains", [{"used_domains": "1", "assignments": "49"}])


class ProbeDomainsTest(unittest.TestCase):
    def test_without_the_api_the_pillar_is_left_out_of_the_score(self):
        for status, reason in (("forbidden", "insufficient_permission"), ("unavailable", "not_enabled")):
            res, statements = _run_probe({"status": status}, [USED])
            self.assertFalse(res["available"])
            self.assertTrue(res["score_excluded"])
            self.assertEqual(res["unavailable_reason"], reason)
            self.assertEqual(statements, [])  # no tag-based guess
            self.assertIn("aren't guessed from governed tags", res["note"])
            self.assertTrue(res["score_excluded_reason"])
        self.assertIn("MANAGE DISCOVER", _run_probe({"status": "forbidden"}, [])[0]["note"])

    def test_account_published_domains_drive_the_count(self):
        res, statements = _run_probe(API, [USED])
        m = res["metrics"]
        self.assertEqual((m["distinct_domains"], m["subdomains"], m["draft_domains"]), (2, 1, 2))
        self.assertEqual((m["used_domains"], m["domain_tag_assignments"]), (1, 49))
        self.assertEqual(res["score"], 40 + 40 * 2 / 5 + 20)
        used = next(s for s in statements if "used_domains" in s)
        self.assertIn("'careportal/referrals'", used)
        self.assertNotIn("'sales'", used)  # a draft domain's tags don't count
        self.assertNotIn("'finance/audit'", used)
        self.assertIn("catalog_tags", used)  # catalogs belong to domains too

    def test_scoped_run_keeps_the_account_count_and_scores_coverage_separately(self):
        res, _ = _run_probe(API, [("used_domains", [{"used_domains": "0", "assignments": "0"}])],
                            scalar=0, scope=["cp"])
        self.assertEqual(res["metrics"]["distinct_domains"], 2)  # account-wide, same as unscoped
        self.assertEqual(res["metrics"]["used_domains"], 0)
        self.assertEqual(res["score"], 40 + 40 * 2 / 5)
        self.assertTrue(any("None of the account's published domains" in g for g in res["gaps"]))

    def test_tables_inherit_their_schema_and_catalog_domain(self):
        calls = []
        with patch.object(probes, "_native_domains", AsyncMock(return_value=API)), \
             patch.object(probes, "_resolve_sources", AsyncMock(return_value=SOURCES)), \
             patch.object(probes, "execute_sql", side_effect=_fake_sql([USED])[0]), \
             patch.object(probes, "_scalar", AsyncMock(side_effect=lambda q, **k: calls.append(q) or 3)):
            res = asyncio.run(probes.probe_domains())
        q = next(c for c in calls if "table_catalog IN (SELECT catalog_name" in c)
        self.assertIn("(table_catalog, table_schema) IN (SELECT catalog_name, schema_name", q)
        self.assertEqual(res["metrics"]["domain_tagged_assets"], 3)

    def test_falls_back_when_catalog_tags_are_unreadable(self):
        async def run(sql, **kw):
            if "used_domains" in sql and "catalog_tags" in sql:
                raise RuntimeError("catalog_tags not readable")
            return USED[1] if "used_domains" in sql else []
        with patch.object(probes, "_native_domains", AsyncMock(return_value=API)), \
             patch.object(probes, "_resolve_sources", AsyncMock(return_value=SOURCES)), \
             patch.object(probes, "execute_sql", side_effect=run), \
             patch.object(probes, "_scalar", AsyncMock(return_value=5)):
            res = asyncio.run(probes.probe_domains())
        self.assertTrue(res["available"])
        self.assertEqual(res["metrics"]["used_domains"], 1)

    def test_api_count_only_when_tags_are_unreadable(self):
        no_catalogs = {"system_ok": False, "catalogs": [], "schemas": None}
        for scope in (None, ["cp"]):
            res, _ = _run_probe(API, [], sources=no_catalogs, scope=scope)
            self.assertTrue(res["available"])
            self.assertEqual(res["score"], 40 + 40 * 2 / 5)
            self.assertEqual(res["metrics"]["distinct_domains"], 2)


class ScoreExclusionTest(unittest.TestCase):
    def _scorecard(self, domains_probe):
        from server.assessment import scoring
        from server.pillars import PILLARS
        probes_out = {p["key"]: {"available": True, "score": 50.0, "signals": [], "gaps": ["g"], "metrics": {}}
                      for p in PILLARS}
        probes_out["domains"] = domains_probe
        pillars = [scoring._assemble_pillar(p, probes_out[p["key"]]) for p in PILLARS]
        return scoring._finalize(pillars), pillars

    def test_excluded_pillar_is_left_out_of_the_overall(self):
        res, _ = _run_probe({"status": "forbidden"}, [])
        out, pillars = self._scorecard(res)
        self.assertEqual(out["overall"]["score"], 50.0)  # not dragged down by a 0
        self.assertEqual([e["name"] for e in out["overall"]["excluded_pillars"]], ["Domains & Stewardship"])
        self.assertIn("MANAGE DISCOVER", out["overall"]["excluded_pillars"][0]["reason"])
        self.assertTrue(next(p for p in pillars if p["key"] == "domains")["score_excluded"])

    def test_other_unavailable_pillars_still_count_as_zero(self):
        out, _ = self._scorecard({"available": False, "score": 0.0, "signals": [], "gaps": [], "metrics": {}})
        self.assertLess(out["overall"]["score"], 50.0)
        self.assertEqual(out["overall"]["excluded_pillars"], [])


if __name__ == "__main__":
    unittest.main()
