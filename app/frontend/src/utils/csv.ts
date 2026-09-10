// Client-side CSV export for a pillar's drill-down "as shown" — the rows already
// reflect the active workspace filter and drill-down, so we just serialize them.
import type { DrillDownColumn } from '../types';

function escapeCell(value: unknown): string {
  if (value === null || value === undefined) return '';
  const s = String(value);
  // Quote if the value contains a comma, quote, or newline; double embedded quotes.
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function rowsToCsv(
  columns: DrillDownColumn[],
  rows: Record<string, string | number | null>[]
): string {
  const header = columns
    .map((c) => escapeCell(c.unit ? `${c.label} (${c.unit})` : c.label))
    .join(',');
  const body = rows
    .map((r) => columns.map((c) => escapeCell(r[c.key])).join(','))
    .join('\n');
  return `${header}\n${body}`;
}

// Trigger a browser download of an arbitrary blob as `filename`. Works in a
// deployed Databricks App (normal browser download; not an Artifact sandbox).
export function downloadBlob(filename: string, blob: Blob): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// Trigger a browser download of `csv` as `filename`.
export function downloadCsv(filename: string, csv: string): void {
  downloadBlob(filename, new Blob([csv], { type: 'text/csv;charset=utf-8;' }));
}

// A safe, timestamped filename for a pillar export, e.g. "metadata-20260910-1503.csv".
export function csvFilename(pillarKey: string): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, '0');
  const stamp = `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`;
  return `${pillarKey}-${stamp}.csv`;
}
