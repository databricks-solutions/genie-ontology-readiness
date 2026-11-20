import { describe, it, expect } from 'vitest';
import {
  AssessRunError,
  RUN_LOST_MESSAGE,
  RUN_STOPPED_NOTICE,
  abortableSleep,
  isUserStop,
  runAssessment,
  serverErrorDetails,
} from '../src/utils/assessRun';
import { ApiError, isNetworkError, parseError } from '../src/hooks/useApi';

// Hermetic tests for the reconnecting run client: the start + stream calls are
// injected, so no backend or network is involved.

type Wire = Record<string, unknown>;

/** A stream function that serves one scripted "window" per call. A window is a
 * list of events, optionally ending in an Error to throw mid-stream. */
function scripted(windows: Array<Array<Wire | Error>>) {
  const paths: string[] = [];
  const stream = (path: string) => {
    paths.push(path);
    const win = windows.shift() ?? [];
    return (async function* () {
      for (const item of win) {
        if (item instanceof Error) throw item;
        yield item as never;
      }
    })();
  };
  return { stream, paths };
}

const noSleep = async () => undefined;

describe('runAssessment', () => {
  it('reconnects after a dropped connection and resumes from the next event', async () => {
    const { stream, paths } = scripted([
      [
        { type: 'pillar_progress', key: 'metadata', done: 1, total: 13, detail: 'Scanned 25/314', seq: 0 },
        new TypeError('network error'),
      ],
      [{ type: 'reconnect', next: 1 }],
      [
        { type: 'pillar', pillar: { key: 'metadata' }, seq: 1 },
        { type: 'end', next: 2 },
      ],
    ]);
    const seen: string[] = [];
    const res = await runAssessment({
      body: {},
      signal: new AbortController().signal,
      start: async () => ({ run_id: 'run1' }),
      stream,
      sleep: noSleep,
      onEvent: (ev) => seen.push(ev.type),
    });

    expect(res).toEqual({ runId: 'run1', reconnects: 2 });
    expect(seen).toEqual(['pillar_progress', 'pillar']);
    expect(paths).toEqual([
      '/assess/runs/run1/events?after=0',
      '/assess/runs/run1/events?after=1',
      '/assess/runs/run1/events?after=1',
    ]);
  });

  it('gives up after repeated drops with a detailed error', async () => {
    const drop = () => [new TypeError('network error')];
    const { stream } = scripted([drop(), drop(), drop()]);
    let t = 0;
    const err = await runAssessment({
      body: {},
      signal: new AbortController().signal,
      start: async () => ({ run_id: 'run2' }),
      stream,
      sleep: noSleep,
      maxRetries: 2,
      now: () => (t += 100_000),
      pending: () => [{ name: 'Metadata Richness', progress: 'Scanned 175/314 catalogs' }, { name: 'Genie Agents' }],
      onEvent: () => undefined,
    }).catch((e) => e);

    expect(err).toBeInstanceOf(AssessRunError);
    expect(err.message).toMatch(/Lost the connection/);
    const text = err.details.join('\n');
    expect(text).toContain('Cause: TypeError: network error');
    expect(text).toContain('Reconnect attempts: 2 (gave up)');
    expect(text).toContain('Not finished: Metadata Richness (Scanned 175/314 catalogs); Genie Agents');
    expect(text).toContain('Run ID: run2');
    expect(text).toMatch(/Elapsed: \d+m/);
  });

  it('stops at once when the server no longer has the run', async () => {
    const { stream } = scripted([[new ApiError('This assessment run is no longer available.', 404)]]);
    const err = await runAssessment({
      body: {},
      signal: new AbortController().signal,
      start: async () => ({ run_id: 'gone' }),
      stream,
      sleep: noSleep,
      onEvent: () => undefined,
    }).catch((e) => e);

    expect(err).toBeInstanceOf(AssessRunError);
    expect(err.message).toBe(RUN_LOST_MESSAGE);
    expect(err.message).toMatch(/app restarted/);
    expect(err.details[0]).toContain('HTTP 404');
  });

  it('attaches to a run that is already working and replays it from the first event', async () => {
    const { stream, paths } = scripted([
      [
        { type: 'pillar', pillar: { key: 'metadata' }, seq: 0 },
        { type: 'end', next: 1 },
      ],
    ]);
    const starts: unknown[] = [];
    const ids: string[] = [];
    const seen: string[] = [];
    const res = await runAssessment({
      body: {},
      runId: 'live1',
      signal: new AbortController().signal,
      start: async (body) => {
        starts.push(body);
        return { run_id: 'new' };
      },
      stream,
      sleep: noSleep,
      onRunId: (id) => ids.push(id),
      onEvent: (ev) => seen.push(ev.type),
    });

    expect(starts).toEqual([]);
    expect(ids).toEqual(['live1']);
    expect(paths).toEqual(['/assess/runs/live1/events?after=0']);
    expect(seen).toEqual(['pillar']);
    expect(res).toEqual({ runId: 'live1', reconnects: 0 });
  });

  it('passes a 409 (one run per user) through as the ApiError, with its status', async () => {
    const err = await runAssessment({
      body: {},
      signal: new AbortController().signal,
      start: async () => {
        throw new ApiError('An assessment is already running.', 409);
      },
      stream: () => (async function* () {})(),
      sleep: noSleep,
      onEvent: () => undefined,
    }).catch((e) => e);

    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(409);
  });

  it('ends quietly when the user stops the run, and the stop is not an error', async () => {
    const stopped = { type: 'error', error: 'The assessment was stopped.', reason: 'cancelled', seq: 1 };
    const { stream } = scripted([[{ type: 'pillar', pillar: { key: 'k' }, seq: 0 }, stopped, { type: 'end', next: 2 }]]);
    const events: Array<{ type: string; stop: boolean }> = [];
    await runAssessment({
      body: {},
      runId: 'r',
      signal: new AbortController().signal,
      stream,
      sleep: noSleep,
      onEvent: (ev) => events.push({ type: ev.type, stop: isUserStop(ev) }),
    });

    expect(events).toEqual([
      { type: 'pillar', stop: false },
      { type: 'error', stop: true },
    ]);
    expect(RUN_STOPPED_NOTICE).toMatch(/Nothing was saved/);
  });

  it('treats a run that failed (or hit a limit) as an error, not a stop', () => {
    expect(isUserStop({ type: 'error', error: 'x', reason: 'failed' })).toBe(false);
    expect(isUserStop({ type: 'complete', overall: {} as never, pillars: [], top_gaps: [] })).toBe(false);
  });

  it('rejects with AbortError when the page stops following; no cancel is sent', async () => {
    const controller = new AbortController();
    const calls: string[] = [];
    const err = await runAssessment({
      body: {},
      runId: 'r',
      signal: controller.signal,
      stream: (path: string, signal: AbortSignal) => {
        calls.push(path);
        return (async function* () {
          yield { type: 'pillar_progress', key: 'k', done: 0, total: 1, detail: '', seq: 0 } as never;
          controller.abort();
          throw new DOMException('Aborted', 'AbortError');
        })();
      },
      sleep: noSleep,
      onEvent: () => undefined,
    }).catch((e) => e);

    expect((err as Error).name).toBe('AbortError');
    expect(calls).toEqual(['/assess/runs/r/events?after=0']);
  });
});

