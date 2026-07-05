import { useEffect, useState } from 'react';
import { getBinanceSymbol } from '../data/pairs';

const REFRESH_MS = 30000;

// 24h high/low for the currently active pair, pulled from Binance's free
// public 24hr ticker endpoint. Re-fetches whenever the pair changes (so
// switching coins in the dropdown updates these too) and on a timer.
export function useDayStats(pairLabel) {
  const [stats, setStats] = useState({ high: null, low: null });

  useEffect(() => {
    let cancelled = false;
    const binanceSymbol = getBinanceSymbol(pairLabel);

    if (!binanceSymbol) {
      setStats({ high: null, low: null });
      return undefined;
    }

    async function fetchStats() {
      try {
        const res = await fetch(`https://api.binance.com/api/v3/ticker/24hr?symbol=${binanceSymbol.toUpperCase()}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (!cancelled) {
          setStats({ high: parseFloat(data.highPrice), low: parseFloat(data.lowPrice) });
        }
      } catch (err) {
        console.warn(`[DAY STATS] Failed to fetch 24h high/low for ${pairLabel}:`, err);
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
