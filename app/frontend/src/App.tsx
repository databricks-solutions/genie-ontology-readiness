import { useEffect, useState } from 'react';
import {
  Gauge,
  BookOpen,
  Map,
  ChevronDown,
  Network,
  AlertTriangle,
} from 'lucide-react';
import { useConfig } from './hooks/useConfig';
import type { Scorecard as ScorecardType } from './types';
import Scorecard from './components/Scorecard';
import CapabilityExplainer from './components/CapabilityExplainer';
import PlanWizard from './components/PlanWizard';
import HelpButton from './components/HelpButton';
import Spinner from './components/Spinner';

type Tab = 'assess' | 'plan' | 'learn';

const TABS: { id: Tab; label: string; icon: React.ReactNode }[] = [
  { id: 'assess', label: 'Assess', icon: <Gauge size={17} /> },
  { id: 'plan', label: 'Plan', icon: <Map size={17} /> },
  { id: 'learn', label: 'Learn', icon: <BookOpen size={17} /> },
];

export default function App() {
  const { config, error, loading } = useConfig();
  const [tab, setTab] = useState<Tab>('assess');
  const [model, setModel] = useState<string>('');
  const [scorecard, setScorecard] = useState<ScorecardType | null>(null);
  // Mount PlanWizard on first Plan visit and keep it mounted (hidden when
  // inactive) so its conversation + generated plan persist across tab switches.
  const [planMounted, setPlanMounted] = useState(false);

  useEffect(() => {
    if (tab === 'plan') setPlanMounted(true);
  }, [tab]);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <Spinner label="Loading..." size={28} />
      </div>
    );
  }

  if (error || !config) {
    return (
      <div className="min-h-screen flex items-center justify-center px-4">
        <div className="card p-6 max-w-md text-center">
          <AlertTriangle size={28} className="mx-auto text-red-500 mb-2" />
          <h1 className="font-semibold text-ink-900">Could not load the app</h1>
          <p className="text-sm text-ink-600 mt-1">{error || 'Configuration unavailable.'}</p>
        </div>
      </div>
    );
  }

  const activeModel = model || config.default_model;

  return (
    <div className="min-h-screen flex flex-col">
      {/* Header */}
      <header className="bg-ink-900 text-white">
        <div className="max-w-7xl mx-auto px-4 sm:px-6">
          <div className="flex items-center justify-between h-16">
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 rounded-lg bg-databricks-500 flex items-center justify-center shadow">
                <Network size={20} className="text-white" />
              </div>
              <div>
                <h1 className="text-base font-bold tracking-tight leading-none">{config.app_name}</h1>
                <p className="text-[11px] text-ink-200 mt-0.5">{config.brand_name}</p>
              </div>
            </div>

            {/* Model selector */}
            <div className="relative">
              <select
                value={activeModel}
                onChange={(e) => setModel(e.target.value)}
                className="appearance-none bg-ink-800 border border-ink-700 text-white text-sm rounded-md pl-3 pr-8 py-1.5 focus:outline-none focus:ring-2 focus:ring-databricks-400 cursor-pointer"
                title="AI model used to generate the plan"
              >
                {config.ai_models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.label} · {m.provider}
                  </option>
                ))}
              </select>
              <ChevronDown size={15} className="absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none text-ink-300" />
            </div>
          </div>

          {/* Tabs */}
          <nav className="flex gap-1">
            {TABS.map((t) => (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
                  tab === t.id
                    ? 'border-databricks-500 text-white'
                    : 'border-transparent text-ink-200 hover:text-white'
                }`}
              >
                {t.icon}
                {t.label}
              </button>
            ))}
          </nav>
        </div>
      </header>

      {/* Content */}
      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 py-6">
        {/* Keep Scorecard mounted (hidden when inactive) so an in-progress or
            completed run and expanded pillar persist across tab switches. */}
        <div className={tab === 'assess' ? '' : 'hidden'}>
          <Scorecard config={config} scorecard={scorecard} setScorecard={setScorecard} />
        </div>
        {planMounted && (
          <div className={tab === 'plan' ? '' : 'hidden'}>
            <PlanWizard model={activeModel} active={tab === 'plan'} />
          </div>
        )}
        {tab === 'learn' && <CapabilityExplainer scorecard={scorecard} />}
      </main>

      <HelpButton config={config} />
    </div>
  );
}
