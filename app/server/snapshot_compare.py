"""Diff two stored assessment snapshots. Import-free of Lakebase / SDK."""

from server.pillars import PILLARS, PILLARS_BY_KEY, readiness_stage


def _pillar_map(scorecard: dict | None) -> dict[str, dict]:
    pillars = (scorecard or {}).get("pillars") or []
    return {p["key"]: p for p in pillars if isinstance(p, dict) and p.get("key")}


def _side_summary(snapshot: dict) -> dict:
    overall = (snapshot.get("scorecard") or {}).get("overall") or {}
    score = float(overall.get("score") or 0)
    stage = overall.get("readiness_stage") or readiness_stage(score)["label"]
    return {
        "id": snapshot["id"],
        "created_at": snapshot.get("created_at"),
        "overall_score": score,
        "overall_level": int(overall.get("level") or 0),
        "readiness_stage": stage,
    }


def _usable(pillar: dict | None) -> bool:
    return pillar is not None and pillar.get("available", True) is not False


def _pillar_compare_row(key: str, name: str, baseline: dict | None, current: dict | None) -> dict:
    b_present = baseline is not None
    c_present = current is not None
    b_ok = _usable(baseline)
    c_ok = _usable(current)

    if not b_present and not c_present:
        status, delta = "unavailable", None
    elif not b_present:
        status, delta = ("unavailable", None) if not c_ok else ("new", None)
    elif not c_present:
        status, delta = ("unavailable", None) if not b_ok else ("removed", None)
    elif not b_ok or not c_ok:
        status, delta = "unavailable", None
    else:
        b_score = float(baseline.get("score") or 0)
        c_score = float(current.get("score") or 0)
        delta = c_score - b_score
        if delta > 0:
            status = "improved"
        elif delta < 0:
            status = "declined"
        else:
            status = "unchanged"

    def fields(p: dict | None) -> tuple:
        if p is None:
            return None, None, None
        score = p.get("score")
        level = p.get("level")
        return (
            None if score is None else float(score),
            None if level is None else int(level),
            bool(p.get("available", True)),
        )

    b_score, b_level, b_avail = fields(baseline)
    c_score, c_level, c_avail = fields(current)
    return {
        "key": key,
        "name": name,
        "baseline_score": b_score,
        "baseline_level": b_level,
        "baseline_available": b_avail,
        "current_score": c_score,
        "current_level": c_level,
        "current_available": c_avail,
        "delta": delta,
        "status": status,
    }


def build_compare(baseline: dict, current: dict) -> dict:
    """Diff two already-loaded snapshots. Never re-runs the assessment."""
    b_map = _pillar_map(baseline.get("scorecard"))
    c_map = _pillar_map(current.get("scorecard"))
    present = set(b_map) | set(c_map)
    rows = []
    seen = set()
    for spec in PILLARS:
        key = spec["key"]
        if key not in present:
            continue
        seen.add(key)
        rows.append(_pillar_compare_row(key, spec["name"], b_map.get(key), c_map.get(key)))
    extras = [k for k in present if k not in seen]
    for key in extras:
        src = c_map.get(key) or b_map.get(key) or {}
        name = src.get("name") or PILLARS_BY_KEY.get(key, {}).get("name") or key
        rows.append(_pillar_compare_row(key, name, b_map.get(key), c_map.get(key)))
    b_side = _side_summary(baseline)
    c_side = _side_summary(current)
    return {
        "baseline": b_side,
        "current": c_side,
        "overall_delta": c_side["overall_score"] - b_side["overall_score"],
        "pillars": rows,
    }


async def compare_snapshots(baseline_id: int, current_id: int, created_by: str | None, loader) -> dict | None:
    """Load two snapshots via ``loader(id, created_by)`` and return the diff.

    ``loader`` is ``snapshots.get_snapshot`` in production. Returns None when
    either snapshot is missing (wrong id or not owned by ``created_by``).
    """
    baseline = await loader(baseline_id, created_by=created_by)
    current = await loader(current_id, created_by=created_by)
    if baseline is None or current is None:
        return None
    return build_compare(baseline, current)
