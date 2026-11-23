// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { createElement } from 'react';
import Scorecard from '../src/components/Scorecard';
import { ApiError } from '../src/hooks/useApi';

// Component tests for the run lifecycle on the Assess page. The API layer and the SSE
// stream are mocked; the chart library is replaced by empty components. Nothing waits on
// timers: each stream is held open by a gate that the test releases.

const api = vi.hoisted(() => ({
  get: vi.fn(),
  del: vi.fn(),
  post: vi.fn(),
  stream: vi.fn(),
}));

vi.mock('../src/hooks/useApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/hooks/useApi')>();
  return { ...actual, apiGet: api.get, apiDelete: api.del, apiPost: api.post, streamGetEvents: api.stream };
});

vi.mock('recharts', () => {
  const Empty = () => null;
  return {
    Radar: Empty,
    RadarChart: Empty,
    PolarGrid: Empty,
    PolarAngleAxis: Empty,
    PolarRadiusAxis: Empty,
    ResponsiveContainer: Empty,
    LineChart: Empty,
    Line: Empty,
    XAxis: Empty,
    YAxis: Empty,
    Tooltip: Empty,
  };
});

const VIEWER = 'me@x';
const REMEMBER_KEY = 'gor.assess.run';

type Snap = Record<string, unknown>;
let active: { run_id: string | null; viewer?: string; shared?: boolean };
let snaps: Snap[];
let snapshotBodies: Record<string, unknown>;

function routeGet(path: string): unknown {
  if (path.startsWith('/workspaces')) return { workspaces: [], current_workspace_id: null };
  if (path.startsWith('/catalogs')) return { catalogs: [], available: true };
  if (path.startsWith('/schemas')) return { schemas: [], available: true };
  if (path === '/assess/runs/active') return active;
  if (path === '/assess/history') return { snapshots: snaps };
  if (path.startsWith('/assess/snapshot/')) {
    const id = path.split('/').pop() ?? '';
    if (id in snapshotBodies) return { scorecard: snapshotBodies[id] };
    throw new ApiError('Assessment not found.', 404);
  }
  throw new Error(`unexpected GET ${path}`);
}

/** A stream that yields `before`, then waits for `gate`, then yields `after`. */
function gated(before: unknown[], gate: Promise<void>, after: unknown[] = []) {
  return (async function* () {
    for (const e of before) yield e as never;
    await gate;
    for (const e of after) yield e as never;
  })();
}

function gateControl() {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { gate, release };
}

const progress = (seq = 0) => ({ type: 'pillar_progress', key: 'unity_catalog', done: 1, total: 7, detail: 'Scanning', seq });
const cancelled = (seq = 1) => ({ type: 'error', error: 'The assessment was stopped.', reason: 'cancelled', seq });
const end = (next = 2) => ({ type: 'end', next });
const complete = (snapshotId: number | null, seq = 1) => ({
  type: 'complete',
  overall: { score: 80, readiness_stage: 'Core foundation', level: 2, level_label: 'Core', readiness_detail: 'Solid' },
  pillars: [],
  top_gaps: [],
  snapshot_id: snapshotId,
  snapshot_saved: snapshotId != null,
  seq,
});

function config(over: Record<string, unknown> = {}) {
  return {
    pillars: [{ key: 'unity_catalog', name: 'Unity Catalog Foundation' }],
    lakebase_enabled: true,
    history_ready: true,
    workspace_id: '1',
    assess_catalogs: [],
    build: null,
    ...over,
  } as never;
}

function page(over: Record<string, unknown> = {}) {
  return render(createElement(Scorecard, { config: config(over), scorecard: null, setScorecard: vi.fn() }));
}

const runButton = () => screen.getByRole('button', { name: /run assessment/i }) as HTMLButtonElement;

