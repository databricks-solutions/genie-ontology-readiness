import { useEffect, useMemo, useState } from 'react';
import { X, Download, Code, Database, ChevronUp, ChevronDown } from 'lucide-react';
import type { DrillDown, SourceQuery } from '../types';
import { rowsToCsv, downloadCsv, csvFilename } from '../utils/csv';
import SqlModal from './SqlModal';

type Row = Record<string, string | number | null>;

const DIM_LABEL: Record<string, string> = {
  workspace: 'Workspace',
  catalog: 'Catalog',
  schema: 'Schema',
  agent: 'Agent',
};

// Focused overlay for a pillar's drill-down (#10/#22): a filterable, sortable table
// (slice by workspace/catalog/schema where present), CSV export of the current view,
// and a "View SQL" button that opens the source SQL in its own overlay. Opened from
// the "Drill down" button.
export default function DrillDownModal({
  pillarName,
  pillarKey,
  drill,
  queries,
  onClose,
}: {
  pillarName: string;
  pillarKey: string;
  drill: DrillDown | null;
  queries: SourceQuery[];
  onClose: () => void;
}) {
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [sort, setSort] = useState<{ key: string; dir: 'asc' | 'desc' } | null>(null);
  const [sqlOpen, setSqlOpen] = useState(false);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      // Let the SQL overlay handle Escape while it's open, so it closes first.
      if (e.key === 'Escape' && !sqlOpen) onClose();
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose, sqlOpen]);

  const dims = drill?.dimensions ?? [];
  const rows: Row[] = drill?.rows ?? [];

  // Rows matching all active filters EXCEPT the named dimension (for dependent
  // option lists, e.g. schema options narrow to the selected catalog).
  function rowsExcept(dim: string): Row[] {
    return rows.filter((r) =>
      dims.every((d) => d === dim || !filters[d] || String(r[d] ?? '') === filters[d])
    );
  }
  const filtered = useMemo(
    () => rows.filter((r) => dims.every((d) => !filters[d] || String(r[d] ?? '') === filters[d])),
    [rows, dims, filters]
  );

  // Sort the filtered rows on raw cell values: numeric compare when the column holds
  // numbers, else locale string compare; nulls always sort last.
  const sorted = useMemo(() => {
    if (!sort) return filtered;
    const { key, dir } = sort;
    const mul = dir === 'asc' ? 1 : -1;
    const sample = filtered.find((r) => r[key] !== null && r[key] !== undefined)?.[key];
    const numeric = typeof sample === 'number';
    return [...filtered].sort((a, b) => {
      const av = a[key];
      const bv = b[key];
      if (av === null || av === undefined) return 1;
      if (bv === null || bv === undefined) return -1;
      if (numeric) return (Number(av) - Number(bv)) * mul;
      return String(av).localeCompare(String(bv)) * mul;
    });
  }, [filtered, sort]);

  function toggleSort(key: string) {
    setSort((s) => (s && s.key === key ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: 'asc' }));
  }

  const viewSqlButton = queries.length > 0 && (
    <button
      onClick={() => setSqlOpen(true)}
      className="btn-secondary py-1.5 px-3 flex items-center gap-1.5 text-xs"
    >
      <Code size={13} /> View SQL
    </button>
  );

  return (
    <>
      <div
        className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
        onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
      >
        <div className="bg-white rounded-xl shadow-xl w-full max-w-3xl max-h-[85vh] flex flex-col">
          <div className="flex items-center justify-between gap-2 px-5 py-3 border-b border-gray-100">
            <h3 className="flex items-center gap-2 text-sm font-semibold text-ink-900">
              <Database size={16} className="text-databricks-500" />
              {pillarName} — {drill?.title || 'details'}
            </h3>
            <button onClick={onClose} className="text-ink-400 hover:text-ink-700"><X size={18} /></button>
          </div>

          <div className="px-5 py-4 overflow-y-auto space-y-4">
            {drill && drill.rows.length > 0 ? (
              <>
                {/* Slice-by filters + CSV export + View SQL */}
                <div className="flex flex-wrap items-end gap-3">
                  {dims.map((d) => {
                    const opts = Array.from(new Set(rowsExcept(d).map((r) => String(r[d] ?? '')))).filter(Boolean).sort();
                    return (
                      <label key={d} className="flex flex-col gap-1 text-[11px] text-ink-500">
                        {DIM_LABEL[d] || d}
                        <select
                          value={filters[d] || ''}
                          onChange={(e) => setFilters((f) => ({ ...f, [d]: e.target.value }))}
                          className="rounded-md border border-gray-200 px-2 py-1 text-xs text-ink-800 bg-white min-w-[140px]"
                        >
                          <option value="">All</option>
                          {opts.map((o) => <option key={o} value={o}>{o}</option>)}
                        </select>
                      </label>
                    );
                  })}
                  <div className="flex-1" />
                  <button
                    onClick={() => downloadCsv(csvFilename(pillarKey), rowsToCsv(drill.columns, sorted))}
                    className="btn-secondary py-1.5 px-3 flex items-center gap-1.5 text-xs"
                  >
                    <Download size={13} /> Export CSV
                  </button>
                  {viewSqlButton}
                </div>

                <div className="text-[11px] text-ink-400">
                  {filtered.length} of {rows.length} rows{filtered.length > 10 ? ' — scroll for more' : ''}
                </div>

                <div className="overflow-auto max-h-[360px] rounded-md border border-gray-200">
                  <table className="w-full text-sm">
                    <thead className="sticky top-0 z-10">
                      <tr className="text-left">
                        {drill.columns.map((c) => {
                          const active = sort?.key === c.key;
                          return (
                            <th
                              key={c.key}
                              onClick={() => toggleSort(c.key)}
                              aria-sort={active ? (sort!.dir === 'asc' ? 'ascending' : 'descending') : 'none'}
                              className="px-3 py-2 text-xs font-semibold text-ink-600 whitespace-nowrap cursor-pointer select-none bg-gray-50 hover:bg-gray-100"
                              title="Sort by this column"
                            >
                              <span className="inline-flex items-center gap-1">
                                {c.label}{c.unit ? <span className="text-ink-400 font-normal"> ({c.unit})</span> : null}
                                {active ? (sort!.dir === 'asc' ? <ChevronUp size={12} /> : <ChevronDown size={12} />) : null}
                              </span>
                            </th>
                          );
                        })}
                      </tr>
                    </thead>
                    <tbody>
                      {sorted.map((r, i) => (
                        <tr key={i} className="border-t border-gray-100">
                          {drill.columns.map((c, j) => (
                            <td
                              key={c.key}
                              className={`px-3 py-1.5 ${j === 0 ? 'text-ink-800 font-mono text-xs' : 'text-right tabular-nums text-ink-700'}`}
                            >
                              {r[c.key] === null || r[c.key] === undefined ? '—' : `${r[c.key]}${c.unit ? c.unit : ''}`}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            ) : (
              <div className="space-y-3">
                <p className="text-sm text-ink-400">No drill-down data for this pillar.</p>
                {viewSqlButton}
              </div>
            )}
          </div>
        </div>
      </div>

      {sqlOpen && (
        <SqlModal pillarName={pillarName} queries={queries} onClose={() => setSqlOpen(false)} />
      )}
    </>
  );
}
