"""/api/config reports the health facts the deploy checks use.

`warehouse_configured` says a SQL warehouse is bound. `history_ready` says the
Lakebase history pool started (not only that Lakebase is configured).
"""

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

from server import lakebase_client as lc  # noqa: E402
from server.routes import config as config_route  # noqa: E402


def _run(monkeypatch, *, warehouse: str, pool):
    async def _models():
        return []

    async def _default(_models):
        return ""

    monkeypatch.setattr(config_route, "list_available_models", _models)
    monkeypatch.setattr(config_route, "resolve_default_model", _default)
    monkeypatch.setattr(config_route, "WAREHOUSE_ID", warehouse)
    monkeypatch.setattr(lc, "_pool", pool)
    return asyncio.run(config_route.get_config())


def test_health_true_when_warehouse_bound_and_pool_started(monkeypatch):
    out = _run(monkeypatch, warehouse="abc123", pool=object())
    assert out["warehouse_configured"] is True
    assert out["history_ready"] is True


def test_health_false_when_warehouse_missing_and_no_pool(monkeypatch):
    out = _run(monkeypatch, warehouse="", pool=None)
    assert out["warehouse_configured"] is False
    assert out["history_ready"] is False