beforeEach(() => {
  active = { run_id: null, viewer: VIEWER };
  snaps = [];
  snapshotBodies = {};
  api.get.mockImplementation(async (path: string) => routeGet(path));
  api.del.mockReset().mockResolvedValue({ cancelled: true });
  api.post.mockReset();
  api.stream.mockReset();
  localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('Assess page: reattach on load', () => {
  it('follows the run that is still working from its first event, and never starts a new one', async () => {
    const { gate, release } = gateControl();
    active = { run_id: 'live1', viewer: VIEWER };
    api.stream.mockImplementation(() => gated([], gate, [progress(0)]));

    page();

    await waitFor(() => expect(api.stream).toHaveBeenCalledWith('/assess/runs/live1/events?after=0', expect.anything()));
    // Until the run has shown an event, the page is attaching: Run is disabled, nothing is shown as running.
    expect(runButton().disabled).toBe(true);
    expect(screen.queryByText(/Assessing your workspace/)).toBeNull();

    release();
    await waitFor(() => expect(screen.getByText(/Assessing your workspace/)).toBeTruthy());
    expect(screen.getByRole('button', { name: /^stop$/i })).toBeTruthy();
    expect(api.post).not.toHaveBeenCalled();
  });
});

describe('Assess page: a start refused because a run is working (409)', () => {
  it('offers the blocking run: Open it follows that run; the page does not start a second one', async () => {
    active = { run_id: null, viewer: VIEWER };
    api.post.mockRejectedValue(new ApiError('An assessment is already running.', 409));
    page();
    await waitFor(() => expect(runButton().disabled).toBe(false));

    // The blocking run appears only now (the start was refused because it is working).
    active = { run_id: 'busy1', viewer: VIEWER, shared: false };
    api.stream.mockImplementation(() => gated([], Promise.resolve(), [progress(0), complete(70, 1)]));
    fireEvent.click(runButton());

    expect(await screen.findByText(/An assessment is already running/)).toBeTruthy();
    expect(screen.getByRole('button', { name: /open it/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: /stop it/i })).toBeTruthy();
    expect(api.post).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: /open it/i }));
    await waitFor(() => expect(api.stream).toHaveBeenLastCalledWith('/assess/runs/busy1/events?after=0', expect.anything()));
    expect(api.post).toHaveBeenCalledTimes(1);
  });

  it('says the earlier assessment just finished when no run is left to open', async () => {
    api.post.mockRejectedValue(new ApiError('An assessment is already running.', 409));
    page();
    await waitFor(() => expect(runButton().disabled).toBe(false));
    fireEvent.click(runButton());
    expect(await screen.findByText(/just finished/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: /open it/i })).toBeNull();
  });
});

describe('Assess page: Stop', () => {
  it('asks first; declining sends nothing; confirming sends the DELETE and says it was stopped', async () => {
    const stopped = gateControl();
    active = { run_id: 'live1', viewer: VIEWER };
    api.stream.mockImplementation(() => gated([progress(0)], stopped.gate, [cancelled(1), end(2)]));
    api.del.mockImplementation(async () => {
      stopped.release();
      return { cancelled: true };
    });
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    page();
    await screen.findByText(/Assessing your workspace/);

    fireEvent.click(screen.getByRole('button', { name: /^stop$/i }));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(api.del).not.toHaveBeenCalled();

    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole('button', { name: /^stop$/i }));
    await waitFor(() => expect(api.del).toHaveBeenCalledWith('/assess/runs/live1'));
    expect(await screen.findByText('The assessment was stopped. Nothing was saved.')).toBeTruthy();
  });

  it('a run that already finished says so (404), and nothing else is sent', async () => {
    active = { run_id: 'live2', viewer: VIEWER };
    api.stream.mockImplementation(() => gated([progress(0)], new Promise(() => undefined)));
    api.del.mockRejectedValue(new ApiError('This assessment is no longer running.', 404));
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    page();
    await screen.findByText(/Assessing your workspace/);

    fireEvent.click(screen.getByRole('button', { name: /^stop$/i }));
    expect(await screen.findByText('That assessment had already finished.')).toBeTruthy();
    expect(api.del).toHaveBeenCalledTimes(1);
  });
});

