import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { renderToStaticMarkup } from 'react-dom/server';
import { createElement } from 'react';
import { AcceleratorsSection } from '../src/components/CapabilityExplainer';
import type { Accelerator, AcceleratorType } from '../src/types';

// The Learn tab must surface at least one accelerator card for EVERY pillar /
// capability. Accelerator cards are not hard-coded in the frontend — they are
// driven by the backend registry (app/server/content/accelerators.py, exposed
// per-capability via `accelerators_for(cap.key)`) and rendered verbatim by the
// `AcceleratorsSection` component. So this test reads the real registry (the
// source of truth), groups it exactly like the backend does, and then renders
// the genuine UI component to prove a card is produced for each pillar.
//
// Fully hermetic: no live network, no running backend, no jsdom — the component
// is rendered to static HTML via react-dom/server.

// The 7 capability keys the app scores. Each MUST have >=1 accelerator.
const CAPABILITY_KEYS = [
  'unity_catalog',
  'metadata',
  'relationships',
  'metric_views',
  'genie_spaces',
  'domains',
  'adoption',
] as const;

const HERE = dirname(fileURLToPath(import.meta.url));
const REGISTRY_PATH = resolve(HERE, '../../server/content/accelerators.py');

interface ParsedEntry {
  key: string;
  title: string;
  capability: string;
  type: string;
}

// Parse the Python registry the same way the backend groups it: each entry is a
// dict beginning with a "key" field; within that block the first title /
// capability / type fields belong to that entry (mirrors accelerators_for()).
function parseRegistry(): ParsedEntry[] {
  const src = readFileSync(REGISTRY_PATH, 'utf8');
  const blocks = src.split(/"key":\s*"/).slice(1);
  const entries: ParsedEntry[] = [];
  for (const block of blocks) {
    const key = block.match(/^([a-z0-9_-]+)"/)?.[1];
    const title = block.match(/"title":\s*"([^"]*)"/)?.[1];
    const capability = block.match(/"capability":\s*"([^"]*)"/)?.[1];
    const type = block.match(/"type":\s*"([^"]*)"/)?.[1];
    // Only real accelerator entries carry a capability (skip the ACCELERATORS_BY_KEY
    // comprehension `a["key"]` and any incidental "key" strings).
    if (key && title && capability && type) {
      entries.push({ key, title, capability, type });
    }
  }
  return entries;
}

// Group accelerators by capability, exactly like the backend `accelerators_for`.
function acceleratorsByCapability(entries: ParsedEntry[]): Record<string, ParsedEntry[]> {
  const grouped: Record<string, ParsedEntry[]> = {};
  for (const e of entries) {
    (grouped[e.capability] ??= []).push(e);
  }
  return grouped;
}

// Build a minimal-but-valid Accelerator object the card component can render.
function toAccelerator(e: ParsedEntry): Accelerator {
  return {
    key: e.key,
    title: e.title,
    summary: `${e.title} — summary`,
    capability: e.capability,
    type: e.type as AcceleratorType,
    effort: '~1 hour',
    what_it_does: 'what it does',
    prerequisites: [],
    improves_signals: [e.capability],
    target_level: 3,
    review_mode: false,
    steps: [],
  };
}

// The AcceleratorCard root element carries this stable class string; counting it
// tells us how many cards actually rendered.
const CARD_MARKER = 'rounded-lg border border-gray-200 overflow-hidden';

function renderSection(accelerators: Accelerator[]): string {
  return renderToStaticMarkup(createElement(AcceleratorsSection, { accelerators }));
}

function countCards(html: string): number {
  return html.split(CARD_MARKER).length - 1;
}

const entries = parseRegistry();
const grouped = acceleratorsByCapability(entries);

describe('accelerator registry coverage', () => {
  it('parses accelerators from the backend registry', () => {
    expect(entries.length).toBeGreaterThan(0);
  });

  it.each(CAPABILITY_KEYS)('pillar "%s" has at least one accelerator', (key) => {
    const list = grouped[key] ?? [];
    expect(list.length).toBeGreaterThanOrEqual(1);
  });
});

describe('AcceleratorsSection renders a card per pillar', () => {
  it.each(CAPABILITY_KEYS)('renders >=1 accelerator card for pillar "%s"', (key) => {
    const accelerators = (grouped[key] ?? []).map(toAccelerator);
    const html = renderSection(accelerators);

    // The per-pillar section heading is present.
    expect(html).toContain('Accelerators');
    // Exactly one card per registry accelerator for this pillar, and at least one.
    const cards = countCards(html);
    expect(cards).toBe(accelerators.length);
    expect(cards).toBeGreaterThanOrEqual(1);
  });

  it('every one of the 7 pillars surfaces at least one card (aggregate)', () => {
    const uncovered = CAPABILITY_KEYS.filter((key) => {
      const accelerators = (grouped[key] ?? []).map(toAccelerator);
      if (accelerators.length === 0) return true;
      return countCards(renderSection(accelerators)) < 1;
    });
    expect(uncovered).toEqual([]);
  });
});
