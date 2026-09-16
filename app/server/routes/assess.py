"""Assessment endpoints — run the readiness scorecard, manage per-user history,
and export a scorecard as a branded PDF."""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Body, Depends, Header
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from server.assessment.compare import compare_snapshots
from server.assessment.scoring import run_assessment, run_assessment_stream
from server.routes._shared import _cache_get, _cache_set, current_principal
from server.config import set_user_token
from server.pdf import (
    build_pdf_document,
    markdown_to_safe_html,
    pdf_export_unavailable_response,
    render_pdf_response,
)
from server.workspace_filter import set_workspace_filter, set_catalog_scope
from server.security import safe_error
from server import snapshots

logger = logging.getLogger(__name__)
router = APIRouter()

# Header name Databricks Apps uses to forward the end-user's token (on-behalf-of-
# user authorization). When present, assessment metadata reads run as the viewer.
_OBO_HEADER = "x-forwarded-access-token"

# The exported title is length-bounded here; the inline scorecard body is bounded by
# the app-wide 2 MiB BodySizeLimitMiddleware. A scorecard is small structured data, so
# it needs no tighter per-field cap (unlike the plan's free-text Markdown, which adds
# its own _MAX_MARKDOWN on top of the global limit).
_MAX_ASSESS_TITLE = 200


class WorkspaceFilterModel(BaseModel):
    mode: str = "include"  # "include" | "exclude"
    workspace_ids: list[str] = []


class AssessRequest(BaseModel):
    # Which workspaces the activity-based signals (Genie/Adoption/lineage) should
    # scope to. None / empty ids → all workspaces. Metastore-scoped pillars ignore it.
    workspace_filter: Optional[WorkspaceFilterModel] = None
    # Which catalogs the metadata pillars should assess. Empty → derive from the
    # selected workspaces' bindings (or enumerate all visible catalogs).
    catalogs: list[str] = []


@router.get("/assess")
async def assess_get(
    x_forwarded_access_token: Optional[str] = Header(default=None),
    workspace_ids: Optional[str] = None,
    workspace_mode: str = "include",
    catalogs: Optional[str] = None,
):
    """Quick technical-only assessment. Cached briefly. Not persisted.

    Optional ``?workspace_ids=<comma,sep>&workspace_mode=include|exclude`` scopes the
    activity signals and ``?catalogs=<comma,sep>`` scopes the metadata pillars (mainly
    for headless/testing; the UI uses the stream body)."""
    set_user_token(x_forwarded_access_token)
    ids = [w.strip() for w in (workspace_ids or "").split(",") if w.strip()]
    cat_scope = [c.strip() for c in (catalogs or "").split(",") if c.strip()]
    set_workspace_filter({"mode": workspace_mode, "workspace_ids": ids} if ids else None)
    set_catalog_scope(cat_scope or None)
    # Only cache the SP-run result with no filter; a per-user (OBO) or filtered/scoped
    # run is scoped and must not be shared from the cache.
    cacheable = not x_forwarded_access_token and not ids and not cat_scope
    if cacheable:
        cached = _cache_get("assess:technical")
        if cached is not None:
            return cached
    result = await run_assessment()
    if cacheable:
        _cache_set("assess:technical", result)
    return result


@router.post("/assess/stream")
async def assess_stream(
    req: AssessRequest = Body(default=AssessRequest()),
    principal: str = Depends(current_principal),
    x_forwarded_access_token: Optional[str] = Header(default=None),
):
    """Stream the assessment: one SSE event per pillar as it completes, then a
    final 'complete' event with the overall score + top gaps. Every completed run
    is auto-saved to the user's history (when Lakebase is enabled).

    Body: {"workspace_filter": {"mode": "include"|"exclude", "workspace_ids": [...]}}
    scopes the activity-based signals; omit / empty for all workspaces."""

    wsf = req.workspace_filter.model_dump() if req and req.workspace_filter else None
    cat_scope = list(req.catalogs) if req and req.catalogs else None

    async def gen():
        # Set inside the generator too: the streaming body may run in a fresh
        # context, so re-establish the OBO token + workspace/catalog scope here
        # (contextvars set here propagate to the probe tasks created downstream).
        set_user_token(x_forwarded_access_token)
        set_workspace_filter(wsf)
        set_catalog_scope(cat_scope)
        try:
            async for event in run_assessment_stream():
                if event.get("type") == "complete":
                    scorecard = {
                        "overall": event["overall"],
                        "pillars": event["pillars"],
                        "top_gaps": event["top_gaps"],
                    }
                    try:
                        sid = await snapshots.save_snapshot(scorecard, created_by=principal)
                        event["snapshot_id"] = sid
                        event["snapshot_saved"] = sid is not None
                    except Exception as e:
                        logger.warning(f"snapshot save failed: {e}")
                        event["snapshot_saved"] = False
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            # Assessment failures wrap SQL Warehouse errors, which quote the failing
            # statement and the object names involved — never return that verbatim.
            reference, message = safe_error(e, "assessment stream", logger)
            yield f"data: {json.dumps({'type': 'error', 'error': message, 'reference': reference})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/assess/history")
