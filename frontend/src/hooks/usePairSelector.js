import { useCallback, useEffect, useState } from 'react';
import { authFetch } from '../config/api';
import { debugLog } from '../config/debug';
import {
  TRADING_PAIRS,
  getBybitSymbol,
  pairLabelForSymbol,
  setDynamicSymbolMap,
} from '../data/pairs';

const ACTIVE_SYMBOL_KEY = 'ai_trading_bot_active_symbol';

function normalizeSymbol(symbol) {
  const raw = String(symbol || '').trim().toUpperCase().replace('-', '/');
  if (!raw) return '';
  return raw.includes('/') ? raw.split('/')[0] : raw;
}

function readStoredSymbol() {
  try {
    const saved = normalizeSymbol(localStorage.getItem(ACTIVE_SYMBOL_KEY));
    if (saved) return saved;
  } catch {
    /* storage blocked */
  }
  return 'BTC';
}

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
  const [activeSymbol, setActiveSymbol] = useState(readStoredSymbol);
  const [syncing, setSyncing] = useState(false);

  // Fetch dynamic instruments map from backend so new watchlist coins resolve.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await authFetch('/markets');
        const data = await res.json().catch(() => ({}));
        if (!cancelled && data?.status === 'success' && data.symbol_map) {
          setDynamicSymbolMap(data.symbol_map);
          debugLog(`[MARKETS] Loaded ${Object.keys(data.symbol_map).length} dynamic symbols`);
        }
      } catch (err) {
        console.warn('[MARKETS] fetch failed:', err);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const activePair =
    pairs.find((p) => p.symbol === activeSymbol) ||
    { symbol: activeSymbol, icon: String(activeSymbol || '?').charAt(0), color: '#6b7280', price: 0 };
  const activePairLabel = pairLabelForSymbol(activeSymbol || activePair.symbol);

  useEffect(() => {
    try {
      localStorage.setItem(ACTIVE_SYMBOL_KEY, activeSymbol);
    } catch {
      /* storage blocked */
    }
  }, [activeSymbol]);

  const selectPair = useCallback(async (symbol, { silent = false } = {}) => {
    const sym = normalizeSymbol(symbol);
    if (!sym) return { ok: false, message: 'Empty symbol' };

    // Accept any symbol — dynamic map + fallback {symbol}USDT handles unknown coins.
    const existing = pairs.find((p) => p.symbol === sym);
    const pair = existing || { symbol: sym, icon: sym.charAt(0), color: '#6b7280', price: 0 };
    const fullLabel = pairLabelForSymbol(pair.symbol);

    // 1) Instant UI + chart switch (cached seed price), even before network.
    setActiveSymbol(sym);
    setSyncing(true);
    if (!existing) {
      setPairs((prev) => (prev.some((p) => p.symbol === sym) ? prev : [...prev, pair]));
    }

    try {
      const livePrice = await fetchLivePairPrice(sym);
      const seedPrice = livePrice ?? pair.price;
      const priceLabel = seedPrice < 1 ? Number(seedPrice).toPrecision(6) : Number(seedPrice).toFixed(2);
      debugLog(`[PAIR SELECTOR] Switching to ${fullLabel} @ $${priceLabel}`);

      if (livePrice) {
        setPairs((prev) => prev.map((p) => (p.symbol === sym ? { ...p, price: livePrice } : p)));
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
