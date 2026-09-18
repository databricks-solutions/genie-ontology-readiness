"""Lakebase (PostgreSQL-compatible) async client using asyncpg."""

import os
import time
import logging
import ssl
import uuid
import asyncio
import aiohttp
from typing import Optional

from server.user_agent import with_ua

logger = logging.getLogger(__name__)

# Will be set during app lifespan
_pool = None
_pool_created_at: float = 0.0
TOKEN_REFRESH_INTERVAL = 45 * 60  # Refresh pool every 45 minutes (tokens expire at 60 min)


async def _fetch_db_credential(instance_name: str = "", endpoint_name: str = "") -> Optional[str]:
    """Fetch a Lakebase database credential from the Databricks API.

    Autoscaling uses POST /api/2.0/postgres/credentials with an endpoint resource
    name. Provisioned Lakebase uses POST /api/2.0/database/credentials with an
    instance name. Both calls use the app service principal credentials.

    The returned token is typically valid for ~60 minutes.
    """
    from server.config import get_workspace_host, get_auth_headers

    if not endpoint_name and not instance_name:
        logger.warning("_fetch_db_credential: endpoint_name and instance_name are empty")
        return None

    try:
        host = get_workspace_host()
        if not host:
            logger.warning("_fetch_db_credential: could not resolve workspace host")
            return None

        # Use SP credentials (force_sp=True) since the SP owns the Postgres role
        auth_headers = get_auth_headers(force_sp=True)
        if not auth_headers:
            logger.warning("_fetch_db_credential: could not obtain auth headers")
            return None

        if endpoint_name:
            url = f"{host}/api/2.0/postgres/credentials"
            payload = {"endpoint": endpoint_name}
            resource = f"endpoint '{endpoint_name}'"
        else:
            url = f"{host}/api/2.0/database/credentials"
            payload = {
                "request_id": str(uuid.uuid4()),
                "instance_names": [instance_name],
            }
            resource = f"instance '{instance_name}'"

        headers = {
            **auth_headers,
            "Content-Type": "application/json",
        }

        logger.info(f"Fetching SP-scoped Lakebase credential for {resource}")

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10), headers=with_ua()) as session:
            async with session.post(url, json=payload, headers=headers) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.warning(
                        f"Failed to fetch Lakebase credential (status {response.status}): {error_text[:200]}"
                    )
                    return None

                resp_json = await response.json()
                token = resp_json.get("token")
                if not token:
                    # Log the response SHAPE only. The body of a credentials
                    # response is credential material by definition, and logging
                    # it verbatim wrote a live secret to the app log (CWE-532).
                    logger.warning("No token in Lakebase credential response (keys: %s)",
                                   sorted(resp_json.keys()) if isinstance(resp_json, dict) else type(resp_json).__name__)
                    return None

                logger.info("Successfully obtained instance-scoped Lakebase credential")
                return token

    except asyncio.TimeoutError:
        logger.warning("Timeout fetching Lakebase credential")
        return None
    except Exception as e:
        logger.warning(f"Error fetching Lakebase credential: {e}")
        return None


def _get_connection_config() -> dict:
    """Read Lakebase connection config from environment variables.

    Supports two modes:
    1. Database resource (PGHOST/PGUSER injected by Databricks Apps) + OAuth token
    2. Explicit LAKEBASE_* env vars (legacy / secrets-based)

    Returns the sync-readable portion (host/port/database/user).
    Password will be fetched asynchronously in init_pool() if needed.
    """
    host = os.environ.get("LAKEBASE_HOST") or os.environ.get("PGHOST", "localhost")
    port = int(os.environ.get("LAKEBASE_PORT") or os.environ.get("PGPORT", "5432"))
    database = os.environ.get("LAKEBASE_DATABASE") or os.environ.get("PGDATABASE", "ontology_readiness")
    user = os.environ.get("LAKEBASE_USER") or os.environ.get("PGUSER", "")

    return {
        "host": host,
        "port": port,
        "database": database,
        "user": user,
    }


# `require` encrypts the connection but performs NO certificate or hostname check,
# so it does not protect the assessment history against an in-path attacker.
# `verify-full` is the default here; the escape hatch exists only for a deployment
# whose Lakebase endpoint presents a certificate outside the container's CA bundle.
LAKEBASE_SSL_MODE = os.environ.get("LAKEBASE_SSL_MODE", "verify-full")


