import type { AppConfig } from '../types';

// Whether runs are saved: 'saved' (history works), 'not_attached' (no Lakebase database), or
// 'unavailable' (a database is attached but the app couldn't connect, so history is off).
// `history_ready` is absent on older servers: treat that as 'saved', as before.
export type HistoryStatus = 'saved' | 'not_attached' | 'unavailable';

export function historyStatus(config: Pick<AppConfig, 'lakebase_enabled' | 'history_ready'>): HistoryStatus {
  if (!config.lakebase_enabled) return 'not_attached';
  return config.history_ready === false ? 'unavailable' : 'saved';
}
