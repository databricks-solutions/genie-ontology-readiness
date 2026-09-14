"""Plan generation — one AI call that turns the workspace assessment into a
concise, tactical action plan, plus PDF export of the generated plan.

The Plan tab is a single button: it grounds in the workspace scorecard (overall
readiness, per-pillar scores, and top gaps), then generates a prioritized,
tactical plan to prepare for Genie Ontology, naming the public Databricks
accelerators that help close each gap. The plan can be exported to a branded PDF.
"""

import html as _html
import io
import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, StreamingResponse, Response
from pydantic import BaseModel, Field

from server.content.accelerators import list_accelerators
from server.content.methodology import methodology_prompt
from server.routes._shared import stream_llm_chat, _ai_model, current_principal
from server.security import sanitize_html_fragment
from server import snapshots, plans

logger = logging.getLogger(__name__)
router = APIRouter()


class PlanGenerateRequest(BaseModel):
    # A plan is generated against EITHER a saved assessment (snapshot_id) or an
    # in-session assessment passed inline (scorecard). The inline path lets Plan
    # work when history isn't persisted (no Lakebase attached).
    snapshot_id: Optional[int] = None
    scorecard: Optional[dict] = None


# Field limits are the second half of the body-size cap in app.py: that one bounds
# the whole request, these bound what actually reaches the PDF renderer and the
# history table. A generated plan is capped at 2000 output tokens (~10 KB).
_MAX_TITLE = 200
_MAX_MARKDOWN = 256_000


class PlanSaveRequest(BaseModel):
    snapshot_id: Optional[int] = None
    title: str = Field(default="Genie Ontology Readiness — Action Plan", max_length=_MAX_TITLE)
    markdown: str = Field(max_length=_MAX_MARKDOWN)


class PlanPdfRequest(BaseModel):
    title: str = Field(default="Genie Ontology Readiness — Action Plan", max_length=_MAX_TITLE)
    markdown: str = Field(max_length=_MAX_MARKDOWN)


_GROUND_TRUTH = """PRODUCT GROUND TRUTH (do not violate):
- Genie Ontology is a LEARNED enterprise context layer built on top of the customer's governed Unity Catalog Business Semantics (metric views, Pages, domains, synonyms). The foundation FEEDS the ontology. Never describe the learned ontology layer as generally available.
- "Preparing for Genie Ontology" = maturing UC governance, metadata, metric views/semantics, Genie Agents, and domains."""


def _scorecard_digest(sc: Optional[dict]) -> str:
    if not sc:
        return "No assessment is available yet."
    overall = sc.get("overall", {})
    lines = [f"Overall readiness: {overall.get('score')}/100 ({overall.get('level_label')}) — {overall.get('readiness_stage')}."]
    for p in sc.get("pillars", []):
        sigs = ", ".join(f"{s.get('label')}={s.get('value')}{s.get('unit','')}" for s in (p.get("signals") or [])[:3])
        avail = "" if p.get("available", True) else " [not available]"
        line = f"- {p.get('name')}: {p.get('score')} ({p.get('level_label')}){avail}"
        if sigs:
            line += f" — {sigs}"
        lines.append(line)
    top_gaps = sc.get("top_gaps") or []
    if top_gaps:
        lines.append("Top gaps: " + "; ".join(f"{g.get('pillar')}: {g.get('gap')}" for g in top_gaps))
    return "\n".join(lines)


def _scorecard_markdown(sc: Optional[dict]) -> str:
    """A compact, user-facing Markdown summary of the assessment scores, built
    deterministically from the scorecard (NOT the LLM). Prepended to the plan so
    the top of the document always reflects the real numbers and can't be
    truncated or hallucinated. Rendered on screen and flows into the PDF."""
    if not sc:
        return ""
    overall = sc.get("overall", {}) or {}
    lines = ["## Assessment summary", ""]
    score = overall.get("score")
    level = overall.get("level")
    level_label = overall.get("level_label") or ""
    stage = overall.get("readiness_stage") or ""
    header = f"**Overall readiness: {score}/100 — L{level} {level_label}**"
    if stage:
        header += f" · {stage}"
    lines += [header, ""]

    pillars = sc.get("pillars", []) or []
    if pillars:
        lines += ["| Pillar | Score | Level |", "| --- | --- | --- |"]
        for p in pillars:
            avail = "" if p.get("available", True) else " (n/a)"
            lines.append(
                f"| {p.get('name')} | {p.get('score')}{avail} | L{p.get('level')} {p.get('level_label') or ''} |"
            )
        lines.append("")

    top_gaps = sc.get("top_gaps") or []
    if top_gaps:
        lines.append("**Top gaps**")
        lines.append("")
        for g in top_gaps:
            lines.append(f"- **{g.get('pillar')}** — {g.get('gap')}")
        lines.append("")

    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _accelerator_catalog() -> str:
    """Compact catalog of the public Databricks accelerators, grouped by the
    capability/pillar they lift, so the plan can name the right one per gap."""
    lines = []
    for a in list_accelerators():
        url = (a.get("source") or {}).get("url", "")
        lines.append(
            f"- [{a.get('capability')}] {a.get('title')}: {a.get('summary')}"
            + (f" ({url})" if url else "")
        )
    return "\n".join(lines) if lines else "None available."


