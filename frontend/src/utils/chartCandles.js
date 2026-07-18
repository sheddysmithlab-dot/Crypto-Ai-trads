/** Normalize backend kline time (ms or sec) to lightweight-charts unix seconds. */
export function normalizeChartCandleTime(raw) {
  if (raw == null || raw === '') return null;
  const n = Number(raw);
  if (!Number.isFinite(n)) return null;
  if (n > 1_000_000_000_000) return Math.floor(n / 1000);
  return Math.floor(n);
}

export function snapToChartInterval(time, intervalSeconds) {
  if (!intervalSeconds || intervalSeconds <= 0) return time;
  return Math.floor(time / intervalSeconds) * intervalSeconds;
}

/** Candles keep their natural green/red colors — trade fire uses neon overlay instead. */
export function decorateCandlestickSeries(data) {
  return data;
}

export function computeTradeFireMarkers() {
  return [];
}
