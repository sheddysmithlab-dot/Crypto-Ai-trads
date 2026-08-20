import { useEffect, useState } from 'react';
import { authFetch } from '../config/api';

const REFRESH_MS = 30000;

const WINDOW_LABELS = {
  '1hr': '1hr move',
  '2hr': '2hr move',
  '1Day': '1Day move',
  '7Day': '7Day move',
};

export function formatTfMoveLabel(timeframe, windowLabel) {
  const win = WINDOW_LABELS[windowLabel] || windowLabel || 'move';
  return `${timeframe || '1M'} · ${win}`;
}

// Prefer window total % (clear market direction). Avg signed % often cancels to ~0.
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
        // Show total window move; fall back to avg when total missing.
        const displayPct =
          totalPct != null && Number.isFinite(totalPct)
            ? totalPct
            : avgPct != null && Number.isFinite(avgPct)
              ? avgPct
              : null;
        setStats({
          avgPct,
          totalPct,
          displayPct,
          windowLabel: data.window_label || null,
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
