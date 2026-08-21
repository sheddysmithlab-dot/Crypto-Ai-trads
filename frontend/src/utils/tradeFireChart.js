import { normalizeChartCandleTime, snapToChartInterval } from './chartCandles';
import { formatTradeFireTime } from './time';

/** Three pipeline stages + fire side colors. */
const NEON = {
  detected: {
    border: '#00e5ff',
    glow: 'rgba(0, 229, 255, 0.75)',
    bg: 'rgba(0, 229, 255, 0.12)',
    badge: '#67f0ff',
    className: 'trade-fire-neon--detected',
    tipClass: 'trade-fire-tooltip--detected',
    glyph: '◉',
    label: 'DETECTED',
  },
  confirming: {
    border: '#ffb020',
    glow: 'rgba(255, 176, 32, 0.75)',
    bg: 'rgba(255, 176, 32, 0.12)',
    badge: '#ffd060',
    className: 'trade-fire-neon--confirming',
    tipClass: 'trade-fire-tooltip--confirming',
    glyph: '◐',
    label: 'CONFIRMING',
  },
  fired_LONG: {
    border: '#39ff14',
    glow: 'rgba(57, 255, 20, 0.75)',
    bg: 'rgba(57, 255, 20, 0.1)',
    badge: '#7fff00',
    className: 'trade-fire-neon--long',
    tipClass: 'trade-fire-tooltip--long',
    glyph: '⚡',
    label: 'FIRED',
  },
  fired_SHORT: {
    border: '#ff10f0',
    glow: 'rgba(255, 16, 240, 0.75)',
    bg: 'rgba(255, 16, 240, 0.1)',
    badge: '#ff6bff',
    className: 'trade-fire-neon--short',
    tipClass: 'trade-fire-tooltip--short',
    glyph: '⚡',
    label: 'FIRED',
  },
  skipped: {
    border: '#ff4d4d',
    glow: 'rgba(255, 77, 77, 0.7)',
    bg: 'rgba(255, 77, 77, 0.1)',
    badge: '#ff8a8a',
    className: 'trade-fire-neon--skipped',
    tipClass: 'trade-fire-tooltip--skipped',
    glyph: '✕',
    label: 'SKIPPED',
  },
};

const STAGE_RANK = {
  detected: 1,
  confirming: 2,
  skipped: 3,
  fired: 4,
};

function neonForEntry(entry) {
  const stage = entry.stage || 'fired';
  if (stage === 'detected') return NEON.detected;
  if (stage === 'confirming') return NEON.confirming;
  if (stage === 'skipped') return NEON.skipped;
  const isShort = entry.side === 'SHORT' || entry.side === 'SELL';
  return isShort ? NEON.fired_SHORT : NEON.fired_LONG;
}

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

/** Keep only events that belong to the chart's active pair. */
export function filterEntryCandlesForPair(entryCandles, pairLabel) {
  if (!Array.isArray(entryCandles)) return [];
  const want = (pairLabel || '').trim().toUpperCase();
  if (!want) return [];
  return entryCandles.filter((item) => {
    const p = (item?.pair || '').trim().toUpperCase();
    // Legacy rows without pair: hide (avoids BTC fires painting SOL charts).
    if (!p) return false;
    return p === want;
  });
}

function upsertLookupEntry(map, entry) {
  const existing = map.get(entry.time);
  const nextRank = STAGE_RANK[entry.stage] || 0;
  const prevRank = existing ? STAGE_RANK[existing.stage] || 0 : 0;
  if (!existing || nextRank >= prevRank) {
    map.set(entry.time, entry);
  }
}

/**
 * Trade-fire / pipeline neon keyed by chart bar time.
 * Prefer pattern_neon stages (detected → confirming → fired/skipped);
 * fall back to entry_candles as fired for older history.
 */
export function buildTradeFireLookup(
  entryCandles,
  candleData,
  intervalSeconds,
  pairLabel = null,
  patternNeon = [],
) {
  const map = new Map();
  const scopedNeon = pairLabel
    ? filterEntryCandlesForPair(patternNeon, pairLabel)
    : Array.isArray(patternNeon)
      ? patternNeon
      : [];
  const scopedEntry = pairLabel
    ? filterEntryCandlesForPair(entryCandles, pairLabel)
    : entryCandles;

  for (const item of scopedNeon) {
    const rawTime = item.time ?? item.signal_candle_time;
    const resolved = resolveBarTime(rawTime, candleData, intervalSeconds);
    if (!resolved) continue;

    const side = item.side || (item.action === 'SELL' ? 'SHORT' : 'LONG');
    const stage = item.stage || 'fired';
    upsertLookupEntry(map, {
      time: resolved.time,
      bar: resolved.bar,
      side,
      stage,
      pair: item.pair || null,
      pattern: item.pattern || item.taapi_action || 'Pattern',
      reason: item.reason || null,
      opened_at: item.opened_at ?? item.trade_time ?? null,
      signal_candle_time: normalizeChartCandleTime(rawTime),
    });
  }

  if (!Array.isArray(scopedEntry)) return map;

  for (const item of scopedEntry) {
    const rawTime = item.time ?? item.signal_candle_time;
    const resolved = resolveBarTime(rawTime, candleData, intervalSeconds);
    if (!resolved) continue;
    // Don't overwrite pipeline stages (esp. detected on signal bar).
    if (map.has(resolved.time)) continue;

    const side = item.side || (item.action === 'SELL' ? 'SHORT' : 'LONG');
    map.set(resolved.time, {
      time: resolved.time,
      bar: resolved.bar,
      side,
      stage: 'fired',
      pair: item.pair || null,
      pattern: item.pattern || item.taapi_action || 'Trade fire',
      reason: null,
      opened_at: item.opened_at ?? item.trade_time ?? null,
      signal_candle_time: normalizeChartCandleTime(rawTime),
    });
  }
  return map;
}

