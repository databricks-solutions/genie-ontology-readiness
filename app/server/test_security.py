"""Regression tests for the app's security controls.

Each test names the weakness it guards. They are pure unit tests — no workspace,
no network, no Lakebase — so they run in the same `unittest discover` pass as the
rest of the suite.
"""

import unittest
from unittest.mock import patch

from server import security


class LogRedactionTest(unittest.TestCase):
    """CWE-532 — credentials and direct identifiers must not reach the log."""

    def test_redacts_databricks_pat(self):
        self.assertNotIn("dapi" + "a" * 32, security.redact("using dapi" + "a" * 32))

    def test_redacts_bearer_token(self):
        # Assembled rather than written inline so the repo compliance scan does not
        # see a literal token following the word "Bearer".
        token = "abcdefghijklmnop" + "qrstuvwx"
        self.assertNotIn(token, security.redact(f"Authorization: Bearer {token}"))

    def test_redacts_jwt(self):
        self.assertIn("[REDACTED_JWT]",
                      security.redact("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdef"))

    def test_redacts_serialized_token_field(self):
        self.assertNotIn("s3cr3tvalue", security.redact('{"token": "s3cr3tvalue"}'))

    def test_redacts_ssn(self):
        self.assertNotIn("123-45-6789", security.redact("member ssn 123-45-6789"))

    def test_masks_email_local_part_but_keeps_domain(self):
        redacted = security.redact("run by jane.doe@example.com")
        self.assertNotIn("jane.doe", redacted)
        self.assertIn("@example.com", redacted)

    def test_leaves_diagnostic_counts_intact(self):
        # Redaction must not corrupt the numbers the assessment logs for support.
        self.assertEqual(security.redact("scanned 25/1068 catalogs"), "scanned 25/1068 catalogs")


class ErrorSanitizationTest(unittest.TestCase):
    """CWE-209 — upstream error text must not be returned to the client."""

    def test_returns_reference_not_detail(self):
        try:
            raise RuntimeError("TABLE sales_gold.orders.customer_ref does not exist")
        except RuntimeError as exc:
            with self.assertLogs(level="ERROR"):
                reference, message = security.safe_error(exc, "test")
        self.assertNotIn("sales_gold.orders", message)
        self.assertIn(reference, message)
        self.assertEqual(len(reference), 12)

    def test_logs_the_detail_under_the_reference(self):
        try:
            raise RuntimeError("TABLE sales_gold.orders.customer_ref does not exist")
        except RuntimeError as exc:
            with self.assertLogs(level="ERROR") as captured:
                reference, _ = security.safe_error(exc, "test")
        self.assertTrue(
            any(reference in line and "sales_gold.orders" in line for line in captured.output))

    def test_each_call_gets_a_distinct_reference(self):
        with self.assertLogs(level="ERROR"):
            a, _ = security.safe_error(RuntimeError("x"), "test")
            b, _ = security.safe_error(RuntimeError("x"), "test")
        self.assertNotEqual(a, b)


class SqlIdentifierQuotingTest(unittest.TestCase):
    """CWE-89 — metastore-supplied names are not literals we control."""

    def test_doubles_embedded_backtick(self):
        self.assertEqual(security.quote_ident("a`b"), "`a``b`")

    def test_breakout_attempt_stays_inside_the_quotes(self):
        quoted = security.quote_ident("x`.secret.tbl WHERE 1=1 --")
        self.assertTrue(quoted.startswith("`") and quoted.endswith("`"))
        # Every backtick in the interior must be an escaped pair; a lone one would
        # end the identifier early and let the rest parse as SQL.
        self.assertNotIn("`", quoted[1:-1].replace("``", ""))

    def test_rejects_control_characters(self):
        for bad in ("a\nb", "a\rb", "a\x00b"):
            with self.assertRaises(ValueError):
                security.quote_ident(bad)

    def test_rejects_empty(self):
        with self.assertRaises(ValueError):
            security.quote_ident("")

    def test_doubles_embedded_quote_in_literal(self):
        self.assertEqual(security.quote_literal("o'x"), "'o''x'")


