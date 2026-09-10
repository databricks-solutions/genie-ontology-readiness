"""Regression tests for the controls added in the SCA remediation.

Each test names the weakness it guards. They are pure unit tests — no workspace,
no network, no Lakebase — so they run in the same `unittest discover` pass as the
rest of the suite. See docs/SECURITY-REMEDIATION.md for the finding behind each.
"""

import asyncio
import unittest
from unittest.mock import patch

from server import security


class LogRedactionTest(unittest.TestCase):
    """CWE-532 — credentials and direct identifiers must not reach the log."""

    def test_redacts_databricks_pat(self):
        self.assertNotIn("dapi" + "a" * 32, security.redact("using dapi" + "a" * 32))

    def test_redacts_bearer_token(self):
        self.assertNotIn("abcdefghijklmnopqrst",
                         security.redact("Authorization: Bearer abcdefghijklmnopqrstuvwx"))

    def test_redacts_jwt(self):
        self.assertIn("[REDACTED_JWT]",
                      security.redact("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdef"))

    def test_redacts_serialized_token_field(self):
        self.assertNotIn("s3cr3tvalue", security.redact('{"token": "s3cr3tvalue"}'))

    def test_redacts_ssn(self):
        self.assertNotIn("123-45-6789", security.redact("member ssn 123-45-6789"))

    def test_masks_email_local_part_but_keeps_domain(self):
        redacted = security.redact("run by jane.doe@insurer.test")
        self.assertNotIn("jane.doe", redacted)
        self.assertIn("@insurer.test", redacted)

    def test_leaves_diagnostic_counts_intact(self):
        # Redaction must not corrupt the numbers the assessment logs for support.
        self.assertEqual(security.redact("scanned 25/1068 catalogs"), "scanned 25/1068 catalogs")


class ErrorSanitizationTest(unittest.TestCase):
    """CWE-209 — upstream error text must not be returned to the client."""

    def test_returns_reference_not_detail(self):
        try:
            raise RuntimeError("TABLE phi.claims.member_ssn does not exist")
        except RuntimeError as exc:
            with self.assertLogs(level="ERROR"):
                reference, message = security.safe_error(exc, "test")
        self.assertNotIn("phi.claims", message)
        self.assertIn(reference, message)
        self.assertEqual(len(reference), 12)

    def test_logs_the_detail_under_the_reference(self):
        try:
            raise RuntimeError("TABLE phi.claims.member_ssn does not exist")
        except RuntimeError as exc:
            with self.assertLogs(level="ERROR") as captured:
                reference, _ = security.safe_error(exc, "test")
        self.assertTrue(any(reference in line and "phi.claims" in line for line in captured.output))

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
            return "victim@insurer.test"

        with patch.object(security, "_identity_from_token", fake):
            principal = await security.resolve_principal(
                forwarded_email="attacker@evil.test", forwarded_token="tok")
        self.assertEqual(principal, "victim@insurer.test")

    async def test_falls_back_to_the_header_when_the_token_cannot_be_resolved(self):
        async def fake(token):
            return None

        with patch.object(security, "_identity_from_token", fake):
            principal = await security.resolve_principal(
                forwarded_email="Jane.Doe@X.test", forwarded_token="tok")
        self.assertEqual(principal, "jane.doe@x.test")

    async def test_normalises_the_header_identity(self):
        self.assertEqual(
            await security.resolve_principal(forwarded_email="  Jane.Doe@X.test ", forwarded_token=None),
            "jane.doe@x.test")

    async def test_deployed_with_no_identity_is_unattributable(self):
        with patch("server.config.IS_DATABRICKS_APP", True):
            self.assertIsNone(await security.resolve_principal(None, None))

    async def test_local_development_gets_an_explicit_principal_never_null(self):
        with patch("server.config.IS_DATABRICKS_APP", False):
            self.assertEqual(await security.resolve_principal(None, None), security.LOCAL_PRINCIPAL)


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


if __name__ == "__main__":
    unittest.main()
