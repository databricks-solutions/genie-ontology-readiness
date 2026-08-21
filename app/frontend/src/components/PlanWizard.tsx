import { useEffect, useState } from 'react';
import { Sparkles, Copy, Check, AlertTriangle, FileDown, History, Gauge, Plus, Link2 } from 'lucide-react';
import { apiGet, apiPost, streamPost } from '../hooks/useApi';
import type {
  HistoryResponse,
  HistorySnapshot,
  PlansResponse,
  PlanListItem,
  PlanDetail,
  PlanSaveResponse,
  Scorecard as ScorecardType,
} from '../types';
import Markdown from './Markdown';
import Spinner from './Spinner';

const PLAN_TITLE = 'Genie Ontology Readiness — Action Plan';

function fmtWhen(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

export default function PlanWizard({
  model,
  active,
  scorecard,
}: {
  model: string;
  active: boolean;
  scorecard: ScorecardType | null;
}) {

  const [assessments, setAssessments] = useState<HistorySnapshot[]>([]);
  const [plans, setPlans] = useState<PlanListItem[]>([]);
  const [selectedSnapshotId, setSelectedSnapshotId] = useState<number | null>(null);
  const [mode, setMode] = useState<'new' | 'view'>('new');
  const [output, setOutput] = useState('');
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [currentPlanId, setCurrentPlanId] = useState<number | null>(null);

  // No saved history (e.g. no Lakebase) but an assessment was run this session →
  // generate the plan from that in-session scorecard instead of a stored snapshot.
  const fromCurrent = assessments.length === 0 && !!scorecard;
  const currentScore = scorecard ? Math.round(scorecard.overall.score) : null;

  function refreshPlans() {
    apiGet<PlansResponse>('/plan/list').then((r) => setPlans(r.plans || [])).catch(() => {});
  }

  function refreshAssessments() {
    apiGet<HistoryResponse>('/assess/history')
      .then((h) => {
        const snaps = h.snapshots || [];
        setAssessments(snaps);
        // Default to the newest assessment; keep the current selection if it still exists.
        setSelectedSnapshotId((cur) =>
          cur != null && snaps.some((s) => Number(s.id) === cur)
            ? cur
            : snaps.length
            ? Number(snaps[0].id)
            : null
        );
      })
      .catch(() => {});
  }

  // Refetch whenever the Plan tab becomes active, so an assessment just run on the
  // Assess tab is immediately selectable here (PlanWizard stays mounted between visits).
  useEffect(() => {
    if (active) {
      refreshAssessments();
      refreshPlans();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  function labelForSnapshot(id: number | null): string {
    if (id == null) return '—';
    const a = assessments.find((s) => Number(s.id) === id);
    return a ? `${Math.round(a.overall_score)}/100 · ${fmtWhen(a.created_at)}` : `assessment #${id}`;
  }

  function titleFor(id: number | null): string {
    const a = id == null ? null : assessments.find((s) => Number(s.id) === id);
    if (!a) return PLAN_TITLE;
    return `Action plan · ${Math.round(a.overall_score)}/100 · ${fmtWhen(a.created_at)}`;
  }

  function startNewPlan() {
    setMode('new');
    setOutput('');
    setError(null);
    setCurrentPlanId(null);
    if (assessments.length && selectedSnapshotId == null) setSelectedSnapshotId(Number(assessments[0].id));
  }

  async function generate() {
    // Need either a selected saved snapshot, or an in-session assessment to use.
    if (generating || (!fromCurrent && selectedSnapshotId == null)) return;
    setError(null);
    setOutput('');
    setCurrentPlanId(null);
    setGenerating(true);
    let acc = '';
    // Generate from the in-session scorecard when there's no saved history,
    // otherwise from the selected saved snapshot.
    const genBody = fromCurrent ? { scorecard } : { snapshot_id: selectedSnapshotId };
    try {
      for await (const chunk of streamPost('/plan/generate', genBody, model)) {
        acc += chunk;
        setOutput(acc);
      }
      if (acc.trim()) {
        try {
          const saveBody = fromCurrent
            ? { snapshot_id: null, title: PLAN_TITLE, markdown: acc }
            : { snapshot_id: selectedSnapshotId, title: titleFor(selectedSnapshotId), markdown: acc };
          const res = await apiPost<PlanSaveResponse>('/plan/save', saveBody, model);
          if (res.id != null) setCurrentPlanId(res.id);
          setMode('view');
          refreshPlans();
        } catch {
          /* saving is best-effort (needs Lakebase); the plan is still shown */
        }
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setGenerating(false);
    }
  }

  async function loadPlan(id: number) {
    if (generating) return;
    setError(null);
    try {
      const p = await apiGet<PlanDetail>(`/plan/${id}`);
      setOutput(p.plan_markdown);
      setCurrentPlanId(p.id);
      if (p.snapshot_id != null) setSelectedSnapshotId(p.snapshot_id);
      setMode('view');
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function copy() {
    await navigator.clipboard.writeText(output);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  async function exportPdf() {
    if (!output || exporting) return;
    const tab = window.open('', '_blank');
    setExporting(true);
    setError(null);
    try {
      const res = await fetch('/api/plan/pdf', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: PLAN_TITLE, markdown: output }),
      });
      if (!res.ok) throw new Error('PDF generation failed');
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      if (tab) tab.location.href = url;
      else window.open(url, '_blank');
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (e) {
      if (tab) tab.close();
      setError((e as Error).message);
    } finally {
      setExporting(false);
    }
  }

  // No saved assessments AND nothing run this session → point the user at Assess.
  if (assessments.length === 0 && !scorecard) {
    return (
      <div className="card max-w-2xl mx-auto mt-10 p-8 text-center">
        <div className="w-12 h-12 rounded-xl bg-databricks-50 flex items-center justify-center mx-auto mb-4">
          <Gauge size={24} className="text-databricks-500" />
        </div>
        <h2 className="text-xl font-bold text-ink-900">Run an assessment first</h2>
        <p className="text-sm text-ink-600 mt-2 leading-relaxed">
          A plan is generated against one of your saved assessments. Head to the{' '}
          <span className="font-medium">Assess</span> tab and run an assessment, then come back to
          generate a tailored, tactical plan.
        </p>
      </div>
    );
  }

  const viewingPlan = mode === 'view' && (output || generating);

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[260px_1fr] gap-6">
      {/* Left sidebar: plans history */}
      <aside className="lg:sticky lg:top-6 self-start space-y-3">
        <button onClick={startNewPlan} className="btn-primary w-full flex items-center justify-center gap-1.5 text-sm">
          <Plus size={15} /> New plan
        </button>
        <div className="card p-2">
          <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-ink-400 px-2 py-1.5">
            <History size={13} className="text-databricks-500" /> Plans
          </h3>
          {plans.length === 0 ? (
            <p className="text-xs text-ink-400 px-2 py-2">No plans yet. Generate one from an assessment.</p>
          ) : (
            <div className="space-y-1 max-h-[calc(100vh-220px)] overflow-y-auto">
              {plans.map((p) => {
                const active = currentPlanId === p.id;
                return (
                  <button
                    key={p.id}
                    onClick={() => loadPlan(p.id)}
                    className={`w-full text-left rounded-lg border px-3 py-2 transition-colors ${
                      active ? 'border-databricks-400 bg-databricks-50' : 'border-transparent hover:bg-gray-50'
                    }`}
                  >
                    <div className="text-xs font-medium text-ink-700 truncate">{p.title}</div>
                    <div className="text-[11px] text-ink-400 flex items-center gap-1 mt-0.5">
                      <Link2 size={10} /> {labelForSnapshot(p.snapshot_id)}
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </aside>

      {/* Main pane */}
      <div className="min-w-0">
        {!viewingPlan ? (
          // ---- New plan composer -------------------------------------------
          <div className="card p-6 max-w-2xl">
            <div className="w-12 h-12 rounded-xl bg-databricks-50 flex items-center justify-center mb-4">
              <Sparkles size={24} className="text-databricks-500" />
            </div>
            <h2 className="text-xl font-bold text-ink-900">Generate an action plan</h2>
            <p className="text-sm text-ink-600 mt-2 leading-relaxed">
              {fromCurrent
                ? 'The plan is generated with AI from your current assessment’s scores and gaps, with clear tactical steps and the Databricks accelerators that close each gap.'
                : 'Pick the assessment to base this plan on. The plan is generated with AI from that assessment’s scores and gaps, with clear tactical steps and the Databricks accelerators that close each gap.'}
            </p>

            <label className="block mt-5 text-xs font-semibold uppercase tracking-wider text-ink-400 mb-1.5">
              Base assessment
            </label>
            {fromCurrent ? (
              <div className="w-full rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-ink-700 flex items-center gap-2">
                <Gauge size={14} className="text-databricks-500 shrink-0" />
                Current assessment{currentScore != null ? ` · ${currentScore}/100` : ''}
                <span className="text-ink-400">(this session)</span>
              </div>
            ) : (
              <select
                value={selectedSnapshotId ?? ''}
                onChange={(e) => setSelectedSnapshotId(Number(e.target.value))}
                className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-databricks-300"
              >
                {assessments.map((a) => (
                  <option key={a.id} value={Number(a.id)}>
                    {Math.round(a.overall_score)}/100 · {fmtWhen(a.created_at)}
                  </option>
                ))}
              </select>
            )}

            {error && (
              <p className="mt-4 text-sm text-red-600 flex items-center gap-1.5">
                <AlertTriangle size={14} /> {error}
              </p>
            )}

            <button
              onClick={generate}
              disabled={generating || (!fromCurrent && selectedSnapshotId == null)}
              className="btn-primary mt-5 inline-flex items-center gap-2"
            >
              <Sparkles size={16} />
              {generating ? 'Generating…' : 'Generate plan'}
            </button>
            <p className="text-xs text-ink-400 mt-3">
              Uses your workspace's Foundation Model API.{' '}
              {fromCurrent
                ? 'No Lakebase is attached, so this plan lives only in this session (export it to keep a copy).'
                : 'The plan is saved to your history.'}
            </p>
          </div>
        ) : (
          // ---- Viewing / streaming a plan ----------------------------------
          <div className="card p-6">
            <div className="flex items-center justify-between mb-1 gap-3 flex-wrap">
              <h3 className="text-sm font-semibold text-ink-700">Action plan</h3>
              {output && !generating && (
                <div className="flex items-center gap-2">
                  <button onClick={copy} className="btn-secondary py-1 px-3 flex items-center gap-1.5 text-xs">
                    {copied ? <Check size={13} className="text-emerald-600" /> : <Copy size={13} />}
                    {copied ? 'Copied' : 'Copy'}
                  </button>
                  <button onClick={exportPdf} disabled={exporting} className="btn-primary py-1 px-3 flex items-center gap-1.5 text-xs">
                    <FileDown size={13} />
                    {exporting ? 'Exporting…' : 'Export to PDF'}
                  </button>
                </div>
              )}
            </div>
            <p className="text-xs text-ink-400 flex items-center gap-1 mb-4">
              <Link2 size={11} /> Based on assessment ·{' '}
              {fromCurrent
                ? `Current assessment${currentScore != null ? ` · ${currentScore}/100` : ''}`
                : labelForSnapshot(selectedSnapshotId)}
            </p>

            {error && (
              <p className="mb-3 text-sm text-red-600 flex items-center gap-1.5">
                <AlertTriangle size={14} /> {error}
              </p>
            )}

            <Markdown content={output} />

            {generating && (
              <div className="mt-3">
                <Spinner label="Generating…" />
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
