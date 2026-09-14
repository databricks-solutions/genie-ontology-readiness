"""Unit tests for the deterministic scoring primitives in server.pillars.

These are pure, dependency-free functions (no Databricks/network imports), so
they run in CI without installing the app's runtime requirements. They lock in
the maturity-level bands, the readiness-stage mapping, and the pillar-weight
invariant that the whole scorecard depends on.
"""

from server.pillars import (
    PILLARS,
    PILLARS_BY_KEY,
    READINESS_STAGES,
    level_from_score,
    readiness_stage,
)


def test_pillar_weights_sum_to_100():
    # The overall score is a weighted average over these weights; if they don't
    # sum to 100 the reported score is silently mis-scaled.
    assert sum(p["weight"] for p in PILLARS) == 100


def test_pillar_keys_unique_and_indexed():
    keys = [p["key"] for p in PILLARS]
    assert len(keys) == len(set(keys)), "duplicate pillar keys"
    assert set(PILLARS_BY_KEY) == set(keys)
    assert len(PILLARS) == 7


def test_every_pillar_has_required_fields():
    for p in PILLARS:
        for field in ("key", "name", "weight"):
            assert field in p, f"pillar {p.get('key')!r} missing {field}"
        assert isinstance(p["weight"], int) and p["weight"] > 0


def test_level_from_score_bands():
    # Bands: >=85 -> 4, >=65 -> 3, >=40 -> 2, >0 -> 1, else 0.
    assert level_from_score(0) == 0
    assert level_from_score(0.1) == 1
    assert level_from_score(39.9) == 1
    assert level_from_score(40) == 2
    assert level_from_score(64.9) == 2
    assert level_from_score(65) == 3
    assert level_from_score(84.9) == 3
    assert level_from_score(85) == 4
    assert level_from_score(100) == 4


def test_level_from_score_is_monotonic():
    prev = -1
    for s in range(0, 101):
        lvl = level_from_score(s)
        assert lvl >= prev, f"level decreased at score {s}"
        prev = lvl


def test_readiness_stage_returns_highest_reached_stage():
    # Picks the highest stage whose min_score the overall score meets.
    assert readiness_stage(0)["min_score"] == 0
    assert readiness_stage(100)["min_score"] == max(s["min_score"] for s in READINESS_STAGES)
    for s in READINESS_STAGES:
        got = readiness_stage(s["min_score"])
        assert got["min_score"] <= s["min_score"]
        assert "label" in got


def test_readiness_stage_is_monotonic_by_threshold():
    prev = -1
    for score in range(0, 101):
        stage_min = readiness_stage(score)["min_score"]
        assert stage_min >= prev, f"readiness stage regressed at score {score}"
        prev = stage_min
