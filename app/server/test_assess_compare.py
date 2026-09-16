"""Tests for the assessment comparison logic (issue #12).

`compare_snapshots` diffs two stored scorecards into per-pillar and overall deltas.
These lock in the two behaviours that matter most: the signed delta / status is
correct for real score changes, and a pillar that is missing or unavailable on one
side is labeled (new / removed / unavailable) rather than shown as a spurious
full-swing ±score.
"""

import unittest

from server.assessment.compare import compare_snapshots


def _pillar(key, name, score, level, level_label, available=True, signals=None, gaps=None):
    return {
        "key": key,
        "name": name,
        "score": score,
        "level": level,
        "level_label": level_label,
        "available": available,
        "signals": signals or [],
        "gaps": gaps or [],
    }


def _snap(sid, created_at, overall, pillars):
    return {
        "id": sid,
        "created_at": created_at,
        "scorecard": {"overall": overall, "pillars": pillars, "top_gaps": []},
    }


def _baseline():
    return _snap(
        1, "2026-09-01T00:00:00+00:00",
        {"score": 60.0, "level": 2, "level_label": "Developing", "readiness_stage": "Ready for Session 2"},
        [
            _pillar("uc_foundation", "Unity Catalog Foundation", 80, 3, "Established"),
            _pillar("metadata", "Metadata Richness", 40, 2, "Developing"),
            _pillar("metrics", "Metrics", 55, 2, "Developing"),
            _pillar("domains", "Domains & Stewardship", 0, 0, "Absent", available=False),
        ],
    )


def _current():
    return _snap(
        2, "2026-09-15T00:00:00+00:00",
        {"score": 74.0, "level": 3, "level_label": "Established", "readiness_stage": "Ready for Session 3"},
        [
            _pillar("uc_foundation", "Unity Catalog Foundation", 88, 4, "Optimized"),  # +8, level up
            _pillar("metadata", "Metadata Richness", 40, 2, "Developing"),             # unchanged
            _pillar("metrics", "Metrics", 30, 1, "Initial"),                            # -25, regressed
            _pillar("domains", "Domains & Stewardship", 70, 3, "Established"),           # now available
        ],
    )


class CompareOverallTest(unittest.TestCase):
    def test_overall_delta_and_level_and_stage(self):
        r = compare_snapshots(_baseline(), _current())
        o = r["overall"]
        self.assertEqual(o["delta"], 14.0)
        self.assertEqual(o["level_change"]["direction"], "up")
        self.assertEqual(o["level_change"]["to_label"], "Established")
        self.assertEqual(o["stage_change"], {"from": "Ready for Session 2", "to": "Ready for Session 3"})

    def test_ids_and_summary_counts(self):
        r = compare_snapshots(_baseline(), _current())
        self.assertEqual(r["baseline"]["id"], 1)
        self.assertEqual(r["current"]["id"], 2)
        # 4 pillars: uc improved, metrics regressed, metadata unchanged, domains new.
        self.assertEqual(
            r["summary"],
            {"improved": 1, "regressed": 1, "unchanged": 1, "new": 1, "removed": 0, "unavailable": 0},
        )


class ComparePillarTest(unittest.TestCase):
    def setUp(self):
        self.by_key = {p["key"]: p for p in compare_snapshots(_baseline(), _current())["pillars"]}

    def test_gain_is_positive_and_reports_level_up(self):
        p = self.by_key["uc_foundation"]
        self.assertEqual(p["delta"], 8.0)
        self.assertEqual(p["status"], "improved")
        self.assertEqual(p["level_change"]["direction"], "up")

    def test_loss_is_negative(self):
        p = self.by_key["metrics"]
        self.assertEqual(p["delta"], -25.0)
        self.assertEqual(p["status"], "regressed")

    def test_no_change_is_neutral(self):
        p = self.by_key["metadata"]
        self.assertEqual(p["delta"], 0.0)
        self.assertEqual(p["status"], "unchanged")

    def test_unavailable_to_available_is_new_not_plus_70(self):
        # The baseline pillar was unavailable (stored 0); it must NOT show +70.
        p = self.by_key["domains"]
        self.assertIsNone(p["delta"])
        self.assertEqual(p["status"], "new")
        self.assertIsNone(p["baseline_score"])
        self.assertEqual(p["current_score"], 70.0)

    def test_canonical_pillar_order(self):
        keys = [p["key"] for p in compare_snapshots(_baseline(), _current())["pillars"]]
        self.assertEqual(keys, ["uc_foundation", "metadata", "metrics", "domains"])