describe('Assess page: stopping the run that blocked a start', () => {
  async function blockedByBusy1() {
    active = { run_id: null, viewer: VIEWER };
    api.post.mockRejectedValue(new ApiError('An assessment is already running.', 409));
    page();
    await waitFor(() => expect(runButton().disabled).toBe(false));
    active = { run_id: 'busy1', viewer: VIEWER, shared: false };
    fireEvent.click(runButton());
    await screen.findByRole('button', { name: /stop it/i });
  }

  it('Stop it: confirm, DELETE the blocking run, clear the banner, and say it was stopped', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    await blockedByBusy1();

    fireEvent.click(screen.getByRole('button', { name: /stop it/i }));
    await waitFor(() => expect(api.del).toHaveBeenCalledWith('/assess/runs/busy1'));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(await screen.findByText('The earlier assessment was stopped. Nothing was saved. You can run again now.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /stop it/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /open it/i })).toBeNull();
  });

  it('Stop it on a run that already finished (404): says so, and clears the banner', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    api.del.mockRejectedValue(new ApiError('This assessment is no longer running.', 404));
    await blockedByBusy1();

    fireEvent.click(screen.getByRole('button', { name: /stop it/i }));
    expect(await screen.findByText('That assessment had already finished.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /stop it/i })).toBeNull();
  });

  it('Stop it, declined: nothing is sent and the banner stays', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    await blockedByBusy1();

    fireEvent.click(screen.getByRole('button', { name: /stop it/i }));
    expect(api.del).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: /stop it/i })).toBeTruthy();
  });
});

describe('Assess page: a failed stop', () => {
  it('a DELETE that fails (not a 404) is shown, and the run stays on screen', async () => {
    active = { run_id: 'live3', viewer: VIEWER };
    api.stream.mockImplementation(() => gated([progress(0)], new Promise(() => undefined)));
    api.del.mockRejectedValue(new ApiError('HTTP 500', 500));
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    page();
    await screen.findByText(/Assessing your workspace/);

    fireEvent.click(screen.getByRole('button', { name: /^stop$/i }));
    expect(await screen.findByText(/Could not stop the assessment: HTTP 500\. It is still running/)).toBeTruthy();
    // The run is still shown as running, with its Stop button, so the user can try again.
    expect(screen.getByText(/Assessing your workspace/)).toBeTruthy();
    expect(screen.getByRole('button', { name: /^stop$/i })).toBeTruthy();
  });
});

