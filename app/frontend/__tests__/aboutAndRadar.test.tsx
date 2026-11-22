import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { renderToStaticMarkup } from 'react-dom/server';
import { createElement } from 'react';
import { RadarScoreDot } from '../src/components/Scorecard';
import { AboutOverview } from '../src/components/CapabilityExplainer';
import type { Capability } from '../src/types';

// Hermetic (react-dom/server, no jsdom): the radar point label, the About
// overview, and the tab order.

const HERE = dirname(fileURLToPath(import.meta.url));

describe('RadarScoreDot', () => {
  const centre = { cx: 100, cy: 100 };

  it('labels the point with its rounded score', () => {
    const html = renderToStaticMarkup(
      createElement('svg', null,
        createElement(RadarScoreDot, { cx: 100, cy: 50, value: 47.4, index: 0, points: [centre] })),
    );
    expect(html).toContain('data-testid="radar-score-label"');
    expect(html).toMatch(/>47<\/text>/);
  });

  it('places the label outward from the centre', () => {
    // Point straight above the centre: the label must sit above the point.
    const html = renderToStaticMarkup(
      createElement('svg', null,
        createElement(RadarScoreDot, { cx: 100, cy: 50, value: 50, index: 0, points: [centre] })),
    );
    const y = Number(html.match(/<text[^>]* y="([\d.]+)"/)?.[1]);
    expect(y).toBeLessThan(50);
  });

  it('labels a 0 score sitting on the centre', () => {
    const html = renderToStaticMarkup(
      createElement('svg', null,
        createElement(RadarScoreDot, { cx: 100, cy: 100, value: 0, index: 0, points: [centre] })),
    );
    expect(html).toMatch(/>0<\/text>/);
  });

  it('draws nothing for a point with no position', () => {
    const html = renderToStaticMarkup(
      createElement('svg', null, createElement(RadarScoreDot, { cx: NaN, cy: NaN, value: 10, index: 0 })),
    );
    expect(html).toBe('<svg></svg>');
  });
});

describe('AboutOverview', () => {
  const caps = [
    { key: 'overview', name: 'Genie Ontology — the big picture', tagline: 'The context layer' },
    { key: 'metadata', name: 'Metadata Richness', tagline: 'Comments and tags' },
  ] as unknown as Capability[];

  it('describes the tool and links into every section', () => {
    const html = renderToStaticMarkup(
      createElement(AboutOverview, { caps, onSelect: () => {}, onNavigate: () => {} }),
    );
    expect(html).toContain('About this tool');
    expect(html).toContain('Dive into each section');
    expect(html).toContain('Go to Assess');
    expect(html).toContain('Go to Plan');
    for (const c of caps) expect(html).toContain(c.name);
  });
});

describe('App tabs', () => {
  it('puts About first, then Assess and Plan, and opens on About', () => {
    const src = readFileSync(resolve(HERE, '../src/App.tsx'), 'utf8');
    const ids = [...src.matchAll(/\{ id: '(\w+)', label: '(\w+)'/g)].map((m) => `${m[1]}:${m[2]}`);
    expect(ids).toEqual(['about:About', 'assess:Assess', 'plan:Plan']);
    expect(src).toContain("useState<Tab>('about')");
    expect(src).not.toMatch(/label: 'Learn'/);
  });
});
