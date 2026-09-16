import { describe, it, expect } from 'vitest';
import {
  defaultComparisonPair,
  formatDelta,
  formatScore,
  statusClass,
  statusLabel,
} from '../src/utils/compareFormat';
import type { HistorySnapshot } from '../src/types';

const history: HistorySnapshot[] = [
  { id: 3, created_at: '2026-03-03T00:00:00Z', created_by: 'user', overall_score: 60, overall_level: 3 },
  { id: 1, created_at: '2026-03-01T00:00:00Z', created_by: 'user', overall_score: 40, overall_level: 2 },
  { id: 2, created_at: '2026-03-02T00:00:00Z', created_by: 'user', overall_score: 50, overall_level: 2 },
];

describe('compareFormat', () => {
  it('signs positive deltas and leaves zero / negative unsigned', () => {
    expect(formatDelta(6.2)).toBe('+6');
    expect(formatDelta(0)).toBe('0');
    expect(formatDelta(-4.4)).toBe('-4');
  });

  it('renders missing scores and deltas as n/a', () => {
    expect(formatDelta(null)).toBe('n/a');
    expect(formatScore(null)).toBe('n/a');
    expect(statusLabel('unavailable')).toBe('n/a');
  });

  it('maps direction to color classes', () => {
    expect(statusClass('improved')).toContain('emerald');
    expect(statusClass('declined')).toContain('red');
    expect(statusClass('unavailable')).toContain('ink-400');
  });

  it('pairs a selected run with the immediately preceding run', () => {
    expect(defaultComparisonPair(history, 2)).toEqual({ baselineId: 1, currentId: 2 });
    expect(defaultComparisonPair(history, 3)).toEqual({ baselineId: 2, currentId: 3 });
  });

  it('keeps chronological direction when the earliest run is selected', () => {
    expect(defaultComparisonPair(history, 1)).toEqual({ baselineId: 1, currentId: 2 });
  });
});
