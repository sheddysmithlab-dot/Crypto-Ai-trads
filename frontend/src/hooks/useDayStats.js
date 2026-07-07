import { useEffect, useState } from 'react';
import { authFetch } from '../config/api';

const REFRESH_MS = 60000;

// 24h high/low from backend-persisted chart snapshot (refreshed every 24h on server).
export function useDayStats(pairLabel) {
  const [stats, setStats] = useState({ high: null, low: null });

  useEffect(() => {
    let cancelled = false;

    async function fetchStats() {
      try {
        const res = await authFetch(`/chart/24h?pair=${encodeURIComponent(pairLabel)}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (!cancelled) {
          setStats({
            high: data.high != null ? Number(data.high) : null,
            low: data.low != null ? Number(data.low) : null,
          });
        }
      } catch (err) {
        console.warn(`[DAY STATS] Failed to fetch backend 24h stats for ${pairLabel}:`, err);
        if (!cancelled) setStats({ high: null, low: null });
      }
    }

    fetchStats();
    const interval = setInterval(fetchStats, REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [pairLabel]);

  return stats;
}
