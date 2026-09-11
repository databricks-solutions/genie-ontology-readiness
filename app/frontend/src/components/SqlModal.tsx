import { useEffect, useState } from 'react';
import { X, Copy, Check, Code } from 'lucide-react';
import type { SourceQuery } from '../types';

// Overlay listing all the SQL statements behind a pillar's score, each with
// copy-to-clipboard. Opened from the "View SQL" button (in the drill-down modal,
// or directly from a pillar that has no drill-down table). Layers above the
// drill-down modal via a higher z-index.
export default function SqlModal({
  pillarName,
  queries,
  onClose,
}: {
  pillarName: string;
  queries: SourceQuery[];
  onClose: () => void;
}) {
  const [copied, setCopied] = useState<number | null>(null);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  async function copy(sql: string, i: number) {
    await navigator.clipboard.writeText(sql);
    setCopied(i);
    setTimeout(() => setCopied((c) => (c === i ? null : c)), 1500);
  }

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 p-4"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="bg-white rounded-xl shadow-xl w-full max-w-3xl max-h-[85vh] flex flex-col">
        <div className="flex items-center justify-between gap-2 px-5 py-3 border-b border-gray-100">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-ink-900">
            <Code size={16} className="text-databricks-500" />
            {pillarName} — SQL behind this score
          </h3>
          <button onClick={onClose} className="text-ink-400 hover:text-ink-700"><X size={18} /></button>
        </div>

        <div className="px-5 py-4 overflow-y-auto space-y-2">
          {queries.length === 0 ? (
            <p className="text-sm text-ink-400">No SQL was recorded for this pillar.</p>
          ) : (
            queries.map((q, i) => (
              <div key={i} className="rounded-md border border-gray-200 overflow-hidden">
                <div className="flex items-center justify-between gap-2 px-3 py-1.5 bg-gray-50 border-b border-gray-100">
                  <span className="text-[11px] font-medium text-ink-600">{q.label || `Query ${i + 1}`}</span>
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
            ))
          )}
        </div>
      </div>
    </div>
  );
}
