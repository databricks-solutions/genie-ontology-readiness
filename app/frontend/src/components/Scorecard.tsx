import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Radar,
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
} from 'recharts';
import { ChevronDown, RefreshCw, AlertTriangle, TrendingUp, Play, Gauge as GaugeIcon, Loader2, Sparkles, ListChecks, History, Plus } from 'lucide-react';
import { apiGet, streamPostEvents } from '../hooks/useApi';
import type {
  AppConfig,
  Scorecard as ScorecardType,
  ScorecardOverall,
  PillarScore,
  TopGap,
  HistoryResponse,
  HistorySnapshot,
  SnapshotResponse,
} from '../types';
import { levelStyle, scoreColor } from '../theme/levels';
import PillarDetail from './PillarDetail';

type AssessEvent =
  | { type: 'pillar'; pillar: PillarScore }
  | { type: 'complete'; overall: ScorecardOverall; pillars: PillarScore[]; top_gaps: TopGap[]; snapshot_id?: number | null }
  | { type: 'error'; error: string };

function Gauge({ score, color }: { score: number; color: string }) {
  const radius = 52;
  const circ = 2 * Math.PI * radius;
  const offset = circ * (1 - score / 100);
  return (
    <div className="relative w-32 h-32 shrink-0">
      <svg viewBox="0 0 120 120" className="w-32 h-32 -rotate-90">
        <circle cx="60" cy="60" r={radius} fill="none" stroke="#e5e7eb" strokeWidth="10" />
        <circle
          cx="60" cy="60" r={radius} fill="none" stroke={color} strokeWidth="10"
          strokeLinecap="round" strokeDasharray={circ} strokeDashoffset={offset}
          style={{ transition: 'stroke-dashoffset 0.8s ease' }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-3xl font-bold text-ink-900 tabular-nums">{Math.round(score)}</span>
        <span className="text-[10px] uppercase tracking-wider text-ink-400">/ 100</span>
      </div>
    </div>
  );
}

function LevelBadge({ level, label }: { level: number; label: string }) {
  const s = levelStyle(level);
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold ${s.bg} ${s.text}`}>
      <span className="tabular-nums">L{level}</span>
      <span className="font-medium">{label}</span>
    </span>
  );
}

function fmtWhen(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

// Wrap a pillar name to <=~16-char lines so long titles like
// "Relationships & Modeling" don't clip off the radar chart.
function wrapLabel(text: string, maxChars = 16): string[] {
  const words = text.split(' ');
  const lines: string[] = [];
  let line = '';
  for (const w of words) {
    if (!line) { line = w; }
    else if ((line + ' ' + w).length <= maxChars) { line += ' ' + w; }
    else { lines.push(line); line = w; }
  }
  if (line) lines.push(line);
  return lines;
}

// Custom PolarAngleAxis tick: multi-line, anchored by its angular position so
// left-side labels right-align and right-side labels left-align — keeps every
// line inside the chart box.
function RadarTick(props: any) {
  const { x, y, cx, cy, payload } = props;
  const lines = wrapLabel(String(payload?.value ?? ''));
  const dx = x - cx;
  const anchor = Math.abs(dx) < 12 ? 'middle' : dx > 0 ? 'start' : 'end';
  return (
    <text x={x} y={y} textAnchor={anchor} fill="#65868a" fontSize={10.5}>
      {lines.map((ln, i) => (
        <tspan key={i} x={x} dy={i === 0 ? (lines.length > 1 ? '-0.3em' : '0.32em') : '1.1em'}>
          {ln}
        </tspan>
      ))}
    </text>
  );
}

export default function Scorecard({
  config,
  scorecard,
  setScorecard,
}: {
  config: AppConfig;
  scorecard: ScorecardType | null;
  setScorecard: (s: ScorecardType) => void;
}) {
  const [phase, setPhase] = useState<'idle' | 'running' | 'done'>(scorecard ? 'done' : 'idle');
  const [pillarsByKey, setPillarsByKey] = useState<Record<string, PillarScore>>(() =>
    scorecard ? Object.fromEntries(scorecard.pillars.map((p) => [p.key, p])) : {}
  );
  const [overall, setOverall] = useState<ScorecardOverall | null>(scorecard?.overall ?? null);
  const [topGaps, setTopGaps] = useState<TopGap[]>(scorecard?.top_gaps ?? []);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [history, setHistory] = useState<HistorySnapshot[]>([]);
  const [currentId, setCurrentId] = useState<number | null>(null);
  const [loadingId, setLoadingId] = useState<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  function refreshHistory() {
    // No persistence without Lakebase — nothing to fetch or list.
    if (!config.lakebase_enabled) return;
    apiGet<HistoryResponse>('/assess/history')
      .then((h) => setHistory(h.snapshots || []))
      .catch(() => {});
  }

  useEffect(() => {
    refreshHistory();
    return () => abortRef.current?.abort();
  }, []);

  const completedCount = Object.keys(pillarsByKey).length;
  const totalPillars = config.pillars.length;

  const radarData = useMemo(
    () =>
      config.pillars
        .filter((p) => {
          const ps = pillarsByKey[p.key];
          // Always drop score-exempt pillars (e.g. the Beta Pages placeholder) —
          // they carry no real score and would show as a misleading 0.
          if (ps?.score_exempt) return false;
          // While streaming, keep every config pillar so the radar draws a full
          // polygon filling from 0 (Recharts collapses to a dot/line with <3
          // points). On a finalized scorecard, drop pillars absent from it (e.g. an
          // old snapshot saved before a pillar existed) so they don't plot a false
          // 0 spoke.
          if (phase === 'running') return true;
          return !!ps;
        })
        .map((p) => ({
          // Use the pillar's card title (name), not its long description, so the
          // radar labels match the scored pillar cards below.
          pillar: p.name,
          score: Math.round(pillarsByKey[p.key]?.score ?? 0),
        })),
    [config.pillars, pillarsByKey]
  );

  const trendData = useMemo(
    () =>
      [...history]
        .sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime())
        .map((h) => ({
          date: new Date(h.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }),
          score: h.overall_score,
        })),
    [history]
  );

  async function run() {
    if (phase === 'running') return;
    setError(null);
    setPillarsByKey({});
    setOverall(null);
    setTopGaps([]);
    setCurrentId(null);
    setPhase('running');
    const controller = new AbortController();
    abortRef.current = controller;

    const collected: Record<string, PillarScore> = {};
    let completed = false;
    try {
      for await (const ev of streamPostEvents<AssessEvent>(
        '/assess/stream',
        {},
        undefined,
        controller.signal
      )) {
        if (ev.type === 'pillar') {
          collected[ev.pillar.key] = ev.pillar;
          setPillarsByKey({ ...collected });
        } else if (ev.type === 'complete') {
          completed = true;
          setOverall(ev.overall);
          setTopGaps(ev.top_gaps);
          setScorecard({ overall: ev.overall, pillars: ev.pillars, top_gaps: ev.top_gaps });
          setPhase('done');
          if (ev.snapshot_id != null) setCurrentId(ev.snapshot_id);
          refreshHistory();
        } else if (ev.type === 'error') {
          setError(ev.error);
        }
      }
      if (!completed) setPhase(Object.keys(collected).length > 0 ? 'done' : 'idle');
    } catch (e) {
      if ((e as Error).name !== 'AbortError') {
        setError((e as Error).message);
        setPhase(Object.keys(collected).length > 0 ? 'done' : 'idle');
      }
    } finally {
      abortRef.current = null;
    }
  }

  async function loadSnapshot(id: number) {
    if (loadingId) return;
    setLoadingId(id);
    setError(null);
    try {
      const res = await apiGet<SnapshotResponse>(`/assess/snapshot/${id}`);
      const sc = res.scorecard;
      setPillarsByKey(Object.fromEntries(sc.pillars.map((p) => [p.key, p])));
      setOverall(sc.overall);
      setTopGaps(sc.top_gaps);
      setScorecard(sc);
      setExpanded(null);
      setCurrentId(id);
      setPhase('done');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoadingId(null);
    }
  }

  const running = phase === 'running';

  // Left sidebar: chat-history list of this user's assessments.
  const sidebar = (
    <aside className="lg:sticky lg:top-6 self-start space-y-3">
      <button
        onClick={run}
        disabled={running}
        className="btn-primary w-full flex items-center justify-center gap-1.5 text-sm"
      >
        <Plus size={15} /> New assessment
      </button>
      <div className="card p-2">
        <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-ink-400 px-2 py-1.5">
          <History size={13} className="text-databricks-500" /> Assessments
        </h3>
        {running && (
          <div className="rounded-lg border border-databricks-200 bg-databricks-50 px-3 py-2 mb-1 flex items-center gap-2">
            <Loader2 size={14} className="animate-spin text-databricks-500" />
            <span className="text-xs text-ink-600">Running… {completedCount}/{totalPillars}</span>
          </div>
        )}
        {history.length === 0 && !running ? (
          <p className="text-xs text-ink-400 px-2 py-2">No assessments yet. Run one to get started.</p>
        ) : (
          <div className="space-y-1 max-h-[calc(100vh-220px)] overflow-y-auto">
            {history.map((h) => {
              const active = currentId === h.id;
              return (
                <button
                  key={h.id}
                  onClick={() => loadSnapshot(Number(h.id))}
                  disabled={loadingId != null}
                  className={`w-full flex items-center gap-2.5 rounded-lg border px-3 py-2 text-left transition-colors ${
                    active ? 'border-databricks-400 bg-databricks-50' : 'border-transparent hover:bg-gray-50'
                  }`}
                >
                  <span className="text-base font-bold tabular-nums w-8 text-center shrink-0" style={{ color: scoreColor(h.overall_score) }}>
                    {Math.round(h.overall_score)}
                  </span>
                  <span className="flex-1 min-w-0">
                    <span className="block text-xs font-medium text-ink-700 truncate">{fmtWhen(h.created_at)}</span>
                    <span className="block text-[11px] text-ink-400">Readiness score</span>
                  </span>
                  {loadingId === h.id && <Loader2 size={13} className="animate-spin text-ink-400 shrink-0" />}
                </button>
              );
            })}
          </div>
        )}
      </div>
    </aside>
  );

  // ---- Idle main pane: explicit start screen -------------------------------
  const idleMain = (
    <div className="card p-8">
      <div className="text-center">
        <div className="w-12 h-12 rounded-xl bg-databricks-50 flex items-center justify-center mx-auto mb-4">
          <GaugeIcon size={24} className="text-databricks-500" />
        </div>
        <h2 className="text-xl font-bold text-ink-900">Assess your Genie Ontology readiness</h2>
        <p className="text-sm text-ink-600 mt-2 leading-relaxed">
          This reads your Unity Catalog metadata (catalogs, comments, constraints, metric views,
          Genie Agents, tags) to score your readiness across eight pillars. It runs read-only as the
          app's service principal and typically takes 30–60 seconds.
        </p>
      </div>

      {/* Business-user primer: what Genie Ontology is */}
      <div className="mt-6 rounded-lg bg-databricks-50 border border-databricks-100 p-4 text-left">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-ink-900 mb-1.5">
          <Sparkles size={15} className="text-databricks-500" /> What is Genie Ontology?
        </h3>
        <p className="text-sm text-ink-700 leading-relaxed">
          Genie Ontology is the business-aware context layer that lets Genie answer from your
          <span className="font-medium"> authoritative </span> source instead of guessing. It combines
          the context you govern and certify — metric views, domains, and Pages — with
          context Genie learns from the assets you already have (dashboards, saved queries, Genie
          Agents), and ranks every signal by authority so answers stay accurate, governed, and
          permission-aware. Getting ready for it means maturing that governed foundation, which is
          exactly what this assessment measures.
        </p>
      </div>

      {/* What's required to run the assessment */}
      <div className="mt-4 rounded-lg bg-gray-50 border border-gray-200 p-4 text-left">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-ink-900 mb-2">
          <ListChecks size={15} className="text-databricks-500" /> What's required to run this assessment
        </h3>
        <ul className="space-y-1.5 text-sm text-ink-700">
          <li className="flex items-start gap-2"><span className="text-ink-300 mt-0.5">•</span><span>A workspace with <span className="font-medium">Unity Catalog</span> enabled.</span></li>
          <li className="flex items-start gap-2"><span className="text-ink-300 mt-0.5">•</span><span>A <span className="font-medium">SQL warehouse</span> for the read-only metadata queries.</span></li>
          <li className="flex items-start gap-2"><span className="text-ink-300 mt-0.5">•</span><span>Read access for the app's <span className="font-medium">service principal</span>: <code className="text-xs">USE CATALOG</code> / <code className="text-xs">USE SCHEMA</code> / <code className="text-xs">SELECT</code> on <code className="text-xs">system.information_schema</code>, <code className="text-xs">system.access</code>, <code className="text-xs">system.query</code>, plus the catalogs you want assessed. Deploy applies these automatically; it falls back to each catalog's own <code className="text-xs">information_schema</code> if system tables aren't granted.</span></li>
          <li className="flex items-start gap-2"><span className="text-ink-300 mt-0.5">•</span><span><span className="font-medium">Optional:</span> <code className="text-xs">CAN_RUN</code> on your Genie Agents so the Genie pillar can count and assess them.</span></li>
          <li className="flex items-start gap-2"><span className="text-ink-300 mt-0.5">•</span><span>The <span className="font-medium">Plan</span> tab additionally uses your workspace's Foundation Model API to generate a plan against a saved assessment.</span></li>
        </ul>
        <p className="text-xs text-ink-400 mt-2.5">
          It's read-only and degrades gracefully — any pillar the service principal can't read is
          marked "not available" rather than failing the run.
          {config.lakebase_enabled && ' Every run is saved to your history.'}
        </p>
        {!config.lakebase_enabled && (
          <p className="mt-2.5 flex items-start gap-1.5 rounded-md bg-amber-50 border border-amber-200 px-2.5 py-2 text-xs text-amber-800">
            <AlertTriangle size={13} className="mt-0.5 shrink-0 text-amber-500" />
            <span>
              <span className="font-medium">Assessments aren't saved on this deployment.</span> No
              Lakebase database is attached, so results and generated plans live only in this browser
              session and are lost on refresh. Lakebase is optional — attach one at deploy time
              (<code className="text-[11px]">USE_LAKEBASE=true</code>) to persist history per user.
            </span>
          </p>
        )}
      </div>

      <div className="text-center">
        {error && (
          <p className="mt-4 text-sm text-red-600 flex items-center justify-center gap-1.5">
            <AlertTriangle size={14} /> {error}
          </p>
        )}
        <button onClick={run} className="btn-primary mt-6 inline-flex items-center gap-2">
          <Play size={16} /> Run assessment
        </button>
      </div>
    </div>
  );

  const resultsMain = (
    <div className="space-y-6">
      {/* Overall readiness (done) or progress (running) */}
      <div className="card p-6">
        {running ? (
          <div className="flex items-center gap-4">
            <Loader2 size={28} className="animate-spin text-databricks-500 shrink-0" />
            <div className="flex-1">
              <h2 className="text-base font-semibold text-ink-900">Assessing your workspace…</h2>
              <p className="text-sm text-ink-500 mt-0.5">
                {completedCount} of {totalPillars} pillars complete
              </p>
              <div className="mt-2 h-1.5 rounded-full bg-gray-100 overflow-hidden max-w-md">
                <div
                  className="h-full rounded-full bg-databricks-500 transition-all duration-500"
                  style={{ width: `${(completedCount / totalPillars) * 100}%` }}
                />
              </div>
            </div>
          </div>
        ) : (
          overall && (
            <div className="flex flex-col md:flex-row gap-6 items-center md:items-start">
              <Gauge score={overall.score} color={scoreColor(overall.score)} />
              <div className="flex-1 text-center md:text-left">
                <div className="flex items-center justify-center md:justify-start gap-3 mb-1">
                  <h2 className="text-xl font-bold text-ink-900">{overall.readiness_stage}</h2>
                  <LevelBadge level={overall.level} label={overall.level_label} />
                </div>
                <p className="text-sm text-ink-600 leading-relaxed max-w-2xl">{overall.readiness_detail}</p>
                <div className="flex items-center gap-3 flex-wrap mt-2">
                  {config.assess_catalogs.length > 0 && (
                    <p className="text-xs text-ink-400">Assessing catalogs: {config.assess_catalogs.join(', ')}</p>
                  )}
                  {overall.assessed_at && (
                    <p className="text-xs text-ink-400">Assessed {fmtWhen(overall.assessed_at)}</p>
                  )}
                </div>
              </div>
              <button
                onClick={run}
                disabled={running}
                className="btn-secondary py-1.5 px-3 flex items-center gap-1.5 text-xs shrink-0"
              >
                <RefreshCw size={14} /> New assessment
              </button>
            </div>
          )
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-stretch">
        {/* Left column: pillar maturity, then readiness-over-time beneath it */}
        <div className="space-y-6">
          <div className="card p-5">
            <h3 className="text-sm font-semibold text-ink-700 mb-2">Pillar maturity</h3>
            <ResponsiveContainer width="100%" height={340}>
              <RadarChart data={radarData} outerRadius="62%" margin={{ top: 24, right: 56, bottom: 24, left: 56 }}>
                <PolarGrid stroke="#e5e7eb" />
                <PolarAngleAxis dataKey="pillar" tick={<RadarTick />} />
                {/* Place the 0–100 radius ladder in the gap BETWEEN the top two
                    spokes (~64° for 7 pillars) so it doesn't collide with the
                    top "Unity Catalog Foundation" label. */}
                <PolarRadiusAxis domain={[0, 100]} tick={{ fontSize: 9, fill: '#97afb2' }} angle={64} />
                <Radar name="Score" dataKey="score" stroke="#FF3621" fill="#FF3621" fillOpacity={0.25} strokeWidth={2} />
                <Tooltip />
              </RadarChart>
            </ResponsiveContainer>
          </div>

          {config.lakebase_enabled && trendData.length > 1 && (
            <div className="card p-5">
              <div className="flex items-center gap-2 mb-2">
                <TrendingUp size={15} className="text-databricks-500" />
                <h3 className="text-sm font-semibold text-ink-700">Readiness over time</h3>
              </div>
              <ResponsiveContainer width="100%" height={140}>
                <LineChart data={trendData} margin={{ top: 5, right: 10, left: -20, bottom: 0 }}>
                  <XAxis dataKey="date" tick={{ fontSize: 10, fill: '#97afb2' }} />
                  <YAxis domain={[0, 100]} tick={{ fontSize: 10, fill: '#97afb2' }} />
                  <Tooltip />
                  <Line type="monotone" dataKey="score" stroke="#FF3621" strokeWidth={2} dot={{ r: 3 }} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>

        {/* Right column: top gaps, extended to match the left column's height */}
        <div className="card p-5 flex flex-col">
          <h3 className="text-sm font-semibold text-ink-700 mb-3">Top gaps to close</h3>
          {running ? (
            <p className="text-sm text-ink-400">Identifying gaps as pillars complete…</p>
          ) : topGaps.length === 0 ? (
            <p className="text-sm text-ink-400">No major gaps detected.</p>
          ) : (
            <ul className="space-y-2">
              {topGaps.map((g, i) => (
                <li key={i} className="flex items-start gap-2 text-sm">
                  <AlertTriangle size={15} className="mt-0.5 shrink-0 text-amber-500" />
                  <span>
                    <span className="font-medium text-ink-800">{g.pillar}:</span>{' '}
                    <span className="text-ink-600">{g.gap}</span>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {/* Pillar cards — all pillars in canonical order; skeleton until each arrives */}
      <div className="space-y-3">
        <h3 className="text-sm font-semibold text-ink-700">Pillars</h3>
        {config.pillars.map((cp) => {
          const p = pillarsByKey[cp.key];
          if (!p) {
            // Three states for a pillar with no result yet:
            //  - streaming (phase running): still expected → spinner + "Checking…"
            //  - finalized scorecard (completed run or loaded snapshot, so `overall`
            //    is set): genuinely absent — e.g. an old snapshot saved before this
            //    pillar existed → "Not in this assessment"
            //  - otherwise (stream errored/ended before completing): it was expected
            //    but didn't load; don't claim it's absent → "Not loaded"
            const waiting = phase === 'running';
            const finalized = phase === 'done' && !!overall;
            const label = waiting ? 'Checking…' : finalized ? 'Not in this assessment' : 'Not loaded';
            return (
              <div key={cp.key} className="card flex items-center gap-4 px-4 py-3 opacity-70">
                {waiting ? (
                  <Loader2 size={16} className="animate-spin text-ink-300 shrink-0 w-12" />
                ) : (
                  <div className="w-12 text-center shrink-0 text-lg font-bold text-ink-300">—</div>
                )}
                <div className="flex-1 min-w-0">
                  <span className="font-semibold text-ink-500">{cp.name}</span>
                  <p className="text-xs text-ink-400 mt-0.5 truncate">{label}</p>
                </div>
              </div>
            );
          }
          const open = expanded === p.key;
          const s = levelStyle(p.level);
          const exempt = p.score_exempt;
          return (
            <div key={p.key} className={`card overflow-hidden ${!p.available && !exempt ? 'opacity-80' : ''}`}>
              <button
                onClick={() => setExpanded(open ? null : p.key)}
                className="w-full flex items-center gap-4 px-4 py-3 text-left hover:bg-gray-50 transition-colors"
              >
                <div className="w-12 text-center shrink-0">
                  {exempt ? (
                    <div className="text-lg font-bold text-ink-300">—</div>
                  ) : (
                    <div className="text-lg font-bold tabular-nums" style={{ color: scoreColor(p.score) }}>
                      {Math.round(p.score)}
                    </div>
                  )}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-semibold text-ink-900">{p.name}</span>
                    {exempt ? (
                      <span className="text-[11px] font-medium px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 border border-amber-200">
                        Beta · not scored
                      </span>
                    ) : (
                      <>
                        <LevelBadge level={p.level} label={p.level_label} />
                        {!p.available && <span className="text-[11px] text-ink-400 italic">not available</span>}
                      </>
                    )}
                  </div>
                  <p className="text-xs text-ink-500 mt-0.5 truncate">{p.short}</p>
                </div>
                <div className="hidden sm:block w-24 shrink-0">
                  {!exempt && (
                    <div className="h-1.5 rounded-full bg-gray-100 overflow-hidden">
                      <div className="h-full rounded-full" style={{ width: `${p.score}%`, backgroundColor: s.hex }} />
                    </div>
                  )}
                </div>
                <ChevronDown size={18} className={`shrink-0 text-ink-400 transition-transform ${open ? 'rotate-180' : ''}`} />
              </button>
              {open && <PillarDetail pillar={p} config={config} />}
            </div>
          );
        })}
      </div>

      {error && (
        <p className="text-sm text-red-600 flex items-center gap-1.5">
          <AlertTriangle size={14} /> {error}
        </p>
      )}
    </div>
  );

  // The history sidebar (assessments list + "New assessment" button) is only
  // meaningful when runs persist. Without Lakebase there is nothing to list and
  // no cross-run navigation, so drop the sidebar and go single-column.
  if (!config.lakebase_enabled) {
    return (
      <div className="min-w-0 max-w-4xl mx-auto">
        {phase === 'idle' ? idleMain : resultsMain}
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[260px_1fr] gap-6">
      {sidebar}
      <div className="min-w-0">
        {phase === 'idle' ? idleMain : resultsMain}
      </div>
    </div>
  );
}
