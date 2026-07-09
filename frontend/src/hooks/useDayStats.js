import { useEffect, useState } from 'react';
import { authFetch } from '../config/api';
import { getBybitSymbol } from '../data/pairs';

const REFRESH_MS = 60000;

async function fetchBybitDayStats(pairLabel) {
  const bybitSymbol = getBybitSymbol(pairLabel);
  if (!bybitSymbol) return { high: null, low: null };

  try {
    const res = await fetch(
      `https://api.bybit.com/v5/market/tickers?category=spot&symbol=${bybitSymbol}`
    );
    if (!res.ok) return { high: null, low: null };
    const json = await res.json();
    const ticker = json?.result?.list?.[0];
    if (!ticker) return { high: null, low: null };
    const high = parseFloat(ticker.highPrice24h);
    const low = parseFloat(ticker.lowPrice24h);
    return {
      high: Number.isFinite(high) && high > 0 ? high : null,
      low: Number.isFinite(low) && low > 0 ? low : null,
    };
  } catch {
    return { high: null, low: null };
  }
}

// 24h high/low — backend /chart/24h first, Bybit public ticker as fallback.
export function useDayStats(pairLabel) {
  const [stats, setStats] = useState({ high: null, low: null });

  useEffect(() => {
    let cancelled = false;

    async function fetchStats() {
      let high = null;
      let low = null;

      try {
        const res = await authFetch(`/chart/24h?pair=${encodeURIComponent(pairLabel)}`);
        if (res.ok) {
          const data = await res.json();
          high = data.high != null ? Number(data.high) : null;
          low = data.low != null ? Number(data.low) : null;
        }
      } catch (err) {
        console.warn(`[DAY STATS] Backend 24h fetch failed for ${pairLabel}:`, err);
      }

      if ((high == null || low == null) && !cancelled) {
        const fallback = await fetchBybitDayStats(pairLabel);
        if (high == null) high = fallback.high;
        if (low == null) low = fallback.low;
      }

      if (!cancelled) setStats({ high, low });
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
