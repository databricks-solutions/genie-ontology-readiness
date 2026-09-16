import type { CompareStatus, HistorySnapshot } from '../types';

function snapshotId(snapshot: HistorySnapshot): number {
  return Number(snapshot.id);
}

export function defaultComparisonPair(
  history: HistorySnapshot[],
  selectedSnapshotId: number | null
): { baselineId: number; currentId: number } | null {
  const chronological = [...history]
    .filter((snapshot) => Number.isFinite(snapshotId(snapshot)))
    .sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
  if (chronological.length < 2) return null;

  const selectedIndex =
    selectedSnapshotId == null
      ? chronological.length - 1
      : chronological.findIndex((snapshot) => snapshotId(snapshot) === selectedSnapshotId);
  const currentIndex = selectedIndex >= 0 ? selectedIndex : chronological.length - 1;

  // A selected run normally compares to its immediately preceding run. If the
  // selected run is the first ever snapshot, treat it as the baseline and use
  // the next run as current so the comparison never runs backwards in time.
  if (currentIndex === 0) {
    return {
      baselineId: snapshotId(chronological[0]),
      currentId: snapshotId(chronological[1]),
    };
  }
  return {
    baselineId: snapshotId(chronological[currentIndex - 1]),
    currentId: snapshotId(chronological[currentIndex]),
  };
}

export function formatDelta(delta: number | null | undefined): string {
  if (delta == null || Number.isNaN(delta)) return 'n/a';
  const rounded = Math.round(delta);
  if (rounded > 0) return `+${rounded}`;
  return String(rounded);
}

export function formatScore(score: number | null | undefined): string {
  if (score == null || Number.isNaN(score)) return 'n/a';
  return String(Math.round(score));
}

export function statusLabel(status: CompareStatus): string {
  switch (status) {
    case 'improved':
      return 'improved';
    case 'declined':
      return 'declined';
    case 'unchanged':
      return 'unchanged';
    case 'new':
      return 'new';
    case 'removed':
      return 'removed';
    case 'unavailable':
      return 'n/a';
    default:
      return 'n/a';
  }
}

export function statusClass(status: CompareStatus): string {
  switch (status) {
    case 'improved':
      return 'text-emerald-700';
    case 'declined':
      return 'text-red-700';
    case 'new':
      return 'text-databricks-700';
    case 'removed':
    case 'unavailable':
      return 'text-ink-400';
    default:
      return 'text-ink-600';
  }
}
