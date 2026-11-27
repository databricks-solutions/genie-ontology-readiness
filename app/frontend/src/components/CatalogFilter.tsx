import { useMemo, useRef, useState, useEffect } from 'react';
import { Database, Search, Check, ChevronDown, Loader2, AlertTriangle } from 'lucide-react';
import type { CatalogInfo } from '../types';
import { selectedFirst } from '../utils/pickerOrder';

// Shown when the catalog list came from the SQL fallback (available === false):
// the app couldn't read the UC catalog/bindings REST APIs, so there's no binding
// info — no access badges and no automatic workspace->catalog scoping. The filter
// still works as a plain manual scope control. Binding-aware scoping needs either
// the app SP to hold metastore read on catalogs/bindings, or user authorization
// (OBO) turned on. See README "Permissions required" / CLAUDE.md.
export const CATALOG_FALLBACK_NOTE =
  "Manual catalog scope: the app couldn't read workspace-to-catalog bindings, so there " +
  'are no access badges and no automatic workspace scoping — pick the catalogs to assess ' +
  "below. Binding-aware scoping needs the app's service principal to have metastore read " +
  'on catalogs/bindings, or user authorization (OBO) turned on.';

export function CatalogFallbackNote() {
  return (
    <p
      data-testid="catalog-fallback"
      className="flex items-start gap-1.5 rounded-sm border border-amber-200 bg-amber-50 px-2 py-1.5 text-[11px] text-amber-800"
    >
      <AlertTriangle size={12} className="mt-0.5 shrink-0 text-amber-500" />
      <span>{CATALOG_FALLBACK_NOTE}</span>
    </p>
  );
}

// Pre-run catalog filter: which catalogs the metadata pillars assess. Its options
// are the catalogs accessible to the selected workspaces (UC bindings + OPEN),
// so the parent refetches this list whenever the workspace selection changes.
// Value is the explicit list of selected catalog names; empty means NO filter —
// the assessment scans all accessible catalogs. The "All catalogs" control is a
// select-all / clear-all lever; checking specific catalogs narrows the scan.
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
  // Selection as of when the list was opened or the search text last changed (#65).
  // The list is ordered by this snapshot, so ticking a box never moves that row.
  const [pinned, setPinned] = useState<Set<string>>(() => new Set(value));
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
    const matches = q ? catalogs.filter((c) => c.name.toLowerCase().includes(q)) : catalogs;
    return selectedFirst(matches, (c) => pinned.has(c.name));
  }, [query, catalogs, pinned]);

  const allSelected = catalogs.length > 0 && value.length === catalogs.length;
  const summary =
    catalogs.length === 0
      ? loading
        ? 'Loading…'
        : 'No catalogs'
      : value.length === 0 || allSelected
      ? `All ${catalogs.length} catalogs` // empty = no filter = assess all
      : `${value.length} of ${catalogs.length} catalogs`;

  function toggle(name: string) {
    // Explicit selection: empty means none. Plain add/remove — no "empty == all"
    // magic, so a single catalog can be checked or unchecked without the list
    // snapping back to everything.
    onChange(selectedSet.has(name) ? value.filter((n) => n !== name) : [...value, name]);
  }

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        disabled={disabled || catalogs.length === 0}
        onClick={() => {
          if (!open) setPinned(new Set(value));
          setOpen(!open);
        }}
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
          {!available && <CatalogFallbackNote />}
          <div className="flex items-center justify-between gap-2 text-[11px]">
            {/* Lever: select every catalog, or clear back to no filter (= all). */}
            <button
              className="text-databricks-600 hover:underline font-medium"
              onClick={() => onChange(allSelected ? [] : allNames)}
            >
              {allSelected ? 'Clear all' : `Select all ${catalogs.length}`}
            </button>
            <span className="text-ink-400">{value.length === 0 ? 'assessing all' : `${value.length} selected`}</span>
          </div>
          {value.length === 0 && (
            <p className="text-[11px] text-ink-400">No catalogs selected — the assessment scans all {catalogs.length}.</p>
          )}
          <div className="flex items-center gap-1.5 rounded-md border border-gray-200 px-2 py-1">
            <Search size={13} className="text-ink-400 shrink-0" />
            <input
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setPinned(new Set(value));
              }}
              placeholder="Search catalogs…"
              className="w-full text-xs outline-hidden bg-transparent"
            />
          </div>
          <div className="max-h-60 overflow-y-auto rounded-md border border-gray-100 divide-y divide-gray-50">
            {shown.length === 0 ? (
              <p className="text-[11px] text-ink-400 px-2 py-2">No catalogs.</p>
            ) : (
              shown.map((c) => {
                const on = selectedSet.has(c.name);
                return (
                  <button
                    key={c.name}
                    onClick={() => toggle(c.name)}
                    className="w-full flex items-center gap-2 px-2 py-1.5 text-left text-xs hover:bg-gray-50"
                  >
                    <span className={`w-4 h-4 rounded-sm border flex items-center justify-center shrink-0 ${on ? 'bg-databricks-500 border-databricks-500' : 'border-gray-300'}`}>
                      {on && <Check size={11} className="text-white" />}
                    </span>
                    <span className="flex-1 min-w-0 truncate text-ink-800">{c.name}</span>
                    {c.access && c.access !== 'ALL' && (
                      <span className={`rounded-sm border px-1 py-0.5 text-[10px] ${ACCESS_BADGE[c.access] || ACCESS_BADGE.OPEN}`}>
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
