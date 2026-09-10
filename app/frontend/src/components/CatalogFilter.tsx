import { useMemo, useRef, useState, useEffect } from 'react';
import { Database, Search, Check, ChevronDown, Loader2 } from 'lucide-react';
import type { CatalogInfo } from '../types';

// Pre-run catalog filter: which catalogs the metadata pillars assess. Its options
// are the catalogs accessible to the selected workspaces (UC bindings + OPEN),
// so the parent refetches this list whenever the workspace selection changes.
// Value is a list of catalog names; empty means "all accessible".
const ACCESS_BADGE: Record<string, string> = {
  READ_WRITE: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  READ: 'bg-sky-50 text-sky-700 border-sky-200',
  OPEN: 'bg-gray-100 text-ink-500 border-gray-200',
  ALL: 'bg-gray-100 text-ink-500 border-gray-200',
};

export default function CatalogFilter({
  catalogs,
  available,
  loading,
  value,
  onChange,
  disabled,
}: {
  catalogs: CatalogInfo[];
  available: boolean;
  loading?: boolean;
  value: string[];
  onChange: (v: string[]) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, []);

  const selectedSet = useMemo(() => new Set(value), [value]);
  const allNames = useMemo(() => catalogs.map((c) => c.name), [catalogs]);
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? catalogs.filter((c) => c.name.toLowerCase().includes(q)) : catalogs;
  }, [query, catalogs]);

  const allSelected = value.length === 0 || value.length === catalogs.length;
  const summary =
    catalogs.length === 0
      ? 'No catalogs'
      : allSelected
      ? `All ${catalogs.length} catalogs`
      : `${value.length} of ${catalogs.length} catalogs`;

  function toggle(name: string) {
    // Represent selection as an explicit list; toggling from "all" (empty) starts
    // from the full set so the user can deselect from everything.
    const base = value.length === 0 ? allNames : value;
    const next = selectedSet.has(name) && value.length !== 0
      ? base.filter((n) => n !== name)
      : value.length === 0
      ? base.filter((n) => n !== name)
      : [...base, name];
    onChange(next.length === catalogs.length ? [] : next);
  }

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        disabled={disabled || catalogs.length === 0}
        onClick={() => setOpen((o) => !o)}
        className="btn-secondary py-1.5 px-3 flex items-center gap-1.5 text-xs disabled:opacity-50"
        title="Choose which catalogs the metadata pillars assess (from the selected workspaces)"
      >
        <Database size={14} />
        <span className="text-ink-500">Catalogs:</span>
        <span className="font-semibold text-ink-800 max-w-[160px] truncate">{summary}</span>
        {loading ? <Loader2 size={13} className="animate-spin" /> : <ChevronDown size={14} className={`transition-transform ${open ? 'rotate-180' : ''}`} />}
      </button>

      {open && (
        <div className="absolute z-20 mt-1 w-72 rounded-lg border border-gray-200 bg-white shadow-lg p-3 space-y-2">
          {!available && (
            <p className="text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1.5">
              Couldn't resolve catalog bindings (needs privilege to read them). Showing all
              catalogs the app can see.
            </p>
          )}
          <div className="flex items-center gap-2 text-[11px]">
            <button className="text-databricks-600 hover:underline" onClick={() => onChange([])}>All catalogs</button>
            {value.length > 0 && (
              <>
                <span className="text-ink-300">·</span>
                <button className="text-ink-500 hover:underline" onClick={() => onChange(allNames.slice(0, 1))}>Only first</button>
              </>
            )}
          </div>
          <div className="flex items-center gap-1.5 rounded-md border border-gray-200 px-2 py-1">
            <Search size={13} className="text-ink-400 shrink-0" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search catalogs…"
              className="w-full text-xs outline-none bg-transparent"
            />
          </div>
          <div className="max-h-60 overflow-y-auto rounded-md border border-gray-100 divide-y divide-gray-50">
            {shown.length === 0 ? (
              <p className="text-[11px] text-ink-400 px-2 py-2">No catalogs.</p>
            ) : (
              shown.map((c) => {
                const on = value.length === 0 || selectedSet.has(c.name);
                return (
                  <button
                    key={c.name}
                    onClick={() => toggle(c.name)}
                    className="w-full flex items-center gap-2 px-2 py-1.5 text-left text-xs hover:bg-gray-50"
                  >
                    <span className={`w-4 h-4 rounded border flex items-center justify-center shrink-0 ${on ? 'bg-databricks-500 border-databricks-500' : 'border-gray-300'}`}>
                      {on && <Check size={11} className="text-white" />}
                    </span>
                    <span className="flex-1 min-w-0 truncate text-ink-800">{c.name}</span>
                    {c.access && c.access !== 'ALL' && (
                      <span className={`rounded border px-1 py-0.5 text-[10px] ${ACCESS_BADGE[c.access] || ACCESS_BADGE.OPEN}`}>
                        {c.access === 'READ_WRITE' ? 'RW' : c.access === 'READ' ? 'R' : 'open'}
                      </span>
                    )}
                  </button>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}
