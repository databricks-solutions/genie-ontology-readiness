"""Impact statements (server/impact.py): fixed text, wired into scoring and the PDF.

Covers: the statement form and product-accuracy wording, the gap ids that metadata
probe emits, the scoring join (a pillar with gap ids and a pillar without them),
and the Markdown readout used by the PDF export.
"""

import asyncio
import re
import unittest
from unittest.mock import AsyncMock, patch

from server import impact
from server.pillars import PILLAR_KEYS, PILLARS_BY_KEY
from server.assessment import probes
from server.assessment.scoring import _assemble_pillar, _finalize
from server.routes.assess import _assessment_markdown

ALL_STATEMENTS = {**{f"pillar:{k}": v for k, v in impact.PILLAR_IMPACT.items()},
                  **{f"gap:{k}": v for k, v in impact.GAP_IMPACT.items()}}

REQUIRED_GAP_IDS = {
    "table_comments_low", "column_comments_low", "no_tagged_tables",
    "no_user_catalogs", "small_table_footprint", "hive_metastore_tables",
    "no_gold_layer", "no_fk_constraints",
    "no_metric_views", "few_metric_views", "metric_views_uncommented",
    "no_genie_agents", "no_active_genie_agents",
}


class StatementShapeTest(unittest.TestCase):
    def test_every_pillar_has_a_statement(self):
        self.assertEqual(set(impact.PILLAR_IMPACT), set(PILLAR_KEYS))

    def test_required_gap_statements_exist(self):
        self.assertTrue(REQUIRED_GAP_IDS <= set(impact.GAP_IMPACT))

    def test_statements_follow_the_fixed_form(self):
        for name, text in ALL_STATEMENTS.items():
            with self.subTest(name=name):
                self.assertTrue(text.startswith("Because you "), text)
                self.assertIn("Genie Ontology will likely", text)
                self.assertTrue(text.endswith("."), text)

    def test_plain_wording_no_contractions(self):
        for name, text in ALL_STATEMENTS.items():
            with self.subTest(name=name):
                self.assertNotIn("'", text)

    def test_never_positions_the_learned_layer_as_ga(self):
        for name, text in ALL_STATEMENTS.items():
            with self.subTest(name=name):
                self.assertIsNone(re.search(r"\bGA\b|generally available", text, re.I), text)
        self.assertIn("Private Preview", impact.OVERALL_FRAMING)

    def test_metadata_gap_ids_match_the_spec(self):
        from server.signal_spec import METADATA

        spec_ids = {g["id"] for g in METADATA["gaps"]}
        self.assertTrue(spec_ids <= set(impact.GAP_IMPACT))

    def test_unknown_keys_return_none(self):
        self.assertIsNone(impact.pillar_impact("nope", score=0.0, has_gaps=True, available=True))
        self.assertIsNone(impact.gap_impact("nope"))
        self.assertIsNone(impact.gap_impact(None))


def _pillar(key, probe):
    return _assemble_pillar(PILLARS_BY_KEY[key], probe)


class ScoringImpactTest(unittest.TestCase):
    def test_pillar_with_gap_ids_gets_one_statement_per_gap(self):
        probe = {"available": True, "score": 40.0,
                 "gaps": ["g1", "g2"], "gap_ids": ["table_comments_low", "no_tagged_tables"]}
        p = _pillar("metadata", probe)
        self.assertEqual(p["impact"], impact.PILLAR_IMPACT["metadata"])
        self.assertEqual(p["gap_impacts"], [impact.GAP_IMPACT["table_comments_low"],
                                            impact.GAP_IMPACT["no_tagged_tables"]])

        out = _finalize([p])
        self.assertEqual(out["top_gaps"][0]["impact"], impact.GAP_IMPACT["table_comments_low"])
        self.assertEqual(out["top_gaps"][1]["impact"], impact.GAP_IMPACT["no_tagged_tables"])

    def test_pillar_without_gap_ids_shows_only_the_pillar_statement(self):
        probe = {"available": True, "score": 10.0, "gaps": ["No user catalogs found."]}
        p = _pillar("uc_foundation", probe)
        self.assertEqual(p["gap_impacts"], [None])
        self.assertEqual(p["impact"], impact.PILLAR_IMPACT["uc_foundation"])

        out = _finalize([p])
        self.assertEqual(out["top_gaps"], [{"pillar": PILLARS_BY_KEY["uc_foundation"]["name"],
                                            "gap": "No user catalogs found.",
                                            "impact": impact.PILLAR_IMPACT["uc_foundation"]}])

    def test_mismatched_gap_ids_fall_back_to_the_pillar_statement(self):
        probe = {"available": True, "score": 10.0, "gaps": ["a", "b"], "gap_ids": ["no_gold_layer"]}
        p = _pillar("relationships", probe)
        self.assertEqual(p["gap_impacts"], [None, None])

    def test_unavailable_pillar_has_no_statement(self):
        # An unmeasured pillar must not claim a gap it never measured.
        p = _pillar("domains", {"available": False})
        self.assertIsNone(p["impact"])
        self.assertEqual(p["gap_impacts"], [])

    def test_pillar_with_no_gaps_has_no_statement(self):
        # A well-scoring pillar with no gaps must not claim a gap it does not have.
        p = _pillar("metadata", {"available": True, "score": 90.0, "gaps": []})
        self.assertIsNone(p["impact"])
        self.assertEqual(_finalize([p])["top_gaps"], [])

    def test_pillar_with_a_gap_has_its_statement(self):
        p = _pillar("metadata", {"available": True, "score": 70.0, "gaps": ["Sparse."]})
        self.assertEqual(p["impact"], impact.PILLAR_IMPACT["metadata"])

    def test_adoption_without_gap_probe_shows_statement_only_below_threshold(self):
        # Adoption has no gap probe, so its gap list is always empty.
        self.assertEqual(impact.NO_GAP_IMPACT_MAX_SCORE, 40)
        low = _pillar("adoption", {"available": True, "score": 20.0, "gaps": []})
        self.assertEqual(low["impact"], impact.PILLAR_IMPACT["adoption"])
        edge_low = _pillar("adoption", {"available": True, "score": 39.9, "gaps": []})
        self.assertEqual(edge_low["impact"], impact.PILLAR_IMPACT["adoption"])
        at = _pillar("adoption", {"available": True, "score": 40.0, "gaps": []})
        self.assertIsNone(at["impact"])
        high = _pillar("adoption", {"available": True, "score": 90.0, "gaps": []})
        self.assertIsNone(high["impact"])