def _generate_system(sc: Optional[dict]) -> str:
    return f"""You are a Databricks Solutions Architect. Write a CONCISE, tactical action plan in Markdown that prepares this customer for Genie Ontology, based ENTIRELY on their workspace assessment below. Ground every recommendation in their real scores and gaps.

{_GROUND_TRUTH}

THIS WORKSPACE'S ASSESSMENT (this is your source of truth — reference the actual numbers, levels, and gaps):
{_scorecard_digest(sc)}

PUBLIC DATABRICKS ACCELERATORS you may recommend (only these; each is a real, Databricks-built, publicly available asset). When an accelerator maps to a weak pillar, name it and include its link so the customer can act:
{_accelerator_catalog()}

{methodology_prompt()}

Keep it tight and scannable — no filler, no generic multi-phase project plan. The document already opens with a deterministic score summary, so do NOT restate the score table; start directly at "Where you are". Produce exactly these sections:
1. **Where you are** — 2-3 sentences on their readiness, tied to their overall score/stage and their biggest levers (the lowest-scoring, highest-weight pillars).
2. **Top recommendations** — the 4-6 highest-impact actions, prioritized worst-gap first. Each bullet must: (a) name the specific pillar/gap it closes, (b) give the concrete technical step AND the business/ownership step, and (c) where one applies, name the relevant accelerator above with its link.
3. **Suggested sequence** — a NUMBERED list of clear, tactical steps the customer can follow in order (what to do first → next). Each step is a concrete action (e.g. "Declare PK/FK constraints on your 8 gold fact tables"), not a theme. Where the work involves building metric views, Genie Agents, or domain tags, follow the BUILD METHODOLOGY above — reflect its phases and non-negotiable techniques (one source per metric view, validate one measure at a time, base views for multi-fact KPIs, one focused Genie Agent per domain, benchmark + regression-test). Make these specific enough to hand to a data team.

Do not invent scores or accelerators that are not listed above. Be specific to the assessment numbers."""


@router.post("/plan/generate")
async def plan_generate(req: PlanGenerateRequest, principal: str = Depends(current_principal)):
    """Generate the action plan against an assessment (no conversation).

    The assessment comes from EITHER a saved snapshot (``snapshot_id``, loaded
    server-side from the user's own history) or an inline ``scorecard`` sent by the
    client — the latter lets the Plan tab work from the in-session assessment even
    when history isn't persisted (no Lakebase attached).
    """
    if req.snapshot_id is not None:
        snap = await snapshots.get_snapshot(req.snapshot_id, created_by=principal)
        if snap is None:
            return JSONResponse(status_code=404, content={"error": "Assessment not found."})
        scorecard = snap.get("scorecard") or {}
    elif req.scorecard is not None:
        # Client-supplied (in-session assessment, when history isn't persisted).
        scorecard = req.scorecard
    else:
        return JSONResponse(
            status_code=400,
            content={"error": "Provide a snapshot_id or an assessment scorecard."},
        )
    # Validate the resolved scorecard from EITHER source has real content — an empty
    # or malformed one (client dict, or a degraded/legacy snapshot) would otherwise
    # stream a generic "no assessment available" plan instead of a clear error.
    if not isinstance(scorecard, dict) or not scorecard.get("pillars"):
        return JSONResponse(
            status_code=400,
            content={"error": "The assessment is empty — run an assessment first."},
        )
    system = _generate_system(scorecard)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": "Generate the action plan now."},
    ]
    logger.info("Plan generate for %s",
                f"snapshot {req.snapshot_id}" if req.snapshot_id is not None else "in-session assessment")

    async def _gen():
        # Emit the deterministic score summary first (as one SSE content frame) so
        # the top of the plan is always the real scores; then stream the LLM plan.
        header = _scorecard_markdown(scorecard)
        if header:
            yield f"data: {json.dumps({'content': header})}\n\n"
        async for chunk in stream_llm_chat(messages, max_tokens=2400, temperature=0.4):
            yield chunk

    return StreamingResponse(_gen(), media_type="text/event-stream")


@router.post("/plan/save")
async def plan_save(req: PlanSaveRequest, principal: str = Depends(current_principal)):
    """Persist a generated plan for the current user, linked to its assessment."""
    plan_id = await plans.save_plan(
        created_by=principal,
        snapshot_id=req.snapshot_id,
        title=req.title,
        model=_ai_model.get(),
        plan_markdown=req.markdown,
    )
    return {"id": plan_id, "saved": plan_id is not None}


