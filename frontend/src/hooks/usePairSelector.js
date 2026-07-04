import { useCallback, useState } from 'react';
import { API_BASE } from '../config/api';
import { debugLog } from '../config/debug';
import { TRADING_PAIRS } from '../data/pairs';

// "Select Trading Pair" state - drives the chart symbol switch, the free
// Binance feed re-subscription, and backend single-coin-focus sync.
export function usePairSelector() {
  const [pairs, setPairs] = useState(TRADING_PAIRS);
  const [activeSymbol, setActiveSymbol] = useState('BTC');

  const activePair = pairs.find((p) => p.symbol === activeSymbol) || pairs[0];

  const selectPair = useCallback(
    (symbol) => {
      const pair = pairs.find((p) => p.symbol === symbol);
      if (!pair) return;

      const fullLabel = `${pair.symbol}/USDT`;
      debugLog(`[PAIR SELECTOR] Switching to ${fullLabel} @ $${pair.price.toFixed(2)}`);
      setActiveSymbol(symbol);

      fetch(`${API_BASE}/set-pair`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pair: fullLabel, price: pair.price }),
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
