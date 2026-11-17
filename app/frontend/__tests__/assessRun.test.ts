import { describe, it, expect } from 'vitest';
import { AssessRunError, runAssessment, serverErrorDetails } from '../src/utils/assessRun';
import { ApiError } from '../src/hooks/useApi';

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
    expect(err.message).toMatch(/lost on the server/);
    expect(err.details[0]).toContain('HTTP 404');
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
