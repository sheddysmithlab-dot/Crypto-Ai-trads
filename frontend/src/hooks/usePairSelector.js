import { useCallback, useState } from 'react';
import { authFetch } from '../config/api';
import { debugLog } from '../config/debug';
import { TRADING_PAIRS, getBybitSymbol, pairLabelForSymbol } from '../data/pairs';

async function fetchLivePairPrice(symbol) {
  const bybitSymbol = getBybitSymbol(pairLabelForSymbol(symbol));
  if (!bybitSymbol) return null;
  try {
    const res = await fetch(
      `https://api.bybit.com/v5/market/tickers?category=linear&symbol=${bybitSymbol}`
    );
    if (!res.ok) return null;
    const json = await res.json();
    const last = parseFloat(json?.result?.list?.[0]?.lastPrice);
    return Number.isFinite(last) && last > 0 ? last : null;
  } catch {
    return null;
  }
}

/**
 * Pair switch drives: main chart reload, Bybit live WS, and backend /set-pair.
 * UI updates immediately; backend sync follows with live Bybit price.
 */
export function usePairSelector() {
  const [pairs, setPairs] = useState(TRADING_PAIRS);
  const [activeSymbol, setActiveSymbol] = useState('BTC');
  const [syncing, setSyncing] = useState(false);

  const activePair = pairs.find((p) => p.symbol === activeSymbol) || pairs[0];
  const activePairLabel = pairLabelForSymbol(activePair.symbol);

  const selectPair = useCallback(async (symbol, { silent = false } = {}) => {
    const pair = pairs.find((p) => p.symbol === symbol);
    if (!pair) return { ok: false, message: 'Unknown pair' };

    const fullLabel = pairLabelForSymbol(pair.symbol);

    // 1) Instant UI + chart switch (cached seed price), even before network.
    setActiveSymbol(symbol);
    setSyncing(true);

    try {
      const livePrice = await fetchLivePairPrice(symbol);
      const seedPrice = livePrice ?? pair.price;
      const priceLabel = seedPrice < 1 ? Number(seedPrice).toPrecision(6) : Number(seedPrice).toFixed(2);
      debugLog(`[PAIR SELECTOR] Switching to ${fullLabel} @ $${priceLabel}`);

      if (livePrice) {
        setPairs((prev) => prev.map((p) => (p.symbol === symbol ? { ...p, price: livePrice } : p)));
      }

      // 2) Backend focus — chart engine / auto-entries follow this pair.
      const res = await authFetch('/set-pair', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pair: fullLabel, price: seedPrice }),
      });
      const data = await res.json().catch(() => ({}));
      if (data.status === 'error') {
        if (!silent) window.alert(data.message || `Could not sync ${fullLabel} to backend.`);
        return { ok: false, message: data.message, pair: fullLabel };
      }
      debugLog(`[BACKEND] ${data.message || `Active pair → ${fullLabel}`}`);
      return { ok: true, pair: fullLabel, price: data.price ?? seedPrice };
    } catch (err) {
      console.error('[BACKEND] Failed to sync pair:', err);
      if (!silent) window.alert(`Backend sync failed for ${fullLabel}. Chart is local-only until reconnect.`);
      return { ok: false, message: String(err), pair: fullLabel };
    } finally {
      setSyncing(false);
    }
  }, [pairs]);

  const toggleStar = useCallback((symbol) => {
    setPairs((prev) => prev.map((p) => (p.symbol === symbol ? { ...p, starred: !p.starred } : p)));
  }, []);

  return { pairs, activeSymbol, activePair, activePairLabel, selectPair, toggleStar, syncing };
}
