"""Assessment endpoints — run the readiness scorecard and manage per-user history."""

import json
import logging
from typing import Optional

from fastapi import APIRouter, Body, Depends, Header
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from server.assessment.scoring import run_assessment, run_assessment_stream
from server.routes._shared import _cache_get, _cache_set, current_principal
from server.config import set_user_token
from server.workspace_filter import set_workspace_filter, set_catalog_scope
from server.security import safe_error
from server import snapshots

logger = logging.getLogger(__name__)
router = APIRouter()

# Header name Databricks Apps uses to forward the end-user's token (on-behalf-of-
# user authorization). When present, assessment metadata reads run as the viewer.
_OBO_HEADER = "x-forwarded-access-token"


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
