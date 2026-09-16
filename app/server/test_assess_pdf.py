"""Tests for the assessment PDF export (issue #15).

The route renders a deterministic, executive-ready readout of a scorecard —
overall + stage, the per-pillar table, top gaps, and per-pillar detail — reusing
the shared branded PDF machinery in ``server.pdf``. These lock in that the
Markdown carries the real numbers, unavailable pillars are labeled (not shown as
0), the title renders exactly once, and the document round-trips through pisa.
"""

import unittest

from server.routes.assess import (
    _assessment_markdown,
    _build_assessment_pdf_html,
    _assessment_filename,
)
from server.pdf import block_external_resources


def _scorecard() -> dict:
    return {
        "overall": {
            "score": 62.5,
            "level": 3,
            "level_label": "Developing",
            "readiness_stage": "Foundation forming",
            "readiness_detail": "A solid governed base with gaps in semantics.",
            "assessed_at": "2026-09-15T12:00:00+00:00",
        },
        "pillars": [
            {
                "key": "uc",
                "name": "Unity Catalog Foundation",
                "short": "Governed metadata coverage.",
                "summary": "Most catalogs are governed; comments are sparse.",
                "weight": 3,
                "score": 74,
                "level": 4,
                "level_label": "Established",
                "available": True,
                "identity": {"ran_as": "user", "label": "You (on-behalf-of)"},
                "signals": [
                    {"label": "Catalogs", "value": 12, "detail": "governed by UC"},
                    {"label": "Comment coverage", "value": 41, "unit": "%", "detail": "of tables"},
                ],
                "gaps": ["Add table/column comments on gold tables."],
                "best_practices": ["Certify gold tables and document them."],
                "drill_down": {"title": "x", "columns": [], "rows": [{"a": 1}]},
                "source_queries": [{"title": "q", "sql": "SELECT 1"}],
            },
            {
                "key": "domains",
                "name": "Domains & Stewardship",
                "short": "Domain tags and owners.",
                "summary": "",
                "weight": 2,
                "score": 0,
                "level": 1,
                "level_label": "Absent",
                "available": False,
                "note": "The service principal lacks access to read tags.",
                "signals": [],
                "gaps": [],
                "best_practices": [],
            },
        ],
        "top_gaps": [
            {"pillar": "Domains & Stewardship", "gap": "No domains defined."},
            {"pillar": "Unity Catalog Foundation", "gap": "Sparse comments."},
        ],
    }


class AssessmentMarkdownTest(unittest.TestCase):
    def test_includes_overall_and_stage(self):
        md = _assessment_markdown(_scorecard())
        self.assertIn("## Overall readiness", md)
        self.assertIn("62.5/100", md)
        self.assertIn("L3 Developing", md)
        self.assertIn("Foundation forming", md)
        self.assertIn("A solid governed base", md)

    def test_pillar_table_has_scores_levels_weights(self):
        md = _assessment_markdown(_scorecard())
        self.assertIn("| Pillar | Score | Maturity | Weight |", md)
        self.assertIn("Unity Catalog Foundation", md)
        self.assertIn("L4 Established", md)

    def test_unavailable_pillar_labeled_not_zero(self):
        # An unavailable pillar must read "not available" with its reason, never a 0.
        md = _assessment_markdown(_scorecard())
        self.assertIn("Domains & Stewardship — not available", md)
        self.assertIn("lacks access to read tags", md)

    def test_top_gaps_and_signals_and_practices(self):
        md = _assessment_markdown(_scorecard())
        self.assertIn("## Top gaps to close", md)
        self.assertIn("No domains defined.", md)
        self.assertIn("**Catalogs:** 12", md)
        self.assertIn("**Comment coverage:** 41%", md)
        self.assertIn("**Recommended practices**", md)
        self.assertIn("Certify gold tables", md)

    def test_identity_attribution_rendered(self):
        md = _assessment_markdown(_scorecard())
        self.assertIn("Assessed as: You (on-behalf-of)", md)

    def test_partial_overall_renders_no_none_literals(self):
        # A degraded snapshot with pillars but a missing/partial `overall` must never
        # print a literal "None/100" or "LNone" header — it degrades cleanly (#15).
        sc = _scorecard()
        sc["overall"] = {"readiness_stage": "Foundation forming"}  # no score/level
        md = _assessment_markdown(sc)
        self.assertNotIn("None/100", md)
        self.assertNotIn("LNone", md)
        self.assertIn("Foundation forming", md)

    def test_no_document_h1(self):
        # The builder supplies the single H1; the Markdown must not add one, or the
        # title would render twice (the bug fixed for the plan export in #21).
        md = _assessment_markdown(_scorecard())
        self.assertFalse(any(ln.lstrip().startswith("# ") for ln in md.splitlines()))


class BuildAssessmentPdfHtmlTest(unittest.TestCase):
    def test_title_escaped(self):
        html_doc = _build_assessment_pdf_html(_scorecard(), "Acme <R&D> Assessment")
        self.assertIn("Acme &lt;R&amp;D&gt; Assessment", html_doc)
        self.assertNotIn("<R&D>", html_doc)

    def test_includes_footer_and_real_css(self):
        html_doc = _build_assessment_pdf_html(_scorecard(), "Assessment")
        self.assertIn("@frame footer", html_doc)
        self.assertIn("<pdf:pagenumber>", html_doc)

    def test_filename_is_dated(self):
        name = _assessment_filename("Genie Ontology Readiness — Assessment")
        self.assertRegex(name, r"\d{4}-\d{2}-\d{2}$")

    def test_renders_pdf_with_title_once(self):
        # End-to-end through the real builder → pisa → text. Skip if deps absent.
        try:
            import io
            from xhtml2pdf import pisa
            from pypdf import PdfReader
        except Exception:  # pragma: no cover - optional deps
            self.skipTest("xhtml2pdf/pypdf not installed")

        title = "Genie Ontology Readiness — Assessment"
        html_doc = _build_assessment_pdf_html(_scorecard(), title)
        buf = io.BytesIO()
        result = pisa.CreatePDF(
            src=html_doc, dest=buf, encoding="utf-8", link_callback=block_external_resources
        )
        self.assertFalse(result.err, "PDF generation should succeed with the real CSS + footer")
        text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(buf.getvalue())).pages)
        self.assertEqual(text.count("Genie Ontology Readiness — Assessment"), 1)
        self.assertNotIn("<pdf:pagenumber>", text)
        self.assertIn("Unity Catalog Foundation", text)
        self.assertIn("Domains & Stewardship", text)


if __name__ == "__main__":
    unittest.main()