class SignalAndGapDiffTest(unittest.TestCase):
    def test_signal_delta_matched_by_label(self):
        base = _snap(1, "t", {"score": 50}, [_pillar(
            "metadata", "Metadata Richness", 50, 2, "Developing",
            signals=[{"label": "Tables commented", "value": 48.4, "unit": "%"},
                     {"label": "Tagged tables", "value": 547}],
        )])
        cur = _snap(2, "t", {"score": 62}, [_pillar(
            "metadata", "Metadata Richness", 62, 2, "Developing",
            signals=[{"label": "Tables commented", "value": 61.2, "unit": "%"},
                     {"label": "Tagged tables", "value": 903}],
        )])
        sigs = {s["label"]: s for s in compare_snapshots(base, cur)["pillars"][0]["signals"]}
        self.assertEqual(sigs["Tables commented"]["delta"], 12.8)
        self.assertEqual(sigs["Tables commented"]["unit"], "%")
        self.assertEqual(sigs["Tagged tables"]["delta"], 356.0)

    def test_non_numeric_signal_has_no_delta(self):
        base = _snap(1, "t", {"score": 1}, [_pillar("x", "X", 1, 1, "Initial",
                     signals=[{"label": "Mode", "value": "off"}])])
        cur = _snap(2, "t", {"score": 1}, [_pillar("x", "X", 1, 1, "Initial",
                     signals=[{"label": "Mode", "value": "on"}])])
        s = compare_snapshots(base, cur)["pillars"][0]["signals"][0]
        self.assertIsNone(s["delta"])
        self.assertEqual(s["baseline"], "off")
        self.assertEqual(s["current"], "on")

    def test_gap_resolved_and_introduced(self):
        base = _snap(1, "t", {"score": 1}, [_pillar("x", "X", 1, 1, "Initial",
                     gaps=["No primary keys declared.", "Only 2 domains defined."])])
        cur = _snap(2, "t", {"score": 1}, [_pillar("x", "X", 1, 1, "Initial",
                     gaps=["Only 5 domains defined.", "No stewards assigned."])])
        g = compare_snapshots(base, cur)["pillars"][0]["gaps"]
        # PK gap disappeared -> resolved; stewards gap appeared -> introduced.
        self.assertIn("No primary keys declared.", g["resolved"])
        self.assertIn("No stewards assigned.", g["introduced"])
        # The domains gap only changed its number -> neither resolved nor new.
        self.assertNotIn("Only 2 domains defined.", g["resolved"])
        self.assertFalse(any("domains defined" in x for x in g["introduced"]))


class CompareShapeMismatchTest(unittest.TestCase):
    def test_pillar_only_in_baseline_is_removed(self):
        base = _snap(1, "t", {"score": 50}, [_pillar("metrics", "Metrics", 50, 2, "Developing")])
        cur = _snap(2, "t", {"score": 50}, [])
        p = compare_snapshots(base, cur)["pillars"][0]
        self.assertEqual(p["status"], "removed")
        self.assertIsNone(p["delta"])
        self.assertEqual(p["baseline_score"], 50.0)
        self.assertIsNone(p["current_score"])

    def test_both_unavailable_is_unavailable(self):
        base = _snap(1, "t", {"score": 0}, [_pillar("domains", "Domains & Stewardship", 0, 0, "Absent", available=False)])
        cur = _snap(2, "t", {"score": 0}, [_pillar("domains", "Domains & Stewardship", 0, 0, "Absent", available=False)])
        p = compare_snapshots(base, cur)["pillars"][0]
        self.assertEqual(p["status"], "unavailable")
        self.assertIsNone(p["delta"])

    def test_missing_overall_does_not_raise(self):
        base = _snap(1, "t", {}, [])
        cur = _snap(2, "t", {}, [])
        r = compare_snapshots(base, cur)
        self.assertIsNone(r["overall"]["delta"])
        self.assertIsNone(r["overall"]["stage_change"])


if __name__ == "__main__":
    unittest.main()