async def assess_history(principal: str = Depends(current_principal)):
    """The current user's past assessment runs (requires Lakebase; empty otherwise)."""
    return {"snapshots": await snapshots.list_snapshots(created_by=principal)}


@router.get("/assess/snapshot/{snapshot_id}")
async def assess_snapshot(snapshot_id: int, principal: str = Depends(current_principal)):
    """Load one past assessment's full scorecard (scoped to the current user)."""
    snap = await snapshots.get_snapshot(snapshot_id, created_by=principal)
    if snap is None:
        return JSONResponse(status_code=404, content={"error": "Assessment not found."})
    return snap


# --- PDF export (issue #15) -------------------------------------------------
# Mirror the plan export: build a branded document from a scorecard sourced from
# EITHER a saved snapshot (snapshot_id, scoped to the requester) or an inline
# scorecard (in-session, when Lakebase history isn't attached). The document is
# a deterministic, executive-ready readout — no LLM, no live re-probing.

_ASSESS_TITLE = "Genie Ontology Readiness — Assessment"


class AssessPdfRequest(BaseModel):
    title: str = Field(default=_ASSESS_TITLE, max_length=_MAX_ASSESS_TITLE)
    snapshot_id: Optional[int] = None
    scorecard: Optional[dict] = None


def _cell(text) -> str:
    """Escape a value for a Markdown table cell (a literal ``|`` would split it)."""
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ")


def _fmt_signal(sig: dict) -> str:
    """One signal as ``**Label:** value unit — detail`` (unit/detail optional)."""
    label = sig.get("label") or ""
    value = sig.get("value")
    unit = sig.get("unit") or ""
    detail = sig.get("detail") or ""
    val = "" if value is None else f"{value}{unit}"
    head = f"**{label}:** {val}".rstrip()
    return f"{head} — {detail}" if detail else head


def _assessment_markdown(sc: dict) -> str:
    """A deterministic Markdown readout of the scorecard: overall + stage, the
    per-pillar table (score / level / weight), top gaps, then per-pillar detail
    (summary, signals, gaps, recommended practices, and identity attribution).

    Unavailable pillars are labeled rather than shown as a blank/0 (issue #15 AC)."""
    overall = sc.get("overall", {}) or {}
    pillars = sc.get("pillars", []) or []
    top_gaps = sc.get("top_gaps", []) or []

    lines: list[str] = []

    # Overall readiness. Rendered defensively: a legacy/degraded snapshot may carry
    # pillars but a missing or partial `overall`, and the readout must never print a
    # literal "None/100 — LNone" header (issue #15: degrade cleanly).
    score = overall.get("score")
    level = overall.get("level")
    level_label = overall.get("level_label") or ""
    stage = overall.get("readiness_stage") or ""
    detail = overall.get("readiness_detail") or ""
    lines.append("## Overall readiness")
    lines.append("")
    if score is not None:
        header = f"**{score}/100"
        if level is not None:
            header += f" — L{level} {level_label}".rstrip()
        header += "**"
        if stage:
            header += f" · {stage}"
        lines.append(header)
        lines.append("")
    elif stage:
        lines.append(f"**{stage}**")
        lines.append("")
    if detail:
        lines.append(detail)
        lines.append("")

    # Per-pillar scorecard table.
    if pillars:
        lines.append("## Pillar scores")
        lines.append("")
        lines.append("| Pillar | Score | Maturity | Weight |")
        lines.append("| --- | --- | --- | --- |")
        for p in pillars:
            score_cell = str(p.get("score")) if p.get("available", True) else "n/a"
            lines.append(
                f"| {_cell(p.get('name'))} | {_cell(score_cell)} "
                f"| {_cell('L' + str(p.get('level')) + ' ' + (p.get('level_label') or ''))} "
                f"| {_cell(p.get('weight'))} |"
            )
        lines.append("")

    # Top gaps.
    if top_gaps:
        lines.append("## Top gaps to close")
        lines.append("")
        for g in top_gaps:
            lines.append(f"- **{g.get('pillar')}** — {g.get('gap')}")
        lines.append("")

    # Per-pillar detail.
    if pillars:
        lines.append("## Pillar detail")
        lines.append("")
        for p in pillars:
            name = p.get("name") or ""
            available = p.get("available", True)
            if available:
                lines.append(f"### {name} — {p.get('score')}/100 (L{p.get('level')} {p.get('level_label') or ''})")
            else:
                lines.append(f"### {name} — not available")
            lines.append("")

            if not available:
                reason = (p.get("note") or "").strip() or "This pillar could not be assessed for this run."
                lines.append(f"*{reason}*")
                lines.append("")
                continue

            summary = (p.get("summary") or p.get("short") or "").strip()
            if summary:
                lines.append(summary)
                lines.append("")

            identity = p.get("identity") or {}
            if identity.get("label"):
                lines.append(f"*Assessed as: {identity.get('label')}.*")
                lines.append("")

            signals = p.get("signals") or []
            if signals:
                lines.append("**Signals**")
                lines.append("")
                for sig in signals:
                    lines.append(f"- {_fmt_signal(sig)}")
                lines.append("")

            gaps = p.get("gaps") or []
            if gaps:
                lines.append("**Gaps**")
                lines.append("")
                for g in gaps:
                    lines.append(f"- {g}")
                lines.append("")

            practices = p.get("best_practices") or []
            if practices:
                lines.append("**Recommended practices**")
                lines.append("")
                for bp in practices:
                    lines.append(f"- {bp}")
                lines.append("")

    return "\n".join(lines).strip() + "\n"


