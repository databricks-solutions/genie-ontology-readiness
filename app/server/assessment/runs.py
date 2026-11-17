"""Background assessment runs that outlive a single HTTP request.

The Databricks Apps proxy cuts a streamed response at about 300 seconds and
expects bytes at least every ~10 seconds. A full assessment on a large account
(hundreds of catalogs) can take longer than that. When one request carried the
whole run, the proxy cut the stream, the browser showed "network error", and
the probes were cancelled with the request.

So a run now executes as a background task. The browser reads its events in
short SSE windows (below the proxy cap), with keepalive comments while the
probes work. When a window closes, the browser reconnects with the sequence
number of the next event it needs, and the stream replays from there.

The registry is in process memory. The app runs one uvicorn process, so every
request reaches the same registry. A restart drops in-flight runs; the events
endpoint then returns 404 and the UI says to start a new run.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

from server import snapshots
from server.assessment.scoring import run_assessment_stream
from server.config import set_user_token
from server.security import safe_error
from server.workspace_filter import set_catalog_scope, set_schema_scope, set_workspace_filter

logger = logging.getLogger(__name__)

# One SSE window. Below the Apps proxy's ~300s cut, with margin.
STREAM_WINDOW_SECONDS = 240.0
# Keepalive interval. The proxy expects bytes at least every ~10s.
HEARTBEAT_SECONDS = 8.0
# How long a finished run stays readable (for a late reconnect).
FINISHED_TTL_SECONDS = 15 * 60
# A run still working after this long is cancelled. The metadata scan has its
# own no-progress timeout, so this only catches a run that is truly stuck.
MAX_RUN_SECONDS = 45 * 60


@dataclass
class AssessmentRun:
    id: str
    principal: str
    started_at: float = field(default_factory=time.monotonic)
    finished_at: Optional[float] = None
    events: list[dict] = field(default_factory=list)
    task: Optional[asyncio.Task] = None
    _changed: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def done(self) -> bool:
        return self.finished_at is not None

    def elapsed(self) -> float:
        return (self.finished_at or time.monotonic()) - self.started_at

    def append(self, event: dict) -> None:
        self.events.append(event)
        self._notify()

    def finish(self) -> None:
        if self.finished_at is None:
            self.finished_at = time.monotonic()
        self._notify()

    def _notify(self) -> None:
        # Wake every reader waiting on the current event, then arm a fresh one.
        # asyncio is single-threaded and readers take the event with no await
        # between their check and the wait, so no update is missed.
        self._changed.set()
        self._changed = asyncio.Event()

    async def wait_for_change(self, timeout: float) -> bool:
        changed = self._changed
        try:
            await asyncio.wait_for(changed.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False


_runs: dict[str, AssessmentRun] = {}


def _sweep() -> None:
    """Forget finished runs past their TTL and cancel runs past the hard cap."""
    now = time.monotonic()
    for run_id, run in list(_runs.items()):
        if run.done and now - (run.finished_at or now) > FINISHED_TTL_SECONDS:
            _runs.pop(run_id, None)
        elif not run.done and now - run.started_at > MAX_RUN_SECONDS and run.task:
            run.task.cancel()


def get_run(run_id: str, principal: str) -> Optional[AssessmentRun]:
    """The run, if it exists and belongs to ``principal`` (else None, so one
    user can't read another user's run by guessing an id)."""
    run = _runs.get(run_id)
    if run is None or run.principal != principal:
        return None
    return run


def cancel_run(run_id: str, principal: str) -> bool:
    run = get_run(run_id, principal)
    if run is None:
        return False
    if run.task and not run.task.done():
        run.task.cancel()
    return True


def start_run(
    principal: str,
    user_token: Optional[str],
    workspace_filter: Optional[dict],
    catalogs: Optional[list[str]],
    schemas: Optional[list[str]],
) -> AssessmentRun:
    """Start an assessment in the background and return its handle.

    A user's earlier run that is still working is cancelled first: the UI shows
    one run at a time, and leftover runs would compete for the warehouse."""
    _sweep()
    for other in _runs.values():
        if other.principal == principal and not other.done and other.task:
            other.task.cancel()

    run = AssessmentRun(id=uuid.uuid4().hex, principal=principal)
    _runs[run.id] = run
    run.task = asyncio.create_task(
        _drive(run, user_token, workspace_filter, catalogs, schemas),
        name=f"assessment-{run.id[:8]}",
    )
    return run


async def _drive(
    run: AssessmentRun,
    user_token: Optional[str],
    workspace_filter: Optional[dict],
    catalogs: Optional[list[str]],
    schemas: Optional[list[str]],
) -> None:
    # create_task copied the caller's context, so these settings stay local to
    # this run and reach the probe tasks it creates.
    set_user_token(user_token)
    set_workspace_filter(workspace_filter)
    set_catalog_scope(catalogs)
    set_schema_scope(schemas)
    try:
        async for event in run_assessment_stream():
            if event.get("type") == "complete":
                await _save_snapshot(event, run.principal)
            run.append(event)
    except asyncio.CancelledError:
        run.append(_error_event(run, "The assessment was cancelled.", reason="cancelled"))
        raise
    except Exception as e:
        # The message is generic on purpose: warehouse errors quote statements
        # and object names. The reference ties it to the full log entry.
        reference, message = safe_error(e, f"assessment run {run.id}", logger)
        run.append(_error_event(run, message, reason="failed", reference=reference, error_type=type(e).__name__))
    finally:
        run.finish()


async def _save_snapshot(event: dict, principal: str) -> None:
    scorecard = {"overall": event["overall"], "pillars": event["pillars"], "top_gaps": event["top_gaps"]}
    try:
        sid = await snapshots.save_snapshot(scorecard, created_by=principal)
        event["snapshot_id"] = sid
        event["snapshot_saved"] = sid is not None
    except Exception as e:
        logger.warning(f"snapshot save failed: {e}")
        event["snapshot_saved"] = False


def _error_event(run: AssessmentRun, message: str, *, reason: str, reference: str | None = None,
                 error_type: str | None = None) -> dict:
    done_keys = [e["pillar"]["key"] for e in run.events if e.get("type") == "pillar"]
    event = {
        "type": "error",
        "error": message,
        "reason": reason,
        "run_id": run.id,
        "elapsed_s": round(run.elapsed(), 1),
        "completed_pillars": done_keys,
    }
    if reference:
        event["reference"] = reference
    if error_type:
        event["error_type"] = error_type
    return event


def sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


async def stream_events(
    run: AssessmentRun,
    after: int = 0,
    window: Optional[float] = None,
    heartbeat: Optional[float] = None,
) -> AsyncIterator[str]:
    """SSE for one window of ``run``, starting at event ``after``.

    Each event carries ``seq`` (its index). The window ends with
    ``{"type": "end"}`` + ``[DONE]`` when the run is finished, or with
    ``{"type": "reconnect", "next": n}`` when the window time is up and the
    client must reconnect with ``after=n``."""
    window = STREAM_WINDOW_SECONDS if window is None else window
    heartbeat = HEARTBEAT_SECONDS if heartbeat is None else heartbeat
    loop = asyncio.get_running_loop()
    deadline = loop.time() + window
    nxt = max(0, after)
    # Send bytes at once so the proxy sees the response start.
    yield ": connected\n\n"
    while True:
        while nxt < len(run.events):
            yield sse({**run.events[nxt], "seq": nxt})
            nxt += 1
        if run.done:
            yield sse({"type": "end", "next": nxt, "elapsed_s": round(run.elapsed(), 1)})
            yield "data: [DONE]\n\n"
            return
        remaining = deadline - loop.time()
        if remaining <= 0:
            yield sse({"type": "reconnect", "next": nxt, "elapsed_s": round(run.elapsed(), 1)})
            return
        if not await run.wait_for_change(min(heartbeat, remaining)):
            yield ": keepalive\n\n"
