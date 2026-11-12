// Assessment scope helpers (#89): label a run's scope and keep the "Readiness over
// time" trend to runs that covered the same catalogs, schemas and workspaces.
import type { AssessmentScope, HistorySnapshot } from '../types';

/** Short label for a run's scope, e.g. "All catalogs", "sales", "a, b", "a, b +3 more". */
export function scopeLabel(scope: AssessmentScope | null | undefined): string {
  if (!scope) return 'Unknown scope';
  const n = scope.catalogs.length;
  if (n === 0) return 'All catalogs';
  if (n === 1) {
    const s = scope.schemas.length;
    return s ? `${scope.catalogs[0]} · ${s} schema${s === 1 ? '' : 's'}` : scope.catalogs[0];
  }
  const shown = scope.catalogs.slice(0, 2).join(', ');
  return n > 2 ? `${shown} +${n - 2} more` : shown;
}

/** Full description for a tooltip: every catalog and schema in the scope. */
export function scopeDetail(scope: AssessmentScope | null | undefined): string {
  if (!scope) return 'Saved before scopes were recorded; not included in scoped trends.';
  const parts = [
    scope.catalogs.length ? `Catalogs: ${scope.catalogs.join(', ')}` : 'Catalogs: all in scope',
  ];
  if (scope.schemas.length) parts.push(`Schemas: ${scope.schemas.join(', ')}`);
  if (scope.workspace_ids.length) {
    parts.push(`Workspaces (${scope.workspace_mode ?? 'include'}): ${scope.workspace_ids.join(', ')}`);
  }
  return parts.join('\n');
}

export interface ScopeOption {
  hash: string;
  label: string;
  runs: number;
}

/** The distinct known scopes in a history (newest-first), with run counts. Two
 *  different scopes can share a short label (e.g. "All catalogs" on two workspace
 *  filters); those get a short id so the picker never shows look-alike options. */
export function scopeOptions(history: HistorySnapshot[]): ScopeOption[] {
  const out = new Map<string, ScopeOption>();
  for (const h of history) {
    if (!h.scope_hash) continue;
    const o = out.get(h.scope_hash);
    if (o) o.runs += 1;
    else out.set(h.scope_hash, { hash: h.scope_hash, label: scopeLabel(h.scope), runs: 1 });
  }
  const opts = [...out.values()];
  const seen = new Map<string, number>();
  for (const o of opts) seen.set(o.label, (seen.get(o.label) ?? 0) + 1);
  return opts.map((o) => ((seen.get(o.label) ?? 0) > 1 ? { ...o, label: `${o.label} · #${o.hash.slice(0, 6)}` } : o));
}

/** The trend scope to show: the user's pick while it still exists, else the default. */
export function resolveTrendScope(
  picked: string | null,
  options: ScopeOption[],
  fallback: string | null,
): string | null {
  return picked && options.some((o) => o.hash === picked) ? picked : fallback;
}

/** Wording for Compare when two scopes differ; names the difference when the
 *  short labels alone would read the same. */
export function scopeDifference(a: AssessmentScope | null | undefined, b: AssessmentScope | null | undefined): string {
  const la = scopeLabel(a);
  const lb = scopeLabel(b);
  return la !== lb ? `${la} vs ${lb}` : `both "${la}", but not the same catalogs, schemas or workspaces`;
}

/** The scope the trend should show by default: the run on screen, else the newest
 *  run with a known scope. null when no run has a known scope. */
export function defaultTrendScope(history: HistorySnapshot[], currentId: number | null): string | null {
  const current = currentId != null ? history.find((h) => Number(h.id) === Number(currentId)) : undefined;
  if (current?.scope_hash) return current.scope_hash;
  return history.find((h) => h.scope_hash)?.scope_hash ?? null;
}

/** Runs in one scope, oldest first, for the trend chart. Runs without a known
 *  scope are never included. */
export function trendRuns(history: HistorySnapshot[], scopeHash: string | null): HistorySnapshot[] {
  if (!scopeHash) return [];
  return history
    .filter((h) => h.scope_hash === scopeHash)
    .sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
}
