"""Catalog enumeration for the pre-run catalog filter.

Returns the catalogs the metadata pillars could assess for the current workspace
selection, so the UI can offer a catalog multi-select that refreshes when the
workspace selection changes:

  GET /api/catalogs?workspace_ids=<csv>&mode=include|exclude

With an ``include`` selection, returns the catalogs bound (READ/READ_WRITE) to
those workspaces plus OPEN catalogs, via server.bindings.accessible_catalogs.
Otherwise (all / exclude), returns all non-internal catalogs. Degrades gracefully
to ``available: false`` with an empty list when the UC binding APIs can't be read.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Header

from server.config import set_user_token
from server.bindings import accessible_catalogs, all_catalogs, sql_enumerate_catalogs

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/catalogs")
async def list_catalogs(
    workspace_ids: Optional[str] = None,
    mode: str = "include",
    x_forwarded_access_token: Optional[str] = Header(default=None),
):
    """Catalogs assessable for the given workspace selection: [{name, access, isolation}]."""
    set_user_token(x_forwarded_access_token)
    ids = [w.strip() for w in (workspace_ids or "").split(",") if w.strip()]
    catalogs = await (accessible_catalogs(set(ids)) if (mode == "include" and ids) else all_catalogs())
    if catalogs is not None:
        catalogs.sort(key=lambda c: c["name"])
        return {"catalogs": catalogs, "available": True}
    # UC REST (catalogs/bindings) unreadable — usually an app SP without metastore
    # admin. Fall back to a plain SQL catalog list so the filter still works as a
    # manual scoping control; available=False tells the UI binding info is missing.
    fallback = await sql_enumerate_catalogs()
    fallback.sort(key=lambda c: c["name"])
    return {"catalogs": fallback, "available": False}
