import { useEffect, useState } from 'react';
import { getBinanceSymbol, getBybitSymbol } from '../data/pairs';

const REFRESH_MS = 30000;

// 24h high/low for the active pair from Bybit public tickers (Binance fallback).
export function useDayStats(pairLabel) {
  const [stats, setStats] = useState({ high: null, low: null });

  useEffect(() => {
    let cancelled = false;
    const bybitSymbol = getBybitSymbol(pairLabel);
    const binanceSymbol = getBinanceSymbol(pairLabel);

    if (!bybitSymbol && !binanceSymbol) {
      setStats({ high: null, low: null });
      return undefined;
    }

    async function fetchStats() {
      try {
        if (bybitSymbol) {
          const res = await fetch(`https://api.bybit.com/v5/market/tickers?category=spot&symbol=${bybitSymbol}`);
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const json = await res.json();
          const item = json?.result?.list?.[0];
          if (!item) throw new Error('Empty ticker');
          if (!cancelled) {
            setStats({ high: parseFloat(item.highPrice24h), low: parseFloat(item.lowPrice24h) });
          }
          return;
        }

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
