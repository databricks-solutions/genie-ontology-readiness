import { describe, it, expect } from 'vitest';
import { selectedFirst } from '../src/utils/pickerOrder';

const names = (xs: { name: string }[]) => xs.map((x) => x.name);
const catalogs = ['alpha', 'bronze', 'core', 'delta', 'edge', 'finance'].map((name) => ({ name }));

describe('selectedFirst', () => {
  it('moves selected items to the top and keeps each group in its original order', () => {
    const sel = new Set(['delta', 'bronze']);
    const out = selectedFirst(catalogs, (c) => sel.has(c.name));
    expect(names(out)).toEqual(['bronze', 'delta', 'alpha', 'core', 'edge', 'finance']);
  });

  it('leaves the order unchanged when nothing is selected', () => {
    expect(names(selectedFirst(catalogs, () => false))).toEqual(names(catalogs));
  });

  it('leaves the order unchanged when everything is selected', () => {
    expect(names(selectedFirst(catalogs, () => true))).toEqual(names(catalogs));
  });

  it('does not mutate the input array', () => {
    const copy = [...catalogs];
    selectedFirst(catalogs, (c) => c.name === 'finance');
    expect(catalogs).toEqual(copy);
  });

  it('orders by a snapshot, so a later selection change does not move rows', () => {
    // The picker snapshots the selection on open. Ticking "finance" afterwards must
    // leave it in the unselected block, where it was when the list opened.
    const snapshot = new Set(['delta']);
    expect(names(selectedFirst(catalogs, (c) => snapshot.has(c.name)))).toEqual([
      'delta', 'alpha', 'bronze', 'core', 'edge', 'finance',
    ]);
    // Only a fresh snapshot (on the next open or search) would pull "finance" up.
    const liveSelection = new Set(['delta', 'finance']);
    expect(names(selectedFirst(catalogs, (c) => liveSelection.has(c.name)))).toEqual([
      'delta', 'finance', 'alpha', 'bronze', 'core', 'edge',
    ]);
  });

  it('keeps selected items first within a search-filtered list', () => {
    const q = 'e';
    const matches = catalogs.filter((c) => c.name.includes(q)); // bronze, core, delta, edge, finance
    const sel = new Set(['finance', 'core']);
    expect(names(selectedFirst(matches, (c) => sel.has(c.name)))).toEqual([
      'core', 'finance', 'bronze', 'delta', 'edge',
    ]);
  });
});
