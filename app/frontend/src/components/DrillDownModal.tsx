import { useEffect, useMemo, useState } from 'react';
import { X, Download, Copy, Check, Code, Database } from 'lucide-react';
import type { DrillDown, SourceQuery } from '../types';
import { rowsToCsv, downloadCsv, csvFilename } from '../utils/csv';

type Row = Record<string, string | number | null>;

const DIM_LABEL: Record<string, string> = {
  workspace: 'Workspace',
  catalog: 'Catalog',
  schema: 'Schema',
  agent: 'Agent',
};

// Focused overlay for a pillar's drill-down (#10/#22): a filterable table (slice by
// workspace/catalog/schema where present), CSV export of the filtered view, and the
// source SQL with copy-to-clipboard. Opened from the "Drill down" button.
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
  const [copied, setCopied] = useState<number | null>(null);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

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

  async function copy(sql: string, i: number) {
    await navigator.clipboard.writeText(sql);
    setCopied(i);
    setTimeout(() => setCopied((c) => (c === i ? null : c)), 1500);
  }

  return (
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
              {/* Slice-by filters + CSV export */}
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
                  onClick={() => downloadCsv(csvFilename(pillarKey), rowsToCsv(drill.columns, filtered))}
                  className="btn-secondary py-1.5 px-3 flex items-center gap-1.5 text-xs"
                >
                  <Download size={13} /> Export CSV
                </button>
              </div>

              <div className="text-[11px] text-ink-400">{filtered.length} of {rows.length} rows</div>

              <div className="overflow-x-auto rounded-md border border-gray-200">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="bg-gray-50 text-left">
                      {drill.columns.map((c) => (
                        <th key={c.key} className="px-3 py-2 text-xs font-semibold text-ink-600 whitespace-nowrap">
                          {c.label}{c.unit ? <span className="text-ink-400 font-normal"> ({c.unit})</span> : null}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.map((r, i) => (
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
            <p className="text-sm text-ink-400">No drill-down data for this pillar.</p>
          )}

          {/* Source SQL with copy-to-clipboard */}
          {queries.length > 0 && (
            <div className="pt-2 border-t border-gray-100">
              <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-ink-400 mb-2">
                <Code size={13} /> SQL behind this score
              </h4>
              <div className="space-y-2">
                {queries.map((q, i) => (
                  <div key={i} className="rounded-md border border-gray-200 overflow-hidden">
                    <div className="flex items-center justify-between gap-2 px-3 py-1.5 bg-gray-50 border-b border-gray-100">
                      <span className="text-[11px] font-medium text-ink-600">Query {i + 1}</span>
                      <button
                        onClick={() => copy(q.sql, i)}
                        className="inline-flex items-center gap-1 rounded bg-white border border-gray-200 hover:bg-databricks-50 hover:text-databricks-700 px-1.5 py-0.5 text-[11px] text-ink-600 transition-colors"
                        title="Copy query"
                      >
                        {copied === i ? <Check size={12} /> : <Copy size={12} />}
                        {copied === i ? 'Copied' : 'Copy'}
                      </button>
                    </div>
                    <pre className="bg-ink-900 text-gray-100 text-[11px] leading-relaxed p-3 overflow-x-auto whitespace-pre-wrap">
                      {q.sql}
                      {q.parameters && Object.keys(q.parameters).length > 0
                        ? `\n-- parameters: ${JSON.stringify(q.parameters)}`
                        : ''}
                    </pre>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
