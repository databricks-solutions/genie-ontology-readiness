import { useEffect, useState } from 'react';
import {
  CheckCircle2, ExternalLink, ChevronDown, Sparkles,
  Rocket, Download, Clock, Copy, Check, TrendingUp,
} from 'lucide-react';
import { apiGet } from '../hooks/useApi';
import type {
  Accelerator, Capability, ContentResponse, ContentQuery,
  Scorecard as ScorecardType, TopAccessedTable,
} from '../types';
import Spinner from './Spinner';

// The top-N most-accessed resources (from the last assessment run) + whether each
// is certified. Lives on the Learn page next to the popularity-discovery SQL.
function TopAccessedTableBlock({ rows }: { rows: TopAccessedTable[] }) {
  if (!rows.length) return null;
  const certified = rows.filter((r) => r.certified).length;
  return (
    <div className="pt-3 border-t border-gray-100">
      <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-ink-400 mb-2">
        <TrendingUp size={13} className="text-databricks-500" /> Your top {rows.length} most-accessed resources — {certified} certified
      </h4>
      <div className="overflow-hidden rounded-md border border-gray-200">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-gray-50 text-left">
              <th className="px-3 py-2 text-xs font-semibold text-ink-600">Resource (catalog.schema.table)</th>
              <th className="px-3 py-2 text-xs font-semibold text-ink-600 text-right">Distinct users (90d)</th>
              <th className="px-3 py-2 text-xs font-semibold text-ink-600 text-center">Certified</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-t border-gray-100">
                <td className="px-3 py-1.5 text-ink-800 font-mono text-xs max-w-[360px] truncate" title={r.name}>{r.name}</td>
                <td className="px-3 py-1.5 text-right tabular-nums text-ink-700">{r.accesses.toLocaleString()}</td>
                <td className="px-3 py-1.5 text-center">
                  {r.certified
                    ? <CheckCircle2 size={15} className="inline text-emerald-500" />
                    : <span className="text-ink-300">—</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function QueriesBlock({ queries }: { queries: ContentQuery[] }) {
  const [copied, setCopied] = useState<number | null>(null);
  async function copy(sql: string, i: number) {
    await navigator.clipboard.writeText(sql);
    setCopied(i);
    setTimeout(() => setCopied((c) => (c === i ? null : c)), 1500);
  }
  return (
    <div className="pt-3 border-t border-gray-100">
      <h4 className="text-xs font-semibold uppercase tracking-wider text-ink-400 mb-2">Queries you can run</h4>
      <div className="space-y-2">
        {queries.map((q, i) => (
          <div key={i} className="rounded-md border border-gray-200 overflow-hidden">
            <div className="flex items-center justify-between gap-2 px-3 py-1.5 bg-gray-50 border-b border-gray-100">
              <span className="text-xs font-medium text-ink-700">{q.title}</span>
              <button
                onClick={() => copy(q.sql, i)}
                className="inline-flex items-center gap-1 rounded bg-white border border-gray-200 hover:bg-databricks-50 hover:text-databricks-700 px-1.5 py-0.5 text-[11px] text-ink-600 transition-colors"
                title="Copy query"
              >
                {copied === i ? <Check size={12} /> : <Copy size={12} />}
                {copied === i ? 'Copied' : 'Copy'}
              </button>
            </div>
            <pre className="bg-ink-900 text-gray-100 text-[11px] leading-relaxed p-3 overflow-x-auto whitespace-pre">{q.sql}</pre>
          </div>
        ))}
      </div>
    </div>
  );
}

const ACCEL_TYPE_LABELS: Record<Accelerator['type'], string> = {
  notebook: 'Notebook',
  sql: 'SQL',
  dab: 'Asset Bundle',
  dashboard: 'Dashboard',
  repo: 'Repository',
  guide: 'Guide',
};

function importCommand(a: Accelerator): string {
  const base = (a.artifact_file || 'notebook.py').replace(/\.py$/, '');
  return (
    `databricks workspace import \\\n` +
    `  --file ${a.artifact_file} --language PYTHON --format SOURCE \\\n` +
    `  /Workspace/Users/<you>/${base}`
  );
}

function AcceleratorCard({ accelerator: a }: { accelerator: Accelerator }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const hasArtifact = Boolean(a.artifact_file);
  // Only code artifacts are imported into the workspace; docs (e.g. a workshop
  // plan) are just downloaded, so don't show a bogus import command for them.
  const importable = hasArtifact && (a.type === 'notebook' || a.type === 'sql');

  async function copyCmd() {
    await navigator.clipboard.writeText(importCommand(a));
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <div className="rounded-lg border border-gray-200 overflow-hidden">
      <div className="px-4 py-3 space-y-2">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h5 className="text-sm font-semibold text-ink-900">{a.title}</h5>
            <p className="text-sm text-ink-600 mt-0.5">{a.summary}</p>
          </div>
          <span className="shrink-0 rounded-full bg-databricks-50 text-databricks-700 px-2 py-0.5 text-[11px] font-medium">
            {ACCEL_TYPE_LABELS[a.type] || a.type}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-ink-400">
          <span className="inline-flex items-center gap-1"><Clock size={12} /> {a.effort}</span>
          {a.review_mode && (
            <span className="inline-flex items-center gap-1 text-emerald-700">
              <CheckCircle2 size={12} /> Review before apply
            </span>
          )}
          {a.valid_as_of && <span>Valid as of {a.valid_as_of}</span>}
        </div>

        <button
          onClick={() => setOpen((o) => !o)}
          className="flex items-center gap-1 text-xs font-medium text-databricks-700 hover:text-databricks-800"
        >
          <ChevronDown size={14} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
          {open ? 'Hide details' : 'How it works & how to run'}
        </button>
      </div>

      {open && (
        <div className="px-4 pb-4 space-y-4 border-t border-gray-100 pt-3">
          <p className="text-sm text-ink-700 leading-relaxed">{a.what_it_does}</p>

          {a.prerequisites?.length > 0 && (
            <div>
              <h6 className="text-xs font-semibold uppercase tracking-wider text-ink-400 mb-1.5">Prerequisites</h6>
              <ul className="space-y-1">
                {a.prerequisites.map((p, i) => (
                  <li key={i} className="text-sm text-ink-700 flex items-start gap-2">
                    <span className="text-ink-300 mt-0.5">•</span><span>{p}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {a.steps?.length > 0 && (
            <div>
              <h6 className="text-xs font-semibold uppercase tracking-wider text-ink-400 mb-1.5">Steps</h6>
              <ol className="space-y-1 list-decimal pl-5">
                {a.steps.map((s, i) => (
                  <li key={i} className="text-sm text-ink-700">{s}</li>
                ))}
              </ol>
            </div>
          )}

          {importable && (
            <div>
              <h6 className="text-xs font-semibold uppercase tracking-wider text-ink-400 mb-1.5">Import command</h6>
              <div className="relative">
                <pre className="bg-ink-900 text-gray-100 text-[11px] rounded-md p-3 overflow-x-auto whitespace-pre">{importCommand(a)}</pre>
                <button
                  onClick={copyCmd}
                  className="absolute top-2 right-2 rounded bg-white/10 hover:bg-white/20 text-white px-1.5 py-1"
                  title="Copy command"
                >
                  {copied ? <Check size={13} /> : <Copy size={13} />}
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2 px-4 py-2.5 bg-gray-50 border-t border-gray-100">
        {hasArtifact && (
          <a
            href={`/api/accelerators/${a.key}/artifact`}
            className="btn-primary py-1 px-3 flex items-center gap-1.5 text-xs"
          >
            <Download size={13} /> Download {a.type === 'notebook' ? 'notebook' : a.type === 'guide' && a.artifact_file?.endsWith('.md') ? 'handbook' : a.artifact_file?.endsWith('.md') ? 'workshop plan' : 'file'}
          </a>
        )}
        {a.source?.url && (
          <a
            href={a.source.url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 rounded-full bg-gray-100 hover:bg-databricks-50 hover:text-databricks-700 px-2.5 py-1 text-xs text-ink-600 transition-colors"
          >
            <ExternalLink size={12} /> {a.source.title}
          </a>
        )}
      </div>
    </div>
  );
}

export function AcceleratorsSection({ accelerators }: { accelerators: Accelerator[] }) {
  if (!accelerators?.length) return null;
  return (
    <div>
      <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-ink-400 mb-2">
        <Rocket size={13} className="text-databricks-500" /> Accelerators — raise this score yourself
      </h4>
      <div className="space-y-3">
        {accelerators.map((a) => (
          <AcceleratorCard key={a.key} accelerator={a} />
        ))}
      </div>
    </div>
  );
}

function ValueBlock({ title, body }: { title: string; body: string }) {
  return (
    <div>
      <h4 className="text-xs font-semibold uppercase tracking-wider text-ink-400 mb-1">{title}</h4>
      <p className="text-sm text-ink-700 leading-relaxed">{body}</p>
    </div>
  );
}

function ListBlock({ title, items }: { title: string; items: string[] }) {
  if (!items?.length) return null;
  return (
    <div>
      <h4 className="text-xs font-semibold uppercase tracking-wider text-ink-400 mb-2">{title}</h4>
      <ul className="space-y-1.5">
        {items.map((it, i) => (
          <li key={i} className="flex items-start gap-2 text-sm text-ink-700">
            <CheckCircle2 size={15} className="mt-0.5 shrink-0 text-databricks-500" />
            <span>{it}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function CapabilityExplainer({ scorecard }: { scorecard?: ScorecardType | null }) {
  const [caps, setCaps] = useState<Capability[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    apiGet<ContentResponse>('/content')
      .then((r) => {
        setCaps(r.capabilities || []);
        if (r.capabilities?.length) setSelected(r.capabilities[0].key);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="flex justify-center py-20">
        <Spinner label="Loading content..." size={28} />
      </div>
    );
  }
  if (error) {
    return <p className="text-sm text-red-600">{error}</p>;
  }

  const cap = caps.find((c) => c.key === selected);

  // For the Domains capability, surface the top-accessed resources from the last
  // assessment run (if one exists), right beside the popularity-discovery SQL.
  const topAccessed: TopAccessedTable[] =
    cap?.key === 'domains' && scorecard
      ? ((scorecard.pillars.find((p) => p.key === 'domains')?.metrics
          ?.top_accessed_list as TopAccessedTable[] | undefined) || [])
      : [];

  return (
    <div className="grid grid-cols-1 md:grid-cols-[240px_1fr] gap-6">
      {/* Left nav */}
      <nav className="space-y-1">
        {caps.map((c, idx) => (
          <button
            key={c.key}
            onClick={() => setSelected(c.key)}
            className={`w-full text-left rounded-md px-3 py-2 transition-colors ${
              selected === c.key
                ? 'bg-databricks-50 border border-databricks-200'
                : 'hover:bg-gray-100 border border-transparent'
            }`}
          >
            <span className={`text-sm font-medium ${idx === 0 ? 'text-databricks-700' : 'text-ink-800'}`}>
              {c.name}
            </span>
          </button>
        ))}
      </nav>

      {/* Detail */}
      {cap && (
        <div className="card p-6 space-y-5">
          <div>
            <h2 className="text-2xl font-bold text-ink-900">{cap.name}</h2>
            <p className="text-sm text-ink-500 mt-1">{cap.tagline}</p>
          </div>

          <ValueBlock title="What it is" body={cap.what} />

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
            <ValueBlock title="Technical value" body={cap.technical_value} />
            <ValueBlock title="Business value" body={cap.business_value} />
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
            <ListBlock title="Technical recommendations" items={cap.technical_enablement} />
            <ListBlock title="Business recommendations" items={cap.business_adoption} />
          </div>

          <div>
            <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-ink-400 mb-2">
              <Sparkles size={13} className="text-databricks-500" /> Best practices
            </h4>
            <ul className="space-y-1.5">
              {cap.best_practices.map((b, i) => (
                <li key={i} className="flex items-start gap-2 text-sm text-ink-700">
                  <CheckCircle2 size={15} className="mt-0.5 shrink-0 text-databricks-500" />
                  <span>{b}</span>
                </li>
              ))}
            </ul>
          </div>

          {cap.queries && cap.queries.length > 0 && (
            <QueriesBlock queries={cap.queries} />
          )}

          {topAccessed.length > 0 && <TopAccessedTableBlock rows={topAccessed} />}

          {cap.accelerators && cap.accelerators.length > 0 && (
            <AcceleratorsSection accelerators={cap.accelerators} />
          )}

          {cap.sources?.length > 0 && (
            <div className="pt-3 border-t border-gray-100">
              <h4 className="text-xs font-semibold uppercase tracking-wider text-ink-400 mb-2">Sources</h4>
              <ul className="flex flex-wrap gap-2">
                {cap.sources.map((src, i) => (
                  <li key={i}>
                    <a
                      href={src.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 rounded-full bg-gray-100 hover:bg-databricks-50 hover:text-databricks-700 px-2.5 py-1 text-xs text-ink-600 transition-colors"
                    >
                      <ExternalLink size={12} />
                      {src.title}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
