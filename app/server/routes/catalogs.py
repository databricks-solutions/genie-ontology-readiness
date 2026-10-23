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
from server.security import quote_ident
from server.sql_client import execute_sql

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


@router.get("/schemas")
async def list_schemas(
    catalog: str,
    x_forwarded_access_token: Optional[str] = Header(default=None),
):
    """Schemas in ``catalog`` for the optional single-schema assessment picker.

    Excludes ``information_schema``. Returns ``{schemas: [{name}], available}``.
    """
    set_user_token(x_forwarded_access_token)
    name = (catalog or "").strip()
    if not name:
        return {"schemas": [], "available": False}
    try:
        rows = await execute_sql(
            f"SELECT schema_name AS name FROM {quote_ident(name)}.information_schema.schemata "
            "WHERE schema_name <> 'information_schema' ORDER BY schema_name"
        )
        schemas = [{"name": r.get("name")} for r in rows if r.get("name")]
        return {"schemas": schemas, "available": True}
    except Exception as e:
        logger.warning(f"schema listing failed for catalog {name!r}: {e}")
        return {"schemas": [], "available": False}