@router.get("/plan/list")
async def plan_list(principal: str = Depends(current_principal)):
    """The current user's saved plans (metadata only)."""
    return {"plans": await plans.list_plans(created_by=principal)}


@router.get("/plan/{plan_id}")
async def plan_get(plan_id: int, principal: str = Depends(current_principal)):
    """Load one saved plan (including markdown), scoped to the current user."""
    plan = await plans.get_plan(plan_id, created_by=principal)
    if plan is None:
        return JSONResponse(status_code=404, content={"error": "Plan not found."})
    return plan


def _strip_document_h1(markdown_text: str) -> str:
    """Remove the FIRST top-level H1 from the plan Markdown (the redundant title).

    Fixes the duplicated-opening-line bug (issue #21): the PDF route renders the
    document title itself (``<h1>{title}</h1>``), but the generated plan body also
    tends to open with its own ``# <title>`` heading (the model adds one despite the
    prompt), so the title rendered twice in the exported PDF. We remove that title by
    structure — the first H1 — not by matching its text, so it's robust to the model
    rephrasing the title or using a different dash. Only the FIRST H1 is removed: a
    later legitimate ``# Appendix``-style heading is left intact (removing every H1
    would orphan its content under the preceding section).

    Matching details:
    - ATX H1 is a single ``#`` (not ``##``) with up to 3 leading spaces; Python-
      Markdown treats ``#Title`` (no space after ``#``) as an H1 too, so the space is
      optional here — otherwise a space-less title would slip through and still dup.
    - Setext H1 (a text line underlined by ``===``) is handled.
    - Fenced code blocks are respected: a ``# comment`` inside a ``` / ~~~ fence is
      code, not a heading. The closing fence must use the same character and be at
      least as long as the opening one (CommonMark), so a longer outer fence isn't
      closed early by a shorter inner one."""
    lines = markdown_text.split("\n")
    out: list[str] = []
    fence_char: Optional[str] = None
    fence_len = 0
    removed = False
    i = 0
    while i < len(lines):
        line = lines[i]
        # Track fenced code blocks; never treat their contents as headings.
        if fence_char is None:
            m_open = re.match(r"^ {0,3}(\x60{3,}|~{3,})", line)  # \x60 = backtick
            if m_open:
                fence_char, fence_len = m_open.group(1)[0], len(m_open.group(1))
                out.append(line)
                i += 1
                continue
        else:
            if re.match(rf"^ {{0,3}}{re.escape(fence_char)}{{{fence_len},}}\s*$", line):
                fence_char, fence_len = None, 0
            out.append(line)
            i += 1
            continue
        if not removed:
            # ATX H1: one '#' (not '##'), optional space, then content.
            if re.match(r"^ {0,3}#(?!#)\s*\S", line):
                removed = True
                i += 1
                continue
            # Setext H1: a text line immediately underlined by a run of '='.
            if (
                i + 1 < len(lines)
                and line.strip()
                and re.match(r"^\s*=+\s*$", lines[i + 1])
            ):
                removed = True
                i += 2
                continue
        out.append(line)
        i += 1
    # Collapse the blank-line gap a removed heading leaves behind.
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip() + "\n"


_PDF_CSS = """
@page {
  size: A4; margin: 2.2cm 2cm;
  @frame footer { -pdf-frame-content: footerContent; left: 2cm; width: 17cm; bottom: 1.2cm; height: 1cm; }
}
body { font-family: Helvetica, Arial, sans-serif; font-size: 10.5pt; color: #1B3139; line-height: 1.5; }
h1 { color: #1B3139; font-size: 19pt; border-bottom: 3px solid #FF3621; padding-bottom: 6px; margin: 0 0 4px 0; }
.subtitle { color: #5B6B70; font-size: 9pt; margin-bottom: 16px; }
h2 { color: #1B3139; font-size: 13pt; margin-top: 18px; border-bottom: 1px solid #E3E8E9; padding-bottom: 3px; -pdf-keep-with-next: true; page-break-after: avoid; }
h3 { color: #1B3139; font-size: 11pt; margin-top: 12px; -pdf-keep-with-next: true; page-break-after: avoid; }
ul, ol { margin: 6px 0 6px 0; padding-left: 18px; }
li { margin-bottom: 4px; }
strong { color: #1B3139; }
code { font-family: Courier, monospace; background: #F4F6F6; padding: 1px 3px; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; -pdf-keep-in-frame-mode: shrink; }
th, td { border: 1px solid #D5DCDD; padding: 5px 7px; text-align: left; font-size: 9.5pt; }
th { background: #1B3139; color: #fff; }
.footer { color: #8A9499; font-size: 8pt; text-align: center; }
"""


