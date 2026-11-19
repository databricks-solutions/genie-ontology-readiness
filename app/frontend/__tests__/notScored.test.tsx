import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { createElement } from 'react';
import { ExcludedPillarsNote, NOT_SCORED_EXPLAINER } from '../src/components/NotScored';

describe('ExcludedPillarsNote', () => {
  it('names each pillar that is not scored and says why', () => {
    const html = renderToStaticMarkup(
      createElement(ExcludedPillarsNote, {
        excluded: [
          {
            key: 'domains',
            name: 'Domains & Stewardship',
            reason: "The app's service principal needs MANAGE DISCOVER to read Discover domains.",
          },
        ],
      }),
    );
    expect(html).toContain('Not scored:');
    expect(html).toContain('Domains &amp; Stewardship');
    expect(html).toContain('instead of counting it as 0');
    expect(html).toContain('needs MANAGE DISCOVER to read Discover domains');
  });

  it('renders nothing when every pillar is scored (or on older snapshots)', () => {
    expect(renderToStaticMarkup(createElement(ExcludedPillarsNote, { excluded: [] }))).toBe('');
    expect(renderToStaticMarkup(createElement(ExcludedPillarsNote, {}))).toBe('');
  });

  it('explains that the pillar is left out rather than scored 0', () => {
    expect(NOT_SCORED_EXPLAINER).toContain('left out of the overall score instead of counting as 0');
  });
});
