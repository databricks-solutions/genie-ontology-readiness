import { describe, it, expect } from 'vitest';
import {
  AssessRunError,
  RUN_JUST_FINISHED,
  RUN_LOST_MESSAGE,
  STOP_CONFIRM,
  conflictingRunId,
  followFailure,
  lastRunGoneNotice,
  parseRemembered,
  reattachActive,
  stopWithConfirm,
  unsavedRunNotice,
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

describe('reattach on page load', () => {
  it('follows the active run from its first event and never starts a new one', async () => {
    const { stream, paths } = scripted([[{ type: 'pillar', pillar: { key: 'metadata' }, seq: 0 }, { type: 'end', next: 1 }]]);
    let starts = 0;
    const attached: string[] = [];
    await reattachActive({
      getActive: async () => ({ run_id: 'live9', viewer: 'me@x' }),
      attach: async (id) => {
        attached.push(id);
        await runAssessment({
          body: {},
          runId: id,
          signal: new AbortController().signal,
          start: async () => {
            starts += 1;
            return { run_id: 'nope' };
          },
          stream,
          sleep: noSleep,
          onEvent: () => undefined,
        });
      },
      mounted: () => true,
    });

    expect(attached).toEqual(['live9']);
    expect(starts).toBe(0);
    expect(paths).toEqual(['/assess/runs/live9/events?after=0']);
  });

  it('does nothing when no run is active, the lookup fails, or the page is gone', async () => {
    const attached: string[] = [];
    const attach = async (id: string) => {
      attached.push(id);
    };
    await reattachActive({ getActive: async () => ({ run_id: null, viewer: 'me@x' }), attach, mounted: () => true });
    await reattachActive({
      getActive: async () => {
        throw new TypeError('Failed to fetch');
      },
      attach,
      mounted: () => true,
    });
    await reattachActive({ getActive: async () => ({ run_id: 'x', viewer: 'me@x' }), attach, mounted: () => false });
    expect(attached).toEqual([]);
  });
});

describe('reload after a run was followed here', () => {
  it('follows the remembered run for this viewer when none is active', async () => {
    const attached: Array<[string, boolean]> = [];
    await reattachActive({
      getActive: async () => ({ run_id: null, viewer: 'me@x' }),
      attach: async (id, fromRemembered) => {
        attached.push([id, fromRemembered]);
      },
      mounted: () => true,
      remembered: (viewer) => parseRemembered(JSON.stringify({ runId: 'old7', viewer: 'me@x' }), viewer),
    });
    expect(attached).toEqual([['old7', true]]);
  });

  it('prefers the active run over a remembered one', async () => {
    const attached: Array<[string, boolean]> = [];
    await reattachActive({
      getActive: async () => ({ run_id: 'live', viewer: 'me@x' }),
      attach: async (id, fromRemembered) => {
        attached.push([id, fromRemembered]);
      },
      mounted: () => true,
      remembered: () => 'old',
    });
    expect(attached).toEqual([['live', false]]);
  });

  it('ignores a remembered run that belongs to another viewer (shared browser)', () => {
    const raw = JSON.stringify({ runId: 'theirs', viewer: 'alice@x' });
    expect(parseRemembered(raw, 'bob@x')).toBeNull();
    expect(parseRemembered(raw, 'alice@x')).toBe('theirs');
    expect(parseRemembered('not json', 'alice@x')).toBeNull();
    expect(parseRemembered(null, 'alice@x')).toBeNull();
  });

  it('classifies a 404 on a remembered run (finished long ago) as not an error', async () => {
    const { stream } = scripted([[new ApiError('This assessment run is no longer available.', 404)]]);
    const err = await runAssessment({
      body: {},
      runId: 'old7',
      signal: new AbortController().signal,
      stream,
      sleep: noSleep,
      onEvent: () => undefined,
    }).catch((e) => e);
    expect(followFailure(err, { fromRemembered: true, began: false })).toBe('last-run-gone');
    expect(lastRunGoneNotice(true)).toMatch(/Assessments/);
    expect(lastRunGoneNotice(false)).toMatch(/not saved/);
  });

  it('keeps the lost-run error for a run this tab was following, or was attaching to as active', async () => {
    const lost = new AssessRunError(RUN_LOST_MESSAGE, []);
    expect(followFailure(lost, { fromRemembered: true, began: true })).toBe('lost');
    expect(followFailure(lost, { fromRemembered: false, began: false })).toBe('lost');
    expect(followFailure(new ApiError('HTTP 500', 500), { fromRemembered: true, began: false })).toBe('other');
    expect(followFailure(new ApiError('An assessment is already running.', 409), { fromRemembered: false, began: false })).toBe('conflict');
  });
});

describe('the page keeps what it shows until a run is live', () => {
  it('announces a started run once the start succeeds, and never on a refused start', async () => {
    const lives: string[] = [];
    const refused = await runAssessment({
      body: {},
      signal: new AbortController().signal,
      start: async () => {
        throw new ApiError('An assessment is already running.', 409);
      },
      stream: () => (async function* () {})(),
      sleep: noSleep,
      onRunId: (id) => lives.push(id),
      onEvent: () => undefined,
    }).catch((e) => e);
    expect(refused.status).toBe(409);
    expect(lives).toEqual([]);

    await runAssessment({
      body: {},
      signal: new AbortController().signal,
      start: async () => ({ run_id: 'new1' }),
      stream: scripted([[{ type: 'end', next: 0 }]]).stream,
      sleep: noSleep,
      onRunId: (id) => lives.push(id),
      onEvent: () => undefined,
    });
    expect(lives).toEqual(['new1']);
  });

  it('does not announce an attached run that is gone (404), so the shown scorecard stays', async () => {
    const lives: string[] = [];
    const { stream } = scripted([[new ApiError('gone', 404)]]);
    await runAssessment({
      body: {},
      runId: 'gone1',
      signal: new AbortController().signal,
      stream,
      sleep: noSleep,
      onRunId: (id) => lives.push(id),
      onEvent: () => undefined,
    }).catch(() => undefined);
    expect(lives).toEqual([]);
  });

  it('announces an attached run on its first event', async () => {
    const lives: string[] = [];
    const { stream } = scripted([[{ type: 'pillar', pillar: { key: 'k' }, seq: 0 }, { type: 'end', next: 1 }]]);
    await runAssessment({
      body: {},
      runId: 'live2',
      signal: new AbortController().signal,
      stream,
      sleep: noSleep,
      onRunId: (id) => lives.push(id),
      onEvent: () => undefined,
    });
    expect(lives).toEqual(['live2']);
  });
});

describe('a start refused with 409 (one run per user)', () => {
  it('finds the blocking run and "Open it" follows that id', async () => {
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
    expect(err.status).toBe(409);

    const blocking = await conflictingRunId(async () => ({ run_id: 'busy1' }));
    expect(blocking).toBe('busy1');

    const { stream, paths } = scripted([[{ type: 'end', next: 0 }]]);
    await runAssessment({
      body: {},
      runId: blocking ?? undefined,
      signal: new AbortController().signal,
      stream,
      sleep: noSleep,
      onEvent: () => undefined,
    });
    expect(paths).toEqual(['/assess/runs/busy1/events?after=0']);
  });

  it('says "just finished" when no run is active any more, or the lookup fails', async () => {
    expect(await conflictingRunId(async () => ({ run_id: null }))).toBeNull();
    expect(
      await conflictingRunId(async () => {
        throw new TypeError('Failed to fetch');
      })
    ).toBeNull();
    expect(RUN_JUST_FINISHED).toMatch(/just finished/);
  });
});

describe('stopWithConfirm', () => {
  it('sends nothing when the user declines', async () => {
    const calls: string[] = [];
    const out = await stopWithConfirm({
      runId: 'abc',
      message: STOP_CONFIRM,
      confirm: () => false,
      del: async (p) => {
        calls.push(p);
      },
    });
    expect(out).toBe('declined');
    expect(calls).toEqual([]);
  });

  it('sends the DELETE for the right run once confirmed', async () => {
    const asked: string[] = [];
    const calls: string[] = [];
    const out = await stopWithConfirm({
      runId: 'abc',
      message: STOP_CONFIRM,
      confirm: (m) => {
        asked.push(m);
        return true;
      },
      del: async (p) => {
        calls.push(p);
        return { cancelled: true };
      },
    });
    expect(asked).toEqual([STOP_CONFIRM]);
    expect(calls).toEqual(['/assess/runs/abc']);
    expect(out).toBe('stopped');
  });

  it('reports a run that already ended (404) as ended, not as a failure', async () => {
    const out = await stopWithConfirm({
      runId: 'gone',
      message: STOP_CONFIRM,
      confirm: () => true,
      del: async () => {
        throw new ApiError('Assessment run not found.', 404);
      },
    });
    expect(out).toBe('ended');
  });

  it('rejects when the stop fails, so the caller keeps the run on screen', async () => {
    const err = await stopWithConfirm({
      runId: 'abc',
      message: STOP_CONFIRM,
      confirm: () => true,
      del: async () => {
        throw new ApiError('HTTP 500', 500);
      },
    }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(500);
  });
});

describe('unsavedRunNotice', () => {
  const complete = (snapshot_saved?: boolean) =>
    ({ type: 'complete', overall: {} as never, pillars: [], top_gaps: [], snapshot_saved } as const);

  it('warns only for a completed history-on run that did not save', () => {
    expect(unsavedRunNotice(complete(false), true)).toBe("This run finished but couldn't be saved to history.");
    expect(unsavedRunNotice(complete(true), true)).toBeNull();
    // History off: nothing is saved by design, so no warning.
    expect(unsavedRunNotice(complete(false), false)).toBeNull();
    expect(unsavedRunNotice({ type: 'pillar', pillar: {} as never }, true)).toBeNull();
  });
});
