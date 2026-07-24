/** Public Bybit linear market data — no API key required. */
export const BYBIT_PUBLIC_REST = 'https://api.bybit.com';
export const BYBIT_PUBLIC_WS_LINEAR = 'wss://stream.bybit.com/v5/public/linear';
export const BYBIT_MARKET_CATEGORY = 'linear';
/** Bybit v5 kline max per request. */
export const BYBIT_KLINE_MAX_LIMIT = 1000;

export function bybitKlineUrl(symbol, interval, limit = 200, endMs = null) {
  const capped = Math.min(Math.max(1, limit), BYBIT_KLINE_MAX_LIMIT);
  let url =
    `${BYBIT_PUBLIC_REST}/v5/market/kline?category=${BYBIT_MARKET_CATEGORY}` +
    `&symbol=${symbol}&interval=${interval}&limit=${capped}`;
  if (endMs != null && Number.isFinite(endMs)) {
    url += `&end=${Math.floor(endMs)}`;
  }
  return url;
}

export function bybitRecentTradeUrl(symbol, limit = 1000) {
  return `${BYBIT_PUBLIC_REST}/v5/market/recent-trade?category=${BYBIT_MARKET_CATEGORY}&symbol=${symbol}&limit=${limit}`;
}

export function bybitPublicTradeTopic(symbol) {
  return `publicTrade.${symbol}`;
}

/** Live candle stream — keeps 1m/5m/… charts contiguous (no trade-bucket gaps). */
export function bybitPublicKlineTopic(symbol, interval) {
  return `kline.${interval}.${symbol}`;
}

/** How many bars cover ~1 calendar day for a TF (seconds). Daily TF → 90 days history. */
export function barsForOneDay(intervalSeconds, tfKey = '') {
  if (tfKey === '1D' || intervalSeconds >= 86400) return 90;
  const day = 86400;
  return Math.ceil(day / Math.max(intervalSeconds, 1)) + 8;
}

function mapKlineRow(k) {
  return {
    time: Math.floor(parseInt(k[0], 10) / 1000),
    open: parseFloat(k[1]),
    high: parseFloat(k[2]),
    low: parseFloat(k[3]),
    close: parseFloat(k[4]),
    volume: parseFloat(k[5]),
  };
}

/**
 * Fetch ~1 full day of Bybit linear klines (paginated if >1000 bars, e.g. 1m).
 * Oldest → newest. Call on every symbol/TF reload for a realistic chart backbone.
 */
export async function fetchBybitDayKlines(bybitSymbol, interval, intervalSeconds, tfKey = '') {
  const need = barsForOneDay(intervalSeconds, tfKey);
  const byTime = new Map();
  let endMs = null;
  let guard = 0;

  while (byTime.size < need && guard < 6) {
    guard += 1;
    const batch = Math.min(BYBIT_KLINE_MAX_LIMIT, need - byTime.size + 5);
    const res = await fetch(bybitKlineUrl(bybitSymbol, interval, batch, endMs));
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const json = await res.json();
    const raw = json?.result?.list;
    if (!Array.isArray(raw) || raw.length === 0) break;

    for (const row of raw) {
      const bar = mapKlineRow(row);
      if (Number.isFinite(bar.time) && Number.isFinite(bar.close)) {
        byTime.set(bar.time, bar);
      }
    }

    // Oldest in this batch (Bybit returns newest-first).
    const oldestMs = parseInt(raw[raw.length - 1][0], 10);
    if (!Number.isFinite(oldestMs)) break;
    if (raw.length < batch) break;
    endMs = oldestMs - 1;
  }

  const candles = Array.from(byTime.values()).sort((a, b) => a.time - b.time);
  if (!candles.length) throw new Error('Empty klines');
  return candles;
}
