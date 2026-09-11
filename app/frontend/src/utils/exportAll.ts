// "Export all to CSV": bundle every pillar's drill-down into one ZIP, plus a
// summary sheet. A single combined CSV would be wide and sparse (pillars have
// different drill-down schemas), so each pillar keeps its own file and clean
// columns; summary.csv gives the at-a-glance scorecard.
import JSZip from 'jszip';
import type { PillarScore } from '../types';
import { rowsToCsv, downloadBlob } from './csv';

const SUMMARY_COLUMNS = [
  { key: 'pillar', label: 'Pillar' },
  { key: 'score', label: 'Score' },
  { key: 'level', label: 'Level' },
  { key: 'maturity', label: 'Maturity' },
  { key: 'available', label: 'Available' },
  { key: 'gaps', label: 'Gaps' },
];

export function buildSummaryCsv(pillars: PillarScore[]): string {
  const rows = pillars.map((p) => ({
    pillar: p.name,
    score: p.score,
    level: p.level,
    maturity: p.level_label,
    available: p.available ? 'yes' : 'no',
    gaps: p.gaps.length ? p.gaps.join(' | ') : '—',
  }));
  return rowsToCsv(SUMMARY_COLUMNS, rows);
}

function stamp(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`;
}

// Build and download a ZIP: summary.csv + one <pillar-key>.csv per pillar that
// has drill-down rows (the same content that pillar's own Export CSV produces).
export async function downloadAllZip(pillars: PillarScore[]): Promise<void> {
  const zip = new JSZip();
  zip.file('summary.csv', buildSummaryCsv(pillars));
  for (const p of pillars) {
    const dd = p.drill_down;
    if (dd && dd.rows.length > 0) {
      zip.file(`${p.key}.csv`, rowsToCsv(dd.columns, dd.rows));
    }
  }
  const blob = await zip.generateAsync({ type: 'blob' });
  downloadBlob(`genie-readiness-all-${stamp()}.zip`, blob);
}
