"""Unity Catalog workspace-catalog binding resolution.

Determines which catalogs are accessible from a given set of workspaces, so the
catalog-metadata pillars can scope to the catalogs actually tied to the workspaces
the user selected (instead of one pinned catalog or every account catalog).

A catalog is accessible from a workspace when it is either:
  * OPEN (``isolation_mode = OPEN``) — reachable from every workspace, or
  * ISOLATED and explicitly bound to that workspace (READ_ONLY or READ_WRITE).

Uses the UC REST API (no system table exposes bindings):
  GET /api/2.1/unity-catalog/catalogs              → name + isolation_mode
  GET /api/2.1/unity-catalog/bindings/catalog/{n}  → [{binding_type, workspace_id}]

Reading bindings can require elevated privilege; callers treat a ``None`` result
as "couldn't resolve" and fall back to enumerating SP-visible catalogs.
"""

import asyncio
import logging
from typing import Optional

import aiohttp

from server.config import get_workspace_host, get_auth_headers
from server.sql_client import record_rest_identity, execute_sql
from server.user_agent import with_ua

logger = logging.getLogger(__name__)

# Never part of a customer's own data estate — excluded from binding resolution.
_INTERNAL = {"system", "__databricks_internal", "samples", "hive_metastore"}
_BINDINGS_CONCURRENCY = 8


def _access_from_binding_type(bt: str) -> str:
    return "READ_WRITE" if str(bt).upper().endswith("READ_WRITE") else "READ"


def _select_accessible(
    catalogs_meta: list[dict],
    bindings_by_catalog: dict[str, list[dict]],
    workspace_ids: set[str],
) -> list[dict]:
    """Pure decision: which catalogs are accessible from ``workspace_ids``.

    OPEN catalogs are always accessible; ISOLATED catalogs only when bound to one
    of the workspaces. Returns [{name, access, isolation}] where access is
    'OPEN' | 'READ' | 'READ_WRITE' (the strongest matching binding wins).
    """
    out = []
    for c in catalogs_meta:
        name = c.get("name")
        if not name or name in _INTERNAL:
            continue
        isolation = (c.get("isolation_mode") or "OPEN").upper()
        if isolation != "ISOLATED":
            out.append({"name": name, "access": "OPEN", "isolation": isolation})
            continue
        access = None
        for b in bindings_by_catalog.get(name, []):
            if str(b.get("workspace_id")) in workspace_ids:
                a = _access_from_binding_type(b.get("binding_type", ""))
                if a == "READ_WRITE" or access is None:
                    access = a
        if access:
            out.append({"name": name, "access": access, "isolation": isolation})
    return out


async def _get_json(session: aiohttp.ClientSession, url: str, headers: dict) -> Optional[dict]:
    try:
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                return None
            return await resp.json()
    except Exception:
        return None


async def accessible_catalogs(workspace_ids: set[str]) -> Optional[list[dict]]:
    """Catalogs accessible from ``workspace_ids`` per UC bindings, or None if the
    catalog/binding APIs can't be read (caller falls back to enumeration)."""
    host = get_workspace_host()
    headers = get_auth_headers()
    if not host or not headers:
        return None

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=30, connect=10, sock_connect=10, sock_read=20),
        headers=with_ua(),
    ) as session:
        cat_data = await _get_json(session, f"{host}/api/2.1/unity-catalog/catalogs", headers)
        if not isinstance(cat_data, dict):
            return None
        catalogs_meta = [
            {"name": c.get("name"), "isolation_mode": c.get("isolation_mode")}
            for c in (cat_data.get("catalogs") or [])
            if c.get("name") and c.get("name") not in _INTERNAL
        ]
        if not catalogs_meta:
            return []

        # Only ISOLATED catalogs need a bindings lookup (OPEN is reachable from all).
        isolated = [c["name"] for c in catalogs_meta if (c.get("isolation_mode") or "OPEN").upper() == "ISOLATED"]
        sem = asyncio.Semaphore(_BINDINGS_CONCURRENCY)

        async def _bindings(name: str) -> tuple[str, list[dict]]:
            async with sem:
                d = await _get_json(session, f"{host}/api/2.1/unity-catalog/bindings/catalog/{name}", headers)
                return name, (d.get("bindings", []) if isinstance(d, dict) else [])

        results = await asyncio.gather(*(_bindings(n) for n in isolated)) if isolated else []
        bindings_by_catalog = dict(results)

    record_rest_identity()
    return _select_accessible(catalogs_meta, bindings_by_catalog, {str(w) for w in workspace_ids})


async def all_catalogs() -> Optional[list[dict]]:
    """All non-internal catalogs on the metastore (name + isolation), access 'ALL'.
    Used when the workspace filter isn't a specific include selection. None on failure."""
    host = get_workspace_host()
    headers = get_auth_headers()
    if not host or not headers:
        return None
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=30, connect=10, sock_connect=10, sock_read=20),
        headers=with_ua(),
    ) as session:
        cat_data = await _get_json(session, f"{host}/api/2.1/unity-catalog/catalogs", headers)
    if not isinstance(cat_data, dict):
        return None
    record_rest_identity()
    return [
        {"name": c.get("name"), "access": "ALL", "isolation": (c.get("isolation_mode") or "OPEN")}
        for c in (cat_data.get("catalogs") or [])
        if c.get("name") and c.get("name") not in _INTERNAL
    ]


async def sql_enumerate_catalogs() -> list[dict]:
    """SQL fallback catalog list when the UC REST APIs can't be read (the common
    case for an app service principal without metastore-admin). Returns
    [{name, access:'ALL', isolation:'UNKNOWN'}] — no binding info, but enough to
    populate a manual catalog filter. Excludes internal/`__`-prefixed catalogs."""
    rows = []
    try:
        rows = await execute_sql("SELECT catalog_name AS c FROM system.information_schema.catalogs")
    except Exception:
        try:
            rows = await execute_sql("SHOW CATALOGS")
        except Exception:
            return []
    names = [(r.get("c") if isinstance(r, dict) and "c" in r else list(r.values())[0]) for r in rows]
    return [
        {"name": n, "access": "ALL", "isolation": "UNKNOWN"}
        for n in names
        if n and n not in _INTERNAL and not str(n).startswith("__")
    ]
