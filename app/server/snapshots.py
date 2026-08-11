"""Optional persistence of assessment snapshots in Lakebase (for trending over time).

No-ops gracefully when Lakebase is disabled/unavailable so the app stays usable
as a stateless deployment.
"""

import json
import logging

from server.config import USE_LAKEBASE

logger = logging.getLogger(__name__)

_TABLE = "ontology_assessment_snapshots"


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
                    overall_score DOUBLE PRECISION,
                    overall_level INT,
                    scorecard JSONB NOT NULL
                )"""
        )


async def save_snapshot(scorecard: dict, created_by: str | None = None) -> int | None:
    """Persist a completed assessment; returns the new snapshot id (or None)."""
    if not await _enabled():
        return None
    try:
        from server.lakebase_client import get_pool
        pool = await get_pool()
        if pool is None:
            return None
        overall = scorecard.get("overall", {})
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"INSERT INTO {_TABLE} (created_by, overall_score, overall_level, scorecard) "
                f"VALUES ($1, $2, $3, $4) RETURNING id",
                created_by,
                float(overall.get("score") or 0),
                int(overall.get("level") or 0),
                json.dumps(scorecard),
            )
        return row["id"] if row else None
    except Exception as e:
        logger.warning(f"save_snapshot failed: {e}")
        return None


async def list_snapshots(created_by: str | None = None, limit: int = 50) -> list[dict]:
    """List a user's past assessment runs (metadata only), newest first.

    Scoped to `created_by` so each user only sees their own history.
    """
    if not await _enabled():
        return []
    try:
        from server.lakebase_client import get_pool
        pool = await get_pool()
        if pool is None:
            return []
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT id, created_at, created_by, overall_score, overall_level "
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
                "overall_score": r["overall_score"],
                "overall_level": r["overall_level"],
            }
            for r in rows
        ]
    except Exception as e:
        logger.warning(f"list_snapshots failed: {e}")
        return []


async def get_snapshot(snapshot_id: int, created_by: str | None = None) -> dict | None:
    """Return the full stored scorecard for one snapshot, scoped to the user."""
    if not await _enabled():
        return None
    try:
        from server.lakebase_client import get_pool
        pool = await get_pool()
        if pool is None:
            return None
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT id, created_at, created_by, scorecard FROM {_TABLE} "
                f"WHERE id = $1 AND created_by IS NOT DISTINCT FROM $2",
                snapshot_id,
                created_by,
            )
        if not row:
            return None
        scorecard = row["scorecard"]
        if isinstance(scorecard, str):
            scorecard = json.loads(scorecard)
        return {
            "id": row["id"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "created_by": row["created_by"],
            "scorecard": scorecard,
        }
    except Exception as e:
        logger.warning(f"get_snapshot failed: {e}")
        return None
