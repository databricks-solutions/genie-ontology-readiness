// Client for background assessment runs (POST /assess/runs + GET .../events).
//
// The Databricks Apps proxy cuts a streamed response at ~300s. A large
// assessment can run longer, so the server runs it in the background and serves
// its events in short SSE windows. This module reads those windows, reconnects
// from the last event it received (also after a dropped connection), and builds
// a detailed error when it cannot continue.

import { ApiError, apiPost, isNetworkError, streamGetEvents } from '../hooks/useApi';
import type { PillarScore, ScorecardOverall, TopGap } from '../types';

export type AssessEvent =
  | { type: 'pillar'; pillar: PillarScore }
  | { type: 'pillar_progress'; key: string; done: number; total: number; detail: string }
  | {
      type: 'complete';
      overall: ScorecardOverall;
      pillars: PillarScore[];
      top_gaps: TopGap[];
      snapshot_id?: number | null;
      snapshot_saved?: boolean;
    }
  | {
      type: 'error';
      error: string;
      reason?: 'failed' | 'cancelled';
      reference?: string;
      error_type?: string;
      elapsed_s?: number;
      run_id?: string;
      completed_pillars?: string[];
    };

type WireEvent =
  | (AssessEvent & { seq?: number })
  | { type: 'end'; next?: number; elapsed_s?: number }
  | { type: 'reconnect'; next: number; elapsed_s?: number };

/** An assessment that could not finish, with lines that explain why. */
export class AssessRunError extends Error {
  details: string[];
  constructor(message: string, details: string[]) {
    super(message);
    this.name = 'AssessRunError';
    this.details = details;
  }
}

export interface PendingPillar {
  name: string;
  progress?: string;
}

export interface RunAssessmentOptions {
  /** The scope to start a new run with. Ignored when `runId` attaches to a run. */
  body: unknown;
  /** Attach to a run that is already working (after a reload, or from a 409)
   * instead of starting one. Its events replay from the first one. */
  runId?: string;
  signal: AbortSignal;
  onEvent: (ev: AssessEvent) => void;
  /** The run is live: it was started, or (attach) its first event arrived. */
  onRunId?: (runId: string) => void;
  /** Pillars that have not finished yet, read when building an error. */
  pending?: () => PendingPillar[];
  /** Consecutive failed reconnects before giving up. */
  maxRetries?: number;
  // Injection points for tests.
  start?: (body: unknown) => Promise<{ run_id: string }>;
  stream?: (path: string, signal: AbortSignal) => AsyncIterable<WireEvent>;
  sleep?: (ms: number, signal: AbortSignal) => Promise<void>;
  now?: () => number;
}

export interface RunAssessmentResult {
  runId: string;
  reconnects: number;
}

/** Sleep that ends early (with an AbortError) when `signal` aborts. */
export function abortableSleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    if (signal.aborted) return reject(new DOMException('Aborted', 'AbortError'));
    const t = setTimeout(() => {
      signal.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(t);
      reject(new DOMException('Aborted', 'AbortError'));
    };
    signal.addEventListener('abort', onAbort, { once: true });
  });
}

export function formatDuration(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  const m = Math.floor(s / 60);
  return m > 0 ? `${m}m ${s % 60}s` : `${s}s`;
}

function describeCause(e: unknown): string {
  // parseError already starts proxy/HTTP messages with "HTTP <status>".
  if (e instanceof ApiError) return e.message.startsWith('HTTP ') ? e.message : `${e.message} (HTTP ${e.status})`;
  if (e instanceof Error) return `${e.name}: ${e.message}`;
  return String(e);
}

function pendingLine(pending: PendingPillar[]): string | null {
  if (pending.length === 0) return null;
  const parts = pending.map((p) => (p.progress ? `${p.name} (${p.progress})` : p.name));
  return `Not finished: ${parts.join('; ')}`;
}

const HINT =
  'What to try: run it again. If it repeats, check that the app is running and read its logs ' +
  '(Compute → Apps → the app → Logs), narrow the catalog scope (New assessment → pick catalogs → ' +
  'Run assessment), or use a larger SQL warehouse.';

/** Shown when the user stops a run. A stopped run is not saved. */
export const RUN_STOPPED_NOTICE = 'The assessment was stopped. Nothing was saved.';

/** Shown when the app restarted (or otherwise lost the run) while it was working. */
export const RUN_LOST_MESSAGE =
  'The assessment run is no longer on the server: the app restarted, or it ended more than 15 minutes ago. ' +
  'If it finished with history on, it is in your history. Otherwise, run it again.';

/** Shown when a completed run with history on could not be saved. */
export const RUN_NOT_SAVED_NOTICE = "This run finished but couldn't be saved to history.";

/** The notice for a completed run's save result: only a history-on run that did not save warns. */
export function unsavedRunNotice(ev: AssessEvent, historySaves: boolean): string | null {
  return ev.type === 'complete' && historySaves && ev.snapshot_saved === false ? RUN_NOT_SAVED_NOTICE : null;
}

