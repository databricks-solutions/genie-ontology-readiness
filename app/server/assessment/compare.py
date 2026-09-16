"""Compare two assessment snapshots — a deterministic, per-pillar diff (issue #12).

Scores are a pure function of the findings, so two stored snapshots are directly
comparable with no re-probing of the live workspace. This module turns two stored
scorecards into a per-pillar delta (gain / loss), the overall-score delta, and any
maturity-level or readiness-stage crossing.

Shape mismatches are handled explicitly so a pillar that is present on only one
side — added since the baseline, or unavailable/degraded on one run — renders as
"new" / "no longer assessed" rather than a misleading full-swing ±score. An
*unavailable* pillar (stored score 0 because the SP couldn't read it) is treated
as "no comparable score", not as a real 0, for the same reason.

Pure and free of I/O so it unit-tests without Lakebase.
"""

import re
from typing import Optional

from server.pillars import PILLAR_KEYS


def _numeric(value) -> Optional[float]:
    """A signal value as a float, or ``None`` if it isn't a plain number.

    Signal values are usually numeric (counts, percentages) but the type allows
    strings; only a real number gets a computed delta, so a non-numeric signal
    still shows baseline → current without fabricating an arithmetic difference.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _signal_diffs(base_p: Optional[dict], cur_p: Optional[dict]) -> list[dict]:
    """Per-signal baseline → current diff for a pillar, matched by signal label.

    Current-run order first, then any baseline-only labels (a signal dropped since
    the baseline). A label present on only one side keeps that side's value and a
    ``None`` on the other, with no delta — never a fabricated full-swing.
    """
    base = {s.get("label"): s for s in ((base_p or {}).get("signals") or []) if s.get("label")}
    cur = {s.get("label"): s for s in ((cur_p or {}).get("signals") or []) if s.get("label")}
    order = list(cur.keys()) + [lbl for lbl in base if lbl not in cur]
    out: list[dict] = []
    for label in order:
        b, c = base.get(label), cur.get(label)
        b_val = b.get("value") if b else None
        c_val = c.get("value") if c else None
        b_num, c_num = _numeric(b_val), _numeric(c_val)
        delta = round(c_num - b_num, 1) if (b_num is not None and c_num is not None) else None
        out.append({
            "label": label,
            "unit": (c or b or {}).get("unit") or "",
            "baseline": b_val,
            "current": c_val,
            "delta": delta,
        })
    return out


def _normalize_gap(gap: str) -> str:
    """Collapse embedded numbers so a gap that merely *improved* isn't counted as
    both resolved and new. Gap strings template a live metric (e.g. "Only 48.4% of
    tables have descriptions"); replacing the number yields a stable identity, so
    the same gap across two runs matches even when the percentage changed."""
    return re.sub(r"\d[\d,.]*%?", "#", gap or "").strip()


def _gap_diff(base_p: Optional[dict], cur_p: Optional[dict]) -> dict:
    """Which gaps were resolved (present at baseline, gone now) or newly introduced.

    Matched on the number-normalized gap so an improved-but-still-open gap is
    neither resolved nor new (its movement shows in the signal diff instead).
    Membership is tested against a set of normalized forms while the ORIGINAL gap
    strings are preserved and returned — so two distinct gaps are never collapsed
    the way a normalized-key dict would."""
    base_gaps = (base_p or {}).get("gaps") or []
    cur_gaps = (cur_p or {}).get("gaps") or []
    base_norms = {_normalize_gap(g) for g in base_gaps}
    cur_norms = {_normalize_gap(g) for g in cur_gaps}
    return {
        "resolved": [g for g in base_gaps if _normalize_gap(g) not in cur_norms],
        "introduced": [g for g in cur_gaps if _normalize_gap(g) not in base_norms],
    }


def _effective_score(pillar: Optional[dict]) -> Optional[float]:
    """The comparable score for a pillar, or ``None`` when there's nothing to compare.

    ``None`` means the pillar is absent from the scorecard OR present-but-unavailable
    (the SP couldn't read it, so its stored 0 is not a real score). Either way it must
    not produce a delta — comparing against it would fabricate a ±score.
    """
    if not pillar or not pillar.get("available", True):
        return None
    score = pillar.get("score")
    return None if score is None else float(score)


def _level_change(base_p: dict, cur_p: dict) -> Optional[dict]:
    """A maturity-band crossing between two available pillars, or ``None``."""
    b_level, c_level = base_p.get("level"), cur_p.get("level")
    if b_level is None or c_level is None or b_level == c_level:
        return None
    return {
        "from": b_level,
        "to": c_level,
        "from_label": base_p.get("level_label") or "",
        "to_label": cur_p.get("level_label") or "",
        "direction": "up" if c_level > b_level else "down",
    }


def _pillar_diff(key: str, base_p: Optional[dict], cur_p: Optional[dict]) -> dict:
    """One pillar's baseline → current diff, with a status that never fabricates a delta."""
    name = (cur_p or base_p or {}).get("name") or key
    b_score = _effective_score(base_p)
    c_score = _effective_score(cur_p)

    diff: dict = {
        "key": key,
        "name": name,
        "baseline_score": b_score,
        "current_score": c_score,
        "delta": None,
        "status": "unavailable",
        "level_change": None,
        # Optional drill-down (issue #12): per-signal deltas + resolved/new gaps,
        # for the expandable per-pillar view. Computed from the stored detail.
        "signals": _signal_diffs(base_p, cur_p),
        "gaps": _gap_diff(base_p, cur_p),
    }

    if b_score is not None and c_score is not None:
        delta = round(c_score - b_score, 1)
        diff["delta"] = delta
        diff["status"] = "improved" if delta > 0 else "regressed" if delta < 0 else "unchanged"
        diff["level_change"] = _level_change(base_p, cur_p)  # type: ignore[arg-type]
    elif c_score is not None:  # comparable only in the current run
        diff["status"] = "new"
    elif b_score is not None:  # comparable only in the baseline run
        diff["status"] = "removed"
    # else: neither side has a comparable score → "unavailable"
    return diff


def _ordered_keys(base_pillars: dict, cur_pillars: dict) -> list[str]:
    """Canonical pillar order first, then any extra keys (future-proofing), stable."""
    seen = set()
    keys: list[str] = []
    for k in PILLAR_KEYS:
        if k in base_pillars or k in cur_pillars:
            keys.append(k)
            seen.add(k)
    for k in list(base_pillars.keys()) + list(cur_pillars.keys()):
        if k not in seen:
            keys.append(k)
            seen.add(k)
    return keys


def _overall_diff(base_over: dict, cur_over: dict) -> dict:
    """Overall-score delta plus any level/stage crossing."""
    b_score = base_over.get("score")
    c_score = cur_over.get("score")
    delta = None
    if b_score is not None and c_score is not None:
        delta = round(float(c_score) - float(b_score), 1)

    b_level, c_level = base_over.get("level"), cur_over.get("level")
    level_change = None
    if b_level is not None and c_level is not None and b_level != c_level:
        level_change = {
            "from": b_level,
            "to": c_level,
            "from_label": base_over.get("level_label") or "",
            "to_label": cur_over.get("level_label") or "",
            "direction": "up" if c_level > b_level else "down",
        }

    b_stage = base_over.get("readiness_stage") or ""
    c_stage = cur_over.get("readiness_stage") or ""
    stage_change = {"from": b_stage, "to": c_stage} if b_stage != c_stage else None

    return {
        "baseline_score": b_score,
        "current_score": c_score,
        "delta": delta,
        "baseline_level": b_level,
        "current_level": c_level,
        "baseline_level_label": base_over.get("level_label") or "",
        "current_level_label": cur_over.get("level_label") or "",
        "level_change": level_change,
        "stage_change": stage_change,
    }


def compare_snapshots(baseline: dict, current: dict) -> dict:
    """Diff two snapshot dicts (each ``{id, created_at, scorecard}``) → a compare result.

    Deterministic and side-effect free: it reads only the stored scorecards. The
    caller is responsible for loading both snapshots for the requesting identity.
    """
    base_sc = baseline.get("scorecard") or {}
    cur_sc = current.get("scorecard") or {}
    base_pillars = {p.get("key"): p for p in (base_sc.get("pillars") or []) if p.get("key")}
    cur_pillars = {p.get("key"): p for p in (cur_sc.get("pillars") or []) if p.get("key")}

    pillars = [
        _pillar_diff(k, base_pillars.get(k), cur_pillars.get(k))
        for k in _ordered_keys(base_pillars, cur_pillars)
    ]

    # Count every status so the banner's tally can add up to the rows shown — a
    # newly-available or dropped pillar is real movement, not silently omitted.
    summary = {
        status: sum(1 for p in pillars if p["status"] == status)
        for status in ("improved", "regressed", "unchanged", "new", "removed", "unavailable")
    }

    return {
        "baseline": {
            "id": baseline.get("id"),
            "created_at": baseline.get("created_at"),
            "overall": base_sc.get("overall") or {},
        },
        "current": {
            "id": current.get("id"),
            "created_at": current.get("created_at"),
            "overall": cur_sc.get("overall") or {},
        },
        "overall": _overall_diff(base_sc.get("overall") or {}, cur_sc.get("overall") or {}),
        "pillars": pillars,
        "summary": summary,
    }
