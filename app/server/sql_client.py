"""SQL Warehouse client using Databricks Statement Execution API."""

import aiohttp
import asyncio
import logging
from typing import Any, Optional
from server.config import get_workspace_host, get_auth_headers, WAREHOUSE_ID, CATALOG, SCHEMA

logger = logging.getLogger(__name__)


async def execute_sql(query: str, parameters: Optional[dict[str, Any]] = None, force_sp: bool = False) -> list[dict]:
    """Execute a SQL query against the Databricks SQL Warehouse.

    Uses the Statement Execution API:
    POST /api/2.0/sql/statements

    ``force_sp=True`` runs as the app service principal even when a forwarded
    end-user token is present — use it for system-table reads (system.access /
    system.query) that the SP is granted but an arbitrary viewer may not be.
    """
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