describe('Assess page: a remembered run that is gone', () => {
  const remember = () => localStorage.setItem(REMEMBER_KEY, JSON.stringify({ runId: 'old7', viewer: VIEWER }));
  const goneStream = () =>
    (async function* () {
      throw new ApiError('This assessment run is no longer available.', 404);
    })();

  it('opens the saved result of exactly that run, with a note that it finished while away', async () => {
    remember();
    snaps = [
      { id: 12, created_at: '2026-11-23T11:00:00Z', created_by: VIEWER, overall_score: 55, overall_level: 1, run_id: 'other' },
      { id: 9, created_at: '2026-11-23T10:00:00Z', created_by: VIEWER, overall_score: 80, overall_level: 2, run_id: 'old7' },
    ];
    snapshotBodies = { 9: { overall: { score: 80, readiness_stage: 'Core', level: 2, level_label: 'Core', readiness_detail: 'x' }, pillars: [], top_gaps: [] } };
    api.stream.mockImplementation(goneStream);
    page();

    expect(await screen.findByText('This assessment finished while you were away.')).toBeTruthy();
    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/assess/snapshot/9'));
    expect(api.get).not.toHaveBeenCalledWith('/assess/snapshot/12');
    expect(localStorage.getItem(REMEMBER_KEY)).toBeNull();
  });

  it('says the last assessment did not finish when no saved result belongs to it', async () => {
    remember();
    snaps = [{ id: 12, created_at: '2026-11-23T11:00:00Z', created_by: VIEWER, overall_score: 55, overall_level: 1, run_id: 'other' }];
    api.stream.mockImplementation(goneStream);
    page();

    expect(await screen.findByText(/didn't finish/)).toBeTruthy();
    expect(screen.queryByText('This assessment finished while you were away.')).toBeNull();
  });
});

describe('Assess page: completion refreshes the history list', () => {
  const completedRow = {
    id: 65,
    created_at: '2026-11-23T12:00:00Z',
    created_by: VIEWER,
    overall_score: 80,
    overall_level: 2,
    run_id: 'new1',
  };

  it('a run started from this page: the new result appears in the list without a reload', async () => {
    api.post.mockResolvedValue({ run_id: 'new1' });
    // The server saves the result before it sends `complete`, so the list already has it then.
    api.stream.mockImplementation(() =>
      (async function* () {
        snaps = [completedRow];
        yield complete(65, 0) as never;
      })()
    );
    page();
    await waitFor(() => expect(runButton().disabled).toBe(false));
    expect(screen.queryAllByTestId('history-scope')).toHaveLength(0);

    fireEvent.click(runButton());
    await waitFor(() => expect(screen.getAllByTestId('history-scope')).toHaveLength(1));
  });

  it('a run reattached after a reload: the new result appears in the list without a reload', async () => {
    const { gate, release } = gateControl();
    active = { run_id: 'live66', viewer: VIEWER };
    api.stream.mockImplementation(() =>
      gated([], gate, [progress(0), complete(66, 1)])
    );
    // The saved row exists by the time the run completes.
    const completedAfter = async () => {
      snaps = [{ ...completedRow, id: 66, run_id: 'live66' }];
    };
    page();
    await waitFor(() => expect(api.stream).toHaveBeenCalled());
    expect(screen.queryAllByTestId('history-scope')).toHaveLength(0);

    await completedAfter();
    release();
    await waitFor(() => expect(screen.getAllByTestId('history-scope')).toHaveLength(1));
  });
});

describe('Assess page: history off', () => {
  it('says the tab must stay open while the run is in progress', async () => {
    active = { run_id: 'live1', viewer: VIEWER };
    api.stream.mockImplementation(() => gated([progress(0)], new Promise(() => undefined)));
    page({ lakebase_enabled: false });

    const note = await screen.findByTestId('run-history-off');
    expect(note.textContent).toMatch(/keep this tab open until the assessment finishes/);
  });
});

describe('Assess page: before the catalog and workspace lists arrive', () => {
  it('the catalog picker says Loading, not "No catalogs", and the scope never shows a bare id', async () => {
    const catalogs = gateControl();
    const workspaces = gateControl();
    api.get.mockImplementation(async (path: string) => {
      if (path.startsWith('/catalogs')) {
        await catalogs.gate;
        return { catalogs: [{ name: 'main' }], available: true };
      }
      if (path.startsWith('/workspaces')) {
        await workspaces.gate;
        return { workspaces: [{ id: '1', name: 'prod-ws', is_current: true }], current_workspace_id: '1' };
      }
      return routeGet(path);
    });
    page();

    const picker = await screen.findByRole('button', { name: /catalogs:/i });
    expect(picker.textContent).toMatch(/Loading/);
    expect(picker.textContent).not.toMatch(/No catalogs/);
    expect(screen.getByText(/Scoped to/).textContent).toMatch(/the deployed workspace/);
    expect(screen.getByText(/Scoped to/).textContent).not.toMatch(/\b1\b/);

    catalogs.release();
    workspaces.release();
    await waitFor(() => expect(picker.textContent).toMatch(/All 1 catalogs/));
    await waitFor(() => expect(screen.getByText(/Scoped to/).textContent).toMatch(/prod-ws/));
  });

  it('an empty catalog list, once loaded, says "No catalogs"', async () => {
    page();
    const picker = await screen.findByRole('button', { name: /catalogs:/i });
    await waitFor(() => expect(picker.textContent).toMatch(/No catalogs/));
  });
});
