"""Genie endpoints — optionally test answer quality against a configured Genie Agent.

Set GENIE_SPACE_ID to enable. Used in pillar 5 to demonstrate live value.
"""

import logging
import aiohttp

from typing import Optional

from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from server.config import get_workspace_host, get_auth_headers, set_user_token, GENIE_SPACE_ID
from server.genie_client import (
    GENIE_IDENTITY_REQUIRED,
    GenieIdentityUnavailable,
    send_message,
    start_conversation,
)
from server.routes._shared import current_principal
from server.security import safe_error

logger = logging.getLogger(__name__)
router = APIRouter()


# A Genie question is a single natural-language sentence. Bounding it keeps an
# oversized body from being forwarded to the Genie API and billed (CWE-770).
_MAX_QUESTION = 4000
_MAX_CONVERSATION_ID = 128


class GenieStartRequest(BaseModel):
    content: str = Field(min_length=1, max_length=_MAX_QUESTION)


class GenieMessageRequest(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=_MAX_CONVERSATION_ID,
                                 pattern=r"^[A-Za-z0-9_-]+$")
    content: str = Field(min_length=1, max_length=_MAX_QUESTION)


@router.get("/genie/spaces")
async def list_genie_agents(
    principal: str = Depends(current_principal),
    x_forwarded_access_token: Optional[str] = Header(default=None),
):
    """List Genie Agents visible to the app (for the pillar 5 detail view)."""
    set_user_token(x_forwarded_access_token)
    host = get_workspace_host()
    headers = get_auth_headers()
    if not host or not headers:
        return {"spaces": [], "note": "Workspace credentials unavailable."}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.get(f"{host}/api/2.0/genie/spaces", headers=headers, params={"page_size": 100}) as resp:
                if resp.status != 200:
                    return {"spaces": [], "note": f"Genie API returned {resp.status}"}
                data = await resp.json()
                raw = data.get("spaces", []) or data.get("data", []) or []
                return {"spaces": [{"id": s.get("space_id") or s.get("id"), "title": s.get("title") or s.get("name")} for s in raw]}
    except Exception as e:
        # The exception text can quote the upstream response body; return a
        # reference to the (redacted) server log instead (CWE-209).
        reference, message = safe_error(e, "list Genie Agents", logger)
        return {"spaces": [], "note": message, "reference": reference}


# A Genie answer returns ROWS from the customer's warehouse. Both endpoints below
# therefore require an established identity (they were previously open to any
# caller) and run the query on-behalf-of that viewer — see genie_client.
@router.post("/genie/start-conversation")
async def genie_start(
    req: GenieStartRequest,
    principal: str = Depends(current_principal),
    x_forwarded_access_token: Optional[str] = Header(default=None),
):
    if not GENIE_SPACE_ID:
        return JSONResponse(status_code=400, content={"error": "No GENIE_SPACE_ID configured."})
    set_user_token(x_forwarded_access_token)
    try:
        return await start_conversation(req.content)
    except GenieIdentityUnavailable:
        return JSONResponse(status_code=403, content={"error": GENIE_IDENTITY_REQUIRED})


@router.post("/genie/message")
async def genie_message(
    req: GenieMessageRequest,
    principal: str = Depends(current_principal),
    x_forwarded_access_token: Optional[str] = Header(default=None),
):
    if not GENIE_SPACE_ID:
        return JSONResponse(status_code=400, content={"error": "No GENIE_SPACE_ID configured."})
    set_user_token(x_forwarded_access_token)
    try:
        return await send_message(req.conversation_id, req.content)
    except GenieIdentityUnavailable:
        return JSONResponse(status_code=403, content={"error": GENIE_IDENTITY_REQUIRED})