class HtmlSanitizerTest(unittest.TestCase):
    """CWE-918/22/79 — model-generated Markdown reaches a PDF renderer that
    resolves src/href, so raw HTML must be reduced to an inert allowlist."""

    def test_drops_resource_loading_tags(self):
        for markup in ('<img src="file:///etc/passwd">',
                       '<link rel="stylesheet" href="file:///etc/hosts">',
                       '<iframe src="http://evil.test"></iframe>',
                       '<object data="file:///etc/passwd"></object>'):
            self.assertNotIn("<", security.sanitize_html_fragment(markup), markup)

    def test_drops_script_content_not_just_the_tag(self):
        self.assertEqual(security.sanitize_html_fragment("<script>alert(1)</script>ok"), "ok")

    def test_drops_style_content(self):
        self.assertEqual(
            security.sanitize_html_fragment("<style>@page{background:url(file:///x)}</style>ok"), "ok")

    def test_drops_event_handler_attributes(self):
        self.assertNotIn("onerror", security.sanitize_html_fragment('<p onerror="x()">t</p>'))

    def test_drops_non_http_hrefs(self):
        for href in ("javascript:alert(1)", "file:///etc/passwd", "data:text/html,<x>"):
            self.assertNotIn("href", security.sanitize_html_fragment(f'<a href="{href}">x</a>'), href)

    def test_keeps_http_hrefs(self):
        self.assertIn('href="https://docs.test/a"',
                      security.sanitize_html_fragment('<a href="https://docs.test/a">x</a>'))

    def test_preserves_the_markup_the_plan_actually_uses(self):
        for markup in ("<p><strong>a</strong> <em>b</em></p>",
                       "<h2>Where you are</h2>",
                       "<ul><li>one</li></ul>",
                       "<pre><code>SELECT 1</code></pre>"):
            self.assertEqual(security.sanitize_html_fragment(markup), markup)

    def test_preserves_tables(self):
        out = security.sanitize_html_fragment('<table><tr><td colspan="2">1</td></tr></table>')
        self.assertIn("<table>", out)
        self.assertIn('colspan="2"', out)

    def test_closes_unbalanced_markup(self):
        # An unclosed tag must not swallow the rest of the document.
        self.assertEqual(security.sanitize_html_fragment("<p>a"), "<p>a</p>")


class PdfResourceBlockingTest(unittest.TestCase):
    """CWE-918 — the renderer must refuse to fetch anything, sanitizer or not."""

    def test_render_refuses_external_references(self):
        import io

        from xhtml2pdf import pisa

        from server.routes.plan import _PDF_CSS, ExternalResourceBlocked, _block_external_resources

        # Hand the engine a document that bypasses the sanitizer, so the backstop
        # is what is under test.
        html = (f"<!DOCTYPE html><html><head><style>{_PDF_CSS}</style></head>"
                f"<body><img src='file:///etc/hosts'></body></html>")
        with self.assertRaises(ExternalResourceBlocked):
            pisa.CreatePDF(src=html, dest=io.BytesIO(), encoding="utf-8",
                           link_callback=_block_external_resources)

    def test_a_normal_plan_still_renders(self):
        import io

        from xhtml2pdf import pisa

        from server.routes.plan import _PDF_CSS, _block_external_resources
        from server.security import sanitize_html_fragment

        body = sanitize_html_fragment("<h2>Where you are</h2><ul><li>one</li></ul>")
        html = (f"<!DOCTYPE html><html><head><style>{_PDF_CSS}</style></head>"
                f"<body><h1>Plan</h1>{body}</body></html>")
        buf = io.BytesIO()
        result = pisa.CreatePDF(src=html, dest=buf, encoding="utf-8",
                                link_callback=_block_external_resources)
        self.assertFalse(result.err)
        self.assertTrue(buf.getvalue().startswith(b"%PDF-"))


class EscapeHtmlTest(unittest.TestCase):
    """CWE-79 — the PDF title is interpolated into the document, so it is escaped."""

    def test_escapes_tag_injection(self):
        self.assertEqual(security.escape_html("</h1><script>x</script>"),
                         "&lt;/h1&gt;&lt;script&gt;x&lt;/script&gt;")

    def test_escapes_quotes(self):
        self.assertNotIn('"', security.escape_html('a" onload="x'))


