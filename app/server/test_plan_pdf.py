"""Regression tests for the plan PDF-generation path (issue #21).

The PDF route renders the document title once itself (``<h1>{title}</h1>``). The
generated plan body used to also open with its own ``# <title>`` heading, so the
opening line rendered twice in the exported PDF. ``_strip_document_h1`` removes any
top-level H1 from the body; these tests lock that in so the duplicate can't return.
"""

import unittest

from server.routes.plan import _strip_document_h1, _build_plan_pdf_html


class StripDocumentH1Test(unittest.TestCase):
    def test_removes_leading_title_h1(self):
        # The real shape: deterministic H2 summary first, then the model's own
        # '# <title>' H1 after the divider. The title H1 must go; the H2s must stay.
        md = (
            "## Assessment summary\n\n"
            "**Overall readiness: 62/100**\n\n"
            "---\n\n"
            "# Genie Ontology Readiness Action Plan\n\n"
            "## Where you are\n\nStrong foundation.\n"
        )
        out = _strip_document_h1(md)
        self.assertNotIn("# Genie Ontology Readiness Action Plan", out)
        self.assertIn("## Assessment summary", out)
        self.assertIn("## Where you are", out)

    def test_removes_only_first_h1_keeps_later_ones(self):
        # Only the redundant title (first H1) is removed; a later legitimate H1
        # (e.g. an appendix) and its content must survive, not be orphaned.
        md = (
            "# Genie Ontology Readiness Action Plan\n\n"
            "## Where you are\n\nText.\n\n"
            "# Appendix\n\nGlossary definition.\n"
        )
        out = _strip_document_h1(md)
        self.assertNotIn("# Genie Ontology Readiness Action Plan", out)
        self.assertIn("# Appendix", out)
        self.assertIn("Glossary definition.", out)

    def test_removes_h1_without_space(self):
        # Python-Markdown renders '#Title' (no space) as an H1, so the stripper must
        # catch it too — otherwise the title still duplicates in the PDF.
        md = "#Genie Ontology Readiness Action Plan\n\n## Where you are\n\nText.\n"
        out = _strip_document_h1(md)
        self.assertNotIn("Genie Ontology Readiness Action Plan", out)
        self.assertTrue(out.startswith("## Where you are"))

    def test_h1_removed_regardless_of_exact_title_text(self):
        # Robust to the model rephrasing the title / using a different dash than the
        # route's title — we strip by structure (any H1), not by matching the title.
        md = "# Some Rephrased Plan Title\n\n## Where you are\n\nText.\n"
        out = _strip_document_h1(md)
        self.assertNotIn("Some Rephrased Plan Title", out)
        self.assertTrue(out.startswith("## Where you are"))

    def test_removes_setext_h1(self):
        md = "Genie Ontology Readiness Action Plan\n===\n\n## Where you are\n\nText.\n"
        out = _strip_document_h1(md)
        self.assertNotIn("Genie Ontology Readiness Action Plan", out)
        self.assertIn("## Where you are", out)

    def test_preserves_h2_and_h3_and_hashes_in_text(self):
        md = "## Section\n\n### Sub\n\nUse `#tag` inline and C# too.\n"
        out = _strip_document_h1(md)
        self.assertIn("## Section", out)
        self.assertIn("### Sub", out)
        self.assertIn("`#tag`", out)  # a '#' mid-line is not a heading
        self.assertIn("C# too", out)

    def test_preserves_hash_comment_inside_fenced_code(self):
        # A '# comment' line inside a ``` fence is code, not a heading — keep it.
        md = (
            "## Where you are\n\n"
            "```bash\n# install deps\npip install foo\n```\n\n"
            "## Next\n\nText.\n"
        )
        out = _strip_document_h1(md)
        self.assertIn("# install deps", out)
        self.assertIn("pip install foo", out)

    def test_strips_h1_but_keeps_later_fenced_comment(self):
        # Both behaviors together: drop the real title H1, keep the fenced '#' line.
        md = (
            "# Genie Ontology Readiness Action Plan\n\n"
            "## Steps\n\n```sql\n# not a heading\nSELECT 1;\n```\n"
        )
        out = _strip_document_h1(md)
        self.assertNotIn("# Genie Ontology Readiness Action Plan", out)
        self.assertIn("# not a heading", out)

    def test_longer_outer_fence_not_closed_by_shorter_inner(self):
        # A 4-backtick fence must not be closed by a 3-backtick line; the '# ...'
        # inside stays code and is preserved.
        md = (
            "## Steps\n\n"
            "````\n```\n# still code\n```\n````\n\n"
            "## Next\n\nText.\n"
        )
        out = _strip_document_h1(md)
        self.assertIn("# still code", out)

    def test_indented_four_spaces_is_code_not_heading(self):
        # 4+ leading spaces = indented code block per CommonMark, not an H1.
        md = "## Section\n\n    # indented code line\n\nText.\n"
        out = _strip_document_h1(md)
        self.assertIn("    # indented code line", out)

    def test_no_h1_is_noop(self):
        md = "## Where you are\n\nText.\n\n## Next\n\nMore.\n"
        self.assertEqual(_strip_document_h1(md).strip(), md.strip())

class BuildPlanPdfHtmlTest(unittest.TestCase):
    def test_title_escaped(self):
        # A title with HTML-special chars must be escaped so it can't emit stray
        # tags / invalid entities that break xhtml2pdf.
        html_doc = _build_plan_pdf_html("## Where you are\n\nText.\n", "R&D <Draft> Plan")
        self.assertIn("R&amp;D &lt;Draft&gt; Plan", html_doc)
        self.assertNotIn("<Draft>", html_doc)

    def test_includes_footer_and_real_css(self):
        # The assembled HTML uses the real _PDF_CSS + page-number footer syntax.
        html_doc = _build_plan_pdf_html("## Where you are\n\nText.\n", "Plan")
        self.assertIn("@frame footer", html_doc)
        self.assertIn("<pdf:pagenumber>", html_doc)

    def test_renders_pdf_title_once_with_footer(self):
        # End-to-end through the REAL builder (real CSS + footer) → pisa → text.
        # Skip cleanly if the optional PDF deps aren't installed here.
        try:
            import io
            from xhtml2pdf import pisa
            from pypdf import PdfReader
        except Exception:  # pragma: no cover - optional deps
            self.skipTest("xhtml2pdf/pypdf not installed")

        body = (
            "## Assessment summary\n\n**Overall: 62/100**\n\n---\n\n"
            "# Genie Ontology Readiness Action Plan\n\n## Where you are\n\nStrong.\n"
        )
        html_doc = _build_plan_pdf_html(body, "Genie Ontology Readiness — Action Plan")
        buf = io.BytesIO()
        result = pisa.CreatePDF(src=html_doc, dest=buf, encoding="utf-8")
        self.assertFalse(result.err, "PDF generation should succeed with the real CSS + footer")
        text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(buf.getvalue())).pages)
        self.assertEqual(text.count("Action Plan"), 1, "title must appear exactly once")
        # Footer page-number rendered (not left as the literal tag).
        self.assertNotIn("<pdf:pagenumber>", text)
        self.assertIn("page 1 of 1", " ".join(text.split()))


if __name__ == "__main__":
    unittest.main()
