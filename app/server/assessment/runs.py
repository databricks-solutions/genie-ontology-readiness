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
import math
import time
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

from server import snapshots
from server.assessment.scoring import run_assessment_stream
from server.config import MAX_RUN_SECONDS as CONFIG_MAX_RUN_SECONDS, set_user_token
from server.security import safe_error
from server.workspace_filter import set_catalog_scope, set_schema_scope, set_workspace_filter

logger = logging.getLogger(__name__)

# One SSE window. Below the Apps proxy's ~300s cut, with margin.
STREAM_WINDOW_SECONDS = 240.0
# Keepalive interval. The proxy expects bytes at least every ~10s.
HEARTBEAT_SECONDS = 8.0
# How long a finished run stays readable (for a late reconnect).
FINISHED_TTL_SECONDS = 15 * 60
# A run still working after this long is cancelled (GOR_MAX_RUN_SECONDS,
# default 3 h; parsed in server/config.py with the other deploy settings).
MAX_RUN_SECONDS = CONFIG_MAX_RUN_SECONDS
# A working run with no reader for this long is abandoned (tab closed, network
# gone) and is cancelled. Longer than the client's full reconnect backoff.
ABANDON_SECONDS = 90.0
# How often the sweeper enforces the limits above.
SWEEP_INTERVAL_SECONDS = 15.0


@dataclass
class AssessmentRun:
    id: str
    principal: str
    started_at: float = field(default_factory=time.monotonic)
    finished_at: Optional[float] = None
    events: list[dict] = field(default_factory=list)
    task: Optional[asyncio.Task] = None
    # UI runs replace each other (one at a time per user). Legacy one-request
    # streams are not exclusive: they never replace, and are never replaced.
    exclusive: bool = True
    # Readers attached now, and when one was last attached (abandonment check).
    readers: int = 0
    last_read: float = field(default_factory=time.monotonic)
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

    def has_error(self) -> bool:
        return any(e.get("type") == "error" for e in self.events)

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
_sweeper: Optional[asyncio.Task] = None


def _cancel(run: AssessmentRun, why: str) -> None:
    if run.task and not run.task.done():
        logger.info("cancelling assessment run %s: %s", run.id, why)
        run.task.cancel()


def _sweep(now: Optional[float] = None) -> None:
    """Forget finished runs past their TTL; cancel runs past the hard cap or
    with no reader for ABANDON_SECONDS."""
    now = time.monotonic() if now is None else now
    for run_id, run in list(_runs.items()):
        if run.done:
            if now - (run.finished_at or now) > FINISHED_TTL_SECONDS:
                _runs.pop(run_id, None)
        elif now - run.started_at > MAX_RUN_SECONDS:
            _cancel(run, "past the run time cap")
        elif run.readers == 0 and now - run.last_read > ABANDON_SECONDS:
            _cancel(run, "no reader attached")


async def _sweep_forever() -> None:
    while True:
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
        try:
            _sweep()
        except Exception:  # never let the sweeper die
            logger.exception("assessment run sweep failed")


def _ensure_sweeper() -> None:
    global _sweeper
    if _sweeper is None or _sweeper.done():
        _sweeper = asyncio.create_task(_sweep_forever(), name="assessment-run-sweeper")


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
    _cancel(run, "cancelled by the user")
    return True


def _on_task_done(run: AssessmentRun, task: asyncio.Task) -> None:
    # Runs even when the task was cancelled before _drive's first step (then its
    # try/finally never ran), so a run can never stay "working" forever.
    if not run.done:
        if not run.has_error():
            reason = "cancelled" if task.cancelled() else "failed"
            run.append(_error_event(run, "The assessment was cancelled." if task.cancelled()
                                    else "The assessment stopped unexpectedly.", reason=reason))
        run.finish()
    # Drop the task (and the context that holds the user's token).
    run.task = None


def start_run(
    principal: str,
    user_token: Optional[str],
    workspace_filter: Optional[dict],
    catalogs: Optional[list[str]],
    schemas: Optional[list[str]],
    replace_existing: bool = True,
) -> AssessmentRun:
    """Start an assessment in the background and return its handle.

    With ``replace_existing`` (the UI), a user's earlier UI run that is still
    working is cancelled first: the UI shows one run at a time, and leftover
    runs would compete for the warehouse. A legacy stream run is never
    cancelled this way (it stops when its own client disconnects)."""
    _ensure_sweeper()
    _sweep()
    if replace_existing:
        for other in list(_runs.values()):
            if other.principal == principal and other.exclusive and not other.done:
                _cancel(other, "replaced by a new run")

    run = AssessmentRun(id=uuid.uuid4().hex, principal=principal, exclusive=replace_existing)
    _runs[run.id] = run
    run.task = asyncio.create_task(
        _drive(run, user_token, workspace_filter, catalogs, schemas),
        name=f"assessment-{run.id[:8]}",
    )
    run.task.add_done_callback(lambda t: _on_task_done(run, t))
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
        set_user_token(None)
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
    legacy: bool = False,
) -> AsyncIterator[str]:
    """SSE for one window of ``run``, starting at event ``after``.

    Each event carries ``seq`` (its index). The window ends with
    ``{"type": "end"}`` + ``[DONE]`` when the run is finished, or with
    ``{"type": "reconnect", "next": n}`` when the window time is up and the
    client must reconnect with ``after=n``.

    ``legacy`` keeps the old ``/assess/stream`` shape: no ``seq``, no ``end``
    event, just ``[DONE]`` at the end."""
    window = STREAM_WINDOW_SECONDS if window is None else window
    heartbeat = HEARTBEAT_SECONDS if heartbeat is None else heartbeat
    loop = asyncio.get_running_loop()
    deadline = loop.time() + window
    nxt = max(0, after)
    run.readers += 1
    run.last_read = time.monotonic()
    try:
        # Send bytes at once so the proxy sees the response start.
        yield ": connected\n\n"
        while True:
            while nxt < len(run.events):
                yield sse(run.events[nxt] if legacy else {**run.events[nxt], "seq": nxt})
                nxt += 1
            if run.done:
                if not legacy:
                    yield sse({"type": "end", "next": nxt, "elapsed_s": round(run.elapsed(), 1)})
                yield "data: [DONE]\n\n"
                return
            remaining = deadline - loop.time()
            if remaining <= 0:
                yield sse({"type": "reconnect", "next": nxt, "elapsed_s": round(run.elapsed(), 1)})
                return
            if not await run.wait_for_change(min(heartbeat, remaining)):
                yield ": keepalive\n\n"
    finally:
        run.readers -= 1
        run.last_read = time.monotonic()


async def stream_whole_run(run: AssessmentRun) -> AsyncIterator[str]:
    """The legacy one-request stream: the whole run in one response, and the run
    is cancelled as soon as the client disconnects (as before)."""
    try:
        async for chunk in stream_events(run, 0, window=math.inf, legacy=True):
            yield chunk
    finally:
        if not run.done:
            _cancel(run, "legacy stream client disconnected")
