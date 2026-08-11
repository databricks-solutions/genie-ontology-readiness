"""Assemble the readiness scorecard from read-only probe results.

Scores are a deterministic function of the technical findings only (no
self-assessment), so repeated runs on the same workspace are directly comparable.
"""

import asyncio
import logging
from datetime import datetime, timezone

from server.pillars import (
    PILLARS,
    PILLARS_BY_KEY,
    LEVEL_LABELS,
    level_from_score,
    readiness_stage,
)
from server.assessment.probes import PROBES
from server.content.library import best_practices_for, capability_summary

logger = logging.getLogger(__name__)


def _error_probe(detail: str) -> dict:
    return {"available": False, "score": 0.0, "signals": [], "gaps": [], "note": detail, "metrics": {}}


def _assemble_pillar(pillar_def: dict, probe: dict) -> dict:
    """Turn one probe result into a pillar scorecard entry.

    The pillar score is exactly the probe's technical score when the probe ran,
    otherwise 0. This keeps scoring deterministic and findings-based.
    """
    key = pillar_def["key"]
    tech_available = bool(probe.get("available"))
    tech_score = float(probe.get("score") or 0.0)
    score = tech_score if tech_available else 0.0

    level = level_from_score(score)
    return {
        "key": key,
        "name": pillar_def["name"],
        "short": pillar_def["short"],
        "capability": pillar_def["capability"],
        "weight": pillar_def["weight"],
        "score": score,
        "technical_score": tech_score if tech_available else None,
        "level": level,
        "level_label": LEVEL_LABELS[level],
        "available": tech_available,
        "note": probe.get("note"),
        "signals": probe.get("signals", []),
        "gaps": probe.get("gaps", []),
        "best_practices": best_practices_for(key),
        "summary": capability_summary(pillar_def["capability"]),
        "metrics": probe.get("metrics", {}),
    }


def _finalize(pillars_out: list[dict]) -> dict:
    """Compute the overall score + prioritized gaps from all pillar entries."""
    weighted_sum = sum(p["score"] * p["weight"] for p in pillars_out)
    weight_total = sum(p["weight"] for p in pillars_out)
    overall_score = round(weighted_sum / weight_total, 1) if weight_total else 0.0
    overall_level = level_from_score(overall_score)
    stage = readiness_stage(overall_score)

    # Prioritized gaps: lowest-scoring pillars first, weighted by importance.
    ranked = sorted(pillars_out, key=lambda x: (x["score"], -x["weight"]))
    top_gaps = []
    for pil in ranked:
        for g in pil["gaps"]:
            top_gaps.append({"pillar": pil["name"], "gap": g})
    top_gaps = top_gaps[:6]

    return {
        "overall": {
            "score": overall_score,
            "level": overall_level,
            "level_label": LEVEL_LABELS[overall_level],
            "readiness_stage": stage["label"],
            "readiness_detail": stage["detail"],
            "assessed_at": datetime.now(timezone.utc).isoformat(),
        },
        "top_gaps": top_gaps,
    }


async def _run_probe(key: str) -> tuple[str, dict]:
    """Run a single probe, converting any exception into an unavailable result."""
    try:
        return key, await PROBES[key]()
    except Exception as e:
        logger.warning(f"probe {key} raised: {e}")
        return key, _error_probe(f"Probe error: {str(e)[:120]}")


async def run_assessment() -> dict:
    """Run every probe concurrently and build the full scorecard."""
    results = await asyncio.gather(*(_run_probe(k) for k in PROBES))
    probe_by_key = dict(results)
    pillars_out = [
        _assemble_pillar(p, probe_by_key.get(p["key"], _error_probe("No result")))
        for p in PILLARS
    ]
    return {"pillars": pillars_out, **_finalize(pillars_out)}


async def run_assessment_stream():
    """Async generator: yield each pillar as its probe completes, then a final event.

    Yields {"type":"pillar","pillar":{...}} per pillar (in completion order), then
    {"type":"complete","overall":{...},"top_gaps":[...],"pillars":[...]} with the
    pillars ordered canonically.
    """
    by_key: dict[str, dict] = {}
    tasks = [asyncio.ensure_future(_run_probe(k)) for k in PROBES]
    for fut in asyncio.as_completed(tasks):
        key, probe = await fut
        pillar = _assemble_pillar(PILLARS_BY_KEY[key], probe)
        by_key[key] = pillar
        yield {"type": "pillar", "pillar": pillar}

    ordered = [by_key[p["key"]] for p in PILLARS if p["key"] in by_key]
    yield {"type": "complete", "pillars": ordered, **_finalize(ordered)}