class MetadataProbeGapIdsTest(unittest.TestCase):
    def _run(self, t_com, c_com, tagged):
        sources = {"system_ok": True, "catalogs": ["main"], "schemas": None}
        trows = [{"cat": "main", "sch": "sales", "total": "100", "commented": str(t_com)}]
        with patch.object(probes, "_resolve_sources", AsyncMock(return_value=sources)), \
             patch.object(probes, "execute_sql", AsyncMock(return_value=trows)), \
             patch.object(probes, "_column_coverage_chunked", AsyncMock(return_value=(100, c_com, {}, 0))), \
             patch.object(probes, "_scalar", AsyncMock(return_value=tagged)):
            return asyncio.run(probes.probe_metadata())

    def test_gap_ids_line_up_with_gaps(self):
        out = self._run(t_com=10, c_com=10, tagged=0)
        self.assertEqual(out["gap_ids"], ["table_comments_low", "column_comments_low", "no_tagged_tables"])
        self.assertEqual(len(out["gap_ids"]), len(out["gaps"]))

    def test_no_gaps_means_no_gap_ids(self):
        out = self._run(t_com=90, c_com=90, tagged=5)
        self.assertEqual(out["gaps"], [])
        self.assertEqual(out["gap_ids"], [])


class PdfReadoutImpactTest(unittest.TestCase):
    def _scorecard(self, with_impact=True):
        meta = _pillar("metadata", {"available": True, "score": 40.0, "gaps": ["Sparse."],
                                    "gap_ids": ["table_comments_low"]})
        uc = _pillar("uc_foundation", {"available": True, "score": 10.0, "gaps": ["No catalogs."]})
        # A well-scoring pillar with no gaps: its readout must not carry a statement.
        adoption = _pillar("adoption", {"available": True, "score": 90.0, "gaps": []})
        pillars = [meta, uc, adoption]
        out = _finalize(pillars)
        if not with_impact:  # a snapshot saved before impact statements existed
            for p in pillars:
                p.pop("impact", None)
                p.pop("gap_impacts", None)
            for g in out["top_gaps"]:
                g.pop("impact", None)
            out["overall"].pop("readiness_framing", None)
        return {"overall": out["overall"], "pillars": pillars, "top_gaps": out["top_gaps"]}

    def test_readout_carries_framing_pillar_and_gap_statements(self):
        md = _assessment_markdown(self._scorecard())
        self.assertIn(impact.OVERALL_FRAMING, md)
        self.assertIn(impact.GAP_IMPACT["table_comments_low"], md)
        self.assertIn(impact.PILLAR_IMPACT["uc_foundation"], md)
        self.assertIn("Impact for Genie Ontology", md)
        self.assertNotIn(impact.PILLAR_IMPACT["adoption"], md)

    def test_legacy_snapshot_falls_back_to_pillar_statements(self):
        md = _assessment_markdown(self._scorecard(with_impact=False))
        self.assertIn(impact.PILLAR_IMPACT["metadata"], md)
        self.assertIn(impact.OVERALL_FRAMING, md)
        self.assertNotIn(impact.GAP_IMPACT["table_comments_low"], md)


if __name__ == "__main__":
    unittest.main()
