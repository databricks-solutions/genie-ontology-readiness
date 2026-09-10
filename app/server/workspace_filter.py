"""Request-scoped workspace filter for the assessment.

The activity-based signals (Genie Agents, Adoption, Domains top-accessed) read
account-level system tables (``system.access.audit``, ``system.query.history``,
``system.access.table_lineage``) that all carry a ``workspace_id``. On a shared
metastore those reads would otherwise count every workspace in the account. The
UI lets the user pick which workspaces are in scope (searchable include/exclude,
defaulting to the deployed workspace); the assess route records that choice here
and the probes turn it into a parameterized SQL predicate.

Metastore-scoped signals (UC Foundation, Metadata, Relationships, Metrics, and
the Domains tag proxy) read ``information_schema``/tags, which are NOT
workspace-attributable — the filter does not apply to them, and the UI says so.

Scoped to the current task context exactly like the OBO token and progress sink:
the assess handler calls ``set_workspace_filter`` before dispatching probes, and
contextvars are copied into each probe task, so a probe reads the filter without
any change to its call signature.
"""

import contextvars

# Filter shape: {"mode": "include"|"exclude", "workspace_ids": [str, ...]} or None.
_workspace_filter: contextvars.ContextVar = contextvars.ContextVar("workspace_filter", default=None)


def set_workspace_filter(f: dict | None) -> None:
    """Record the workspace filter for the current request context.

    Normalizes to None when there's nothing to filter by (no ids), so the
    predicate helper is a clean no-op and every signal reads account-wide.
    """
    if not f:
        _workspace_filter.set(None)
        return
    ids = [str(x).strip() for x in (f.get("workspace_ids") or []) if str(x).strip()]
    if not ids:
        _workspace_filter.set(None)
        return
    mode = "exclude" if f.get("mode") == "exclude" else "include"
    _workspace_filter.set({"mode": mode, "workspace_ids": ids})


def get_workspace_filter() -> dict | None:
    """The active workspace filter, or None (all workspaces) when unset."""
    return _workspace_filter.get()


def is_multi_workspace() -> bool:
    """True when more than one workspace is explicitly in scope — the signal to
    add a per-workspace dimension to the activity drill-downs. An exclude filter
    (or no filter) leaves the scope open-ended, which also counts as multi."""
    f = _workspace_filter.get()
    if f is None:
        return True  # no filter → account-wide, so many workspaces
    if f["mode"] == "exclude":
        return True
    return len(f["workspace_ids"]) > 1


def workspace_predicate(column: str = "workspace_id", prefix: str = "wsf") -> tuple[str, dict]:
    """Build a parameterized ``AND ... IN/NOT IN (...)`` fragment for the active
    filter, plus the params dict to merge into the ``execute_sql`` call.

    Returns ("", {}) when no filter is active (all workspaces). The column is cast
    to STRING so a numeric ``workspace_id`` (audit/query.history/table_lineage) and
    the string ids from ``system.access.workspaces_latest`` compare consistently.
    A trailing space keeps it safe to interpolate mid-WHERE.
    """
    f = _workspace_filter.get()
    if not f:
        return "", {}
    ids = f["workspace_ids"]
    params = {f"{prefix}_{i}": v for i, v in enumerate(ids)}
    placeholders = ", ".join(f":{k}" for k in params)
    op = "NOT IN" if f["mode"] == "exclude" else "IN"
    return f"AND CAST({column} AS STRING) {op} ({placeholders}) ", params
