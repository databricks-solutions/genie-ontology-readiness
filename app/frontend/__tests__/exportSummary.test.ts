import { describe, it, expect } from 'vitest';
import { buildSummaryCsv } from '../src/utils/exportAll';
import type { PillarScore } from '../src/types';

const base = {
  short: '', capability: '', weight: 10, technical_score: null, note: null, signals: [], gaps: [],
  metrics: {}, unavailable_reason: null,
} as unknown as PillarScore;

describe('buildSummaryCsv', () => {
  it('writes a not-scored pillar without a score or level', () => {
    const csv = buildSummaryCsv([
      { ...base, key: 'metrics', name: 'Metrics', score: 80, level: 3, level_label: 'Managed', available: true },
      {
        ...base, key: 'domains', name: 'Domains & Stewardship', score: 0, level: 0, level_label: 'Foundational',
        available: false, score_excluded: true,
        score_excluded_reason: "The app's service principal needs MANAGE DISCOVER to read Discover domains.",
      },
    ]);
    expect(csv).toContain('Metrics,80,3,Managed,yes');
    expect(csv).toMatch(/Domains & Stewardship,not scored,,,no,.*MANAGE DISCOVER/);
    expect(csv).not.toContain('Foundational');
  });
});
