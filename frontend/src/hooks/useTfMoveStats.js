import { useEffect, useState } from 'react';
import { authFetch } from '../config/api';

const REFRESH_MS = 30000;

const WINDOW_LABELS = {
  '1h avg': '1h avg',
  '4h avg': '4h avg',
  '10h avg': '10h avg',
  '24h avg': '24h avg',
  '7d avg': '7d avg',
  // legacy keys
  '1hr': '1h avg',
  '2hr': '2hr move',
  '1Day': '24h avg',
  '7Day': '7d avg',
};

export function formatTfMoveLabel(timeframe, windowLabel) {
  const win = WINDOW_LABELS[windowLabel] || windowLabel || 'avg';
  return `${timeframe || '1M'} · ${win}`;
}

/** Lookback High/Low → avg % for active TF bar (1h/4h/10h/24h/7d). */
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
