"""What build of the app is running: release version, Git commit, deployment.

Shown as a small watermark in the UI and returned by ``/api/config`` so a
screenshot or a copied error report says exactly which build it came from.

A Git deploy in the Apps UI has no ``.git``, so the commit comes from, in order:
1. ``GOR_GIT_SHA`` (set by a deploy pipeline, or by hand), then
2. the app's own active deployment, read once from the Apps API as the app's
   service principal (``GET /api/2.0/apps/{DATABRICKS_APP_NAME}``), then
3. ``git rev-parse`` (local development).
Every step is best-effort: when nothing is known the build is just the version.

Slice 1 of the #60 SDK migration. Discover data-domains (``/api/2.0/domains``),
FMAPI SSE chat streaming, and autoscaling Lakebase postgres credentials remain
on raw HTTP due to SDK gaps / streaming and are out of scope.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from typing import Optional

from server._async import run_sync
from server.config import _get_workspace_client
from server.user_agent import PRODUCT_VERSION

logger = logging.getLogger(__name__)

# A lookup that found no commit is retried after this long (a transient Apps
# API failure at startup must not hide the commit until the next restart).
_RETRY_SECONDS = 60.0
_cached: Optional[dict] = None
_cached_at = 0.0
_lock = asyncio.Lock()


def _short(sha: Optional[str]) -> Optional[str]:
    return sha[:7] if sha else None


def _git_sha() -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=2,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    sha = out.stdout.strip()
    return sha if out.returncode == 0 and len(sha) >= 7 else None


async def _active_deployment(app_name: str) -> dict:
    """The app's own active deployment, or {} (no access, not on Apps, timeout)."""
    try:
        # Client/auth initialization and the SDK request can both block.
        app = await run_sync(lambda: _get_workspace_client().apps.get(app_name))
        return app.active_deployment.as_dict() if app and app.active_deployment else {}
    except Exception as e:  # never block the UI on this
        logger.info("build info: could not read the active deployment: %s", type(e).__name__)
        return {}


async def _resolve() -> dict:
    info: dict = {
        "version": PRODUCT_VERSION,
        "commit": None,
        "commit_short": None,
        "ref": None,
        "deployment_id": None,
        "deployed_at": None,
        "source": None,
    }
    env_sha = os.environ.get("GOR_GIT_SHA", "").strip() or None
    if env_sha:
        info.update(commit=env_sha, source="env")
    app_name = os.environ.get("DATABRICKS_APP_NAME", "").strip()
    if app_name:
        dep = await _active_deployment(app_name)
        git = dep.get("git_source") or {}
        dep_sha = git.get("resolved_commit") or git.get("commit")
        info.update(deployment_id=dep.get("deployment_id"),
                    deployed_at=dep.get("update_time") or dep.get("create_time"))
        if not info["commit"] and dep_sha:
            info.update(commit=dep_sha, source="deployment")
        if info["source"] == "deployment":
            # The branch/tag describes the deployment's commit, so only pair them then.
            info["ref"] = git.get("tag") or git.get("branch")
        elif env_sha and dep_sha and not dep_sha.startswith(env_sha) and not env_sha.startswith(dep_sha):
            logger.warning("build info: GOR_GIT_SHA %s differs from the deployed commit %s", env_sha, dep_sha)
    if not info["commit"]:
        sha = await asyncio.to_thread(_git_sha)
        if sha:
            info.update(commit=sha, source="git")
    info["commit_short"] = _short(info["commit"])
    return info


def _fresh(now: float) -> bool:
    # A result with a commit is final for the process (a deploy restarts it);
    # one without is retried after _RETRY_SECONDS.
    return _cached is not None and (_cached["commit"] is not None or now - _cached_at < _RETRY_SECONDS)


async def get_build_info() -> dict:
    """The running build, resolved lazily and cached (see ``_fresh``)."""
    global _cached, _cached_at
    loop = asyncio.get_running_loop()
    if not _fresh(loop.time()):
        async with _lock:
            if not _fresh(loop.time()):
                _cached = await _resolve()
                _cached_at = loop.time()
                logger.info("Build: %s (commit %s from %s, deployment %s)", build_label(_cached),
                            _cached["commit"], _cached["source"], _cached["deployment_id"])
    return _cached


def build_label(info: dict) -> str:
    """Short human label, e.g. ``v1.4.0 · b5f83a7``."""
    parts = [f"v{info.get('version')}"]
    if info.get("commit_short"):
        parts.append(info["commit_short"])
    return " · ".join(parts)
