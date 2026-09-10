import { useState } from 'react';
import { AlertTriangle, CheckCircle2, Info, Database, ChevronDown, Lock, Download, Code } from 'lucide-react';
import type {
  PillarScore,
  AppConfig,
  GenieSpaceCuration,
  UcSchemaCount,
  SignalIdentity,
  DrillDown,
  SourceQuery,
  UnavailableReason,
} from '../types';
import { rowsToCsv, downloadCsv, csvFilename } from '../utils/csv';
import GenieTester from './GenieTester';

// Formats a drill-down cell, appending the column unit (e.g. "82%") when present.
function fmtCell(value: string | number | null, unit?: string): string {
  if (value === null || value === undefined) return '—';
  return unit ? `${value}${unit}` : String(value);
}

// Generic per-asset drill-down table (#10): renders any {title, columns, rows}
// payload and offers a client-side CSV export of exactly what's shown (which
// already reflects the active workspace filter). Collapsed by default.
function DrillDownTable({ drill, pillarKey }: { drill: DrillDown; pillarKey: string }) {
  const [open, setOpen] = useState(false);
  if (!drill.rows.length) return null;
  return (
    <div>
      <div className="flex items-center justify-between gap-2">
        <button
          onClick={() => setOpen((o) => !o)}
          className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-ink-400 hover:text-ink-600 transition-colors"
        >
          <ChevronDown size={13} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
          <Database size={13} className="text-databricks-500" />
          {drill.title} — {drill.rows.length} row{drill.rows.length === 1 ? '' : 's'}
        </button>
        {open && (
          <button
            onClick={() => downloadCsv(csvFilename(pillarKey), rowsToCsv(drill.columns, drill.rows))}
            className="flex items-center gap-1 text-[11px] text-databricks-600 hover:text-databricks-800 hover:underline shrink-0"
            title="Download this table as CSV (reflects the current workspace filter)"
          >
            <Download size={12} /> CSV
          </button>
        )}
      </div>
      {open && (
        <div className="overflow-x-auto rounded-md border border-gray-200 mt-2">
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
              {drill.rows.map((r, i) => (
                <tr key={i} className="border-t border-gray-100">
                  {drill.columns.map((c, j) => (
                    <td
                      key={c.key}
                      className={`px-3 py-1.5 ${j === 0 ? 'text-ink-800 font-mono text-xs max-w-[280px] truncate' : 'text-right tabular-nums text-ink-700'}`}
                      title={j === 0 ? String(r[c.key] ?? '') : undefined}
                    >
                      {fmtCell(r[c.key], c.unit)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// Collapsed disclosure of the SQL a pillar ran (#22). Uses a native <details> so
// it's nested and not shown directly. Dark code style matches the app's markdown.
function SourceQueries({ queries }: { queries: SourceQuery[] }) {
  if (!queries.length) return null;
  return (
    <details className="rounded-md border border-gray-200 group">
      <summary className="flex items-center gap-1.5 cursor-pointer select-none px-3 py-2 text-xs font-semibold uppercase tracking-wider text-ink-400 hover:text-ink-600">
        <Code size={13} className="text-ink-400" />
        How this score was generated — {queries.length} quer{queries.length === 1 ? 'y' : 'ies'}
      </summary>
      <div className="px-3 pb-3 space-y-2">
        {queries.map((q, i) => (
          <pre key={i} className="bg-ink-900 text-gray-100 rounded-md p-3 overflow-x-auto text-[11px] leading-relaxed whitespace-pre-wrap">
            {q.sql}
            {q.parameters && Object.keys(q.parameters).length > 0
              ? `\n-- parameters: ${JSON.stringify(q.parameters)}`
              : ''}
          </pre>
        ))}
      </div>
    </details>
  );
}

// Icon + palette for an unavailable pillar's reason (#20), so a scan/access
// failure is visually distinct from a genuine 0.
function unavailableStyle(reason: UnavailableReason | null) {
  switch (reason) {
    case 'insufficient_permission':
      return { Icon: Lock, box: 'bg-red-50 border-red-200 text-red-800', label: 'Insufficient permission' };
    case 'scan_failed':
      return { Icon: AlertTriangle, box: 'bg-amber-50 border-amber-200 text-amber-800', label: 'Scan failed' };
    default:
      return { Icon: Info, box: 'bg-gray-50 border-gray-200 text-ink-500', label: 'Not available' };
  }
}

// A visible "read as" line naming the identity that served this pillar's reads
// (OBO viewer / SP fallback / SP-forced), so viewers know whose grants the signal
// reflects — without relying on a hover tooltip.
function IdentityLine({ identity }: { identity: SignalIdentity }) {
  return (
    <p className="flex items-start gap-1.5 text-xs text-ink-500 leading-relaxed">
      <Info size={13} className="mt-0.5 shrink-0" />
      <span>
        <span className="font-semibold text-ink-600">Read as {identity.label}.</span>{' '}
        {identity.detail}
      </span>
    </p>
  );
}

// Click-to-expand breakdown of the tables that are NOT in Unity Catalog, grouped
// by legacy hive_metastore schema. Collapsed by default so a long list doesn't
// dominate the pillar.
function LegacyBreakdown({ rows }: { rows: UcSchemaCount[] }) {
  const [open, setOpen] = useState(false);
  if (!rows.length) return null;
  const total = rows.reduce((sum, r) => sum + r.tables, 0);
  return (
    <div>
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-ink-400 hover:text-ink-600 transition-colors"
      >
        <ChevronDown size={13} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
        <Database size={13} className="text-amber-500" />
        Not in Unity Catalog — {total} legacy table{total === 1 ? '' : 's'} by schema
      </button>
      {open && (
        <div className="overflow-hidden rounded-md border border-gray-200 mt-2">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 text-left">
                <th className="px-3 py-2 text-xs font-semibold text-ink-600">hive_metastore schema</th>
                <th className="px-3 py-2 text-xs font-semibold text-ink-600 text-right">Tables</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i} className="border-t border-gray-100">
                  <td className="px-3 py-1.5 text-ink-800 font-mono text-xs max-w-[320px] truncate" title={r.schema}>{r.schema}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums text-ink-700">{r.tables}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

const GENIE_COLS: { key: keyof Omit<GenieSpaceCuration, 'title'>; label: string }[] = [
  { key: 'instructions', label: 'Instructions' },
  { key: 'sample_questions', label: 'Sample Q' },
  { key: 'example_sqls', label: 'Example SQL' },
  { key: 'functions', label: 'Functions' },
  { key: 'benchmarks', label: 'Benchmarks' },
  { key: 'tables', label: 'Tables' },
];

function GenieSpacesTable({ spaces }: { spaces: GenieSpaceCuration[] }) {
  return (
    <div>
      <h4 className="text-xs font-semibold uppercase tracking-wider text-ink-400 mb-2">
        Per-space curation
      </h4>
      <div className="overflow-x-auto rounded-md border border-gray-200">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-gray-50 text-left">
              <th className="px-3 py-2 text-xs font-semibold text-ink-600">Genie Agent</th>
              {GENIE_COLS.map((c) => (
                <th key={c.key} className="px-3 py-2 text-xs font-semibold text-ink-600 text-center whitespace-nowrap">
                  {c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {spaces.map((sp, i) => (
              <tr key={i} className="border-t border-gray-100">
                <td className="px-3 py-2 text-ink-800 max-w-[260px] truncate" title={sp.title}>{sp.title}</td>
                {GENIE_COLS.map((c) => {
                  const v = sp[c.key];
                  return (
                    <td key={c.key} className="px-3 py-2 text-center tabular-nums">
                      {v > 0 ? (
                        <span className="text-emerald-700 font-medium">{v}</span>
                      ) : (
                        <span className="text-ink-300">—</span>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function PillarDetail({
  pillar,
  config,
}: {
  pillar: PillarScore;
  config: AppConfig;
}) {
  if (!pillar.available) {
    const { Icon, box, label } = unavailableStyle(pillar.unavailable_reason);
    return (
      <div className="px-4 pb-4 pt-1 space-y-2">
        <div className={`flex items-start gap-2 rounded-md border px-3 py-3 text-sm ${box}`}>
          <Icon size={16} className="mt-0.5 shrink-0" />
          <span>
            <span className="font-semibold">{label}.</span>{' '}
            {pillar.note || 'This signal is not available in the current workspace context.'}
          </span>
        </div>
        {pillar.identity && <IdentityLine identity={pillar.identity} />}
        <SourceQueries queries={pillar.source_queries} />
      </div>
    );
  }

  return (
    <div className="px-4 pb-4 pt-1 space-y-4">
      {pillar.summary && (
        <p className="text-sm text-ink-700 leading-relaxed">{pillar.summary}</p>
      )}

      {pillar.identity && <IdentityLine identity={pillar.identity} />}

      {pillar.signals.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wider text-ink-400 mb-2">
            Signals
          </h4>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
            {pillar.signals.map((s, i) => (
              <div key={i} className="stat-tile" title={s.detail}>
                <div className="text-xs text-ink-400">{s.label}</div>
                <div className="text-lg font-semibold text-ink-900 tabular-nums">
                  {s.value}
                  {s.unit ? <span className="text-xs font-normal text-ink-400 ml-0.5">{s.unit}</span> : null}
                </div>
                {s.detail && <div className="text-[11px] text-ink-400 mt-0.5 leading-snug">{s.detail}</div>}
              </div>
            ))}
          </div>
        </div>
      )}

      {pillar.gaps.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wider text-ink-400 mb-2">
            Gaps
          </h4>
          <ul className="space-y-1.5">
            {pillar.gaps.map((g, i) => (
              <li key={i} className="flex items-start gap-2 text-sm text-amber-800">
                <AlertTriangle size={15} className="mt-0.5 shrink-0 text-amber-500" />
                <span>{g}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {pillar.best_practices.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wider text-ink-400 mb-2">
            Best practices
          </h4>
          <ul className="space-y-1.5">
            {pillar.best_practices.map((b, i) => (
              <li key={i} className="flex items-start gap-2 text-sm text-ink-700">
                <CheckCircle2 size={15} className="mt-0.5 shrink-0 text-emerald-500" />
                <span>{b}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Per-asset drill-down: where the gap is, by catalog/schema/agent/workspace (#10). */}
      {pillar.drill_down && <DrillDownTable drill={pillar.drill_down} pillarKey={pillar.key} />}

      {pillar.key === 'uc_foundation' &&
        Array.isArray(pillar.metrics?.legacy_by_schema) &&
        (pillar.metrics.legacy_by_schema as UcSchemaCount[]).length > 0 && (
          <LegacyBreakdown rows={pillar.metrics.legacy_by_schema as UcSchemaCount[]} />
        )}

      {pillar.key === 'genie_agents' &&
        Array.isArray(pillar.metrics?.spaces) &&
        (pillar.metrics.spaces as GenieSpaceCuration[]).length > 0 && (
          <GenieSpacesTable spaces={pillar.metrics.spaces as GenieSpaceCuration[]} />
        )}

      {/* The SQL behind this score (#22) — collapsed by default. */}
      <SourceQueries queries={pillar.source_queries} />

      {pillar.key === 'genie_agents' && config.genie_space_configured && (
        <GenieTester />
      )}
    </div>
  );
}
