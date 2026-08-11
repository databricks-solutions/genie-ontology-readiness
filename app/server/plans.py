"""Per-user persistence of generated action plans in Lakebase.

Each plan is linked to exactly one assessment snapshot (``snapshot_id``) and is
scoped to the user who created it. No-ops gracefully when Lakebase is disabled so
the app still runs stateless.
"""

import logging

from server.config import USE_LAKEBASE

logger = logging.getLogger(__name__)

_TABLE = "ontology_plans"


async def _enabled() -> bool:
    if not USE_LAKEBASE:
        return False
    try:
        from server.lakebase_client import is_available
        return is_available()
    except Exception:
        return False


async def ensure_table() -> None:
    if not await _enabled():
        return
    from server.lakebase_client import get_pool
    pool = await get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            f"""CREATE TABLE IF NOT EXISTS {_TABLE} (
                    id BIGSERIAL PRIMARY KEY,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    created_by TEXT,
                    snapshot_id BIGINT,
                    title TEXT,
                    model TEXT,
                    plan_markdown TEXT NOT NULL
                )"""
        )


async def save_plan(
    created_by: str | None,
    snapshot_id: int | None,
    title: str,
    model: str,
    plan_markdown: str,
) -> int | None:
    """Persist a generated plan; returns the new plan id (or None if unavailable)."""
    if not await _enabled():
        return None
    try:
        from server.lakebase_client import get_pool
        pool = await get_pool()
        if pool is None:
            return None
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"INSERT INTO {_TABLE} (created_by, snapshot_id, title, model, plan_markdown) "
                f"VALUES ($1, $2, $3, $4, $5) RETURNING id",
                created_by,
                snapshot_id,
                title,
                model,
                plan_markdown,
            )
        return row["id"] if row else None
    except Exception as e:
        logger.warning(f"save_plan failed: {e}")
        return None


async def list_plans(created_by: str | None = None, limit: int = 50) -> list[dict]:
    """List a user's saved plans (metadata only), newest first."""
    if not await _enabled():
        return []
    try:
        from server.lakebase_client import get_pool
        pool = await get_pool()
        if pool is None:
            return []
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT id, created_at, created_by, snapshot_id, title, model "
                f"FROM {_TABLE} WHERE created_by IS NOT DISTINCT FROM $1 "
                f"ORDER BY created_at DESC LIMIT $2",
                created_by,
                limit,
            )
        return [
            {
                "id": r["id"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "created_by": r["created_by"],
                "snapshot_id": r["snapshot_id"],
                "title": r["title"],
                "model": r["model"],
            }
            for r in rows
        ]
    except Exception as e:
        logger.warning(f"list_plans failed: {e}")
        return []


async def get_plan(plan_id: int, created_by: str | None = None) -> dict | None:
    """Return one saved plan (including markdown), scoped to the user."""
    if not await _enabled():
        return None
    try:
        from server.lakebase_client import get_pool
        pool = await get_pool()
        if pool is None:
            return None
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT id, created_at, created_by, snapshot_id, title, model, plan_markdown "
                f"FROM {_TABLE} WHERE id = $1 AND created_by IS NOT DISTINCT FROM $2",
                plan_id,
                created_by,
            )
        if not row:
            return None
        return {
            "id": row["id"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "created_by": row["created_by"],
            "snapshot_id": row["snapshot_id"],
            "title": row["title"],
            "model": row["model"],
            "plan_markdown": row["plan_markdown"],
        }
    except Exception as e:
        logger.warning(f"get_plan failed: {e}")
        return None
