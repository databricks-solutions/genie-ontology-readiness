import { useMemo, useRef, useState, useEffect } from 'react';
import { Layers, Search, Check, ChevronDown, Loader2, Info } from 'lucide-react';

const WHY_SINGLE_CATALOG =
  'To select an individual schema, first pick a single catalog. Schema scope is only available when exactly one catalog is selected.';

// Always visible next to the catalog picker. Empty value means every schema in
// the selected catalog; a single schema name narrows the metadata pillars.
// Locked (no single catalog yet) greys the control out and explains why via the
// info button — the picker itself stays on screen so the option is discoverable.
export default function SchemaFilter({
  schemas,
  available,
  loading,
  value,
  onChange,
  disabled,
  locked,
}: {
  schemas: { name: string }[];
  available: boolean;
  loading?: boolean;
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
  locked?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [infoOpen, setInfoOpen] = useState(false);
  const [query, setQuery] = useState('');
  const ref = useRef<HTMLDivElement>(null);
  const pickerDisabled = Boolean(disabled || locked);

  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
        setInfoOpen(false);
      }
    }
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, []);

  useEffect(() => {
    if (locked) setOpen(false);
  }, [locked]);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? schemas.filter((s) => s.name.toLowerCase().includes(q)) : schemas;
  }, [query, schemas]);

  const summary = locked
    ? 'All schemas'
    : schemas.length === 0
    ? loading
      ? 'Loading…'
      : 'All schemas'
    : !value
    ? `All ${schemas.length} schemas`
    : value;

  return (
    <div className="relative flex items-center gap-1" ref={ref}>
      <button
        type="button"
        disabled={pickerDisabled || (schemas.length === 0 && !loading && !locked)}
        onClick={() => {
          setInfoOpen(false);
          setOpen((o) => !o);
        }}
        className="btn-secondary py-1.5 px-3 flex items-center gap-1.5 text-xs disabled:opacity-50"
        title={
          locked
            ? WHY_SINGLE_CATALOG
            : 'Optionally assess a single schema inside the selected catalog'
        }
      >
        <Layers size={14} />
        <span className="text-ink-500">Schema:</span>
        <span className="font-semibold text-ink-800 max-w-[160px] truncate">{summary}</span>
        {loading && !locked ? (
          <Loader2 size={13} className="animate-spin" />
        ) : (
          <ChevronDown size={14} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
        )}
      </button>

      <button
        type="button"
        className="inline-flex items-center justify-center w-6 h-6 rounded-full text-ink-400 hover:text-databricks-600 hover:bg-databricks-50"
        aria-label="Why schema selection needs one catalog"
        title={WHY_SINGLE_CATALOG}
        onClick={() => {
          setOpen(false);
          setInfoOpen((o) => !o);
        }}
      >
        <Info size={13} />
      </button>

      {infoOpen && (
        <div className="absolute z-30 top-full left-0 mt-1 w-72 rounded-lg border border-gray-200 bg-white shadow-lg p-3 text-[11px] text-ink-600 leading-relaxed">
          {WHY_SINGLE_CATALOG}
        </div>
      )}

      {open && !pickerDisabled && (
        <div className="absolute z-20 top-full left-0 mt-1 w-72 rounded-lg border border-gray-200 bg-white shadow-lg p-3 space-y-2">
          {!available && (
            <p className="text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded-sm px-2 py-1.5">
              Couldn't list schemas in this catalog. The assessment will cover every schema it can read.
            </p>
          )}
          <div className="flex items-center justify-between gap-2 text-[11px]">
            <button
              className="text-databricks-600 hover:underline font-medium"
              onClick={() => {
                onChange('');
                setOpen(false);
              }}
            >
              All schemas
            </button>
            <span className="text-ink-400">{value ? '1 selected' : 'assessing all'}</span>
          </div>
          <div className="flex items-center gap-1.5 rounded-md border border-gray-200 px-2 py-1">
            <Search size={13} className="text-ink-400 shrink-0" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search schemas…"
              className="w-full text-xs outline-hidden bg-transparent"
            />
          </div>
          <div className="max-h-60 overflow-y-auto rounded-md border border-gray-100 divide-y divide-gray-50">
            {shown.length === 0 ? (
              <p className="text-[11px] text-ink-400 px-2 py-2">No schemas.</p>
            ) : (
              shown.map((s) => {
                const on = value === s.name;
                return (
                  <button
                    key={s.name}
                    onClick={() => {
                      onChange(on ? '' : s.name);
                      setOpen(false);
                    }}
                    className="w-full flex items-center gap-2 px-2 py-1.5 text-left text-xs hover:bg-gray-50"
                  >
                    <span
                      className={`w-4 h-4 rounded-full border flex items-center justify-center shrink-0 ${
                        on ? 'bg-databricks-500 border-databricks-500' : 'border-gray-300'
                      }`}
                    >
                      {on && <Check size={11} className="text-white" />}
                    </span>
                    <span className="flex-1 min-w-0 truncate text-ink-800">{s.name}</span>
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