describe('runAssessment edge cases', () => {
  it('backs off and gives up when the stream keeps closing with no marker', async () => {
    const { stream, paths } = scripted([[], [], []]);
    const sleeps: number[] = [];
    const err = await runAssessment({
      body: {},
      signal: new AbortController().signal,
      start: async () => ({ run_id: 'r' }),
      stream,
      sleep: async (ms) => {
        sleeps.push(ms);
      },
      maxRetries: 2,
      onEvent: () => undefined,
    }).catch((e) => e);

    expect(err).toBeInstanceOf(AssessRunError);
    expect(err.message).toMatch(/closed the progress stream/);
    expect(sleeps).toEqual([1000, 2000]);
    expect(paths).toHaveLength(3);
  });

  it('does not retry a bug in the event handler and keeps the event number', async () => {
    const { stream, paths } = scripted([[{ type: 'pillar', pillar: { key: 'x' }, seq: 4 }]]);
    const err = await runAssessment({
      body: {},
      signal: new AbortController().signal,
      start: async () => ({ run_id: 'r' }),
      stream,
      sleep: async () => undefined,
      onEvent: () => {
        throw new TypeError("Cannot read properties of undefined (reading 'fetch')");
      },
    }).catch((e) => e);

    expect(err).toBeInstanceOf(AssessRunError);
    expect(err.message).toMatch(/could not show an assessment update/);
    expect(err.details).toContain('Event: pillar #4');
    expect(paths).toHaveLength(1);
  });

  it('reports the HTTP status once', async () => {
    const { stream } = scripted([[new ApiError('HTTP 502 Bad Gateway: proxy', 502)]]);
    const err = await runAssessment({
      body: {},
      signal: new AbortController().signal,
      start: async () => ({ run_id: 'r' }),
      stream,
      sleep: async () => undefined,
      maxRetries: 0,
      onEvent: () => undefined,
    }).catch((e) => e);
    expect(err.details[0]).toBe('Cause: HTTP 502 Bad Gateway: proxy');
  });

  it('abortableSleep stops at once on abort', async () => {
    const c = new AbortController();
    const p = abortableSleep(60_000, c.signal);
    c.abort();
    await expect(p).rejects.toMatchObject({ name: 'AbortError' });
  });
});

