import { useEffect, useMemo, useState } from 'react';
import {
  CartesianGrid,
  LabelList,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { ChevronDown, TrendingUp } from 'lucide-react';
import { apiGet } from '../hooks/useApi';
import type { CompareResponse, HistorySnapshot } from '../types';
import {
  defaultComparisonPair,
  formatDelta,
  formatScore,
  statusClass,
  statusLabel,
} from '../utils/compareFormat';
import { levelStyle } from '../theme/levels';

// Maturity levels 0-4, keyed to server.pillars.level_from_score thresholds
// (>0 => L1, >=40 => L2, >=65 => L3, >=85 => L4). The chart bands and the
// per-level footer reuse the app's 0-4 level palette (red -> green).
const LEVEL_LINES = [
  { y: 40, level: 2 },
  { y: 65, level: 3 },
  { y: 85, level: 4 },
];

const LEVEL_AREAS = [
  { y1: 0, y2: 40, fill: '#fef2f2' }, // L0/L1 — absent/initial
  { y1: 40, y2: 65, fill: '#fffbeb' }, // L2 — developing
  { y1: 65, y2: 85, fill: '#f7fee7' }, // L3 — established
  { y1: 85, y2: 100, fill: '#ecfdf5' }, // L4 — optimized
];

function snapId(h: HistorySnapshot): number {
  return Number(h.id);
}

function optionLabel(h: HistorySnapshot): string {
  const d = new Date(h.created_at);
  const when = d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  return `${when} · ${Math.round(h.overall_score)}`;
}

function levelName(labels: string[], level: number | null): string {
  if (level == null) return '';
  return labels[level] ?? `L${level}`;
}

export default function ProgressOverTime({
  history,
  lakebaseEnabled,
  currentSnapshotId,
  levelLabels,
}: {
  history: HistorySnapshot[];
  lakebaseEnabled: boolean;
  currentSnapshotId: number | null;
  levelLabels: string[];
}) {
  const chronological = useMemo(
    () =>
      [...history]
        .filter((h) => Number.isFinite(snapId(h)))
        .sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime()),
    [history]
  );

  const newestFirst = useMemo(
    () => [...chronological].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()),
    [chronological]
  );

  const [baselineId, setBaselineId] = useState<number | null>(null);
  const [currentId, setCurrentId] = useState<number | null>(null);
  const [compare, setCompare] = useState<CompareResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(true);

  useEffect(() => {
    const pair = defaultComparisonPair(chronological, currentSnapshotId);
    if (!pair) return;
    setCurrentId(pair.currentId);
    setBaselineId(pair.baselineId);
  }, [chronological, currentSnapshotId]);

  useEffect(() => {
    if (baselineId == null || currentId == null) return;
    let cancelled = false;
    setError(null);
    apiGet<CompareResponse>(`/assess/compare?baseline_id=${baselineId}&current_id=${currentId}`)
      .then((res) => {
        if (!cancelled) setCompare(res);
      })
      .catch((e) => {
        if (!cancelled) {
          setCompare(null);
          setError((e as Error).message || 'Could not compare assessments.');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [baselineId, currentId]);

  if (!lakebaseEnabled || chronological.length < 2) return null;

  const trendData = chronological.map((h) => ({
    id: snapId(h),
    date: new Date(h.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }),
    score: Math.round(h.overall_score),
  }));

  const overallDelta = compare?.overall_delta ?? null;
  const deltaCls =
    overallDelta == null ? 'text-ink-500' : overallDelta > 0 ? 'text-emerald-700' : overallDelta < 0 ? 'text-red-700' : 'text-ink-600';

  return (
    <section className="card overflow-hidden">
      <button
        type="button"
        className="w-full px-5 py-4 flex items-center justify-between gap-4 text-left hover:bg-gray-50 transition-colors"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
        aria-controls="readiness-progress-details"
      >
        <div className="flex items-center gap-3 min-w-0">
          <span className="w-9 h-9 rounded-lg bg-databricks-50 border border-databricks-100 flex items-center justify-center shrink-0">
            <TrendingUp size={18} className="text-databricks-500" />
          </span>
          <span className="min-w-0">
            <span className="block text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-400">
              Assessment history
            </span>
            <span className="block text-base font-semibold text-ink-900">Readiness progression</span>
            {compare && (
              <span className="block text-xs text-ink-500 mt-0.5 truncate">
                {compare.current.readiness_stage}
              </span>
            )}
          </span>
        </div>
        <span className="flex items-center gap-3 shrink-0">
          {compare && (
            <span className="hidden sm:flex items-center gap-2 rounded-full border border-gray-200 bg-white px-3 py-1.5">
              <span className="text-lg font-bold tabular-nums text-ink-900">
                {Math.round(compare.current.overall_score)}
              </span>
              <span className={`text-xs font-semibold tabular-nums ${deltaCls}`}>
                {formatDelta(overallDelta)}
              </span>
            </span>
          )}
          <span className="text-xs font-medium text-ink-500">{expanded ? 'Hide' : 'Show'}</span>
          <ChevronDown
            size={17}
            className={`text-ink-400 transition-transform duration-200 ${expanded ? 'rotate-180' : ''}`}
          />
        </span>
      </button>

      {expanded && (
        <div id="readiness-progress-details" className="border-t border-gray-100 px-5 pb-5">
          <div className="flex flex-col lg:flex-row lg:items-end lg:justify-between gap-4 py-4">
            <div>
              <p className="text-xs font-medium text-ink-500">Compare assessments</p>
              {compare && (
                <p className="text-sm text-ink-600 mt-1">
                  <span className="font-semibold tabular-nums text-ink-900">
                    {Math.round(compare.baseline.overall_score)}
                  </span>
                  <span className="mx-1.5 text-ink-300">→</span>
                  <span className="font-semibold tabular-nums text-ink-900">
                    {Math.round(compare.current.overall_score)}
                  </span>
                  <span className={`ml-2 font-semibold tabular-nums ${deltaCls}`}>
                    {formatDelta(overallDelta)}
                  </span>
                </p>
              )}
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <label className="text-xs font-medium text-ink-500">
                Baseline
                <select
                  className="mt-1 block w-full sm:min-w-[12rem] rounded-md border border-gray-300 bg-white px-2.5 py-2 text-sm text-ink-800 focus:border-databricks-400 focus:outline-none focus:ring-2 focus:ring-databricks-100"
                  value={baselineId ?? ''}
                  onChange={(e) => setBaselineId(Number(e.target.value))}
                >
                  {newestFirst.map((h) => (
                    <option key={snapId(h)} value={snapId(h)}>
                      {optionLabel(h)}
                    </option>
                  ))}
                </select>
              </label>
              <label className="text-xs font-medium text-ink-500">
                Current
                <select
                  className="mt-1 block w-full sm:min-w-[12rem] rounded-md border border-gray-300 bg-white px-2.5 py-2 text-sm text-ink-800 focus:border-databricks-400 focus:outline-none focus:ring-2 focus:ring-databricks-100"
                  value={currentId ?? ''}
                  onChange={(e) => setCurrentId(Number(e.target.value))}
                >
                  {newestFirst.map((h) => (
                    <option key={snapId(h)} value={snapId(h)}>
                      {optionLabel(h)}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </div>

          <div className="rounded-lg border border-gray-200 bg-white px-2 pt-4">
            <div className="px-3 flex items-start justify-between gap-3">
              <div>
                <h4 className="text-xs font-semibold uppercase tracking-[0.12em] text-ink-500">Overall score over time</h4>
                <p className="text-xs text-ink-400 mt-1">Select any point to make it the comparison baseline.</p>
              </div>
              <span className="rounded-full bg-databricks-50 px-2.5 py-1 text-[11px] font-semibold text-databricks-700">
                {trendData.length} runs
              </span>
            </div>
            <ResponsiveContainer width="100%" height={290}>
              <LineChart
                data={trendData}
                margin={{ top: 34, right: 86, left: 0, bottom: 8 }}
                onClick={(state) => {
                  const id = (state?.activePayload?.[0]?.payload as { id?: number } | undefined)?.id;
                  if (id != null) setBaselineId(id);
                }}
              >
                {LEVEL_AREAS.map((area) => (
                  <ReferenceArea
                    key={area.y1}
                    y1={area.y1}
                    y2={area.y2}
                    fill={area.fill}
                    fillOpacity={0.72}
                    strokeOpacity={0}
                  />
                ))}
                <CartesianGrid stroke="#dfe7e8" strokeDasharray="2 5" vertical={false} />
                <XAxis dataKey="date" tick={{ fontSize: 11, fill: '#65868a' }} tickLine={false} axisLine={{ stroke: '#c1d0d2' }} />
                <YAxis domain={[0, 100]} tick={{ fontSize: 11, fill: '#65868a' }} tickLine={false} axisLine={false} width={36} />
                {LEVEL_LINES.map((line) => (
                  <ReferenceLine
                    key={line.y}
                    y={line.y}
                    stroke="#97afb2"
                    strokeDasharray="4 5"
                    label={{ value: `L${line.level} · ${levelName(levelLabels, line.level)}`, position: 'right', fontSize: 10, fill: '#65868a' }}
                  />
                ))}
                <Tooltip
                  formatter={(value) => [`${value} / 100`, 'Readiness score']}
                  labelFormatter={(_, payload) => (payload?.[0]?.payload as { date?: string } | undefined)?.date ?? ''}
                  contentStyle={{ borderColor: '#c1d0d2', borderRadius: 8, fontSize: 12 }}
                />
                <Line
                  type="linear"
                  dataKey="score"
                  stroke="#FF3621"
                  strokeWidth={3}
                  isAnimationActive={false}
                  activeDot={{ r: 7, fill: '#FF3621', stroke: '#fff', strokeWidth: 2 }}
                  dot={(props: { cx?: number; cy?: number; payload?: { id: number } }) => {
                    const { cx, cy, payload } = props;
                    if (cx == null || cy == null || !payload) return <g />;
                    const selected = payload.id === baselineId || payload.id === currentId;
                    return (
                      <circle
                        cx={cx}
                        cy={cy}
                        r={selected ? 6 : 4}
                        fill={payload.id === currentId ? '#FF3621' : payload.id === baselineId ? '#1B3139' : '#ff9883'}
                        stroke="#fff"
                        strokeWidth={2}
                        style={{ cursor: 'pointer' }}
                      />
                    );
                  }}
                >
                  <LabelList dataKey="score" position="top" fontSize={12} fontWeight={600} fill="#1B3139" />
                </Line>
              </LineChart>
            </ResponsiveContainer>
            <div className="grid grid-cols-5 border-t border-gray-100 mx-3">
              {levelLabels.map((label, level) => (
                <div key={label} className={`py-2 text-center ${level > 0 ? 'border-l border-gray-100' : ''}`}>
                  <span className={`block text-[11px] font-semibold ${levelStyle(level).text}`}>L{level}</span>
                  <span className="block text-[10px] text-ink-400">{label}</span>
                </div>
              ))}
            </div>
          </div>

          {error && <p className="mt-3 text-sm text-red-600">{error}</p>}

          {compare && (
            <div className="mt-4 overflow-x-auto rounded-lg border border-gray-200">
              <div className="px-4 py-3 border-b border-gray-200 bg-gray-50">
                <h4 className="text-sm font-semibold text-ink-800">Pillar movement</h4>
                <p className="text-xs text-ink-400 mt-0.5">See where readiness improved, declined, or stayed unchanged.</p>
              </div>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs font-semibold uppercase tracking-wider text-ink-400 border-b border-gray-200">
                <th className="py-2 pl-4 pr-3 font-semibold">Pillar</th>
                <th className="py-2 px-3 font-semibold">Baseline</th>
                <th className="py-2 px-3 font-semibold">Current</th>
                <th className="py-2 px-3 font-semibold">Change</th>
                <th className="py-2 pl-3 pr-4 font-semibold">Direction</th>
              </tr>
            </thead>
            <tbody>
              {compare.pillars.map((p) => (
                <tr key={p.key} className="border-b border-gray-100 last:border-0 hover:bg-gray-50/70">
                  <td className="py-3 pl-4 pr-3 font-medium text-ink-800">{p.name}</td>
                  <td className="py-3 px-3 tabular-nums text-ink-700">
                    {p.baseline_available === false || p.baseline_score == null ? (
                      <span className="text-ink-400">n/a</span>
                    ) : (
                      <>
                        {formatScore(p.baseline_score)}
                        {p.baseline_level != null && (
                          <span className={`font-medium ${levelStyle(p.baseline_level).text}`}> · {levelName(levelLabels, p.baseline_level)}</span>
                        )}
                      </>
                    )}
                  </td>
                  <td className="py-3 px-3 tabular-nums text-ink-700">
                    {p.current_available === false || p.current_score == null ? (
                      <span className="text-ink-400">n/a</span>
                    ) : (
                      <>
                        {formatScore(p.current_score)}
                        {p.current_level != null && (
                          <span className={`font-medium ${levelStyle(p.current_level).text}`}> · {levelName(levelLabels, p.current_level)}</span>
                        )}
                      </>
                    )}
                  </td>
                  <td className={`py-3 px-3 tabular-nums font-semibold ${statusClass(p.status)}`}>
                    {p.delta == null ? <span className="text-ink-400 font-normal">n/a</span> : formatDelta(p.delta)}
                  </td>
                  <td className="py-3 pl-3 pr-4">
                    <span className={`inline-flex rounded-full bg-gray-100 px-2 py-1 text-xs font-medium capitalize ${statusClass(p.status)}`}>
                      {statusLabel(p.status)}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
