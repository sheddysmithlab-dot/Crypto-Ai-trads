import { useCallback, useState } from 'react';
import { authFetch } from '../config/api';
import { debugLog } from '../config/debug';
import { TRADING_PAIRS, getBybitSymbol } from '../data/pairs';

async function fetchLivePairPrice(symbol) {
  const bybitSymbol = getBybitSymbol(`${symbol}/USDT`);
  if (!bybitSymbol) return null;
  try {
    const res = await fetch(`https://api.bybit.com/v5/market/tickers?category=spot&symbol=${bybitSymbol}`);
    if (!res.ok) return null;
    const json = await res.json();
    const last = parseFloat(json?.result?.list?.[0]?.lastPrice);
    return Number.isFinite(last) && last > 0 ? last : null;
  } catch {
    return null;
  }
}

// "Select Trading Pair" state - drives the chart symbol switch, the free
// Pair switch: refresh chart history + backend single-coin-focus sync.
export function usePairSelector() {
  const [pairs, setPairs] = useState(TRADING_PAIRS);
  const [activeSymbol, setActiveSymbol] = useState('BTC');

  const activePair = pairs.find((p) => p.symbol === activeSymbol) || pairs[0];

  const selectPair = useCallback(
    async (symbol) => {
      const pair = pairs.find((p) => p.symbol === symbol);
      if (!pair) return;

      const fullLabel = `${pair.symbol}/USDT`;
      const livePrice = await fetchLivePairPrice(symbol);
      const seedPrice = livePrice ?? pair.price;
      debugLog(`[PAIR SELECTOR] Switching to ${fullLabel} @ $${seedPrice.toFixed(2)}`);
      setActiveSymbol(symbol);

      if (livePrice) {
        setPairs((prev) => prev.map((p) => (p.symbol === symbol ? { ...p, price: livePrice } : p)));
      }

      authFetch('/set-pair', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pair: fullLabel, price: seedPrice }),
      })
        .then((res) => res.json())
        .then((data) => debugLog(`[BACKEND] ${data.message}`))
        .catch((err) => console.error('[BACKEND] Failed to sync pair:', err));
    },
    [pairs]
  );

  const toggleStar = useCallback((symbol) => {
    setPairs((prev) => prev.map((p) => (p.symbol === symbol ? { ...p, starred: !p.starred } : p)));
  }, []);

  return { pairs, activeSymbol, activePair, activePairLabel: `${activePair.symbol}/USDT`, selectPair, toggleStar };
}
