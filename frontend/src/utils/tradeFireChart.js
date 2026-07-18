import { normalizeChartCandleTime, snapToChartInterval } from './chartCandles';
import { formatTradeFireTime } from './time';

const NEON = {
  LONG: {
    border: '#39ff14',
    glow: 'rgba(57, 255, 20, 0.75)',
    bg: 'rgba(57, 255, 20, 0.1)',
    badge: '#7fff00',
  },
  SHORT: {
    border: '#ff10f0',
    glow: 'rgba(255, 16, 240, 0.75)',
    bg: 'rgba(255, 16, 240, 0.1)',
    badge: '#ff6bff',
  },
};

function resolveBarTime(rawTime, candleData, intervalSeconds) {
  const normalized = normalizeChartCandleTime(rawTime);
  if (normalized == null) return null;

  const exact = candleData?.find((b) => b.time === normalized);
  if (exact) return { time: normalized, bar: exact };

  const snapped = snapToChartInterval(normalized, intervalSeconds);
  const snappedBar = candleData?.find((b) => b.time === snapped);
  if (snappedBar) return { time: snapped, bar: snappedBar };

  return null;
}

/** Trade-fire candles keyed by chart bar time (for crosshair + overlay). */
export function buildTradeFireLookup(entryCandles, candleData, intervalSeconds) {
  const map = new Map();
  if (!Array.isArray(entryCandles)) return map;

  for (const item of entryCandles) {
    const rawTime = item.time ?? item.signal_candle_time;
    const resolved = resolveBarTime(rawTime, candleData, intervalSeconds);
    if (!resolved) continue;

    const side = item.side || (item.action === 'SELL' ? 'SHORT' : 'LONG');
    const entry = {
      time: resolved.time,
      bar: resolved.bar,
      side,
      pattern: item.pattern || item.taapi_action || 'Trade fire',
      opened_at: item.opened_at ?? item.trade_time ?? null,
      signal_candle_time: normalizeChartCandleTime(rawTime),
    };
    map.set(resolved.time, entry);
  }
  return map;
}

export function clearTradeFireOverlay(overlayEl) {
  if (overlayEl) overlayEl.innerHTML = '';
}

function formatPatternLabel(pattern) {
  if (!pattern) return 'Trade fire';
  return String(pattern).replace(/_/g, ' ').toUpperCase();
}

function appendTooltip(overlayEl, left, top, entry, isShort) {
  const tip = document.createElement('div');
  tip.className = `trade-fire-tooltip ${isShort ? 'trade-fire-tooltip--short' : 'trade-fire-tooltip--long'}`;
  tip.style.cssText = [
    'position:absolute',
    `left:${left}px`,
    `top:${Math.max(4, top - 52)}px`,
    'transform:translateX(-50%)',
    'pointer-events:none',
    'z-index:30',
  ].join(';');

  const patternEl = document.createElement('div');
  patternEl.className = 'trade-fire-tooltip__pattern';
  patternEl.textContent = `⚡ ${formatPatternLabel(entry.pattern)}`;

  const timeEl = document.createElement('div');
  timeEl.className = 'trade-fire-tooltip__time';
  timeEl.textContent = formatTradeFireTime(entry.opened_at || entry.signal_candle_time);

  tip.appendChild(patternEl);
  tip.appendChild(timeEl);
  overlayEl.appendChild(tip);
  return tip;
}

/**
 * Neon glow frames on exact pattern-detected candles (candle body colors stay natural).
 */
export function renderTradeFireOverlay({
  chart,
  series,
  overlayEl,
  lookup,
  intervalSecs,
  hoveredTime = null,
}) {
  clearTradeFireOverlay(overlayEl);
  if (!chart || !series || !overlayEl || !lookup?.size) return;

  for (const [time, entry] of lookup) {
    const { bar, side } = entry;
    if (!bar) continue;

    const xCenter = chart.timeScale().timeToCoordinate(time);
    const nextX = chart.timeScale().timeToCoordinate(time + intervalSecs);
    const yHigh = series.priceToCoordinate(bar.high);
    const yLow = series.priceToCoordinate(bar.low);
    if (xCenter == null || yHigh == null || yLow == null) continue;

    const barSpan = nextX != null ? Math.abs(nextX - xCenter) : 10;
    const width = Math.max(8, barSpan * 0.78);
    const left = xCenter - width / 2;
    const top = Math.min(yHigh, yLow);
    const height = Math.max(Math.abs(yLow - yHigh), 6);

    const isShort = side === 'SHORT' || side === 'SELL';
    const neon = isShort ? NEON.SHORT : NEON.LONG;
    const isHovered = hoveredTime === time;

    const wrap = document.createElement('div');
    wrap.className = [
      'trade-fire-neon',
      isShort ? 'trade-fire-neon--short' : 'trade-fire-neon--long',
      isHovered ? 'trade-fire-neon--hover' : '',
    ].join(' ');
    wrap.style.cssText = [
      'position:absolute',
      `left:${left}px`,
      `top:${top}px`,
      `width:${width}px`,
      `height:${height}px`,
      'pointer-events:none',
    ].join(';');
    wrap.dataset.time = String(time);

    const glow = document.createElement('div');
    glow.className = 'trade-fire-neon__glow';
    glow.style.cssText = [
      'position:absolute',
      'inset:0',
      'border-radius:3px',
      `border:2px solid ${neon.border}`,
      `background:${neon.bg}`,
      'pointer-events:none',
    ].join(';');
    wrap.appendChild(glow);

    const badge = document.createElement('span');
    badge.className = 'trade-fire-neon__badge';
    badge.style.color = neon.badge;
    badge.textContent = '⚡';
    wrap.appendChild(badge);

    overlayEl.appendChild(wrap);

    if (isHovered) {
      appendTooltip(overlayEl, xCenter, top, entry, isShort);
    }
  }
}

export function tradeFireTooltipFromLookup(lookup, chartTime) {
  if (!lookup || chartTime == null) return null;
  const hit = lookup.get(chartTime);
  if (!hit) return null;
  return {
    pattern: formatPatternLabel(hit.pattern),
    opened_at: hit.opened_at,
    signal_candle_time: hit.signal_candle_time,
    side: hit.side,
    time: hit.time,
  };
}
