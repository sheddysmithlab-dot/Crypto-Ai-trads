import { useEffect, useState } from 'react';
import { authFetch } from '../config/api';

const REFRESH_MS = 30000;

const WINDOW_LABELS = {
  '1hr': '1hr move',
  '2hr': '2hr move',
  '1Day': '1Day move',
  '7Day': '7Day move',
  '24h avg': '24h avg',
};

export function formatTfMoveLabel(timeframe, windowLabel) {
  const win = WINDOW_LABELS[windowLabel] || windowLabel || '24h avg';
  return `${timeframe || '1M'} · ${win}`;
}

/** 24h High/Low → avg % move for active TF bar (always magnitude ≥ 0). */
export function useTfMoveStats(pairLabel, timeframe) {
  const [stats, setStats] = useState({
    avgPct: null,
    totalPct: null,
    displayPct: null,
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
        const avgPct = data.avg_pct != null ? Number(data.avg_pct) : null;
        const totalPct = data.total_pct != null ? Number(data.total_pct) : null;
        const displayRaw =
          data.display_pct != null
            ? Number(data.display_pct)
            : avgPct != null
              ? avgPct
              : totalPct;
        const displayPct =
          displayRaw != null && Number.isFinite(displayRaw) ? Math.abs(displayRaw) : null;
        setStats({
          avgPct: displayPct,
          totalPct: displayPct,
          displayPct,
          windowLabel: data.window_label || '24h avg',
          candleCount: Number(data.candle_count) || 0,
        });
      } catch (err) {
        console.warn(`[TF MOVE] Fetch failed for ${pairLabel} ${timeframe}:`, err);
        if (!cancelled) {
          setStats({
            avgPct: null,
            totalPct: null,
            displayPct: null,
            windowLabel: null,
            candleCount: 0,
          });
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