/** True for the end of a run that the user stopped (not an error to show). */
export function isUserStop(ev: AssessEvent): boolean {
  return ev.type === 'error' && ev.reason === 'cancelled';
}

export const STOP_CONFIRM = 'Stop this assessment? A stopped run is not saved to history.';
export const STOP_CONFLICT_CONFIRM = 'Stop the assessment that is already running? A stopped run is not saved to history.';
export const RUN_JUST_FINISHED = 'The earlier assessment just finished. Run it again.';
export const RUN_STOPPED_EARLIER = 'The earlier assessment was stopped. Nothing was saved. You can run again now.';
export const RUN_ALREADY_ENDED = 'That assessment had already finished.';

/** The run this browser last followed, kept so a reload can tell a lost or finished
 * run from none. It is tied to the viewer, so one user never sees another's. */
export const RUN_ID_KEY = 'gor.assess.run';

/** The remembered run id for `viewer`, or null (another viewer's entry is ignored). */
export function parseRemembered(raw: string | null, viewer: string): string | null {
  if (!raw) return null;
  try {
    const v = JSON.parse(raw) as { runId?: unknown; viewer?: unknown };
    return v.viewer === viewer && typeof v.runId === 'string' ? v.runId : null;
  } catch {
    return null;
  }
}

export function rememberRun(runId: string, viewer: string): void {
  try {
    localStorage.setItem(RUN_ID_KEY, JSON.stringify({ runId, viewer }));
  } catch {
    // Storage can be blocked. The notice for a run that is gone is then not shown.
  }
}

export function forgetRun(): void {
  try {
    localStorage.removeItem(RUN_ID_KEY);
  } catch {
    // Nothing to clear when storage is blocked.
  }
}

export function rememberedRun(viewer: string): string | null {
  try {
    return parseRemembered(localStorage.getItem(RUN_ID_KEY), viewer);
  } catch {
    return null;
  }
}

/** Shown when the run this browser last followed is no longer running (it ended long
 * ago, or the app restarted). Not an error: it points to the history instead. */
export function lastRunGoneNotice(historySaves: boolean): string {
  return historySaves
    ? "Your last assessment is no longer running. If it finished, it's in Assessments."
    : 'Your last assessment is no longer running. It was not saved, so run it again to get a result.';
}

export type FollowFailure = 'conflict' | 'last-run-gone' | 'lost' | 'other';

/** What a failed follow means. `began` is whether the page had already shown the run. */
export function followFailure(e: unknown, ctx: { fromRemembered: boolean; began: boolean }): FollowFailure {
  if (e instanceof ApiError && e.status === 409) return 'conflict';
  if (e instanceof AssessRunError && e.message === RUN_LOST_MESSAGE) {
    return ctx.fromRemembered && !ctx.began ? 'last-run-gone' : 'lost';
  }
  return 'other';
}

export interface ActiveRunResponse {
  run_id: string | null;
  /** The signed-in user the server sees (ties a remembered run to them). */
  viewer?: string;
}

/** The run that a refused start (409) is blocked by, or null when none is found
 * (it finished in the meantime, or the lookup failed). */
export async function conflictingRunId(getActive: () => Promise<ActiveRunResponse>): Promise<string | null> {
  try {
    return (await getActive()).run_id ?? null;
  } catch {
    return null;
  }
}

/** On page load, follow the user's run that is still working. When none is working,
 * follow the run this browser last followed (for this viewer). The attach fails
 * with 404 when the server no longer has it. Never starts a run. */
export async function reattachActive(opts: {
  getActive: () => Promise<ActiveRunResponse>;
  attach: (runId: string, fromRemembered: boolean) => Promise<unknown>;
  mounted: () => boolean;
  remembered?: (viewer: string) => string | null;
}): Promise<void> {
  let active: ActiveRunResponse;
  try {
    active = await opts.getActive();
  } catch {
    return;
  }
  if (!opts.mounted()) return;
  if (active.run_id) {
    await opts.attach(active.run_id, false);
    return;
  }
  const old = active.viewer ? opts.remembered?.(active.viewer) ?? null : null;
  if (old) await opts.attach(old, true);
}

export type StopOutcome = 'declined' | 'stopped' | 'ended';

/** Stop a run after the user confirms. Sends the DELETE only when confirmed. A 404
 * means the run had already ended ('ended'). Any other failure rejects, so the
 * caller keeps showing the run as it was. */
export async function stopWithConfirm(opts: {
  runId: string;
  message: string;
  confirm: (message: string) => boolean;
  del: (path: string) => Promise<unknown>;
}): Promise<StopOutcome> {
  if (!opts.confirm(opts.message)) return 'declined';
  try {
    await opts.del(`/assess/runs/${opts.runId}`);
    return 'stopped';
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return 'ended';
    throw e;
  }
}

