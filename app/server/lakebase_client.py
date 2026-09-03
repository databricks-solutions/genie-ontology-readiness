"""Lakebase (PostgreSQL-compatible) async client using asyncpg."""

import os
import time
import logging
import uuid
import asyncio
import aiohttp
from typing import Optional

logger = logging.getLogger(__name__)

# Will be set during app lifespan
_pool = None
_pool_created_at: float = 0.0
TOKEN_REFRESH_INTERVAL = 45 * 60  # Refresh pool every 45 minutes (tokens expire at 60 min)


async def _fetch_db_credential(instance_name: str) -> Optional[str]:
    """Fetch an instance-scoped database credential from the Databricks API.

    Calls POST /api/2.0/database/credentials with the given instance name,
    using the app service principal credentials. Returns the token string
    suitable as a Postgres password, or None on failure.

    The returned token is typically valid for ~60 minutes.
    """
    from server.config import get_workspace_host, get_auth_headers

    if not instance_name:
        logger.warning("_fetch_db_credential: instance_name is empty")
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

        url = f"{host}/api/2.0/database/credentials"
        request_id = str(uuid.uuid4())
        payload = {
            "request_id": request_id,
            "instance_names": [instance_name],
        }

        headers = {
            **auth_headers,
            "Content-Type": "application/json",
        }

        logger.info(f"Fetching instance-scoped Lakebase credential for instance '{instance_name}'")

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.warning(
                        f"Failed to fetch Lakebase credential (status {response.status}): {error_text[:200]}"
                    )
                    return None

                resp_json = await response.json()
                token = resp_json.get("token")
                if not token:
                    logger.warning(f"No token in Lakebase credential response: {resp_json}")
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


async def init_pool() -> None:
    """Create the asyncpg connection pool. Call during app startup."""
    global _pool, _pool_created_at
    try:
        import asyncpg

        config = _get_connection_config()
        password = os.environ.get("LAKEBASE_PASSWORD", "")

        # If no explicit password set and running in Databricks App, fetch instance-scoped credential
        if not password and os.environ.get("DATABRICKS_APP_NAME"):
            instance = os.environ.get("LAKEBASE_INSTANCE_NAME", "")
            if instance:
                password = await _fetch_db_credential(instance) or ""
            else:
                logger.warning(
                    "Running in Databricks App but LAKEBASE_INSTANCE_NAME not set; "
                    "no Lakebase instance credential available"
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
                ssl="require",
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
