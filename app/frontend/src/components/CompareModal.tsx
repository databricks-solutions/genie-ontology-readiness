import { Fragment, useEffect, useMemo, useState } from 'react';
import { X, GitCompareArrows, ArrowUp, ArrowDown, Minus, Loader2, AlertTriangle, ChevronDown, CheckCircle2, AlertCircle } from 'lucide-react';
import { apiGet, ApiError } from '../hooks/useApi';
import type { CompareResult, HistorySnapshot, PillarDiff, SignalDiff } from '../types';

function fmtWhen(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

// A signed, colored score delta (gain green / loss red / no-change neutral). For
// pillars with no comparable score on one side it renders a status pill instead
// (new / removed / n/a) so an unavailable→available pillar never reads as +score.
function DeltaCell({ p }: { p: PillarDiff }) {
  if (p.status === 'new') {
    return <span className="inline-flex items-center rounded-full bg-sky-50 text-sky-700 border border-sky-200 px-2 py-0.5 text-[11px] font-medium">Now assessed</span>;
  }
  if (p.status === 'removed') {
    return <span className="inline-flex items-center rounded-full bg-gray-100 text-ink-500 border border-gray-200 px-2 py-0.5 text-[11px] font-medium">No longer assessed</span>;
  }
  if (p.status === 'unavailable' || p.delta === null) {
    return <span className="text-xs text-ink-400">n/a</span>;
  }
  if (p.delta === 0) {
    return (
      <span className="inline-flex items-center gap-1 text-ink-500 font-medium tabular-nums">
        <Minus size={13} /> 0
      </span>
    );
  }
  const up = p.delta > 0;
  const cls = up ? 'text-emerald-600' : 'text-red-600';
  return (
    <span className={`inline-flex items-center gap-1 font-semibold tabular-nums ${cls}`}>
      {up ? <ArrowUp size={13} /> : <ArrowDown size={13} />}
      {up ? '+' : '−'}{Math.abs(p.delta)}
    </span>
  );
}

function scoreText(v: number | null): string {
  return v === null ? '—' : String(Math.round(v * 10) / 10);
}

function signalText(v: number | string | null, unit: string): string {
  if (v === null || v === undefined) return '—';
  return `${v}${unit || ''}`;
}

// A signed signal delta. Direction is shown (arrow + sign) but NOT colored
// good/bad: some signals are "lower is better" (e.g. "Not in Unity Catalog", which
// drops as tables migrate into UC), so a green=up/red=down rule would paint an
// improvement as a regression. Raw signals stay neutral; only the pillar SCORE
// delta (0–100, unambiguously higher-is-better) gets green/red.
function SignalDelta({ s }: { s: SignalDiff }) {
  if (s.delta === null) return <span className="text-ink-300">—</span>;
  if (s.delta === 0) return <span className="text-ink-400 tabular-nums">0</span>;
  const up = s.delta > 0;
  return (
    <span className="inline-flex items-center gap-0.5 font-medium tabular-nums text-ink-600">
      {up ? <ArrowUp size={11} /> : <ArrowDown size={11} />}
      {up ? '+' : '−'}{Math.abs(s.delta)}{s.unit}
    </span>
  );
}

function hasDetail(p: PillarDiff): boolean {
  return p.signals.length > 0 || p.gaps.resolved.length > 0 || p.gaps.introduced.length > 0;
}

// The expandable per-pillar drill-down: signal-by-signal deltas + resolved/new gaps.
function PillarDetailDiff({ p }: { p: PillarDiff }) {
  return (
    <div className="bg-gray-50 rounded-lg p-3 space-y-3">
      {p.signals.length > 0 && (
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-[10px] uppercase tracking-wider text-ink-400">
              <th className="py-1 pr-2 font-medium">Signal</th>
              <th className="py-1 px-2 font-medium text-right">Baseline</th>
              <th className="py-1 px-2 font-medium text-right">Current</th>
              <th className="py-1 pl-2 font-medium text-right">Δ</th>
            </tr>
          </thead>
          <tbody>
            {p.signals.map((s) => (
              <tr key={s.label}>
                <td className="py-1 pr-2 text-ink-700">{s.label}</td>
                <td className="py-1 px-2 text-right tabular-nums text-ink-500">{signalText(s.baseline, s.unit)}</td>
                <td className="py-1 px-2 text-right tabular-nums text-ink-700">{signalText(s.current, s.unit)}</td>
                <td className="py-1 pl-2 text-right"><SignalDelta s={s} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {p.gaps.resolved.map((g) => (
        <div key={`r-${g}`} className="flex items-start gap-1.5 text-xs text-emerald-700">
          <CheckCircle2 size={13} className="mt-0.5 shrink-0" />
          <span><span className="font-medium">Resolved:</span> {g}</span>
        </div>
      ))}
      {p.gaps.introduced.map((g) => (
        <div key={`n-${g}`} className="flex items-start gap-1.5 text-xs text-amber-700">
          <AlertCircle size={13} className="mt-0.5 shrink-0" />
          <span><span className="font-medium">New gap:</span> {g}</span>
        </div>
      ))}
    </div>
  );
}

// Overall-score delta chip for the header banner.
function OverallDelta({ delta }: { delta: number | null }) {
  if (delta === null) return <span className="text-ink-400 text-sm">n/a</span>;
  if (delta === 0) {
    return <span className="inline-flex items-center gap-1 text-ink-500 font-semibold"><Minus size={16} /> No change</span>;
  }
  const up = delta > 0;
  return (
    <span className={`inline-flex items-center gap-1 text-lg font-bold tabular-nums ${up ? 'text-emerald-600' : 'text-red-600'}`}>
      {up ? <ArrowUp size={18} /> : <ArrowDown size={18} />}
      {up ? '+' : '−'}{Math.abs(delta)}
    </span>
  );
}

// Compare two of the user's saved assessments. Two pickers (baseline vs current,
// defaulting to previous-vs-latest) drive a per-pillar diff table plus an overall
// delta + level/stage-change banner. Read-only; computed from stored scores.
export default function CompareModal({
  history,
  initialCurrentId,
  onClose,
}: {
  history: HistorySnapshot[];
  initialCurrentId?: number | null;
  onClose: () => void;
}) {
  // History is newest-first (index 0 = newest). Anchor on the caller's current run
  // (else the latest), take its nearest neighbor, then assign baseline = the OLDER
  // of the two and current = the newer — so the delta always reads forward in time
  // (a gain is positive), even when the anchor is the oldest run (its neighbor is
  // then newer and becomes `current`, rather than baseline ending up newer).
  const ordered = history;
  const [defaultBaseline, defaultCurrent] = useMemo(() => {
    const anchorIdx = Math.max(
      0,
      initialCurrentId != null
        ? ordered.findIndex((h) => Number(h.id) === Number(initialCurrentId))
        : 0,
    );
    const neighborIdx = anchorIdx + 1 < ordered.length ? anchorIdx + 1 : anchorIdx - 1;
    const baseIdx = Math.max(anchorIdx, neighborIdx); // larger index = older
    const curIdx = Math.min(anchorIdx, neighborIdx); // smaller index = newer
    return [Number(ordered[baseIdx]?.id), Number(ordered[curIdx]?.id)];
  }, [ordered, initialCurrentId]);

  const [baselineId, setBaselineId] = useState<number>(defaultBaseline);
  const [currentId, setCurrentId] = useState<number>(defaultCurrent);
  const [result, setResult] = useState<CompareResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  function toggle(key: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  }

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  useEffect(() => {
    if (!baselineId || !currentId || baselineId === currentId) {
      setResult(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    setExpanded(new Set());
    apiGet<CompareResult>(`/assess/compare?baseline=${baselineId}&current=${currentId}`)
      .then((r) => { if (!cancelled) setResult(r); })
      .catch((e) => { if (!cancelled) setError(e instanceof ApiError ? e.message : 'Comparison failed'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [baselineId, currentId]);

  const label = (h: HistorySnapshot) => `${fmtWhen(h.created_at)} · ${Math.round(h.overall_score)}`;

  const picker = (value: number, onChange: (v: number) => void, testid: string) => (
    <select
      value={value || ''}
      onChange={(e) => onChange(Number(e.target.value))}
      data-testid={testid}
      className="w-full rounded-md border border-gray-200 bg-white px-2.5 py-1.5 text-xs text-ink-700 focus:border-databricks-400 focus:outline-none"
    >
      {ordered.map((h) => (
        <option key={String(h.id)} value={Number(h.id)}>{label(h)}</option>
      ))}
    </select>
  );

  const o = result?.overall;

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 p-4"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="bg-white rounded-xl shadow-xl w-full max-w-3xl max-h-[85vh] flex flex-col">
        <div className="flex items-center justify-between gap-2 px-5 py-3 border-b border-gray-100">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-ink-900">
            <GitCompareArrows size={16} className="text-databricks-500" />
            Compare assessments
          </h3>
          <button onClick={onClose} className="text-ink-400 hover:text-ink-700"><X size={18} /></button>
        </div>

        {/* Pickers */}
        <div className="grid grid-cols-2 gap-3 px-5 py-3 border-b border-gray-100">
          <div>
            <label className="block text-[11px] font-medium uppercase tracking-wider text-ink-400 mb-1">Baseline</label>
            {picker(baselineId, setBaselineId, 'compare-baseline')}
          </div>
          <div>
            <label className="block text-[11px] font-medium uppercase tracking-wider text-ink-400 mb-1">Current</label>
            {picker(currentId, setCurrentId, 'compare-current')}
          </div>
        </div>

        <div className="px-5 py-4 overflow-y-auto">
          {baselineId === currentId ? (
            <p className="text-sm text-ink-400 flex items-center gap-1.5"><AlertTriangle size={14} /> Pick two different assessments to compare.</p>
          ) : loading ? (
            <div className="flex items-center gap-2 text-sm text-ink-500 py-6 justify-center">
              <Loader2 size={16} className="animate-spin text-databricks-500" /> Comparing…
            </div>
          ) : error ? (
            <p className="text-sm text-red-600 flex items-center gap-1.5"><AlertTriangle size={14} /> {error}</p>
          ) : o && result ? (
            <>
              {/* Overall banner */}
              <div className="rounded-lg border border-gray-200 bg-gray-50 p-4 mb-4">
                <div className="flex items-center justify-between gap-3 flex-wrap">
                  <div className="flex items-center gap-2 text-sm text-ink-700">
                    <span className="font-semibold tabular-nums">{scoreText(o.baseline_score)}</span>
                    <span className="text-ink-400">→</span>
                    <span className="font-semibold tabular-nums">{scoreText(o.current_score)}</span>
                    <span className="text-ink-400 text-xs">overall</span>
                  </div>
                  <OverallDelta delta={o.delta} />
                </div>
                <div className="flex items-center gap-2 flex-wrap mt-2">
                  {o.level_change && (
                    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${o.level_change.direction === 'up' ? 'bg-emerald-50 text-emerald-700 border border-emerald-200' : 'bg-red-50 text-red-700 border border-red-200'}`}>
                      {o.level_change.direction === 'up' ? <ArrowUp size={12} /> : <ArrowDown size={12} />}
                      L{o.level_change.from} {o.level_change.from_label} → L{o.level_change.to} {o.level_change.to_label}
                    </span>
                  )}
                  {o.stage_change && (
                    <span className="inline-flex items-center rounded-full bg-databricks-50 text-databricks-700 border border-databricks-200 px-2 py-0.5 text-[11px] font-medium">
                      {o.stage_change.from} → {o.stage_change.to}
                    </span>
                  )}
                  <span className="text-[11px] text-ink-400">
                    {[
                      `${result.summary.improved} improved`,
                      `${result.summary.regressed} regressed`,
                      `${result.summary.unchanged} unchanged`,
                      result.summary.new ? `${result.summary.new} new` : null,
                      result.summary.removed ? `${result.summary.removed} removed` : null,
                    ].filter(Boolean).join(' · ')}
                  </span>
                </div>
              </div>

              {/* Per-pillar table */}
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-[11px] uppercase tracking-wider text-ink-400 border-b border-gray-100">
                    <th className="py-1.5 pr-2 font-medium">Pillar</th>
                    <th className="py-1.5 px-2 font-medium text-right">Baseline</th>
                    <th className="py-1.5 px-2 font-medium text-right">Current</th>
                    <th className="py-1.5 pl-2 font-medium text-right">Change</th>
                  </tr>
                </thead>
                <tbody>
                  {result.pillars.map((p) => {
                    const canExpand = hasDetail(p);
                    const open = expanded.has(p.key);
                    return (
                      <Fragment key={p.key}>
                        <tr
                          className={`border-b border-gray-50 ${canExpand ? 'cursor-pointer hover:bg-gray-50' : ''}`}
                          onClick={canExpand ? () => toggle(p.key) : undefined}
                        >
                          <td className="py-2 pr-2">
                            <span className="inline-flex items-center gap-1.5">
                              {canExpand ? (
                                <ChevronDown size={14} className={`text-ink-400 transition-transform ${open ? 'rotate-180' : ''}`} />
                              ) : (
                                <span className="inline-block w-[14px]" />
                              )}
                              <span className="text-ink-800">{p.name}</span>
                            </span>
                            {p.level_change && (
                              <span className="block text-[11px] text-ink-400 pl-[22px]">
                                L{p.level_change.from} {p.level_change.from_label} → L{p.level_change.to} {p.level_change.to_label}
                              </span>
                            )}
                          </td>
                          <td className="py-2 px-2 text-right tabular-nums text-ink-600">{scoreText(p.baseline_score)}</td>
                          <td className="py-2 px-2 text-right tabular-nums text-ink-600">{scoreText(p.current_score)}</td>
                          <td className="py-2 pl-2 text-right"><DeltaCell p={p} /></td>
                        </tr>
                        {open && (
                          <tr>
                            <td colSpan={4} className="pb-3 px-1">
                              <PillarDetailDiff p={p} />
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
