import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { createElement } from 'react';
import BuildWatermark, { buildLines } from '../src/components/BuildWatermark';
import type { BuildInfo } from '../src/types';

const build: BuildInfo = {
  label: 'v1.4.0 · b5f83a7',
  version: '1.4.0',
  commit: 'b5f83a77380e12ef786580b8634878d33f28a510',
  commit_short: 'b5f83a7',
  ref: null,
  deployment_id: '01f1e2aa5a91126c9a5332e563281bc7',
  deployed_at: '2026-11-17T17:14:51Z',
  source: 'deployment',
};

describe('BuildWatermark', () => {
  it('shows the short label and carries full details in the tooltip', () => {
    const html = renderToStaticMarkup(createElement(BuildWatermark, { build }));
    expect(html).toContain('v1.4.0 · b5f83a7');
    expect(html).toContain('Commit: b5f83a77380e12ef786580b8634878d33f28a510');
    expect(html).toContain('Deployment: 01f1e2aa5a91126c9a5332e563281bc7');
  });

  it('renders nothing without build info', () => {
    expect(renderToStaticMarkup(createElement(BuildWatermark, { build: undefined }))).toBe('');
  });

  it('builds the lines added to error reports', () => {
    expect(buildLines({ ...build, deployment_id: null, deployed_at: null })).toEqual([
      'App build: v1.4.0 · b5f83a7',
      'Commit: b5f83a77380e12ef786580b8634878d33f28a510',
    ]);
    expect(buildLines(undefined)).toEqual([]);
  });
});
