"""Assessment endpoints — run the readiness scorecard and manage per-user history."""

import json
import logging
from typing import Optional

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse, StreamingResponse

from server.assessment.scoring import run_assessment, run_assessment_stream
from server.routes._shared import _cache_get, _cache_set
from server.config import set_user_token
from server import snapshots

logger = logging.getLogger(__name__)
router = APIRouter()

# Header name Databricks Apps uses to forward the end-user's token (on-behalf-of-
# user authorization). When present, assessment metadata reads run as the viewer.
_OBO_HEADER = "x-forwarded-access-token"


@router.get("/assess")
async def assess_get(x_forwarded_access_token: Optional[str] = Header(default=None)):
    """Quick technical-only assessment. Cached briefly. Not persisted."""
    set_user_token(x_forwarded_access_token)
    # Only cache the SP-run result; a per-user (OBO) run is scoped to that viewer.
    if not x_forwarded_access_token:
        cached = _cache_get("assess:technical")
        if cached is not None:
            return cached
    result = await run_assessment()
    if not x_forwarded_access_token:
        _cache_set("assess:technical", result)
    return result


@router.post("/assess/stream")
async def assess_stream(
    x_forwarded_email: Optional[str] = Header(default=None),
    x_forwarded_access_token: Optional[str] = Header(default=None),
):
    """Stream the assessment: one SSE event per pillar as it completes, then a
    final 'complete' event with the overall score + top gaps. Every completed run
    is auto-saved to the user's history (when Lakebase is enabled)."""

    async def gen():
        # Set inside the generator too: the streaming body may run in a fresh
        # context, so re-establish the on-behalf-of-user token here.
        set_user_token(x_forwarded_access_token)
        try:
            async for event in run_assessment_stream():
                if event.get("type") == "complete":
                    scorecard = {
                        "overall": event["overall"],
                        "pillars": event["pillars"],
                        "top_gaps": event["top_gaps"],
                    }
                    try:
                        sid = await snapshots.save_snapshot(scorecard, created_by=x_forwarded_email)
                        event["snapshot_id"] = sid
                        event["snapshot_saved"] = sid is not None
                    except Exception as e:
                        logger.warning(f"snapshot save failed: {e}")
                        event["snapshot_saved"] = False
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            logger.error(f"assess_stream error: {e}", exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)[:200]})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/assess/history")
async def assess_history(x_forwarded_email: Optional[str] = Header(default=None)):
    """The current user's past assessment runs (requires Lakebase; empty otherwise)."""
    return {"snapshots": await snapshots.list_snapshots(created_by=x_forwarded_email)}


@router.get("/assess/snapshot/{snapshot_id}")
async def assess_snapshot(snapshot_id: int, x_forwarded_email: Optional[str] = Header(default=None)):
    """Load one past assessment's full scorecard (scoped to the current user)."""
    snap = await snapshots.get_snapshot(snapshot_id, created_by=x_forwarded_email)
    if snap is None:
        return JSONResponse(status_code=404, content={"error": "Assessment not found."})
    return snap
