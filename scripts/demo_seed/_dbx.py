"""Shared Databricks helpers for the demo-seed scripts.

Auth comes from the Databricks CLI config (a named profile) — no tokens are ever
read from or written to these files. Requires `databricks-sdk`.
"""

import os
import time

try:
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service.sql import StatementState
except Exception as e:  # pragma: no cover
    raise SystemExit(f"databricks-sdk is required: pip install 'databricks-sdk>=0.36.0'\n(import error: {e})")

# Defaults for THIS demo workspace; override via env or CLI flags for reuse.
DEFAULT_PROFILE = (
    os.environ.get("DATABRICKS_CONFIG_PROFILE")
    or os.environ.get("DATABRICKS_CLI_PROFILE")
    or os.environ.get("DATABRICKS_PROFILE")
    or "e2-demo-field-eng"
)
DEFAULT_WAREHOUSE = os.environ.get("DATABRICKS_WAREHOUSE_ID") or "abacc2038735a065"
DEFAULT_CATALOG = os.environ.get("GOR_DEMO_CATALOG") or "ontology_demo"

_TERMINAL = {StatementState.SUCCEEDED, StatementState.FAILED, StatementState.CANCELED, StatementState.CLOSED}


def get_client(profile: str) -> "WorkspaceClient":
    return WorkspaceClient(profile=profile)


class SqlError(RuntimeError):
    """A statement that reached a non-SUCCEEDED terminal state."""


def run_sql(w, warehouse_id: str, statement: str, timeout_s: int = 300):
    """Execute one SQL statement and return its rows (list[list[str]]).

    Polls to a terminal state; raises SqlError on failure with the sanitized
    server message.
    """
    resp = w.statement_execution.execute_statement(
        warehouse_id=warehouse_id,
        statement=statement,
        wait_timeout="50s",
    )
    stmt_id = resp.statement_id
    deadline = time.time() + timeout_s
    while resp.status and resp.status.state not in _TERMINAL:
        if time.time() > deadline:
            raise SqlError(f"timed out after {timeout_s}s (statement {stmt_id})")
        time.sleep(2)
        resp = w.statement_execution.get_statement(stmt_id)

    state = resp.status.state if resp.status else None
    if state != StatementState.SUCCEEDED:
        msg = ""
        if resp.status and resp.status.error:
            msg = resp.status.error.message or ""
        raise SqlError(f"{state}: {msg}")

    if resp.result and resp.result.data_array:
        return resp.result.data_array
    return []


def scalar(w, warehouse_id: str, statement: str):
    rows = run_sql(w, warehouse_id, statement)
    return rows[0][0] if rows and rows[0] else None
