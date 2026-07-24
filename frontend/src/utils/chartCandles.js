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

/**
 * Sort, snap, dedupe — and fill missing interval slots so 1m charts don't
 * stretch into fat blocky candles when live ticks skip minutes.
 */
export function sanitizeCandleData(data, intervalSeconds) {
  if (!Array.isArray(data) || data.length === 0) return [];

  const byTime = new Map();
  for (const bar of data) {
    const raw = normalizeChartCandleTime(bar?.time);
    if (raw == null) continue;
    const time = snapToChartInterval(raw, intervalSeconds || 60);
    const open = Number(bar.open);
    const high = Number(bar.high);
    const low = Number(bar.low);
    const close = Number(bar.close);
    if (![open, high, low, close].every(Number.isFinite)) continue;
    const volume = Number(bar.volume);
    const next = {
      time,
      open,
      high: Math.max(high, open, close),
      low: Math.min(low, open, close),
      close,
      volume: Number.isFinite(volume) ? volume : 0,
    };
    const prev = byTime.get(time);
    if (!prev) {
      byTime.set(time, next);
    } else {
      // Prefer the later update for same bucket (live overwrite).
      byTime.set(time, {
        time,
        open: prev.open,
        high: Math.max(prev.high, next.high),
        low: Math.min(prev.low, next.low),
        close: next.close,
        volume: Math.max(prev.volume, next.volume),
      });
    }
  }

  const sorted = Array.from(byTime.values()).sort((a, b) => a.time - b.time);
  if (!intervalSeconds || intervalSeconds <= 0 || sorted.length < 2) return sorted;

  const filled = [sorted[0]];
  for (let i = 1; i < sorted.length; i++) {
    let cursor = filled[filled.length - 1].time + intervalSeconds;
    const target = sorted[i];
    // Cap gap-fill so a huge hole doesn't explode the series.
    let guard = 0;
    while (cursor < target.time && guard < 500) {
      const bridgeClose = filled[filled.length - 1].close;
      filled.push({
        time: cursor,
        open: bridgeClose,
        high: bridgeClose,
        low: bridgeClose,
        close: bridgeClose,
        volume: 0,
      });
      cursor += intervalSeconds;
      guard += 1;
    }
    filled.push(target);
  }
  return filled;
}

/** Candles keep their natural green/red colors — trade fire uses neon overlay instead. */
export function decorateCandlestickSeries(data) {
  return data.map(({ time, open, high, low, close }) => ({ time, open, high, low, close }));
}

export function computeTradeFireMarkers() {
  return [];
}
