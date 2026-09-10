import { useEffect, useMemo } from 'react';
import { X, Check, Cpu } from 'lucide-react';
import type { AIModel } from '../types';

// Grouped model picker overlay: Proprietary vs Open source, then by family within
// each. Replaces a long flat dropdown when a workspace exposes many serving
// endpoints. Selecting a model sets it and closes.
export default function ModelPickerModal({
  models,
  value,
  onSelect,
  onClose,
}: {
  models: AIModel[];
  value: string;
  onSelect: (id: string) => void;
  onClose: () => void;
}) {
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  // Two sections (proprietary first), each a list of [family, models] groups.
  const sections = useMemo(() => {
    const build = (open: boolean) => {
      const byFamily = new Map<string, AIModel[]>();
      for (const m of models) {
        if (m.open_source !== open) continue;
        const arr = byFamily.get(m.family) || [];
        arr.push(m);
        byFamily.set(m.family, arr);
      }
      return Array.from(byFamily.entries()).sort((a, b) => a[0].localeCompare(b[0]));
    };
    return [
      { title: 'Proprietary', groups: build(false) },
      { title: 'Open source', groups: build(true) },
    ].filter((s) => s.groups.length > 0);
  }, [models]);

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 p-4"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl max-h-[85vh] flex flex-col">
        <div className="flex items-center justify-between gap-2 px-5 py-3 border-b border-gray-100">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-ink-900">
            <Cpu size={16} className="text-databricks-500" />
            Choose the AI model
          </h3>
          <button onClick={onClose} className="text-ink-400 hover:text-ink-700"><X size={18} /></button>
        </div>

        <div className="px-5 py-4 overflow-y-auto space-y-5">
          {sections.map((section) => (
            <div key={section.title} className="space-y-3">
              <h4 className="text-xs font-semibold uppercase tracking-wider text-ink-400">{section.title}</h4>
              {section.groups.map(([family, fam]) => (
                <div key={family} className="space-y-1">
                  <p className="text-[11px] font-medium text-ink-500">{family}</p>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5">
                    {fam.map((m) => {
                      const selected = m.id === value;
                      return (
                        <button
                          key={m.id}
                          onClick={() => { onSelect(m.id); onClose(); }}
                          className={`flex items-center justify-between gap-2 rounded-md border px-3 py-2 text-left text-sm transition-colors ${
                            selected
                              ? 'border-databricks-300 bg-databricks-50 text-databricks-800'
                              : 'border-gray-200 hover:bg-gray-50 text-ink-800'
                          }`}
                        >
                          <span className="min-w-0">
                            <span className="block truncate font-medium">{m.label}</span>
                            <span className="block text-[11px] text-ink-400">{m.provider}</span>
                          </span>
                          {selected && <Check size={15} className="shrink-0 text-databricks-600" />}
                        </button>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          ))}
          {sections.length === 0 && (
            <p className="text-sm text-ink-400">No models are available on this workspace.</p>
          )}
        </div>
      </div>
    </div>
  );
}
