import { useEffect, useState } from 'react';
import { apiGet } from './useApi';
import type { AppConfig } from '../types';

export function useConfig() {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    apiGet<AppConfig>('/config')
      .then((c) => {
        if (active) setConfig(c);
      })
      .catch((e) => {
        if (active) setError(e.message || 'Failed to load config');
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  return { config, error, loading };
}
