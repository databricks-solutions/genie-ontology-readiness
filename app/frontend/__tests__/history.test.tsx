import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { createElement } from 'react';
import { historyStatus } from '../src/utils/history';
import { HistoryOffNotice } from '../src/components/HistoryOffNotice';

describe('historyStatus', () => {
  it('tells apart saved, not attached, and attached but unreachable', () => {
    expect(historyStatus({ lakebase_enabled: true, history_ready: true })).toBe('saved');
    expect(historyStatus({ lakebase_enabled: false, history_ready: false })).toBe('not_attached');
    expect(historyStatus({ lakebase_enabled: true, history_ready: false })).toBe('unavailable');
  });

  it('treats an older server without history_ready as saved', () => {
    expect(historyStatus({ lakebase_enabled: true })).toBe('saved');
  });
});

describe('HistoryOffNotice', () => {
  it('says history is off and where to look', () => {
    const html = renderToStaticMarkup(createElement(HistoryOffNotice));
    expect(html).toContain('History is off.');
    expect(html).toContain('lakebase-db');
    expect(html).toContain('Can connect and create');
  });
});
