// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createElement } from 'react';
import CatalogFilter, { CatalogFallbackNote, CATALOG_FALLBACK_NOTE } from '../src/components/CatalogFilter';
import type { CatalogInfo } from '../src/types';

// The catalog filter surfaces a note only when the catalog list came from the SQL
// fallback (available === false): the UC catalog/bindings REST APIs couldn't be read,
// so there's no binding info. The note must explain the manual-scope fallback and what
// binding-aware scoping needs, and must never show when bindings resolved.

const CATALOGS: CatalogInfo[] = [
  { name: 'main', access: 'ALL', isolation: 'UNKNOWN' },
  { name: 'sales', access: 'ALL', isolation: 'UNKNOWN' },
];

function openFilter(available: boolean) {
  render(
    createElement(CatalogFilter, {
      catalogs: CATALOGS,
      available,
      value: [],
      onChange: vi.fn(),
    })
  );
  fireEvent.click(screen.getByRole('button', { name: /catalogs:/i }));
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('CatalogFallbackNote', () => {
  it('explains the manual scope and what binding-aware scoping needs', () => {
    const html = renderToStaticMarkup(createElement(CatalogFallbackNote));
    expect(html).toContain('Manual catalog scope');
    expect(html).toContain('metastore read');
    expect(html).toContain('OBO');
  });

  it('renders the exported note message verbatim', () => {
    // textContent decodes HTML entities (e.g. the apostrophe), so it matches the raw constant.
    render(createElement(CatalogFallbackNote));
    expect(screen.getByTestId('catalog-fallback').textContent).toBe(CATALOG_FALLBACK_NOTE);
  });
});

describe('CatalogFilter fallback note', () => {
  it('shows the fallback note when bindings are unavailable', () => {
    openFilter(false);
    const note = screen.getByTestId('catalog-fallback');
    expect(note).toBeTruthy();
    expect(note.textContent).toMatch(/Manual catalog scope/);
  });

  it('does not show the note when bindings resolved', () => {
    openFilter(true);
    expect(screen.queryByTestId('catalog-fallback')).toBeNull();
  });
});
