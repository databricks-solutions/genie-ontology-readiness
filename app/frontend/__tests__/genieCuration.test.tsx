import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { createElement } from 'react';
import PillarDetail from '../src/components/PillarDetail';
import type { AppConfig, GenieSpaceCuration, PillarScore } from '../src/types';

// The Genie Agents per-agent curation table is best-effort and read "where visible"
// (#24): an agent whose serialized space couldn't be read shows "not assessed", never
// 0, so a visibility limit isn't mistaken for poor curation.

const config: AppConfig = {
  app_name: 'gor',
  brand_name: 'Databricks',
  workspace_id: '1',
  level_labels: [],
  pillars: [],
  ai_models: [],
  default_model: '',
  lakebase_enabled: false,
  genie_space_configured: false, // keep GenieTester out of the static render
  assess_catalogs: [],
};

function geniePillar(spaces: GenieSpaceCuration[]): PillarScore {
  return {
    key: 'genie_agents',
    name: 'Genie Agents',
    short: '',
    capability: 'genie_agents',
    weight: 16,
    score: 42,
    technical_score: 42,
    level: 2,
    level_label: '',
    available: true,
    note: null,
    signals: [],
    gaps: [],
    best_practices: [],
    summary: '',
    metrics: { spaces },
    identity: null,
    drill_down: null,
    source_queries: [],
    unavailable_reason: null,
  };
}

const readable: GenieSpaceCuration = {
  title: 'Sales Agent', readable: true, type: 'standalone',
  instructions: 3, sample_questions: 4, example_sqls: 2, functions: 0, benchmarks: 1, tables: 5,
};
const notReadable: GenieSpaceCuration = {
  title: 'Gated Agent', readable: false, type: 'standalone',
  instructions: null, sample_questions: null, example_sqls: null,
  functions: null, benchmarks: null, tables: null,
};

describe('GenieSpacesTable (per-agent curation, where visible)', () => {
  it('renders curation counts for a readable agent', () => {
    const html = renderToStaticMarkup(
      createElement(PillarDetail, { pillar: geniePillar([readable]), config }),
    );
    expect(html).toContain('Per-agent curation');
    expect(html).toContain('Sales Agent');
    expect(html).toContain('>3<'); // instructions count shown
    expect(html).toContain('where visible');
  });

  it('shows an unreadable agent as "not assessed", never as 0', () => {
    const html = renderToStaticMarkup(
      createElement(PillarDetail, { pillar: geniePillar([notReadable]), config }),
    );
    expect(html).toContain('Gated Agent');
    expect(html).toContain('not assessed');
    expect(html).toContain('needs CAN_EDIT');
    expect(html).toContain('never affects the score');
    // The not-readable row must not render a curation "0" for any dimension.
    expect(html).not.toContain('>0<');
  });
});
