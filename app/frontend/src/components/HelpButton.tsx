import { useEffect, useState } from 'react';
import { HelpCircle, X, CheckCircle2 } from 'lucide-react';
import type { AppConfig } from '../types';

const REQUIREMENTS: { label: string; detail: string }[] = [
  {
    label: 'Unity Catalog',
    detail: 'Enabled, with a metastore assigned to the workspace.',
  },
  {
    label: 'A SQL warehouse',
    detail: 'The app runs read-only information_schema / system-table queries through it.',
  },
  {
    label: 'Service-principal read access',
    detail:
      'The app SP needs USE CATALOG + USE SCHEMA + SELECT on the catalogs to assess (and system.information_schema / system.access / system.query where available). It falls back to each catalog’s own information_schema when system schemas aren’t granted.',
  },
  {
    label: 'Foundation Model API',
    detail: 'Model serving enabled — powers the guided Plan conversation and the generated action plan (and the model picker in the header).',
  },
  {
    label: 'Genie Spaces (optional)',
    detail: 'Grant the app SP CAN_RUN to list and count spaces; CAN_EDIT to assess curation depth (instructions, example SQL, benchmarks, functions).',
  },
  {
    label: 'Lakebase (optional)',
    detail: 'Only needed to persist assessment snapshots for the "readiness over time" trend.',
  },
];

export default function HelpButton({ config }: { config: AppConfig }) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false);
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        aria-label="About this app"
        title="About this app"
        className="fixed bottom-4 left-4 z-40 w-11 h-11 rounded-full bg-ink-900 text-white shadow-lg flex items-center justify-center hover:bg-databricks-500 transition-colors"
      >
        <HelpCircle size={22} />
      </button>

      {open && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40"
          onClick={() => setOpen(false)}
        >
          <div
            className="card max-w-lg w-full max-h-[85vh] overflow-y-auto p-6 relative"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              onClick={() => setOpen(false)}
              aria-label="Close"
              className="absolute top-3 right-3 text-ink-400 hover:text-ink-700"
            >
              <X size={18} />
            </button>

            <h2 className="text-lg font-bold text-ink-900 pr-6">About {config.app_name}</h2>

            <div className="mt-4">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-ink-400 mb-1">
                What is this app?
              </h3>
              <p className="text-sm text-ink-700 leading-relaxed">
                {config.app_name} assesses your Databricks workspace&apos;s maturity for Genie
                Ontology across seven readiness pillars, explains each capability, and generates a
                tailored, prioritized action plan. The assessment is read-only.
              </p>
            </div>

            <div className="mt-5">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-ink-400 mb-2">
                Environment requirements
              </h3>
              <ul className="space-y-2.5">
                {REQUIREMENTS.map((r, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm">
                    <CheckCircle2 size={15} className="mt-0.5 shrink-0 text-databricks-500" />
                    <span className="text-ink-700">
                      <span className="font-semibold text-ink-900">{r.label}</span> — {r.detail}
                    </span>
                  </li>
                ))}
              </ul>
            </div>

            <p className="mt-5 pt-3 border-t border-gray-100 text-xs text-ink-400">
              Reads run as the app&apos;s service principal — Databricks Apps don&apos;t forward
              end-user tokens, so grants are made to the app SP.
            </p>
          </div>
        </div>
      )}
    </>
  );
}
