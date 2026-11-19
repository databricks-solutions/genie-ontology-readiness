import { AlertTriangle } from 'lucide-react';

// Shown when a Lakebase database is attached but the app couldn't connect to it at startup,
// so history is off. Before, this only appeared as a warning in the app log.
export function HistoryOffNotice() {
  return (
    <p
      data-testid="history-off"
      className="mt-2.5 flex items-start gap-1.5 rounded-md bg-amber-50 border border-amber-200 px-2.5 py-2 text-xs text-amber-800"
    >
      <AlertTriangle size={13} className="mt-0.5 shrink-0 text-amber-500" />
      <span>
        <span className="font-medium">History is off.</span> A Lakebase database is attached, but the app
        couldn&apos;t connect to it when it started, so assessments and plans aren&apos;t saved. Check the
        app&apos;s <code className="text-[11px]">lakebase-db</code> resource (permission{' '}
        <span className="font-medium">Can connect and create</span>), then search the app logs for
        &quot;Lakebase&quot; and restart the app.
      </span>
    </p>
  );
}
