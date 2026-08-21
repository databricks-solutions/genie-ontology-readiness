"""Configuration and authentication for Databricks SQL Warehouse, Lakebase, and Genie.

This app reads the *customer's existing* environment to assess Genie Ontology
readiness. It does not seed demo data.

Auth is on-behalf-of-user (OBO) by default:
  - When Databricks Apps **user authorization** is enabled and a user token is
    forwarded (``x-forwarded-access-token``), every signal runs as the
    **logged-in user**, inheriting their Unity Catalog permissions — so the app
    SP need not be granted on every catalog. The per-request token is held in a
    contextvar.
  - If an OBO read fails (e.g. the viewer lacks a grant the SP holds, such as
    system tables), ``execute_sql`` transparently **falls back to the app SP**.
  - ``get_auth_headers(force_sp=True)`` is the SP-only override, for reads the
    OBO token can't perform (Genie REST API — not covered by the ``sql`` scope;
    Lakebase credential minting — the SP owns the Postgres role).
  - With no forwarded token (local dev / unattended / scheduled), reads run as
    the SP directly.
"""

import os
import logging
import contextvars
from databricks.sdk import WorkspaceClient

logger = logging.getLogger(__name__)

# Per-request forwarded end-user token (Databricks Apps on-behalf-of-user auth).
# Set by the request handler from the ``x-forwarded-access-token`` header; unset
# (None) for local dev or when user authorization isn't enabled → SP is used.
_user_token: contextvars.ContextVar = contextvars.ContextVar("user_token", default=None)


def set_user_token(token: str | None) -> None:
    """Record the forwarded end-user token for the current request context."""
    _user_token.set(token or None)


def get_user_token() -> str | None:
    """The forwarded end-user token for this request, or None when OBO is not the
    effective identity.

    Under the deploy-time ``FORCE_SP`` override, OBO is disabled app-wide, so we
    report None even if a token was forwarded. This makes FORCE_SP a single source
    of truth: every identity decision keyed off this — auth headers (SQL *and* REST
    reads via get_auth_headers), the SP source-resolution cache, and the
    identity-aware user messaging — treats the SP as the identity.
    """
    if FORCE_SP:
        return None
    return _user_token.get()

# Detect if running inside Databricks Apps
IS_DATABRICKS_APP = bool(os.environ.get("DATABRICKS_APP_NAME"))

# SQL Warehouse ID (used for all assessment queries)
WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")

# Default catalog/schema context for the Statement Execution API. Assessment
# queries are fully qualified (system.information_schema.*), so these are just
# a sane default execution context.
CATALOG = os.environ.get("CATALOG_NAME", "system")
SCHEMA = os.environ.get("SCHEMA_NAME", "information_schema")

# Comma-separated list of catalogs to assess. Empty = assess all catalogs the
# SP can see (excluding system/internal catalogs).
ASSESS_CATALOGS = [
    c.strip() for c in os.environ.get("ASSESS_CATALOGS", "").split(",") if c.strip()
]

# Deploy-time identity override. When true, EVERY read runs as the app service
# principal and OBO is never attempted, even if a viewer token is forwarded.
# Off by default (OBO-first with SP fallback — see sql_client.execute_sql). Set
# via the FORCE_SP env in app.yml, e.g. to guarantee consistent, workspace-wide
# system-table signals regardless of the viewer's grants.
FORCE_SP = os.environ.get("FORCE_SP", "false").lower() == "true"

# Lakebase toggle — only used to persist assessment snapshots over time.
# Defaults OFF so the app deploys stateless into any workspace.
USE_LAKEBASE = os.environ.get("USE_LAKEBASE", "false").lower() == "true"

# Workspace id (for building deep links into the customer's workspace UI)
WORKSPACE_ID = os.environ.get("WORKSPACE_ID", "")

# Optional Genie agent to test answer quality against (pillar 5)
GENIE_SPACE_ID = os.environ.get("GENIE_SPACE_ID", "")

# Cache workspace client to avoid repeated creation
_workspace_client = None


def _get_workspace_client() -> WorkspaceClient:
    """Get or create a cached WorkspaceClient."""
    global _workspace_client
    if _workspace_client is None:
        if IS_DATABRICKS_APP:
            logger.info("Creating WorkspaceClient for Databricks App environment")
            _workspace_client = WorkspaceClient()
        else:
            profile = os.environ.get("DATABRICKS_CLI_PROFILE", "")
            logger.info(f"Creating WorkspaceClient with profile: {profile}")
            _workspace_client = WorkspaceClient(profile=profile)
        logger.info(f"WorkspaceClient host: {_workspace_client.config.host}")
        logger.info(f"WorkspaceClient auth_type: {_workspace_client.config.auth_type}")
    return _workspace_client


def get_workspace_host() -> str:
    """Get workspace host URL with https:// prefix."""
    host = os.environ.get("DATABRICKS_HOST", "")
    if host:
        if not host.startswith("http"):
            host = f"https://{host}"
        return host

    try:
        w = _get_workspace_client()
        host = w.config.host or ""
        if host and not host.startswith("http"):
            host = f"https://{host}"
        return host
    except Exception as e:
        logger.error(f"Could not get workspace host: {e}")
        return ""


def get_auth_headers(force_sp: bool = False) -> dict:
    """Get authorization headers -- works locally and in Databricks Apps.

    Returns a dict like {"Authorization": "Bearer <token>"}.

    On-behalf-of-user: unless ``force_sp`` is set (or the ``FORCE_SP`` deploy knob
    is on, which get_user_token() reflects), a forwarded end-user token is used so
    the read runs with the viewer's permissions. ``force_sp=True`` always uses the
    app service principal (the SP-only override — e.g. Genie REST / Lakebase). The
    OBO-first-with-SP-fallback policy for SQL lives in ``sql_client.execute_sql``.
    """
    # On-behalf-of-user (Databricks Apps user authorization). get_user_token()
    # returns None under the FORCE_SP override, so REST reads that call this
    # directly (Genie/domains/serving) also honor SP-only mode — not just execute_sql.
    if not force_sp:
        user_token = get_user_token()
        if user_token:
            return {"Authorization": f"Bearer {user_token}"}

    # Direct token (local dev or injected)
    token = os.environ.get("DATABRICKS_TOKEN")
    if token:
        return {"Authorization": f"Bearer {token}"}

    # OAuth via SDK (Databricks Apps service principal)
    try:
        w = _get_workspace_client()
        if w.config.token:
            return {"Authorization": f"Bearer {w.config.token}"}
        headers = w.config.authenticate()
        if headers:
            return headers
    except Exception as e:
        logger.error(f"Could not get auth headers: {e}")

    return {}