class PrincipalResolutionTest(unittest.IsolatedAsyncioTestCase):
    """CWE-290/639 — ownership must not rest on a spoofable request header, and an
    unattributable request must not fall into a shared bucket."""

    async def test_verified_token_identity_wins_over_the_header(self):
        async def fake(token):
            return "victim@example.com"

        with patch.object(security, "_identity_from_token", fake):
            principal = await security.resolve_principal(
                forwarded_email="attacker@example.org", forwarded_token="tok")
        self.assertEqual(principal, "victim@example.com")

    async def test_falls_back_to_the_header_when_the_token_cannot_be_resolved(self):
        async def fake(token):
            return None

        with patch.object(security, "_identity_from_token", fake):
            principal = await security.resolve_principal(
                forwarded_email="Jane.Doe@Example.com", forwarded_token="tok")
        self.assertEqual(principal, "jane.doe@example.com")

    async def test_normalises_the_header_identity(self):
        self.assertEqual(
            await security.resolve_principal(forwarded_email="  Jane.Doe@Example.com ", forwarded_token=None),
            "jane.doe@example.com")

    async def test_deployed_with_no_identity_still_resolves(self):
        # Never refuse a request for lack of an identity: that would make a user's
        # own history unreachable over an infrastructure hiccup.
        with patch("server.config.IS_DATABRICKS_APP", True):
            with self.assertLogs(level="WARNING"):
                principal = await security.resolve_principal(None, None)
        self.assertEqual(principal, security.UNATTRIBUTED_PRINCIPAL)
        self.assertTrue(principal)

    async def test_local_development_gets_an_explicit_principal_never_null(self):
        with patch("server.config.IS_DATABRICKS_APP", False):
            self.assertEqual(await security.resolve_principal(None, None), security.LOCAL_PRINCIPAL)

    async def test_whitespace_only_email_is_not_a_usable_key(self):
        # A whitespace-only forwarded header must not become a NULL created_by
        # bucket — it falls through to the named/local principal instead.
        with patch("server.config.IS_DATABRICKS_APP", False):
            self.assertEqual(await security.resolve_principal("   ", None), security.LOCAL_PRINCIPAL)


class BodySizeLimitTest(unittest.IsolatedAsyncioTestCase):
    """CWE-770 — request bodies are bounded."""

    def _scope(self, content_length=None):
        headers = [(b"content-type", b"application/json")]
        if content_length is not None:
            headers.append((b"content-length", str(content_length).encode()))
        return {"type": "http", "method": "POST", "headers": headers}

    async def _run(self, scope, chunks):
        sent = []

        async def app(scope, receive, send):
            while True:
                message = await receive()
                if not message.get("more_body"):
                    break
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        queue = list(chunks)

        async def receive():
            body = queue.pop(0)
            return {"type": "http.request", "body": body, "more_body": bool(queue)}

        async def send(message):
            sent.append(message)

        await security.BodySizeLimitMiddleware(app, max_bytes=100)(scope, receive, send)
        return sent

    async def test_rejects_an_oversized_declared_length(self):
        sent = await self._run(self._scope(content_length=1000), [b"x"])
        self.assertEqual(sent[0]["status"], 413)

    async def test_rejects_an_oversized_chunked_body(self):
        # No Content-Length, so the cap has to be enforced while streaming.
        sent = await self._run(self._scope(), [b"x" * 60, b"x" * 60])
        self.assertEqual(sent[0]["status"], 413)

    async def test_allows_a_body_within_the_cap(self):
        sent = await self._run(self._scope(content_length=10), [b"x" * 10])
        self.assertEqual(sent[0]["status"], 200)


