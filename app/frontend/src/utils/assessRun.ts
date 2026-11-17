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
  body: unknown;
  signal: AbortSignal;
  onEvent: (ev: AssessEvent) => void;
  onRunId?: (runId: string) => void;
  /** Pillars that have not finished yet, read when building an error. */
  pending?: () => PendingPillar[];
  /** Consecutive failed reconnects before giving up. */
  maxRetries?: number;
  // Injection points for tests.
  start?: (body: unknown) => Promise<{ run_id: string }>;
  stream?: (path: string, signal: AbortSignal) => AsyncIterable<WireEvent>;
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;
}

export interface RunAssessmentResult {
  runId: string;
  reconnects: number;
}

const defaultSleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

export function formatDuration(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  const m = Math.floor(s / 60);
  return m > 0 ? `${m}m ${s % 60}s` : `${s}s`;
}

function describeCause(e: unknown): string {
  if (e instanceof ApiError) return `${e.message} (HTTP ${e.status})`;
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

/** Run an assessment to the end, reconnecting as needed. Resolves when the run
 * is finished (complete or server-reported error, both sent via onEvent).
 * Rejects with AssessRunError when the run can't be followed, or with the
 * AbortError when `signal` aborts. */
export async function runAssessment(opts: RunAssessmentOptions): Promise<RunAssessmentResult> {
  const start = opts.start ?? ((body: unknown) => apiPost<{ run_id: string }>('/assess/runs', body));
  const stream = opts.stream ?? ((path: string, signal: AbortSignal) => streamGetEvents<WireEvent>(path, signal));
  const sleep = opts.sleep ?? defaultSleep;
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
  try {
    runId = (await start(opts.body)).run_id;
  } catch (e) {
    if (opts.signal.aborted) throw e;
    throw fail('Could not start the assessment.', e, null);
  }
  opts.onRunId?.(runId);

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
          ended = true;
          break;
        }
        if (ev.type === 'reconnect') {
          next = ev.next;
          windowClosed = true;
          break;
        }
        if (typeof ev.seq === 'number') next = ev.seq + 1;
        opts.onEvent(ev as AssessEvent);
      }
    } catch (e) {
      if (opts.signal.aborted || (e as Error)?.name === 'AbortError') throw e;
      if (e instanceof ApiError && e.status === 404) {
        throw fail('The assessment run was lost on the server.', e, runId);
      }
      failures += 1;
      if (failures > maxRetries) {
        const what = isNetworkError(e)
          ? 'Lost the connection to the app before the assessment finished.'
          : 'Could not read the assessment progress from the app.';
        throw fail(what, e, runId, [`Reconnect attempts: ${failures - 1} (gave up)`]);
      }
      await sleep(Math.min(1000 * 2 ** (failures - 1), 10_000));
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
      await sleep(Math.min(1000 * 2 ** (failures - 1), 10_000));
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
