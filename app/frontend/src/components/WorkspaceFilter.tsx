import { useMemo, useRef, useState, useEffect } from 'react';
import { Filter, Search, X, Check, ChevronDown, Building2 } from 'lucide-react';
import type { WorkspaceInfo, WorkspaceFilterValue } from '../types';

// Pre-run workspace filter: a searchable include/exclude multi-select over the
// metastore's workspaces (system.access.workspaces_latest). Defaults to the
// deployed workspace. Scopes the activity-based signals (Genie / Adoption /
// lineage); metastore-scoped pillars are unaffected (the parent notes this).
//
// Presentational: the parent (Scorecard) owns `value` so it can send it in the
// assessment request. An empty include selection means "all workspaces".
export default function WorkspaceFilter({
  workspaces,
  available,
  value,
  onChange,
  disabled,
}: {
  workspaces: WorkspaceInfo[];
  available: boolean;
  value: WorkspaceFilterValue;
  onChange: (v: WorkspaceFilterValue) => void;
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

  const byId = useMemo(() => new Map(workspaces.map((w) => [w.id, w])), [workspaces]);
  const selected = value.workspace_ids;
  const selectedSet = useMemo(() => new Set(selected), [selected]);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return [] as WorkspaceInfo[];
    return workspaces
      .filter((w) => w.name.toLowerCase().includes(q) || w.id.includes(q))
      .slice(0, 50);
  }, [query, workspaces]);

  const summary =
    selected.length === 0
      ? 'All workspaces'
      : value.mode === 'exclude'
      ? `All except ${selected.length}`
      : selected.length === 1
      ? byId.get(selected[0])?.name || selected[0]
      : `${selected.length} workspaces`;

  function toggle(id: string) {
    onChange({
      mode: value.mode,
      workspace_ids: selectedSet.has(id) ? selected.filter((s) => s !== id) : [...selected, id],
    });
  }

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        className="btn-secondary py-1.5 px-3 flex items-center gap-1.5 text-xs disabled:opacity-50"
        title="Choose which workspaces the activity-based signals (Genie, Adoption, lineage) count"
      >
        <Filter size={14} />
        <span className="text-ink-500">Workspaces:</span>
        <span className="font-semibold text-ink-800 max-w-[180px] truncate">{summary}</span>
        <ChevronDown size={14} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div className="absolute z-20 mt-1 w-80 rounded-lg border border-gray-200 bg-white shadow-lg p-3 space-y-2">
          {!available && (
            <p className="text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1.5">
              Couldn't list workspaces (needs SELECT on <code>system.access</code>). Showing the
              current workspace only.
            </p>
          )}

          {/* Include / Exclude mode */}
          <div className="flex items-center gap-1 text-xs">
            {(['include', 'exclude'] as const).map((m) => (
              <button
                key={m}
                onClick={() => onChange({ ...value, mode: m })}
                className={`flex-1 rounded-md border px-2 py-1 capitalize transition-colors ${
                  value.mode === m
                    ? 'border-databricks-400 bg-databricks-50 text-databricks-700 font-semibold'
                    : 'border-gray-200 text-ink-500 hover:bg-gray-50'
                }`}
              >
                {m}
              </button>
            ))}
          </div>

          {/* Quick actions */}
          <div className="flex items-center gap-2 text-[11px]">
            <button className="text-databricks-600 hover:underline" onClick={() => onChange({ mode: 'include', workspace_ids: [] })}>
              All workspaces
            </button>
            <span className="text-ink-300">·</span>
            <button
              className="text-databricks-600 hover:underline"
              onClick={() => {
                const cur = workspaces.find((w) => w.is_current);
                if (cur) onChange({ mode: 'include', workspace_ids: [cur.id] });
              }}
            >
              This workspace
            </button>
            {selected.length > 0 && (
              <>
                <span className="text-ink-300">·</span>
                <button className="text-ink-500 hover:underline" onClick={() => onChange({ ...value, workspace_ids: [] })}>
                  Clear
                </button>
              </>
            )}
          </div>

          {/* Selected chips */}
          {selected.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {selected.map((id) => (
                <span key={id} className="inline-flex items-center gap-1 rounded-full bg-gray-100 px-2 py-0.5 text-[11px] text-ink-700">
                  <Building2 size={11} className="text-ink-400" />
                  <span className="max-w-[140px] truncate">{byId.get(id)?.name || id}</span>
                  <button onClick={() => toggle(id)} className="text-ink-400 hover:text-ink-700"><X size={11} /></button>
                </span>
              ))}
            </div>
          )}

          {/* Search */}
          <div className="flex items-center gap-1.5 rounded-md border border-gray-200 px-2 py-1">
            <Search size={13} className="text-ink-400 shrink-0" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search workspaces by name…"
              className="w-full text-xs outline-none bg-transparent"
            />
          </div>
          {query.trim() && (
            <div className="max-h-52 overflow-y-auto rounded-md border border-gray-100 divide-y divide-gray-50">
              {matches.length === 0 ? (
                <p className="text-[11px] text-ink-400 px-2 py-2">No matching workspaces.</p>
              ) : (
                matches.map((w) => {
                  const on = selectedSet.has(w.id);
                  return (
                    <button
                      key={w.id}
                      onClick={() => toggle(w.id)}
                      className="w-full flex items-center gap-2 px-2 py-1.5 text-left text-xs hover:bg-gray-50"
                    >
                      <span className={`w-4 h-4 rounded border flex items-center justify-center shrink-0 ${on ? 'bg-databricks-500 border-databricks-500' : 'border-gray-300'}`}>
                        {on && <Check size={11} className="text-white" />}
                      </span>
                      <span className="flex-1 min-w-0">
                        <span className="block truncate text-ink-800">{w.name}</span>
                        <span className="block text-[10px] text-ink-400 tabular-nums">{w.id}{w.is_current ? ' · current' : ''}</span>
                      </span>
                    </button>
                  );
                })
              )}
            </div>
          )}
          <p className="text-[10px] text-ink-400 leading-snug">
            Scopes activity signals (Genie Agents, Adoption, most-accessed). Catalog-metadata
            pillars are metastore-wide and aren't affected.
          </p>
        </div>
      )}
    </div>
  );
}