def _ssl_arg():
    """The asyncpg ``ssl`` argument for LAKEBASE_SSL_MODE.

    asyncpg's string modes ``verify-ca``/``verify-full`` look for a CA cert at
    ``~/.postgresql/root.crt``, which the Databricks Apps runtime does not ship — so
    passing the bare string makes pool creation fail and silently disables history.
    The Lakebase endpoint presents a publicly-signed certificate, so for the verify
    modes we build an SSLContext from a trusted CA bundle (certifi if present, else
    the system store) and let asyncpg verify against it: real certificate (and, for
    verify-full, hostname) verification without needing a bundled root.crt. The
    non-verifying modes (``require``/``prefer``/``allow``/``disable``) pass through
    to asyncpg as-is."""
    mode = (LAKEBASE_SSL_MODE or "").lower()
    if mode not in ("verify-ca", "verify-full"):
        return LAKEBASE_SSL_MODE
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()
    if mode == "verify-ca":
        # verify the chain but not the hostname (verify-full does both).
        ctx.check_hostname = False
    return ctx


async def init_pool() -> None:
    """Create the asyncpg connection pool. Call during app startup."""
    global _pool, _pool_created_at
    try:
        import asyncpg

        config = _get_connection_config()
        password = os.environ.get("LAKEBASE_PASSWORD", "")

        # If no explicit password is set in a Databricks App, mint a credential
        # as the app service principal. Prefer Autoscaling endpoint credentials;
        # retain Provisioned instance credentials for existing deployments.
        if not password and os.environ.get("DATABRICKS_APP_NAME"):
            endpoint = os.environ.get("LAKEBASE_ENDPOINT_NAME", "")
            instance = os.environ.get("LAKEBASE_INSTANCE_NAME", "")
            if endpoint or instance:
                password = await _fetch_db_credential(
                    instance_name=instance,
                    endpoint_name=endpoint,
                ) or ""
            else:
                logger.warning(
                    "Running in Databricks App but neither LAKEBASE_ENDPOINT_NAME "
                    "nor LAKEBASE_INSTANCE_NAME is set; no Lakebase credential available"
                )

        # If still no password, log and skip pool creation
        if not password:
            logger.warning(
                "Lakebase password not set and no instance credential available — "
                "assessment history will not persist"
            )
            _pool = None
            return

        logger.info(f"Connecting to Lakebase at {config['host']}:{config['port']}/{config['database']}")
        _pool = await asyncio.wait_for(
            asyncpg.create_pool(
                host=config["host"],
                port=config["port"],
                database=config["database"],
                user=config["user"],
                password=password,
                ssl=_ssl_arg(),
                min_size=2,
                max_size=10,
                command_timeout=30,
            ),
            timeout=10,  # Don't hang for more than 10s on pool creation
        )
        _pool_created_at = time.time()
        logger.info("Lakebase connection pool initialized successfully")
    except Exception as e:
        logger.warning(f"Failed to initialize Lakebase pool (will fall back to SQL Warehouse): {e}")
        _pool = None


async def _refresh_pool_if_needed() -> None:
    """Recreate pool if OAuth token is near expiry (~45 min)."""
    global _pool, _pool_created_at
    if _pool is None or _pool_created_at == 0:
        return
    elapsed = time.time() - _pool_created_at
    if elapsed < TOKEN_REFRESH_INTERVAL:
        return
    logger.info(f"Refreshing Lakebase pool (age: {elapsed/60:.0f} min)")
    try:
        old_pool = _pool
        _pool = None
        await old_pool.close()
    except Exception:
        pass
    await init_pool()


async def close_pool() -> None:
    """Close the connection pool. Call during app shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("Lakebase connection pool closed")


def is_available() -> bool:
    """Check if Lakebase pool is available."""
    return _pool is not None


async def get_pool():
    """Get the connection pool, refreshing the token if needed. Returns None if unavailable."""
    await _refresh_pool_if_needed()
    return _pool


async def execute_query(query: str, params: Optional[list] = None) -> list[dict]:
    """Execute a SQL query against Lakebase and return results as list of dicts."""
    await _refresh_pool_if_needed()

    if _pool is None:
        raise RuntimeError("Lakebase pool not initialized")

    async with _pool.acquire() as conn:
        if params:
            rows = await conn.fetch(query, *params)
        else:
            rows = await conn.fetch(query)
        return [dict(row) for row in rows]
