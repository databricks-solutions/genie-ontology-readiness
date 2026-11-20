// A failed jszip chunk load (stale tab after a redeploy) must surface a reload message,
// not an unhandled rejection. Mocking the module to throw makes its dynamic import reject.
import { describe, it, expect, vi } from 'vitest';

vi.mock('jszip', () => {
  throw new Error('Failed to fetch dynamically imported module: jszip.min-OLD.js');
});

import { downloadAllZip, EXPORT_LOAD_ERROR } from '../src/utils/exportAll';

describe('downloadAllZip chunk load failure', () => {
  it('rejects with the reload message when the jszip chunk cannot load', async () => {
    await expect(downloadAllZip([])).rejects.toThrow(EXPORT_LOAD_ERROR);
  });
});