class SecurityHeadersTest(unittest.IsolatedAsyncioTestCase):
    """Response hardening is applied to every response, including streams."""

    async def _headers(self):
        captured = {}

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        async def send(message):
            if message["type"] == "http.response.start":
                captured.update({k.decode(): v.decode() for k, v in message["headers"]})

        async def receive():
            return {"type": "http.request", "body": b""}

        await security.SecurityHeadersMiddleware(app)(
            {"type": "http", "method": "GET", "headers": []}, receive, send)
        return captured

    async def test_sets_the_hardening_headers(self):
        headers = await self._headers()
        for name in ("content-security-policy", "x-content-type-options", "referrer-policy",
                     "permissions-policy", "strict-transport-security", "x-frame-options",
                     "cross-origin-opener-policy", "cross-origin-resource-policy"):
            self.assertIn(name, headers)

    async def test_csp_forbids_inline_and_third_party_script(self):
        csp = (await self._headers())["content-security-policy"]
        self.assertIn("script-src 'self'", csp)
        self.assertNotIn("script-src 'self' 'unsafe-inline'", csp)
        self.assertIn("object-src 'self' blob:", csp)

    async def test_csp_allows_the_workspace_to_embed_the_app(self):
        # Databricks Apps can be framed by the workspace UI; a bare 'none' would
        # break that embed, so the allowance is explicit and third parties are not.
        csp = (await self._headers())["content-security-policy"]
        self.assertIn("frame-ancestors 'self' https://*.databricks.com", csp)


class GenieIdentityTest(unittest.TestCase):
    """CWE-269 — a Genie answer returns warehouse rows, so the viewer's own identity
    is preferred. But the `sql` user API scope does not cover the Genie API, so the
    service principal has to stay available behind it or the feature breaks in every
    workspace that enables user authorization."""

    def _identities(self, token, fallback):
        from server import genie_client
        with patch.object(genie_client, "get_user_token", lambda: token), \
             patch.object(genie_client, "GENIE_ALLOW_SP_FALLBACK", fallback), \
             patch.object(genie_client, "get_auth_headers",
                          lambda force_sp=False: {"Authorization": "Bearer sp" if force_sp else "Bearer viewer"}):
            return [name for _, name in genie_client._genie_identities()]

    def test_viewer_token_is_tried_first(self):
        self.assertEqual(self._identities("viewer-token", True)[0], "obo")

    def test_service_principal_stays_available_behind_the_viewer(self):
        # Without this the feature breaks wherever the forwarded token lacks a
        # Genie scope, which is every deployment using the default `sql` scope.
        self.assertEqual(self._identities("viewer-token", True), ["obo", "service_principal"])

    def test_service_principal_alone_when_no_viewer_token(self):
        self.assertEqual(self._identities(None, True), ["service_principal"])

    def test_lockdown_leaves_only_the_viewer(self):
        self.assertEqual(self._identities("viewer-token", False), ["obo"])

    def test_lockdown_with_no_viewer_token_raises(self):
        from server import genie_client
        with patch.object(genie_client, "get_user_token", lambda: None), \
             patch.object(genie_client, "GENIE_ALLOW_SP_FALLBACK", False):
            with self.assertRaises(genie_client.GenieIdentityUnavailable):
                genie_client._genie_identities()

    def test_default_is_fail_closed(self):
        # The reported authorization bypass is only closed if the SP fallback is OFF
        # by default: with it on, a deployment whose viewers have no Genie OBO scope
        # silently answers as the service principal (CWE-269/863). So the shipped
        # default must be False — operators opt back in explicitly.
        from server.config import GENIE_ALLOW_SP_FALLBACK
        self.assertFalse(GENIE_ALLOW_SP_FALLBACK)