/** Run an assessment to the end, reconnecting as needed. Resolves when the run
 * is finished (complete or server-reported error, both sent via onEvent).
 * Rejects with AssessRunError when the run can't be followed, or with the
 * AbortError when `signal` aborts. Aborting only stops following: the server
 * run keeps going. A 409 (one run per user) rejects with the ApiError. */
export async function runAssessment(opts: RunAssessmentOptions): Promise<RunAssessmentResult> {
  const start = opts.start ?? ((body: unknown) => apiPost<{ run_id: string }>('/assess/runs', body));
  const stream = opts.stream ?? ((path: string, signal: AbortSignal) => streamGetEvents<WireEvent>(path, signal));
  const sleep = opts.sleep ?? abortableSleep;
  const now = opts.now ?? (() => Date.now());
  const maxRetries = opts.maxRetries ?? 5;
  const pending = opts.pending ?? (() => []);
  const startedAt = now();

  const fail = (message: string, cause: unknown, runId: string | null, extra: string[] = []): AssessRunError => {
    const lines = [
      `Cause: ${describeCause(cause)}`,
      `Elapsed: ${formatDuration(now() - startedAt)}`,
      ...extra,
    ];
    const p = pendingLine(pending());
    if (p) lines.push(p);
    if (runId) lines.push(`Run ID: ${runId}`);
    lines.push(HINT);
    return new AssessRunError(message, lines);
  };

  let runId: string;
  // onRunId means "this run is live": a started run at once; an attached run on its first
  // event (a run that is gone never gets one, so the caller keeps what it was showing).
  let announced = false;
  const announce = () => {
    if (announced) return;
    announced = true;
    opts.onRunId?.(runId);
  };
  if (opts.runId) {
    runId = opts.runId;
  } else {
    try {
      runId = (await start(opts.body)).run_id;
    } catch (e) {
      if (opts.signal.aborted) throw e;
      if (e instanceof ApiError && e.status === 409) throw e;
      throw fail('Could not start the assessment.', e, null);
    }
    announce();
  }

  let next = 0;
  let failures = 0;
  let reconnects = 0;
  while (true) {
    let ended = false;
    let windowClosed = false;
    try {
      for await (const ev of stream(`/assess/runs/${runId}/events?after=${next}`, opts.signal)) {
        failures = 0;
        if (ev.type === 'end') {
          announce();
          ended = true;
          break;
        }
        if (ev.type === 'reconnect') {
          next = ev.next;
          windowClosed = true;
          break;
        }
        announce();
        try {
          opts.onEvent(ev as AssessEvent);
        } catch (handlerError) {
          // A bug in the page, not a connection problem: don't retry it.
          throw fail('The page could not show an assessment update.', handlerError, runId, [
            `Event: ${ev.type}${typeof ev.seq === 'number' ? ` #${ev.seq}` : ''}`,
          ]);
        }
        if (typeof ev.seq === 'number') next = ev.seq + 1;
      }
    } catch (e) {
      if (e instanceof AssessRunError) throw e;
      if (opts.signal.aborted || (e as Error)?.name === 'AbortError') throw e;
      if (e instanceof ApiError && e.status === 404) {
        throw fail(RUN_LOST_MESSAGE, e, runId);
      }
      failures += 1;
      if (failures > maxRetries) {
        const what = isNetworkError(e)
          ? 'Lost the connection to the app before the assessment finished.'
          : 'Could not read the assessment progress from the app.';
        throw fail(what, e, runId, [`Reconnect attempts: ${failures - 1} (gave up)`]);
      }
      await sleep(Math.min(1000 * 2 ** (failures - 1), 10_000), opts.signal);
      reconnects += 1;
      continue;
    }
    if (ended) return { runId, reconnects };
    if (!windowClosed) {
      // The stream closed with no end or reconnect marker: treat it like a drop.
      failures += 1;
      if (failures > maxRetries) {
        throw fail(
          'The app closed the progress stream before the assessment finished.',
          new Error('stream closed without an end event'),
          runId,
          [`Reconnect attempts: ${failures - 1} (gave up)`]
        );
      }
      await sleep(Math.min(1000 * 2 ** (failures - 1), 10_000), opts.signal);
    }
    reconnects += 1;
  }
}

/** Detail lines for an error event that the server sent. */
export function serverErrorDetails(ev: Extract<AssessEvent, { type: 'error' }>, pending: PendingPillar[]): string[] {
  const lines: string[] = [];
  if (ev.error_type) lines.push(`Error type: ${ev.error_type}`);
  if (typeof ev.elapsed_s === 'number') lines.push(`Elapsed: ${formatDuration(ev.elapsed_s * 1000)}`);
  const p = pendingLine(pending);
  if (p) lines.push(p);
  if (ev.run_id) lines.push(`Run ID: ${ev.run_id}`);
  if (ev.reference) lines.push(`Reference: ${ev.reference} (search the app logs for it)`);
  return lines;
}
