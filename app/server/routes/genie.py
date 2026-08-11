"""Genie endpoints — optionally test answer quality against a configured Genie Space.

Set GENIE_SPACE_ID to enable. Used in pillar 5 to demonstrate live value.
"""

import logging
import aiohttp

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from server.config import get_workspace_host, get_auth_headers, GENIE_SPACE_ID
from server.genie_client import start_conversation, send_message

logger = logging.getLogger(__name__)
router = APIRouter()


class GenieStartRequest(BaseModel):
    content: str


class GenieMessageRequest(BaseModel):
    conversation_id: str
    content: str


@router.get("/genie/spaces")
async def list_genie_spaces():
    """List Genie Spaces visible to the app (for the pillar 5 detail view)."""
    host = get_workspace_host()
    headers = get_auth_headers()
    if not host or not headers:
        return {"spaces": [], "note": "Workspace credentials unavailable."}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{host}/api/2.0/genie/spaces", headers=headers, params={"page_size": 100}) as resp:
                if resp.status != 200:
                    return {"spaces": [], "note": f"Genie API returned {resp.status}"}
                data = await resp.json()
                raw = data.get("spaces", []) or data.get("data", []) or []
                return {"spaces": [{"id": s.get("space_id") or s.get("id"), "title": s.get("title") or s.get("name")} for s in raw]}
    except Exception as e:
        logger.warning(f"list_genie_spaces failed: {e}")
        return {"spaces": [], "note": str(e)[:120]}


@router.post("/genie/start-conversation")
async def genie_start(req: GenieStartRequest):
    if not GENIE_SPACE_ID:
        return JSONResponse(status_code=400, content={"error": "No GENIE_SPACE_ID configured."})
    return await start_conversation(req.content)


@router.post("/genie/message")
async def genie_message(req: GenieMessageRequest):
    if not GENIE_SPACE_ID:
        return JSONResponse(status_code=400, content={"error": "No GENIE_SPACE_ID configured."})
    return await send_message(req.conversation_id, req.content)
