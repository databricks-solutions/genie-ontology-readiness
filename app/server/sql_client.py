"""SQL Warehouse client using Databricks Statement Execution API."""

import aiohttp
import asyncio
import logging
from typing import Any, Optional
from server.config import get_workspace_host, get_auth_headers, get_user_token, FORCE_SP, WAREHOUSE_ID, CATALOG, SCHEMA

logger = logging.getLogger(__name__)


async def execute_sql(query: str, parameters: Optional[dict[str, Any]] = None, force_sp: bool = False) -> list[dict]:
    """Execute a SQL query against the Databricks SQL Warehouse.

    Identity model — every signal defaults to on-behalf-of-user (OBO):

    * ``force_sp=True`` is the **override**: run as the app service principal and
      never attempt OBO (for reads the OBO token can't do — e.g. the Genie REST
      API, which the ``sql`` scope doesn't cover, or Lakebase credential minting).
    * Otherwise, when a forwarded end-user token is present, run **as the viewer**
      (OBO). If that read fails (typically because the viewer lacks a grant the
      app SP holds — e.g. system tables), transparently **fall back to the SP**.
    * With no forwarded token (local dev / unattended / scheduled), there is no
      OBO identity, so the read runs as the SP directly.
    * The deploy-time ``FORCE_SP`` knob (config) forces SP-only mode for the whole
      app — OBO is never attempted — regardless of a forwarded token.

    NOTE: the SP fallback trades a little of the "reflects exactly what *you* can
    see" OBO promise for completeness — a viewer may see a signal via the SP that
    they couldn't read themselves. That is the intended behaviour here.
    """
    # Override: SP only, no OBO attempt, no fallback. Either the per-call override
    # (force_sp) or the deploy-time FORCE_SP knob (SP-only mode for the whole app).
    if force_sp or FORCE_SP:
        return await _execute_once(query, parameters, force_sp=True)

    # No forwarded viewer token → nothing to run OBO as; go straight to the SP.
    if not get_user_token():
        return await _execute_once(query, parameters, force_sp=True)

    # Default: try OBO (as the viewer). Fall back to the SP only when the failure
    # looks like an AUTHORIZATION gap (the case the SP can actually cover). Other
    # failures (timeout, bad SQL, warehouse down) would fail identically as the SP,
    # so re-raise them instead of doubling latency with a pointless retry.
    try:
        return await _execute_once(query, parameters, force_sp=False)
    except Exception as e:
        if not _is_authz_error(e):
            raise
        logger.warning(f"OBO read denied ({str(e)[:160]}); falling back to app SP")
        return await _execute_once(query, parameters, force_sp=True)


# Substrings that mark a failure as an authorization/permission problem — the only
# case where retrying as the app SP can succeed. Matched case-insensitively against
# the SQL Warehouse / Statement Execution error text.
_AUTHZ_MARKERS = (
    "permission_denied", "does not have", "not authorized", "access denied",
    "forbidden", "insufficient priv", "unauthorized", " 403", "requires", "no permission",
)


def _is_authz_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(m in msg for m in _AUTHZ_MARKERS)


async def _execute_once(query: str, parameters: Optional[dict[str, Any]], force_sp: bool) -> list[dict]:
    """Run the query once with a single resolved identity (OBO or SP)."""
    host = get_workspace_host()
    auth_headers = get_auth_headers(force_sp=force_sp)

    if not host:
        raise Exception("DATABRICKS_HOST not configured")
    if not auth_headers:
        raise Exception("No authentication headers available")

    url = f"{host}/api/2.0/sql/statements"
    headers = {
        **auth_headers,
        "Content-Type": "application/json",
    }

    logger.info(f"Executing SQL against warehouse {WAREHOUSE_ID}, host: {host}")

    payload = {
        "warehouse_id": WAREHOUSE_ID,
        "statement": query,
        "catalog": CATALOG,
        "schema": SCHEMA,
        "wait_timeout": "50s",
        "disposition": "INLINE",
        "format": "JSON_ARRAY",
    }

    if parameters:
        params_list = []
        for name, value in parameters.items():
            param_type = "STRING"
            if isinstance(value, int):
                param_type = "INT"
            elif isinstance(value, float):
                param_type = "DOUBLE"
            params_list.append({"name": name, "value": str(value), "type": param_type})
        payload["parameters"] = params_list

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as response:
            if response.status != 200:
                error_text = await response.text()
                logger.error(f"SQL Warehouse error ({response.status}): {error_text}")
                raise Exception(f"SQL Warehouse error ({response.status}): {error_text}")

            result = await response.json()

            status = result.get("status", {}).get("state", "")
            if status == "FAILED":
                error_msg = result.get("status", {}).get("error", {}).get("message", "Unknown error")
                logger.error(f"SQL query failed: {error_msg}")
                raise Exception(f"SQL query failed: {error_msg}")

            # Handle PENDING/RUNNING -- poll until complete
            if status in ("PENDING", "RUNNING"):
                statement_id = result.get("statement_id")
                result = await _poll_statement(session, host, auth_headers, statement_id)

            return _parse_result(result)


async def _poll_statement(session: aiohttp.ClientSession, host: str, auth_headers: dict, statement_id: str) -> dict:
    """Poll for statement completion."""
    url = f"{host}/api/2.0/sql/statements/{statement_id}"

    for _ in range(120):  # up to 2 minutes
        await asyncio.sleep(1)
        async with session.get(url, headers=auth_headers) as resp:
            result = await resp.json()
            state = result.get("status", {}).get("state", "")
            if state == "SUCCEEDED":
                return result
            elif state == "FAILED":
                error_msg = result.get("status", {}).get("error", {}).get("message", "Unknown")
                raise Exception(f"SQL query failed: {error_msg}")
            elif state in ("CANCELED", "CLOSED"):
                raise Exception(f"SQL query {state.lower()}")

    raise Exception("SQL query timed out after 120 seconds")


def _parse_result(result: dict) -> list[dict]:
    """Parse Statement Execution API response into list of dicts."""
    manifest = result.get("manifest", {})
    columns = manifest.get("schema", {}).get("columns", [])
    col_names = [c["name"] for c in columns]

    data_array = result.get("result", {}).get("data_array", [])

    rows = []
    for row_data in data_array:
        row = {}
        for i, col_name in enumerate(col_names):
            row[col_name] = row_data[i] if i < len(row_data) else None
        rows.append(row)

    return rows