def _build_plan_pdf_html(markdown_text: str, title: str) -> str:
    """Assemble the full branded HTML document for the plan PDF.

    Renders the title exactly once (issue #21): the body's own redundant title H1 is
    stripped, and the title is supplied here as the single ``<h1>``. ``title`` is
    HTML-escaped so a title containing ``&``/``<``/``>`` can't produce malformed
    markup that breaks xhtml2pdf. A page-number footer and a dated subtitle are
    added for an executive-ready readout. Pure/deterministic so it can be unit-tested
    against the real CSS + footer syntax without hitting the network.

    Security (CWE-79/22/918): python-markdown emits raw HTML in the source document
    verbatim, and the plan Markdown is model-generated and round-trips through the
    client, so the rendered body is sanitized to an inert allowlist before it reaches
    the PDF engine — stripping resource-loading tags (img/iframe/etc.) that xhtml2pdf
    would otherwise resolve into a local-file read. The renderer's link_callback
    (_block_external_resources) is the belt-and-suspenders backstop at call sites."""
    import markdown as md

    clean_md = _strip_document_h1(markdown_text or "")
    body_html = sanitize_html_fragment(
        md.markdown(clean_md, extensions=["tables", "fenced_code", "toc", "sane_lists"])
    )
    d = datetime.now(timezone.utc)
    generated_on = f"{d:%B} {d.day}, {d.year}"  # portable — avoids the glibc-only %-d
    esc_title = _html.escape(title or "Action Plan")
    return (
        f"<!DOCTYPE html><html><head><meta charset='utf-8'><style>{_PDF_CSS}</style></head>"
        f"<body>"
        f"<div id='footerContent' class='footer'>Genie Ontology Readiness · Databricks · "
        f"page <pdf:pagenumber> of <pdf:pagecount></div>"
        f"<h1>{esc_title}</h1>"
        f"<div class='subtitle'>Generated by the Genie Ontology Readiness app · Databricks · {generated_on}</div>"
        f"{body_html}</body></html>"
    )


class ExternalResourceBlocked(Exception):
    """Raised when the plan document references a resource the renderer may not fetch."""


def _block_external_resources(uri: str, rel: str) -> str:
    """xhtml2pdf resource resolver that refuses every external reference.

    xhtml2pdf resolves `src`/`href` on the document it renders, and will happily
    open a `file://`, absolute-path or `http(s)://` target. The plan Markdown is
    model-generated and arrives from the client, so without this the endpoint is a
    server-side file read and request forge in one (CWE-918, CWE-22): posting
    `<img src="file:///…">` would embed host file content into the returned PDF.

    The branded template references no external resource, and the body is
    sanitized before it gets here, so this is the backstop: refuse outright rather
    than maintain an allowlist. Raising aborts the render, which is the point —
    a partially-rendered PDF is better than one containing host file contents.
    """
    raise ExternalResourceBlocked(uri)


@router.post("/plan/pdf")
async def plan_pdf(req: PlanPdfRequest):
    """Render the plan Markdown to a branded PDF, returned inline for a new-tab viewer.

    The PDF engine is imported lazily so that, if these optional deps fail to
    load in the runtime, only this endpoint degrades — the rest of the app
    (Assess, Learn, Plan chat) keeps working.
    """
    try:
        import markdown  # noqa: F401 - availability gate; imported for real in the builder
        from xhtml2pdf import pisa
    except Exception as e:  # pragma: no cover - depends on runtime deps
        logger.error(f"PDF engine unavailable: {e}")
        return Response(content="PDF export is unavailable on this deployment.", status_code=503)

    # Body is stripped-of-duplicate-title, sanitized, and title-escaped inside the
    # builder (issue #21 + CWE-79/22). The link_callback is the belt-and-suspenders
    # backstop: it refuses every external/file reference the sanitizer might miss, so
    # the export can never be turned into a server-side file read.
    html_doc = _build_plan_pdf_html(req.markdown or "", req.title)
    buf = io.BytesIO()
    try:
        result = pisa.CreatePDF(
            src=html_doc, dest=buf, encoding="utf-8", link_callback=_block_external_resources
        )
    except ExternalResourceBlocked as e:
        # Unreachable for a sanitized body; if it does fire, the document asked for
        # something it may not have, which is the caller's problem, not a server fault.
        logger.warning("plan PDF refused an external resource reference: %.120s", str(e))
        return JSONResponse(
            status_code=400,
            content={"error": "The plan references an external resource and cannot be exported."},
        )
    if result.err:
        logger.error("PDF generation failed")
        return Response(content="PDF generation failed", status_code=500)
    safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in req.title).strip() or "action-plan"
    return Response(
        content=buf.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{safe}.pdf"'},
    )
