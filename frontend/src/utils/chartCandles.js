/** Normalize backend kline time (ms or sec) to lightweight-charts unix seconds. */
export function normalizeChartCandleTime(raw) {
  if (raw == null || raw === '') return null;
  const n = Number(raw);
  if (!Number.isFinite(n)) return null;
  if (n > 1_000_000_000_000) return Math.floor(n / 1000);
  return Math.floor(n);
}

const NEON_LONG = {
  color: '#39ff14',
  borderColor: '#39ff14',
  wickColor: '#7fff00',
};

const NEON_SHORT = {
  color: '#ff10f0',
  borderColor: '#ff10f0',
  wickColor: '#ff6bff',
};

export function buildHighlightMap(entryCandles) {
  const map = new Map();
  if (!Array.isArray(entryCandles)) return map;
  for (const item of entryCandles) {
    const time = normalizeChartCandleTime(item.time ?? item.signal_candle_time);
    if (time == null) continue;
    const side = item.side || (item.action === 'SELL' ? 'SHORT' : 'LONG');
    map.set(time, { side, pattern: item.pattern });
  }
  return map;
}

export function decorateCandlestickBar(bar, highlightMap) {
  const base = {
    time: bar.time,
    open: bar.open,
    high: bar.high,
    low: bar.low,
    close: bar.close,
  };
  const hit = highlightMap?.get(bar.time);
  if (!hit) return base;
  const neon = hit.side === 'SHORT' || hit.side === 'SELL' ? NEON_SHORT : NEON_LONG;
  return { ...base, ...neon };
}

export function decorateCandlestickSeries(data, entryCandles) {
  const map = buildHighlightMap(entryCandles);
  if (map.size === 0) return data;
  return data.map((bar) => decorateCandlestickBar(bar, map));
}

export function computeTradeFireMarkers(entryCandles) {
  if (!Array.isArray(entryCandles) || entryCandles.length === 0) return [];
  return entryCandles
    .map((item) => {
      const time = normalizeChartCandleTime(item.time ?? item.signal_candle_time);
      if (time == null) return null;
      const side = item.side || (item.action === 'SELL' ? 'SHORT' : 'LONG');
      const isShort = side === 'SHORT' || side === 'SELL';
      return {
        time,
        position: isShort ? 'aboveBar' : 'belowBar',
        color: isShort ? '#ff10f0' : '#39ff14',
        shape: 'circle',
        text: '⚡',
      };
    })
    .filter(Boolean)
    .sort((a, b) => a.time - b.time);
}