def _build_assessment_pdf_html(sc: dict, title: str) -> str:
    """Assemble the branded assessment PDF HTML from a scorecard. Deterministic —
    the Markdown carries no H1, so the shared builder's title renders exactly once."""
    subtitle = "Genie Ontology Readiness assessment · Databricks"
    body_html = markdown_to_safe_html(_assessment_markdown(sc or {}))
    return build_pdf_document(title or _ASSESS_TITLE, body_html, subtitle=subtitle)


def _assessment_filename(title: str) -> str:
    """A dated, reasonable filename base, e.g. ``…-assessment-2026-09-15`` (issue #15)."""
    day = f"{datetime.now(timezone.utc):%Y-%m-%d}"
    base = (title or _ASSESS_TITLE).strip() or _ASSESS_TITLE
    return f"{base} {day}"


@router.post("/assess/pdf")
async def assess_pdf(req: AssessPdfRequest, principal: str = Depends(current_principal)):
    """Export a readiness assessment as a branded PDF.

    The scorecard comes from EITHER a saved snapshot (``snapshot_id``, loaded
    server-side and scoped to the requester) or an inline ``scorecard`` (the
    in-session assessment, when history isn't persisted) — mirroring how plan
    generation handles both paths. The PDF reflects the stored/rendered scores;
    it never re-probes the live workspace.
    """
    unavailable = pdf_export_unavailable_response()
    if unavailable is not None:
        return unavailable
    if req.snapshot_id is not None:
        snap = await snapshots.get_snapshot(req.snapshot_id, created_by=principal)
        if snap is None:
            return JSONResponse(status_code=404, content={"error": "Assessment not found."})
        scorecard = snap.get("scorecard") or {}
    elif req.scorecard is not None:
        scorecard = req.scorecard
    else:
        return JSONResponse(
            status_code=400,
            content={"error": "Provide a snapshot_id or an assessment scorecard."},
        )
    if not isinstance(scorecard, dict) or not scorecard.get("pillars"):
        return JSONResponse(
            status_code=400,
            content={"error": "The assessment is empty — run an assessment first."},
        )

    html_doc = _build_assessment_pdf_html(scorecard, req.title)
    return render_pdf_response(html_doc, _assessment_filename(req.title))
@router.get("/assess/compare")
async def assess_compare(
    baseline: int,
    current: int,
    principal: str = Depends(current_principal),
):
    """Compare two of the user's saved assessments → per-pillar + overall deltas (#12).

    Both snapshots are loaded server-side scoped to the requesting identity, so a
    user can only diff their own history. The result is computed from the stored
    pillar scores — the live workspace is never re-probed. ``baseline`` is the
    earlier/reference run and ``current`` the one being measured against it, so a
    positive delta reads as a gain.
    """
    if baseline == current:
        return JSONResponse(
            status_code=400,
            content={"error": "Pick two different assessments to compare."},
        )
    base = await snapshots.get_snapshot(baseline, created_by=principal)
    cur = await snapshots.get_snapshot(current, created_by=principal)
    missing = [sid for sid, snap in ((baseline, base), (current, cur)) if snap is None]
    if missing:
        return JSONResponse(
            status_code=404,
            content={"error": "Assessment not found.", "missing": missing},
        )
    return compare_snapshots(base, cur)