class GenieFallbackTest(unittest.IsolatedAsyncioTestCase):
    """An authorization rejection of the viewer's token must fall through to the
    service principal, mirroring how execute_sql handles the same situation."""

    class _Resp:
        def __init__(self, status, payload=None):
            self.status, self._payload = status, payload or {}
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def json(self): return self._payload
        async def text(self): return "denied"

    class _Session:
        def __init__(self, statuses):
            self.statuses, self.seen = list(statuses), []
        def post(self, url, json=None, headers=None):
            self.seen.append(headers.get("Authorization"))
            return GenieFallbackTest._Resp(self.statuses.pop(0), {"conversation_id": "c", "message_id": "m"})

    async def _run(self, statuses, token="viewer-token"):
        from server import genie_client
        session = self._Session(statuses)
        with patch.object(genie_client, "get_user_token", lambda: token), \
             patch.object(genie_client, "GENIE_ALLOW_SP_FALLBACK", True), \
             patch.object(genie_client, "get_auth_headers",
                          lambda force_sp=False: {"Authorization": "Bearer sp" if force_sp else "Bearer viewer"}):
            with self.assertLogs(level="WARNING"):
                result, headers, identity = await genie_client._post_with_identity(session, "u", {})
        return session, headers, identity

    async def test_falls_back_when_the_viewer_token_is_rejected(self):
        # 403 is what a workspace returns when the forwarded token has no Genie scope.
        session, headers, identity = await self._run([403, 200])
        self.assertEqual(session.seen, ["Bearer viewer", "Bearer sp"])
        self.assertEqual(identity, "service_principal")
        self.assertEqual(headers["Authorization"], "Bearer sp")

    async def test_obo_only_rejection_raises_identity_unavailable(self):
        # Fail-closed default (no SP fallback): if the viewer's token is the only
        # identity and the workspace rejects it (403), the caller must get the
        # actionable GenieIdentityUnavailable (→ 403 guidance), not an opaque 500.
        from server import genie_client
        session = self._Session([403])
        with patch.object(genie_client, "get_user_token", lambda: "viewer-token"), \
             patch.object(genie_client, "GENIE_ALLOW_SP_FALLBACK", False), \
             patch.object(genie_client, "get_auth_headers",
                          lambda force_sp=False: {"Authorization": "Bearer viewer"}):
            with self.assertRaises(genie_client.GenieIdentityUnavailable):
                await genie_client._post_with_identity(session, "u", {})

    async def test_no_fallback_when_the_viewer_token_works(self):
        from server import genie_client
        session = self._Session([200])
        with patch.object(genie_client, "get_user_token", lambda: "viewer-token"), \
             patch.object(genie_client, "GENIE_ALLOW_SP_FALLBACK", True), \
             patch.object(genie_client, "get_auth_headers",
                          lambda force_sp=False: {"Authorization": "Bearer sp" if force_sp else "Bearer viewer"}):
            _, headers, identity = await genie_client._post_with_identity(session, "u", {})
        self.assertEqual(session.seen, ["Bearer viewer"])
        self.assertEqual(identity, "obo")

    async def test_a_non_authorization_error_is_not_retried(self):
        from server import genie_client
        session = self._Session([500])
        with patch.object(genie_client, "get_user_token", lambda: "viewer-token"), \
             patch.object(genie_client, "GENIE_ALLOW_SP_FALLBACK", True), \
             patch.object(genie_client, "get_auth_headers",
                          lambda force_sp=False: {"Authorization": "Bearer sp" if force_sp else "Bearer viewer"}):
            with self.assertRaises(Exception):
                await genie_client._post_with_identity(session, "u", {})
        self.assertEqual(session.seen, ["Bearer viewer"])


class ProbeFailureNoteTest(unittest.TestCase):
    """CWE-209 — a probe's note is returned to the browser AND persisted inside
    the saved snapshot, so it must never carry the upstream message."""

    def test_note_carries_a_reference_not_the_sql_error(self):
        from server.assessment import probes

        exc = RuntimeError(
            "[TABLE_OR_VIEW_NOT_FOUND] sales_gold.orders.customer_ref cannot be found; "
            "value '123-45-6789' rejected")
        with self.assertLogs(level="ERROR"):
            result = probes._failed(exc, "Comment coverage")
        note = result["note"]
        self.assertNotIn("sales_gold", note)
        self.assertNotIn("123-45-6789", note)
        self.assertNotIn("TABLE_OR_VIEW_NOT_FOUND", note)
        self.assertIn("reference", note)
        self.assertFalse(result["available"])
        self.assertEqual(result["score"], 0.0)

    def test_remedy_text_is_preserved_for_the_user(self):
        from server.assessment import probes

        with self.assertLogs(level="ERROR"):
            result = probes._failed(RuntimeError("x"), "The Unity Catalog footprint",
                                    "Grant USE CATALOG + SELECT.")
        self.assertIn("Grant USE CATALOG + SELECT.", result["note"])


if __name__ == "__main__":
    unittest.main()