export function clearTradeFireOverlay(overlayEl) {
  if (overlayEl) overlayEl.innerHTML = '';
}

function formatPatternLabel(pattern) {
  if (!pattern) return 'Pattern';
  return String(pattern).replace(/_/g, ' ').toUpperCase();
}

function appendTooltip(overlayEl, left, top, entry, neon) {
  const tip = document.createElement('div');
  tip.className = `trade-fire-tooltip ${neon.tipClass}`;
  tip.style.cssText = [
    'position:absolute',
    `left:${left}px`,
    `top:${Math.max(4, top - 58)}px`,
    'transform:translateX(-50%)',
    'pointer-events:none',
    'z-index:30',
  ].join(';');

  const stageEl = document.createElement('div');
  stageEl.className = 'trade-fire-tooltip__stage';
  stageEl.textContent = `${neon.glyph} ${neon.label}`;

  const patternEl = document.createElement('div');
  patternEl.className = 'trade-fire-tooltip__pattern';
  patternEl.textContent = formatPatternLabel(entry.pattern);

  const timeEl = document.createElement('div');
  timeEl.className = 'trade-fire-tooltip__time';
  timeEl.textContent = formatTradeFireTime(entry.opened_at || entry.signal_candle_time);

  tip.appendChild(stageEl);
  tip.appendChild(patternEl);
  tip.appendChild(timeEl);
  if (entry.reason && entry.stage === 'skipped') {
    const reasonEl = document.createElement('div');
    reasonEl.className = 'trade-fire-tooltip__time';
    reasonEl.textContent = String(entry.reason).slice(0, 48);
    tip.appendChild(reasonEl);
  }
  overlayEl.appendChild(tip);
  return tip;
}

/**
 * Neon glow frames: cyan detected → amber confirming → lime/magenta fired or red skipped.
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
    const { bar } = entry;
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

    const neon = neonForEntry(entry);
    const isHovered = hoveredTime === time;

    const wrap = document.createElement('div');
    wrap.className = [
      'trade-fire-neon',
      neon.className,
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
    wrap.dataset.stage = entry.stage || 'fired';

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
    badge.textContent = neon.glyph;
    wrap.appendChild(badge);

    overlayEl.appendChild(wrap);

    // Popup toast only while the cursor is on this candle.
    if (isHovered) {
      appendTooltip(overlayEl, xCenter, top, entry, neon);
    }
  }
}

export function tradeFireTooltipFromLookup(lookup, chartTime) {
  if (!lookup || chartTime == null) return null;
  const hit = lookup.get(chartTime);
  if (!hit) return null;
  const neon = neonForEntry(hit);
  return {
    pattern: formatPatternLabel(hit.pattern),
    stage: hit.stage || 'fired',
    stageLabel: neon.label,
    opened_at: hit.opened_at,
    signal_candle_time: hit.signal_candle_time,
    side: hit.side,
    time: hit.time,
    reason: hit.reason || null,
  };
}

/** Newest pipeline stage for the chart header toast strip. */
export function latestTradeFireToast(lookup) {
  if (!lookup?.size) return null;
  let best = null;
  let bestRank = -1;
  let bestTime = -1;
  for (const [, entry] of lookup) {
    const stage = entry.stage || 'fired';
    const rank = STAGE_RANK[stage] || 0;
    const t = Number(entry.time) || 0;
    // Prefer the newest candle; on the same bar prefer higher stage.
    if (t > bestTime || (t === bestTime && rank >= bestRank)) {
      bestRank = rank;
      bestTime = t;
      best = entry;
    }
  }
  if (!best) return null;
  const neon = neonForEntry(best);
  return {
    pattern: formatPatternLabel(best.pattern),
    stage: best.stage || 'fired',
    stageLabel: neon.label,
    opened_at: best.opened_at,
    signal_candle_time: best.signal_candle_time,
    side: best.side,
    time: best.time,
    reason: best.reason || null,
  };
}
