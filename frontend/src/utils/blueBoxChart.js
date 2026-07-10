import { LineStyle } from 'lightweight-charts';

function normalizeTime(raw) {
  if (raw == null) return null;
  if (raw > 1_000_000_000_000) return Math.floor(raw / 1000);
  return raw;
}

function appendBox(overlayEl, left, top, width, height, { border, bg, label }) {
  if (width < 2 || height < 2) return;
  const box = document.createElement('div');
  box.className = 'blue-box-trap';
  box.style.cssText = [
    'position:absolute',
    `left:${left}px`,
    `top:${top}px`,
    `width:${width}px`,
    `height:${height}px`,
    `border:2px solid ${border}`,
    `background:${bg}`,
    'border-radius:4px',
    'box-sizing:border-box',
    'pointer-events:none',
  ].join(';');

  const tag = document.createElement('span');
  tag.textContent = label;
  tag.style.cssText = [
    'position:absolute',
    'top:2px',
    'left:4px',
    'font-size:9px',
    'font-weight:800',
    'letter-spacing:0.06em',
    'text-transform:uppercase',
    `color:${border}`,
    'opacity:0.95',
    'white-space:nowrap',
  ].join(';');
  box.appendChild(tag);
  overlayEl.appendChild(box);
}

export function clearBlueBoxChartGraphics({ series, overlayEl, lineRefs }) {
  if (overlayEl) overlayEl.innerHTML = '';
  if (!series || !lineRefs?.current) return;
  for (const line of lineRefs.current) {
    try {
      series.removePriceLine(line);
    } catch {
      /* already removed */
    }
  }
  lineRefs.current = [];
}

export function renderBlueBoxChartOverlay({
  chart,
  series,
  overlayEl,
  overlay,
  botIsActive,
  intervalSecs,
  lineRefs,
}) {
  if (!chart || !series || !overlayEl || !lineRefs) return;

  clearBlueBoxChartGraphics({ series, overlayEl, lineRefs });

  if (!botIsActive || !overlay?.active) return;

  const lowest = overlay.lowest_20;
  const highest = overlay.highest_20;

  if (typeof lowest === 'number' && lowest > 0) {
    lineRefs.current.push(
      series.createPriceLine({
        price: lowest,
        color: 'rgba(34, 211, 238, 0.9)',
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: 'L20',
      }),
    );
  }

  if (typeof highest === 'number' && highest > 0) {
    lineRefs.current.push(
      series.createPriceLine({
        price: highest,
        color: 'rgba(244, 114, 182, 0.9)',
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: 'H20',
      }),
    );
  }

  const ema50 = overlay.ema50;
  const ema200 = overlay.ema200;
  if (typeof ema50 === 'number' && ema50 > 0) {
    lineRefs.current.push(
      series.createPriceLine({
        price: ema50,
        color: 'rgba(250, 204, 21, 0.85)',
        lineWidth: 1,
        lineStyle: LineStyle.Solid,
        axisLabelVisible: true,
        title: 'EMA50',
      }),
    );
  }
  if (typeof ema200 === 'number' && ema200 > 0) {
    lineRefs.current.push(
      series.createPriceLine({
        price: ema200,
        color: 'rgba(168, 85, 247, 0.85)',
        lineWidth: 1,
        lineStyle: LineStyle.Solid,
        axisLabelVisible: true,
        title: 'EMA200',
      }),
    );
  }

  const drawTrap = (trap, kind) => {
    if (!trap?.active) return;
    const sweepTime = normalizeTime(trap.sweep_time);
    if (sweepTime == null) return;

    const windowBars = trap.bars_window || 3;
    const timeEnd = sweepTime + (windowBars - 1) * intervalSecs;

    let priceTop;
    let priceBottom;
    let border;
    let bg;
    let label;

    if (kind === 'bull') {
      priceTop = trap.zone_top ?? trap.sweep_low;
      priceBottom = trap.sweep_low;
      border = '#22d3ee';
      bg = 'rgba(34, 211, 238, 0.12)';
      label = 'Blue Box · Bull trap';
    } else {
      priceTop = trap.sweep_high;
      priceBottom = trap.zone_bottom ?? trap.sweep_high;
      border = '#f472b6';
      bg = 'rgba(244, 114, 182, 0.12)';
      label = 'Blue Box · Bear trap';
    }

    if (priceTop == null || priceBottom == null) return;

    const x1 = chart.timeScale().timeToCoordinate(sweepTime);
    const x2 = chart.timeScale().timeToCoordinate(timeEnd);
    const y1 = series.priceToCoordinate(priceTop);
    const y2 = series.priceToCoordinate(priceBottom);
    if (x1 == null || x2 == null || y1 == null || y2 == null) return;

    appendBox(
      overlayEl,
      Math.min(x1, x2),
      Math.min(y1, y2),
      Math.abs(x2 - x1) || 8,
      Math.abs(y2 - y1) || 8,
      { border, bg, label },
    );
  };

  drawTrap(overlay.bullish_trap, 'bull');
  drawTrap(overlay.bearish_trap, 'bear');
}

export function blueBoxStatusLabel(overlay, botIsActive) {
  if (!botIsActive || !overlay?.active) return null;
  if (overlay.bullish_trap?.active) return 'Blue Box · Bull trap armed';
  if (overlay.bearish_trap?.active) return 'Blue Box · Bear trap armed';
  if (overlay.status === 'marubozu_watch' || overlay.is_marubozu) {
    const t = overlay.trend ? ` (${overlay.trend})` : '';
    return `Marubozu watch${t}`;
  }
  if (overlay.last_action === 'BUY' || overlay.last_action === 'SELL') {
    return `Blue Box · ${overlay.last_pattern || 'signal'}`;
  }
  return 'Blue Box engine ON';
}
