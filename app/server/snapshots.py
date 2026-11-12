"""Optional persistence of assessment snapshots in Lakebase (for trending over time).

No-ops gracefully when Lakebase is disabled/unavailable so the app stays usable
as a stateless deployment.
"""

import json
import logging

from server.config import USE_LAKEBASE
from server.scope import scope_hash

logger = logging.getLogger(__name__)

_TABLE = "ontology_assessment_snapshots"

# Whether the #89 scope columns exist. Set by ensure_table(); if the migration fails
# (e.g. the app SP doesn't own an older table), reads and saves fall back to the
# pre-#89 columns so history keeps working, just without scopes.
_scope_columns = True


def _table() -> str:
    """The schema-qualified history table (the schema is chosen at startup)."""
    from server.lakebase_client import history_table
    return history_table(_TABLE)


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
            f"""CREATE TABLE IF NOT EXISTS {_table()} (
                    id BIGSERIAL PRIMARY KEY,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    created_by TEXT,
                    overall_score DOUBLE PRECISION,
                    overall_level INT,
                    scorecard JSONB NOT NULL
                )"""
        )
        # #89: the scope each run covered. Added in place so an existing table keeps
        # its rows; those older rows have a NULL scope ("unknown scope"). A failed
        # migration must not stop the app from starting or hide history: fall back
        # to the pre-#89 columns (no scopes) until it is fixed.
        global _scope_columns
        try:
            await conn.execute(
                f"ALTER TABLE {_table()} "
                f"ADD COLUMN IF NOT EXISTS scope JSONB, "
                f"ADD COLUMN IF NOT EXISTS scope_hash TEXT"
            )
            _scope_columns = True
        except Exception as e:
            # The columns may already exist (added earlier by another owner). Check the
            # exact table the app uses (its schema-qualified name), not any table called
            # the same name in another schema.
            try:
                n = await conn.fetchval(
                    "SELECT count(*) FROM pg_attribute "
                    "WHERE attrelid = $1::text::regclass "
                    "AND attname IN ('scope', 'scope_hash') AND NOT attisdropped",
                    _table(),
                )
            except Exception:
                n = 0
            _scope_columns = n == 2
            if not _scope_columns:
                logger.warning(f"could not add scope columns to {_table()}; history will not record scopes: {e}")


def _scope_select() -> str:
    """The extra SELECT columns for scopes (empty when the migration failed)."""
    return ", scope, scope_hash" if _scope_columns else ""


def _scope_fields(row) -> dict:
    """The stored scope of a row as API fields (``None`` for pre-#89 snapshots)."""
    if not _scope_columns:
        return {"scope": None, "scope_hash": None}
    scope = row["scope"]
    if isinstance(scope, str):
        scope = json.loads(scope)
    return {"scope": scope, "scope_hash": row["scope_hash"]}


async def save_snapshot(scorecard: dict, created_by: str | None = None,
                        scope: dict | None = None) -> int | None:
    """Persist a completed assessment; returns the new snapshot id (or None).

    ``scope`` is the normalized request scope (``server.scope.normalize_scope``)."""
    if not await _enabled():
        return None
    try:
        from server.lakebase_client import get_pool
        pool = await get_pool()
        if pool is None:
            return None
        overall = scorecard.get("overall", {})
        values = [created_by, float(overall.get("score") or 0), int(overall.get("level") or 0),
                  json.dumps(scorecard)]
        cols = "created_by, overall_score, overall_level, scorecard"
        if _scope_columns:
            cols += ", scope, scope_hash"
            values += [json.dumps(scope) if scope is not None else None, scope_hash(scope)]
        params = ", ".join(f"${i}" for i in range(1, len(values) + 1))
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"INSERT INTO {_table()} ({cols}) VALUES ({params}) RETURNING id", *values
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
                f"SELECT id, created_at, created_by, overall_score, overall_level{_scope_select()} "
                f"FROM {_table()} WHERE lower(created_by) IS NOT DISTINCT FROM lower($1) "
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
                **_scope_fields(r),
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
                f"SELECT id, created_at, created_by, scorecard{_scope_select()} FROM {_table()} "
                f"WHERE id = $1 AND lower(created_by) IS NOT DISTINCT FROM lower($2)",
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
            **_scope_fields(row),
        }
    except Exception as e:
        logger.warning(f"get_snapshot failed: {e}")
        return None
