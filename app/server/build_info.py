"""What build of the app is running: release version, Git commit, deployment.

Shown as a small watermark in the UI and returned by ``/api/config`` so a
screenshot or a copied error report says exactly which build it came from.

A Git deploy in the Apps UI has no ``.git``, so the commit comes from, in order:
1. ``GOR_GIT_SHA`` (set by a deploy pipeline, or by hand), then
2. the app's own active deployment, read once from the Apps API as the app's
   service principal (``GET /api/2.0/apps/{DATABRICKS_APP_NAME}``), then
3. ``git rev-parse`` (local development).
Every step is best-effort: when nothing is known the build is just the version.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from typing import Optional

import aiohttp

from server.config import get_auth_headers, get_workspace_host
from server.user_agent import PRODUCT_VERSION, with_ua

logger = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=5)
_cached: Optional[dict] = None
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
        url = f"{get_workspace_host()}/api/2.0/apps/{app_name}"
        headers = get_auth_headers(force_sp=True)
        async with aiohttp.ClientSession(timeout=_TIMEOUT, headers=with_ua()) as s:
            async with s.get(url, headers=headers) as r:
                if r.status != 200:
                    logger.info("build info: apps get %s returned HTTP %s", app_name, r.status)
                    return {}
                return (await r.json()).get("active_deployment") or {}
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
        info.update(
            deployment_id=dep.get("deployment_id"),
            deployed_at=dep.get("update_time") or dep.get("create_time"),
            ref=git.get("tag") or git.get("branch"),
        )
        if not info["commit"]:
            sha = git.get("resolved_commit") or git.get("commit")
            if sha:
                info.update(commit=sha, source="deployment")
    if not info["commit"]:
        sha = _git_sha()
        if sha:
            info.update(commit=sha, source="git")
    info["commit_short"] = _short(info["commit"])
    return info


async def get_build_info() -> dict:
    """Resolved once per process (a deploy restarts the app), then cached."""
    global _cached
    if _cached is None:
        async with _lock:
            if _cached is None:
                _cached = await _resolve()
                logger.info("Build: %s (commit %s from %s, deployment %s)", build_label(_cached),
                            _cached["commit"], _cached["source"], _cached["deployment_id"])
    return _cached


def build_label(info: dict) -> str:
    """Short human label, e.g. ``v1.4.0 · b5f83a7``."""
    parts = [f"v{info.get('version')}"]
    if info.get("commit_short"):
        parts.append(info["commit_short"])
    return " · ".join(parts)
