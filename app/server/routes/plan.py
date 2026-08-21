"""Plan generation — one AI call that turns the workspace assessment into a
concise, tactical action plan, plus PDF export of the generated plan.

The Plan tab is a single button: it grounds in the workspace scorecard (overall
readiness, per-pillar scores, and top gaps), then generates a prioritized,
tactical plan to prepare for Genie Ontology, naming the public Databricks
accelerators that help close each gap. The plan can be exported to a branded PDF.
"""

import io
import logging
from typing import Optional

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse, StreamingResponse, Response
from pydantic import BaseModel

from server.content.accelerators import list_accelerators
from server.content.methodology import methodology_prompt
from server.routes._shared import stream_llm_chat, _ai_model
from server import snapshots, plans

logger = logging.getLogger(__name__)
router = APIRouter()


class PlanGenerateRequest(BaseModel):
    snapshot_id: int          # the assessment this plan is generated against


class PlanSaveRequest(BaseModel):
    snapshot_id: Optional[int] = None
    title: str = "Genie Ontology Readiness — Action Plan"
    markdown: str


class PlanPdfRequest(BaseModel):
    title: str = "Genie Ontology Readiness — Action Plan"
    markdown: str


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

Keep it tight and scannable — no filler, no generic multi-phase project plan. Produce exactly these sections:
1. **Where you are** — 2-3 sentences on their readiness, tied to their overall score/stage and their biggest levers (the lowest-scoring, highest-weight pillars).
2. **Top recommendations** — the 4-6 highest-impact actions, prioritized worst-gap first. Each bullet must: (a) name the specific pillar/gap it closes, (b) give the concrete technical step AND the business/ownership step, and (c) where one applies, name the relevant accelerator above with its link.
3. **Suggested sequence** — a NUMBERED list of clear, tactical steps the customer can follow in order (what to do first → next). Each step is a concrete action (e.g. "Declare PK/FK constraints on your 8 gold fact tables"), not a theme. Where the work involves building metric views, Genie Agents, or domain tags, follow the BUILD METHODOLOGY above — reflect its phases and non-negotiable techniques (one source per metric view, validate one measure at a time, base views for multi-fact KPIs, one focused Genie Agent per domain, benchmark + regression-test). Make these specific enough to hand to a data team.
4. **Example Genie use cases** — 2-3 realistic questions Genie could answer once the foundation is in place, each one line (the question + the metric views / tables it would use). Infer the domain from the catalog/schema names in the assessment signals if available; otherwise keep them broadly applicable and say so in a short lead-in line.

Do not invent scores or accelerators that are not listed above. Be specific to the assessment numbers."""


@router.post("/plan/generate")
async def plan_generate(req: PlanGenerateRequest, x_forwarded_email: Optional[str] = Header(default=None)):
    """Generate the action plan against a SELECTED, stored assessment (no conversation).

    The scorecard is loaded server-side from the user's own snapshot, so a plan is
    always tied to a real, persisted assessment.
    """
    snap = await snapshots.get_snapshot(req.snapshot_id, created_by=x_forwarded_email)
    if snap is None:
        return JSONResponse(status_code=404, content={"error": "Assessment not found."})
    scorecard = snap.get("scorecard") or {}
    system = _generate_system(scorecard)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": "Generate the action plan now."},
    ]
    logger.info("Plan generate for snapshot %s", req.snapshot_id)
    return StreamingResponse(
        stream_llm_chat(messages, max_tokens=2000, temperature=0.4),
        media_type="text/event-stream",
    )


@router.post("/plan/save")
async def plan_save(req: PlanSaveRequest, x_forwarded_email: Optional[str] = Header(default=None)):
    """Persist a generated plan for the current user, linked to its assessment."""
    plan_id = await plans.save_plan(
        created_by=x_forwarded_email,
        snapshot_id=req.snapshot_id,
        title=req.title,
        model=_ai_model.get(),
        plan_markdown=req.markdown,
    )
    return {"id": plan_id, "saved": plan_id is not None}


@router.get("/plan/list")
async def plan_list(x_forwarded_email: Optional[str] = Header(default=None)):
    """The current user's saved plans (metadata only)."""
    return {"plans": await plans.list_plans(created_by=x_forwarded_email)}


@router.get("/plan/{plan_id}")
async def plan_get(plan_id: int, x_forwarded_email: Optional[str] = Header(default=None)):
    """Load one saved plan (including markdown), scoped to the current user."""
    plan = await plans.get_plan(plan_id, created_by=x_forwarded_email)
    if plan is None:
        return JSONResponse(status_code=404, content={"error": "Plan not found."})
    return plan


_PDF_CSS = """
@page { size: A4; margin: 2.2cm 2cm; }
body { font-family: Helvetica, Arial, sans-serif; font-size: 10.5pt; color: #1B3139; line-height: 1.5; }
h1 { color: #1B3139; font-size: 19pt; border-bottom: 3px solid #FF3621; padding-bottom: 6px; margin: 0 0 4px 0; }
.subtitle { color: #5B6B70; font-size: 9pt; margin-bottom: 16px; }
h2 { color: #1B3139; font-size: 13pt; margin-top: 18px; border-bottom: 1px solid #E3E8E9; padding-bottom: 3px; }
h3 { color: #1B3139; font-size: 11pt; margin-top: 12px; }
ul, ol { margin: 6px 0 6px 0; padding-left: 18px; }
li { margin-bottom: 4px; }
strong { color: #1B3139; }
code { font-family: Courier, monospace; background: #F4F6F6; padding: 1px 3px; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; }
th, td { border: 1px solid #D5DCDD; padding: 5px 7px; text-align: left; font-size: 9.5pt; }
th { background: #1B3139; color: #fff; }
"""


@router.post("/plan/pdf")
async def plan_pdf(req: PlanPdfRequest):
    """Render the plan Markdown to a branded PDF, returned inline for a new-tab viewer.

    The PDF engine is imported lazily so that, if these optional deps fail to
    load in the runtime, only this endpoint degrades — the rest of the app
    (Assess, Learn, Plan chat) keeps working.
    """
    try:
        import markdown as md
        from xhtml2pdf import pisa
    except Exception as e:  # pragma: no cover - depends on runtime deps
        logger.error(f"PDF engine unavailable: {e}")
        return Response(content="PDF export is unavailable on this deployment.", status_code=503)

    body_html = md.markdown(req.markdown or "", extensions=["tables", "fenced_code", "toc", "sane_lists"])
    html = (
        f"<!DOCTYPE html><html><head><meta charset='utf-8'><style>{_PDF_CSS}</style></head>"
        f"<body><h1>{req.title}</h1>"
        f"<div class='subtitle'>Generated by the Genie Ontology Readiness app · Databricks</div>"
        f"{body_html}</body></html>"
    )
    buf = io.BytesIO()
    result = pisa.CreatePDF(src=html, dest=buf, encoding="utf-8")
    if result.err:
        logger.error("PDF generation failed")
        return Response(content="PDF generation failed", status_code=500)
    safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in req.title).strip() or "action-plan"
    return Response(
        content=buf.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{safe}.pdf"'},
    )
