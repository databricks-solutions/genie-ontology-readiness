import { describe, it, expect } from 'vitest';
import {
  defaultTrendScope,
  resolveTrendScope,
  scopeDetail,
  scopeDifference,
  scopeLabel,
  scopeOptions,
  trendRuns,
} from '../src/utils/scope';
import type { AssessmentScope, HistorySnapshot } from '../src/types';

// "Readiness over time" must only line up runs with the same scope (#89).

const scope = (catalogs: string[], schemas: string[] = []): AssessmentScope => ({
  catalogs,
  schemas,
  workspace_mode: 'include',
  workspace_ids: ['123'],
});

const run = (id: number, day: number, hash: string | null, s: AssessmentScope | null, score = 50): HistorySnapshot => ({
  id,
  created_at: `2026-11-${String(day).padStart(2, '0')}T10:00:00Z`,
  created_by: 'u@x',
  overall_score: score,
  overall_level: 2,
  scope: s,
  scope_hash: hash,
});

// Newest first, as /assess/history returns it.
const history: HistorySnapshot[] = [
  run(5, 5, 'c', scope(['c']), 70),
  run(4, 4, 'ab', scope(['a', 'b']), 65),
  run(3, 3, 'a', scope(['a']), 60),
  run(2, 2, 'a', scope(['a']), 55),
  run(1, 1, null, null, 40), // saved before scopes were recorded
];

describe('scopeLabel / scopeDetail', () => {
  it('labels each kind of scope', () => {
    expect(scopeLabel(null)).toBe('Unknown scope');
    expect(scopeLabel(scope([]))).toBe('All catalogs');
    expect(scopeLabel(scope(['sales']))).toBe('sales');
    expect(scopeLabel(scope(['sales'], ['gold']))).toBe('sales · 1 schema');
    expect(scopeLabel(scope(['a', 'b']))).toBe('a, b');
    expect(scopeLabel(scope(['a', 'b', 'c', 'd']))).toBe('a, b +2 more');
  });

  it('lists every catalog in the tooltip', () => {
    expect(scopeDetail(scope(['a', 'b']))).toContain('Catalogs: a, b');
    expect(scopeDetail(null)).toMatch(/before scopes were recorded/);
  });
});

describe('trend scope', () => {
  it('offers each known scope once, with run counts, and skips unknown scopes', () => {
    expect(scopeOptions(history)).toEqual([
      { hash: 'c', label: 'c', runs: 1 },
      { hash: 'ab', label: 'a, b', runs: 1 },
      { hash: 'a', label: 'a', runs: 2 },
    ]);
  });

  it('defaults to the scope of the run on screen, else the newest known scope', () => {
    expect(defaultTrendScope(history, 3)).toBe('a');
    expect(defaultTrendScope(history, null)).toBe('c');
    expect(defaultTrendScope(history, 1)).toBe('c'); // unknown-scope run → newest known
    expect(defaultTrendScope([run(1, 1, null, null)], null)).toBeNull();
  });

  it('plots only runs with the chosen scope, oldest first', () => {
    expect(trendRuns(history, 'a').map((h) => h.id)).toEqual([2, 3]);
    expect(trendRuns(history, 'ab').map((h) => h.id)).toEqual([4]);
    expect(trendRuns(history, null)).toEqual([]);
  });
});

describe('look-alike scopes', () => {
  it('gives scopes that share a label a short id in the picker', () => {
    const ws2 = { ...scope([]), workspace_ids: ['456'] };
    const opts = scopeOptions([run(2, 2, 'aaaaaa11', scope([])), run(1, 1, 'bbbbbb22', ws2)]);
    expect(opts.map((o) => o.label)).toEqual(['All catalogs · #aaaaaa', 'All catalogs · #bbbbbb']);
  });

  it('names the difference in Compare even when the labels match', () => {
    expect(scopeDifference(scope(['a']), scope(['a', 'b']))).toBe('a vs a, b');
    expect(scopeDifference(scope([]), { ...scope([]), workspace_ids: ['456'] })).toMatch(
      /^both "All catalogs", but not the same/,
    );
  });
});

describe('resolveTrendScope', () => {
  const opts = scopeOptions(history);
  it('keeps a pick that still exists and falls back otherwise', () => {
    expect(resolveTrendScope('ab', opts, 'c')).toBe('ab');
    expect(resolveTrendScope('gone', opts, 'c')).toBe('c');
    expect(resolveTrendScope(null, opts, 'c')).toBe('c');
  });
});
