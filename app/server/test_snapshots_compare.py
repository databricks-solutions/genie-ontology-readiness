"""Compare two stored assessment snapshots (issue #12) without re-probing."""

import sys
import unittest
from unittest.mock import AsyncMock

from server.snapshot_compare import build_compare, compare_snapshots


def _pillar(key, name, score, level, available=True):
    return {
        "key": key,
        "name": name,
        "score": score,
        "level": level,
        "available": available,
    }


def _snap(sid, score, pillars, created_at="2026-09-01T00:00:00+00:00", stage=None):
    return {
        "id": sid,
        "created_at": created_at,
        "created_by": "user@example.com",
        "scorecard": {
            "overall": {
                "score": score,
                "level": 2,
                "readiness_stage": stage or "Foundation building",
            },
            "pillars": pillars,
        },
    }


UC = ("uc_foundation", "Unity Catalog Foundation")
META = ("metadata", "Metadata Richness")
REL = ("relationships", "Relationships & Modeling")
METRICS = ("metrics", "Metrics")


def _assert_scoring_not_imported():
    assert "server.assessment.scoring" not in sys.modules


class BuildCompareTest(unittest.TestCase):
    def test_improved_declined_unchanged(self):
        baseline = _snap(
            1,
            40,
            [
                _pillar(*UC, 40, 2),
                _pillar(*META, 50, 2),
                _pillar(*REL, 30, 1),
            ],
        )
        current = _snap(
            2,
            46,
            [
                _pillar(*UC, 55, 3),
                _pillar(*META, 50, 2),
                _pillar(*REL, 20, 1),
            ],
        )
        _assert_scoring_not_imported()
        result = build_compare(baseline, current)
        _assert_scoring_not_imported()
        by_key = {p["key"]: p for p in result["pillars"]}
        self.assertEqual(result["overall_delta"], 6)
        self.assertEqual(by_key["uc_foundation"]["status"], "improved")
        self.assertEqual(by_key["uc_foundation"]["delta"], 15)
        self.assertEqual(by_key["metadata"]["status"], "unchanged")
        self.assertEqual(by_key["metadata"]["delta"], 0)
        self.assertEqual(by_key["relationships"]["status"], "declined")
        self.assertEqual(by_key["relationships"]["delta"], -10)
        self.assertEqual([p["key"] for p in result["pillars"]], ["uc_foundation", "metadata", "relationships"])

    def test_pillar_only_on_baseline_is_removed(self):
        baseline = _snap(1, 40, [_pillar(*UC, 40, 2), _pillar(*METRICS, 12, 1)])
        current = _snap(2, 40, [_pillar(*UC, 40, 2)])
        row = {p["key"]: p for p in build_compare(baseline, current)["pillars"]}["metrics"]
        self.assertEqual(row["status"], "removed")
        self.assertIsNone(row["delta"])
        self.assertIsNone(row["current_score"])
        self.assertEqual(row["baseline_score"], 12)

    def test_pillar_only_on_current_is_new(self):
        baseline = _snap(1, 40, [_pillar(*UC, 40, 2)])
        current = _snap(2, 40, [_pillar(*UC, 40, 2), _pillar(*METRICS, 12, 1)])
        row = {p["key"]: p for p in build_compare(baseline, current)["pillars"]}["metrics"]
        self.assertEqual(row["status"], "new")
        self.assertIsNone(row["delta"])
        self.assertIsNone(row["baseline_score"])
        self.assertEqual(row["current_score"], 12)

    def test_unavailable_is_not_a_numeric_loss(self):
        baseline = _snap(1, 40, [_pillar(*UC, 40, 2), _pillar(*META, 80, 3, available=True)])
        current = _snap(2, 40, [_pillar(*UC, 40, 2), _pillar(*META, 0, 0, available=False)])
        row = {p["key"]: p for p in build_compare(baseline, current)["pillars"]}["metadata"]
        self.assertEqual(row["status"], "unavailable")
        self.assertIsNone(row["delta"])
        self.assertFalse(row["current_available"])

    def test_readiness_stage_falls_back_from_score(self):
        baseline = _snap(1, 40, [_pillar(*UC, 40, 2)])
        baseline["scorecard"]["overall"].pop("readiness_stage")
        current = _snap(2, 60, [_pillar(*UC, 60, 3)])
        current["scorecard"]["overall"].pop("readiness_stage")
        result = build_compare(baseline, current)
        self.assertIn("Session", result["current"]["readiness_stage"])


class CompareSnapshotsOwnershipTest(unittest.IsolatedAsyncioTestCase):
    async def test_wrong_owner_returns_none(self):
        owned = _snap(2, 50, [_pillar(*UC, 50, 2)])
        getter = AsyncMock(side_effect=[None, owned])
        result = await compare_snapshots(1, 2, created_by="other@example.com", loader=getter)
        self.assertIsNone(result)

    async def test_loads_both_then_diffs(self):
        baseline = _snap(1, 40, [_pillar(*UC, 40, 2)])
        current = _snap(2, 50, [_pillar(*UC, 55, 3)])
        getter = AsyncMock(side_effect=[baseline, current])
        _assert_scoring_not_imported()
        result = await compare_snapshots(1, 2, created_by="user@example.com", loader=getter)
        _assert_scoring_not_imported()
        self.assertEqual(result["overall_delta"], 10)
        getter.assert_any_await(1, created_by="user@example.com")
        getter.assert_any_await(2, created_by="user@example.com")


if __name__ == "__main__":
    unittest.main()
