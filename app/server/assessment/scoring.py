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
from server.assessment.probes import PROBES, prime_request_sources, _progress_sink
from server.sql_client import start_identity_capture, resolved_identity
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
        # Which identity actually served this signal's reads (OBO viewer / SP
        # fallback / SP-forced), so the UI can show whether it reflects the
        # viewer's grants or the app SP's. None when the probe did no instrumented read.
        "identity": probe.get("identity"),
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
    """Run a single probe, converting any exception into an unavailable result.

    Each probe runs in its own task (gather/ensure_future copy the context), so
    starting identity capture here scopes the recording to this probe's reads; we
    then attach the resolved identity to the probe result.
    """
    start_identity_capture()
    try:
        probe = await PROBES[key]()
    except Exception as e:
        logger.warning(f"probe {key} raised: {e}")
        probe = _error_probe(f"Probe error: {str(e)[:120]}")
    ident = resolved_identity()
    if ident is not None:
        probe = {**probe, "identity": ident}
    return key, probe


async def run_assessment() -> dict:
    """Run every probe concurrently and build the full scorecard."""
    # Resolve data sources once for this assessment; primes a request-scoped cache
    # the probes reuse, so we don't re-resolve per probe (a fan-out under OBO).
    await prime_request_sources()
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
    # Prime the shared per-request source resolution before dispatching probes.
    await prime_request_sources()

    # A single queue carries both intra-probe progress (pillar_progress) and probe
    # completions. Priming _progress_sink BEFORE creating the probe tasks means each
    # task inherits it (contextvars are copied at task creation), so a probe can emit
    # progress without a changed signature. We drain the queue until every probe has
    # reported completion — interleaving progress events as they arrive.
    q: asyncio.Queue = asyncio.Queue()
    _progress_sink.set(q)

    async def _runner(k: str) -> None:
        key, probe = await _run_probe(k)
        await q.put({"kind": "done", "key": key, "probe": probe})

    tasks = [asyncio.ensure_future(_runner(k)) for k in PROBES]
    remaining = len(tasks)
    try:
        while remaining > 0:
            item = await q.get()
            if item.get("kind") == "done":
                key = item["key"]
                pillar = _assemble_pillar(PILLARS_BY_KEY[key], item["probe"])
                by_key[key] = pillar
                remaining -= 1
                yield {"type": "pillar", "pillar": pillar}
            else:
                # Already SSE-shaped progress event ({"type":"pillar_progress",...}).
                yield item
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()

    ordered = [by_key[p["key"]] for p in PILLARS if p["key"] in by_key]
    yield {"type": "complete", "pillars": ordered, **_finalize(ordered)}
