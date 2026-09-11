"""Workspace enumeration for the pre-run workspace filter.

Lists the workspaces on this metastore (id + name) from
``system.access.workspaces_latest`` so the Assess tab can offer a searchable
include/exclude filter. Marks the deployed workspace (``WORKSPACE_ID``) as
``is_current`` so the UI can seed the default selection to it.

Reads run as the app service principal (``force_sp=True``): this is infra
metadata the app SP is granted on ``system.access``, not viewer-specific data.
Degrades gracefully — if the read fails (no grant / table unavailable) it returns
just the current workspace so the filter still shows the sensible default.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Header

from server.config import set_user_token, WORKSPACE_ID
from server.sql_client import execute_sql

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/workspaces")
async def list_workspaces(x_forwarded_access_token: Optional[str] = Header(default=None)):
    """Workspaces on this metastore for the filter UI: [{id, name, url, status, is_current}]."""
    set_user_token(x_forwarded_access_token)
    current = str(WORKSPACE_ID) if WORKSPACE_ID else None
    try:
        rows = await execute_sql(
            "SELECT workspace_id, workspace_name, workspace_url, status "
            "FROM system.access.workspaces_latest "
            "ORDER BY workspace_name",
            force_sp=True,
        )
        workspaces = [
            {
                "id": str(r.get("workspace_id")),
                "name": r.get("workspace_name") or str(r.get("workspace_id")),
                "url": r.get("workspace_url"),
                "status": r.get("status"),
                "is_current": current is not None and str(r.get("workspace_id")) == current,
            }
            for r in rows
            if r.get("workspace_id")
        ]
        if workspaces:
            return {"workspaces": workspaces, "current_workspace_id": current, "available": True}
    except Exception as e:
        logger.info(f"workspace enumeration unavailable: {str(e)[:120]}")

    # Fallback: expose just the deployed workspace so the filter still defaults sanely.
    fallback = (
        [{"id": current, "name": f"This workspace ({current})", "url": None,
          "status": None, "is_current": True}]
        if current else []
    )
    return {"workspaces": fallback, "current_workspace_id": current, "available": False}
