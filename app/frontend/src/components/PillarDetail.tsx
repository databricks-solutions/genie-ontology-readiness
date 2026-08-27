import { useState } from 'react';
import { AlertTriangle, CheckCircle2, Info, Database, ChevronDown } from 'lucide-react';
import type { PillarScore, AppConfig, GenieSpaceCuration, UcSchemaCount, SignalIdentity } from '../types';
import { Badge, type BadgeTone } from './ui/Badge';
import GenieTester from './GenieTester';

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

const GENIE_COLS: { key: keyof Omit<GenieSpaceCuration, 'title' | 'tier' | 'description_ok'>; label: string }[] = [
  { key: 'instructions', label: 'Instructions' },
  { key: 'sample_questions', label: 'Sample Q' },
  { key: 'example_sqls', label: 'Example SQL' },
  { key: 'functions', label: 'Functions' },
  { key: 'benchmarks', label: 'Benchmarks' },
  { key: 'synonyms', label: 'Synonyms' },
  { key: 'tables', label: 'Tables' },
];

// Per-agent curation tier (Genie Workbench IQ vocabulary) → shared Badge tone.
function tierTone(tier?: string): BadgeTone {
  if (tier === 'Trusted') return 'emerald';
  if (tier === 'Ready to Optimize') return 'amber';
  return 'gray';
}

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
              <th className="px-3 py-2 text-xs font-semibold text-ink-600 whitespace-nowrap">Tier</th>
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
                <td className="px-3 py-2">
                  {sp.tier ? (
                    <Badge tone={tierTone(sp.tier)} className="px-1.5 text-[11px]">{sp.tier}</Badge>
                  ) : (
                    <span className="text-ink-300">—</span>
                  )}
                </td>
                {GENIE_COLS.map((c) => {
                  const v = sp[c.key] ?? 0;
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
    return (
      <div className="px-4 pb-4 pt-1 space-y-2">
        <div className="flex items-start gap-2 rounded-md bg-gray-50 border border-gray-200 px-3 py-3 text-sm text-ink-500">
          <Info size={16} className="mt-0.5 shrink-0" />
          <span>{pillar.note || 'This signal is not available in the current workspace context.'}</span>
        </div>
        {pillar.identity && <IdentityLine identity={pillar.identity} />}
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

      {pillar.key === 'genie_agents' && config.genie_space_configured && (
        <GenieTester />
      )}
    </div>
  );
}
