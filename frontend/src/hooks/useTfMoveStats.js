import { useEffect, useState } from 'react';
import { authFetch } from '../config/api';

const REFRESH_MS = 30000;

const WINDOW_LABELS = {
  '1hr': '1hr avg',
  '2hr': '2hr avg',
  '1Day': '1Day avg',
  '7Day': '7Day avg',
};

export function formatTfMoveLabel(timeframe, windowLabel) {
  const win = WINDOW_LABELS[windowLabel] || windowLabel || 'avg';
  return `${timeframe || '1M'} · ${win}`;
}

// Timeframe-scoped avg % move per candle — backend /chart/tf-move.
export function useTfMoveStats(pairLabel, timeframe) {
  const [stats, setStats] = useState({
    avgPct: null,
    totalPct: null,
    windowLabel: null,
    candleCount: 0,
  });

  useEffect(() => {
    let cancelled = false;

    async function fetchStats() {
      try {
        const tf = encodeURIComponent(timeframe || '1M');
        const pair = encodeURIComponent(pairLabel);
        const res = await authFetch(`/chart/tf-move?pair=${pair}&timeframe=${tf}`);
        if (!res.ok || cancelled) return;
        const data = await res.json();
        if (cancelled) return;
        setStats({
          avgPct: data.avg_pct != null ? Number(data.avg_pct) : null,
          totalPct: data.total_pct != null ? Number(data.total_pct) : null,
          windowLabel: data.window_label || null,
          candleCount: Number(data.candle_count) || 0,
        });
      } catch (err) {
        console.warn(`[TF MOVE] Fetch failed for ${pairLabel} ${timeframe}:`, err);
        if (!cancelled) {
          setStats({ avgPct: null, totalPct: null, windowLabel: null, candleCount: 0 });
        }
      }
    }

    fetchStats();
    const interval = setInterval(fetchStats, REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [pairLabel, timeframe]);

  return stats;
}