describe('parseError and isNetworkError', () => {
  it('uses the JSON error field', async () => {
    const res = new Response(JSON.stringify({ error: 'run not found' }), { status: 404 });
    expect(await parseError(res)).toBe('run not found');
  });

  it('explains proxy timeout pages', async () => {
    const res = new Response('<html><body>upstream request timeout</body></html>', {
      status: 504,
      statusText: 'Gateway Timeout',
    });
    expect(await parseError(res)).toBe(
      'HTTP 504 Gateway Timeout: the Databricks Apps proxy did not get a reply from the app in time.'
    );
  });

  it('strips HTML and caps other non-JSON bodies', async () => {
    expect(await parseError(new Response('<b>Forbidden</b> by policy', { status: 403, statusText: 'Forbidden' }))).toBe(
      'HTTP 403 Forbidden: Forbidden by policy'
    );
    expect(await parseError(new Response('x'.repeat(500), { status: 400, statusText: 'Bad Request' }))).toBe(
      'HTTP 400 Bad Request'
    );
  });

  it('recognises browser network errors only', () => {
    expect(isNetworkError(new TypeError('network error'))).toBe(true);
    expect(isNetworkError(new TypeError('Failed to fetch'))).toBe(true);
    expect(isNetworkError(new TypeError('Load failed'))).toBe(true);
    expect(isNetworkError(new TypeError('NetworkError when attempting to fetch resource.'))).toBe(true);
    expect(isNetworkError(new TypeError("Cannot read properties of undefined (reading 'fetch')"))).toBe(false);
    expect(isNetworkError(new Error('network error'))).toBe(false);
  });
});

describe('serverErrorDetails', () => {
  it('lists the reference, type, elapsed time and unfinished pillars', () => {
    const lines = serverErrorDetails(
      { type: 'error', error: 'x', reference: 'abc123', error_type: 'TimeoutError', elapsed_s: 312, run_id: 'r' },
      [{ name: 'Domains & Stewardship' }]
    );
    expect(lines).toEqual([
      'Error type: TimeoutError',
      'Elapsed: 5m 12s',
      'Not finished: Domains & Stewardship',
      'Run ID: r',
      'Reference: abc123 (search the app logs for it)',
    ]);
  });
});
