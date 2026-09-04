import { useCallback, useEffect, useRef, useState } from 'react';
import { createChart, CrosshairMode, LineStyle } from 'lightweight-charts';
import { authFetch, backendWsUrl } from '../config/api';
import {
  BYBIT_PUBLIC_WS_LINEAR,
  bybitKlineUrl,
  bybitPublicKlineTopic,
  bybitPublicTradeTopic,
  bybitRecentTradeUrl,
  fetchBybitDayKlines,
  barsForOneDay,
} from '../config/bybitPublic';
import { debugLog } from '../config/debug';
import { fmtNum, getBybitSymbol } from '../data/pairs';
import { formatChartAxisTime, formatLiveClock } from '../utils/time';
import {
  decorateCandlestickSeries,
  computeTradeFireMarkers,
  sanitizeCandleData,
  normalizeChartCandleTime,
  snapToChartInterval,
} from '../utils/chartCandles';
import {
  buildTradeFireLookup,
  renderTradeFireOverlay,
  clearTradeFireOverlay,
  tradeFireTooltipFromLookup,
} from '../utils/tradeFireChart';
import {
  renderBlueBoxChartOverlay,
  clearBlueBoxChartGraphics,
  blueBoxStatusLabel,
} from '../utils/blueBoxChart';

// Timeframe -> candle interval in seconds. Drives BOTH historical bucketing
// and live WebSocket tick bucketing so the chart genuinely reacts to the
// selected timeframe (not just a cosmetic label change).
const TIMEFRAME_SECONDS = { '1M': 60, '5M': 300, '15M': 900, '1H': 3600, '1D': 86400 };
const SECONDS_TO_TIMEFRAME = { 60: '1M', 300: '5M', 900: '15M', 3600: '1H', 86400: '1D' };
const CHART_TIMEFRAME_STORAGE_KEY = 'ai_trading_bot_chart_timeframe';

function readStoredTimeframe() {
  try {
    const saved = localStorage.getItem(CHART_TIMEFRAME_STORAGE_KEY);
    if (saved && TIMEFRAME_SECONDS[saved]) return saved;
  } catch {
    /* private browsing / storage blocked */
  }
  return '1M';
}

function timeframeFromSeconds(seconds) {
  const s = Number(seconds);
  if (Number.isFinite(s) && SECONDS_TO_TIMEFRAME[s]) return SECONDS_TO_TIMEFRAME[s];
  return null;
}

// Standard kline granularities on each exchange (1M and above).
const BYBIT_KLINE_INTERVAL = { '1M': '1', '5M': '5', '15M': '15', '1H': '60', '1D': 'D' };

const MA_PERIODS = [5, 10, 20, 30];
const MA_COLORS = { 5: '#facc15', 10: '#ec4899', 20: '#38bdf8', 30: '#a855f7' };
const VOLUME_MA_PERIOD = 20;
// Default zoom: only the most recent candles are visible, on both the main
// chart and the volume panel (they're time-synced), instead of the whole
// fetched history all at once.
const DEFAULT_VISIBLE_CANDLES = 40;

function generateMockData(basePrice, intervalSeconds) {
  const data = [];
  let time = Math.floor(Date.now() / 1000 / intervalSeconds) * intervalSeconds - 100 * intervalSeconds;
  let price = basePrice - basePrice * 0.005;

  for (let i = 0; i < 100; i++) {
    const volatility = basePrice * (Math.random() * 0.0013 - 0.0006);
    const open = price;
    const close = i === 99 ? basePrice : price + volatility;
    const high = Math.max(open, close) + Math.abs(volatility) * 0.5;
    const low = Math.min(open, close) - Math.abs(volatility) * 0.5;
    // Volume roughly tracks candle range (bigger moves -> bigger bars) plus noise.
    const volume = ((high - low) / basePrice) * 500 + Math.random() * 3;

    data.push({ time: time + i * intervalSeconds, open, high, low, close, volume });
    price = close;
  }
  return data;
}

async function fetchBybitHistory(bybitSymbol, klineInterval, limit = 200) {
  const url = bybitKlineUrl(bybitSymbol, klineInterval, limit);
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const json = await res.json();
  const raw = json?.result?.list;
  if (!Array.isArray(raw) || raw.length === 0) throw new Error('Empty klines response');
  return raw
    .map((k) => ({
      time: Math.floor(parseInt(k[0], 10) / 1000),
      open: parseFloat(k[1]),
      high: parseFloat(k[2]),
      low: parseFloat(k[3]),
      close: parseFloat(k[4]),
      volume: parseFloat(k[5]),
    }))
    .reverse();
}

function mapBybitKlineWsBar(row) {
  const time = Math.floor(Number(row.start) / 1000);
  return {
    time,
    open: parseFloat(row.open),
    high: parseFloat(row.high),
    low: parseFloat(row.low),
    close: parseFloat(row.close),
    volume: parseFloat(row.volume || 0),
    confirm: row.confirm === true,
  };
}

async function fetchBybitRecentTradesAsCandles(bybitSymbol, intervalSeconds, limit = 1000) {
  const url = bybitRecentTradeUrl(bybitSymbol, limit);
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const json = await res.json();
  const trades = json?.result?.list;
  if (!Array.isArray(trades) || trades.length === 0) throw new Error('Empty trades response');

  const buckets = new Map();
  for (const t of trades) {
    const price = parseFloat(t.price);
    const qty = parseFloat(t.size);
    const bucketTime = Math.floor(parseInt(t.time, 10) / 1000 / intervalSeconds) * intervalSeconds;
    let bucket = buckets.get(bucketTime);
    if (!bucket) {
      bucket = { time: bucketTime, open: price, high: price, low: price, close: price, volume: 0 };
      buckets.set(bucketTime, bucket);
    }
    bucket.high = Math.max(bucket.high, price);
    bucket.low = Math.min(bucket.low, price);
    bucket.close = price;
    bucket.volume += qty;
  }
  const candles = Array.from(buckets.values()).sort((a, b) => a.time - b.time);
  if (candles.length === 0) throw new Error('No candles bucketed from trades');
  return candles;
}

async function fetchBackend24hCandles(pairLabelArg) {
  const res = await authFetch(`/chart/24h?pair=${encodeURIComponent(pairLabelArg)}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const json = await res.json();
  if (!Array.isArray(json.candles) || json.candles.length === 0) {
    throw new Error('Empty backend 24h candles');
  }
  return json.candles;
}

async function loadHistoricalData(pairLabelArg, tfKey, basePrice) {
  const bybitSymbol = getBybitSymbol(pairLabelArg);
  const intervalSecs = TIMEFRAME_SECONDS[tfKey] || 3600;
  const bybitKline = BYBIT_KLINE_INTERVAL[tfKey];

  if (!bybitSymbol) {
    return { data: generateMockData(basePrice, intervalSecs), source: 'mock (no Bybit mapping)' };
  }

  try {
    // Prefer a full ~1 day backbone on every reload (paginated for 1m ≈ 1440 bars).
    if (bybitKline) {
      try {
        const dayNeed = barsForOneDay(intervalSecs, tfKey);
        const raw = await fetchBybitDayKlines(bybitSymbol, bybitKline, intervalSecs, tfKey);
        const data = sanitizeCandleData(raw, intervalSecs);
        return {
          data,
          source: `Bybit linear ~1d (${data.length}/${dayNeed} bars, interval=${bybitKline})`,
        };
      } catch (dayErr) {
        console.warn(`[CHART] Day fetch failed for ${pairLabelArg}, falling back:`, dayErr);
      }
    }
    if (tfKey === '5M') {
      try {
        const data = sanitizeCandleData(await fetchBackend24hCandles(pairLabelArg), intervalSecs);
        return { data, source: 'backend /chart/24h (Bybit 5m persisted)' };
      } catch (backendErr) {
        console.warn(`[CHART] Backend 24h snapshot unavailable for ${pairLabelArg}:`, backendErr);
      }
    }
    const data = bybitKline
      ? sanitizeCandleData(await fetchBybitHistory(bybitSymbol, bybitKline, 1000), intervalSecs)
      : sanitizeCandleData(await fetchBybitRecentTradesAsCandles(bybitSymbol, intervalSecs), intervalSecs);
    return {
      data,
      source: bybitKline ? `Bybit linear klines (${bybitKline})` : 'Bybit linear trades (bucketed)',
    };
  } catch (bybitErr) {
    console.warn(`[CHART] Bybit history failed for ${pairLabelArg} (${tfKey}):`, bybitErr);
  }
  return { data: generateMockData(basePrice, intervalSecs), source: 'mock fallback' };
}

function calcSMA(data, period) {
  const result = [];
  let sum = 0;
  for (let i = 0; i < data.length; i++) {
    sum += data[i].close;
    if (i >= period) sum -= data[i - period].close;
    if (i >= period - 1) result.push({ time: data[i].time, value: sum / period });
  }
  return result;
}

function calcVolumeSMA(data, period) {
  const result = [];
  let sum = 0;
  for (let i = 0; i < data.length; i++) {
    sum += data[i].volume;
    if (i >= period) sum -= data[i - period].volume;
    if (i >= period - 1) result.push({ time: data[i].time, value: sum / period });
  }
  return result;
}

function calcEMAValues(closes, period) {
  const out = new Array(closes.length).fill(null);
  if (closes.length < period) return out;
  let sum = 0;
  for (let i = 0; i < period; i++) sum += closes[i];
  let prev = sum / period;
  out[period - 1] = prev;
  const k = 2 / (period + 1);
  for (let i = period; i < closes.length; i++) {
    prev = prev + k * (closes[i] - prev);
    out[i] = prev;
  }
  return out;
}

function calcBollinger(data, period = 20, mult = 2) {
  const mid = [];
  const upper = [];
  const lower = [];
  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) continue;
    let sum = 0;
    for (let j = i - period + 1; j <= i; j++) sum += data[j].close;
    const m = sum / period;
    let varSum = 0;
    for (let j = i - period + 1; j <= i; j++) varSum += (data[j].close - m) ** 2;
    const sd = Math.sqrt(varSum / period);
    const t = data[i].time;
    mid.push({ time: t, value: m });
    upper.push({ time: t, value: m + mult * sd });
    lower.push({ time: t, value: m - mult * sd });
  }
  return { mid, upper, lower };
}

function calcVWAP(data) {
  const result = [];
  let cumPv = 0;
  let cumV = 0;
  let lastDay = null;
  for (let i = 0; i < data.length; i++) {
    const d = data[i];
    const day = Math.floor(d.time / 86400);
    if (lastDay != null && day !== lastDay) {
      cumPv = 0;
      cumV = 0;
    }
    lastDay = day;
    const typical = (d.high + d.low + d.close) / 3;
    const vol = d.volume > 0 ? d.volume : 1;
    cumPv += typical * vol;
    cumV += vol;
    result.push({ time: d.time, value: cumPv / cumV });
  }
  return result;
}

function calcRSI(data, period = 14) {
  const result = [];
  if (data.length < period + 1) return result;
  let gains = 0;
  let losses = 0;
  for (let i = 1; i <= period; i++) {
    const diff = data[i].close - data[i - 1].close;
    gains += Math.max(diff, 0);
    losses += Math.max(-diff, 0);
  }
  let avgG = gains / period;
  let avgL = losses / period;
  const rsiAt = (ag, al) => (al === 0 ? 100 : 100 - 100 / (1 + ag / al));
  result.push({ time: data[period].time, value: rsiAt(avgG, avgL) });
  for (let i = period + 1; i < data.length; i++) {
    const diff = data[i].close - data[i - 1].close;
    avgG = (avgG * (period - 1) + Math.max(diff, 0)) / period;
    avgL = (avgL * (period - 1) + Math.max(-diff, 0)) / period;
    result.push({ time: data[i].time, value: rsiAt(avgG, avgL) });
  }
  return result;
}

function calcMACD(data, fast = 12, slow = 26, signal = 9) {
  const closes = data.map((d) => d.close);
  const emaFast = calcEMAValues(closes, fast);
  const emaSlow = calcEMAValues(closes, slow);
  const macdVals = closes.map((_, i) =>
    emaFast[i] != null && emaSlow[i] != null ? emaFast[i] - emaSlow[i] : null
  );
  const macdLine = [];
  const signalLine = [];
  const hist = [];
  const firstMacd = macdVals.findIndex((v) => v != null);
  if (firstMacd < 0) return { macdLine, signalLine, hist };
  const seedEnd = firstMacd + signal - 1;
  let sigPrev = null;
  if (seedEnd < macdVals.length) {
    let s = 0;
    let ok = true;
    for (let i = firstMacd; i <= seedEnd; i++) {
      if (macdVals[i] == null) {
        ok = false;
        break;
      }
      s += macdVals[i];
    }
    if (ok) sigPrev = s / signal;
  }
  const k = 2 / (signal + 1);
  for (let i = 0; i < data.length; i++) {
    if (macdVals[i] == null) continue;
    let sig = null;
    if (i === seedEnd && sigPrev != null) {
      sig = sigPrev;
    } else if (i > seedEnd && sigPrev != null) {
      sigPrev = sigPrev + k * (macdVals[i] - sigPrev);
      sig = sigPrev;
    }
    macdLine.push({ time: data[i].time, value: macdVals[i] });
    if (sig != null) {
      signalLine.push({ time: data[i].time, value: sig });
      hist.push({
        time: data[i].time,
        value: macdVals[i] - sig,
        color: macdVals[i] - sig >= 0 ? 'rgba(34,197,94,0.55)' : 'rgba(239,68,68,0.55)',
      });
    }
  }
  return { macdLine, signalLine, hist };
}

function toVolumeBars(data) {
  return data.map((d) => ({
    time: d.time,
    value: d.volume,
    color: d.close >= d.open ? 'rgba(34,197,94,0.55)' : 'rgba(239,68,68,0.55)',
  }));
}

// Marks the highest-high and lowest-low bar in the dataset with their price,
// same as the swing-point labels on a real exchange chart.
function computeExtremeMarkers(data) {
  if (!data.length) return [];
  let highBar = data[0];
  let lowBar = data[0];
  for (const bar of data) {
    if (bar.high > highBar.high) highBar = bar;
    if (bar.low < lowBar.low) lowBar = bar;
  }
  const markers = [
    { time: highBar.time, position: 'aboveBar', color: '#eab308', shape: 'circle', text: fmtNum(highBar.high) },
    { time: lowBar.time, position: 'belowBar', color: '#eab308', shape: 'circle', text: fmtNum(lowBar.low) },
  ];
  return markers.sort((a, b) => a.time - b.time);
}

const darkThemeConfig = {
  layout: { background: { type: 'solid', color: '#161A1E' }, textColor: '#9ca3af' },
  grid: { vertLines: { color: '#1E2329' }, horzLines: { color: '#1E2329' } },
  crosshair: { mode: CrosshairMode.Normal },
  rightPriceScale: { borderColor: '#1E2329' },
  timeScale: { borderColor: '#1E2329', timeVisible: true, secondsVisible: true },
};

function buildTimeScaleOptions(intervalSeconds) {
  // Keep 1m bars readable — auto spacing with gappy data stretches candles into blocks.
  const barSpacing = intervalSeconds <= 60 ? 7 : intervalSeconds <= 300 ? 8 : 10;
  return {
    borderColor: '#1E2329',
    timeVisible: true,
    secondsVisible: false,
    barSpacing,
    minBarSpacing: 3,
    rightOffset: 4,
    tickMarkFormatter: (time) => formatChartAxisTime(time, intervalSeconds),
  };
}

// Candlestick + MA overlay + volume panel. History and live ticks use Bybit
// public linear API/WebSocket (no API key). Backend WS syncs bot lock state.
export function useTradingChart({
  chartContainerRef,
  volumeContainerRef,
  rsiContainerRef,
  macdContainerRef,
  pairLabel,
  pairPrice,
  externalTradingMode,
  setConnected,
  botIsActive = false,
  blueBoxOverlay = null,
  entryCandles = [],
  patternNeon = [],
}) {
  const chartRef = useRef(null);
  const volumeChartRef = useRef(null);
  const rsiChartRef = useRef(null);
  const macdChartRef = useRef(null);
  const candleSeriesRef = useRef(null);
  const maSeriesRef = useRef({});
  const bollMidRef = useRef(null);
  const bollUpperRef = useRef(null);
  const bollLowerRef = useRef(null);
  const vwapSeriesRef = useRef(null);
  const volumeSeriesRef = useRef(null);
  const volumeMaSeriesRef = useRef(null);
  const rsiSeriesRef = useRef(null);
  const macdLineRef = useRef(null);
  const macdSignalRef = useRef(null);
  const macdHistRef = useRef(null);
  const trailingLockLineRef = useRef(null);
  const blueBoxOverlayElRef = useRef(null);
  const tradeFireOverlayElRef = useRef(null);
  const tradeFireLookupRef = useRef(new Map());
  const hoveredTradeFireTimeRef = useRef(null);
  /** Pending scroll+neon focus from Live Trades row click (pair may still be loading). */
  const pendingTradeFocusRef = useRef(null);
  /** Keep clicked-trade neon visible across overlay rebuilds until pair changes / user pans. */
  const pinnedTradeNeonRef = useRef(null);
  const redrawTradeFireOverlayRef = useRef(() => {});
  const blueBoxLineRefsRef = useRef([]);
  const blueBoxOverlayDataRef = useRef(null);
  const entryCandlesRef = useRef([]);
  const patternNeonRef = useRef([]);
  const botIsActiveRef = useRef(botIsActive);
  const mockDataRef = useRef([]);
  const entryPriceRef = useRef(pairPrice);
  const initialTimeframe = readStoredTimeframe();
  const currentIntervalRef = useRef(TIMEFRAME_SECONDS[initialTimeframe] || 60);
  const tradingModeRef = useRef(null);
  const freeSourceWsRef = useRef(null);
  const pairLabelRef = useRef(pairLabel);
  const skipFirstPairEffect = useRef(true);
  // Bumped on every switchSymbol/switchTimeframe call so a slow, superseded
  // real-history fetch can't clobber a newer switch when it finally resolves.
  const loadGenerationRef = useRef(0);
  const zoomTimeoutRef = useRef(null);
  const overlayThrottleRef = useRef(0);
  pairLabelRef.current = pairLabel;

  const [timeframe, setTimeframe] = useState(initialTimeframe);
  const timeframeRef = useRef(timeframe);
  timeframeRef.current = timeframe;
  const [chartSourceMode, setChartSourceModeState] = useState('PAPER_TRADING');
  const [chartHistorySource, setChartHistorySource] = useState('—');
  const [chartLiveSource, setChartLiveSource] = useState('—');
  const [readouts, setReadouts] = useState({
    vol: 0,
    volMA: 0,
    lastUpdated: '--:--:--',
    liveClock: '--:--:--',
    chartCandleTime: '—',
    blueBoxStatus: null,
    tradeFireTooltip: null,
  });

  botIsActiveRef.current = botIsActive;
  blueBoxOverlayDataRef.current = blueBoxOverlay;
  entryCandlesRef.current = entryCandles;
  patternNeonRef.current = patternNeon;

  const redrawTradeFireOverlay = useCallback(() => {
    const chart = chartRef.current;
    const series = candleSeriesRef.current;
    const overlayEl = tradeFireOverlayElRef.current;
    if (!chart || !series || !overlayEl) return;

    const lookup = buildTradeFireLookup(
      entryCandlesRef.current,
      mockDataRef.current,
      currentIntervalRef.current,
      pairLabelRef.current,
      patternNeonRef.current,
    );
    const pin = pinnedTradeNeonRef.current;
    if (pin?.time != null) {
      const bar = mockDataRef.current.find((b) => b.time === pin.time);
      if (bar) {
        const pinStage = pin.stage || 'fired';
        const existing = lookup.get(pin.time);
        const pinRank = { detected: 1, confirming: 2, skipped: 3, fired: 4, exited: 5 }[pinStage] || 4;
        const prevRank = existing
          ? ({ detected: 1, confirming: 2, skipped: 3, fired: 4, exited: 5 }[existing.stage] || 0)
          : 0;
        // Pin fills missing fire neon; never downgrade a live higher stage on this bar.
        if (!existing || pinRank >= prevRank) {
          lookup.set(pin.time, {
            time: pin.time,
            bar,
            side: pin.side || 'LONG',
            stage: pinStage,
            pair: pin.pair || pairLabelRef.current,
            pattern: pin.pattern || 'Trade fire',
            reason: pin.reason || null,
            opened_at: pin.opened_at ?? null,
            signal_candle_time: pin.signal_candle_time ?? pin.time,
          });
        }
      }
    }
    tradeFireLookupRef.current = lookup;

    renderTradeFireOverlay({
      chart,
      series,
      overlayEl,
      lookup,
      intervalSecs: currentIntervalRef.current,
      hoveredTime: hoveredTradeFireTimeRef.current,
    });
  }, []);

  redrawTradeFireOverlayRef.current = redrawTradeFireOverlay;

  const tryApplyPendingTradeFocus = useCallback(() => {
    const pending = pendingTradeFocusRef.current;
    const chart = chartRef.current;
    const series = candleSeriesRef.current;
    const data = mockDataRef.current;
    if (!pending || !chart || !series || !data?.length) return false;

    // Wait until main chart has switched to this trade's pair.
    const wantPair = String(pending.pair || '')
      .trim()
      .toUpperCase();
    const chartPair = String(pairLabelRef.current || '')
      .trim()
      .toUpperCase();
    if (wantPair && chartPair && wantPair !== chartPair) return false;

    const interval = currentIntervalRef.current;
    const want = snapToChartInterval(
      normalizeChartCandleTime(pending.time) ?? pending.time,
      interval,
    );
    if (want == null || !Number.isFinite(want)) {
      pendingTradeFocusRef.current = null;
      return false;
    }

    let idx = data.findIndex((b) => b.time === want);
    if (idx < 0) {
      // Nearest bar (trade candle may sit just outside loaded window).
      let best = -1;
      let bestDist = Infinity;
      for (let i = 0; i < data.length; i++) {
        const d = Math.abs(data[i].time - want);
        if (d < bestDist) {
          bestDist = d;
          best = i;
        }
      }
      // Only accept if within ~2 bars of the target TF.
      if (best < 0 || bestDist > interval * 2) return false;
      idx = best;
    }

    const bar = data[idx];
    const barTime = bar.time;

    // Ensure neon exists for this trade even if WS entry_candles lag.
    let lookup = tradeFireLookupRef.current;
    if (!(lookup instanceof Map)) lookup = new Map();
    const pinEntry = {
      time: barTime,
      bar,
      side: pending.side || 'LONG',
      stage: 'fired',
      pair: pending.pair || pairLabelRef.current,
      pattern: pending.pattern || 'Trade fire',
      reason: null,
      opened_at: pending.opened_at ?? null,
      signal_candle_time: normalizeChartCandleTime(pending.time) ?? barTime,
    };
    lookup.set(barTime, pinEntry);
    tradeFireLookupRef.current = lookup;
    pinnedTradeNeonRef.current = pinEntry;

    const half = Math.floor(DEFAULT_VISIBLE_CANDLES / 2);
    const from = Math.max(0, idx - half);
    const to = Math.min(data.length, Math.max(from + DEFAULT_VISIBLE_CANDLES, idx + 1));
    try {
      chart.timeScale().setVisibleLogicalRange({ from, to });
      volumeChartRef.current?.timeScale().setVisibleLogicalRange({ from, to });
      rsiChartRef.current?.timeScale().setVisibleLogicalRange({ from, to });
      macdChartRef.current?.timeScale().setVisibleLogicalRange({ from, to });
    } catch (err) {
      console.warn('[CHART] focusTradeCandle range failed:', err);
    }

    hoveredTradeFireTimeRef.current = barTime;
    const tip = tradeFireTooltipFromLookup(lookup, barTime);
    setReadouts((prev) => ({
      ...prev,
      tradeFireTooltip: tip,
      chartCandleTime: formatChartAxisTime(barTime, interval),
    }));
    // Render with the enriched lookup — do not call redrawTradeFireOverlay()
    // (it rebuilds from WS and would drop a synthetic pin for manual/lag trades).
    const overlayEl = tradeFireOverlayElRef.current;
    if (overlayEl) {
      renderTradeFireOverlay({
        chart,
        series,
        overlayEl,
        lookup,
        intervalSecs: interval,
        hoveredTime: barTime,
      });
    }
    pendingTradeFocusRef.current = null;
    return true;
  }, []);

  const focusTradeCandle = useCallback(
    (trade) => {
      if (!trade) return false;
      const raw =
        trade.signal_candle_time != null ? trade.signal_candle_time : trade.opened_at;
      const time = normalizeChartCandleTime(raw);
      if (time == null) return false;
      pendingTradeFocusRef.current = {
        time,
        side: trade.side || 'LONG',
        pattern: trade.pattern || 'Trade fire',
        opened_at: trade.opened_at ?? null,
        pair: trade.pair || pairLabelRef.current,
      };
      // Apply now if candles already loaded; else wait for next history/push.
      return tryApplyPendingTradeFocus();
    },
    [tryApplyPendingTradeFocus],
  );

  const pushCandlesToChart = useCallback((data) => {
    const series = candleSeriesRef.current;
    if (!series || !data?.length) {
      series?.setData([]);
      clearTradeFireOverlay(tradeFireOverlayElRef.current);
      tradeFireLookupRef.current = new Map();
      setReadouts((prev) => (prev.tradeFireTooltip ? { ...prev, tradeFireTooltip: null } : prev));
      return;
    }
    series.setData(decorateCandlestickSeries(data));
    const lookup = buildTradeFireLookup(
      entryCandlesRef.current,
      data,
      currentIntervalRef.current,
      pairLabelRef.current,
      patternNeonRef.current,
    );
    tradeFireLookupRef.current = lookup;
    const fireMarkers = computeTradeFireMarkers();
    series.setMarkers(fireMarkers.length ? fireMarkers : computeExtremeMarkers(data));
    redrawTradeFireOverlay();
    tryApplyPendingTradeFocus();
  }, [redrawTradeFireOverlay, tryApplyPendingTradeFocus]);

  const redrawBlueBoxOverlay = useCallback(() => {
    const chart = chartRef.current;
    const series = candleSeriesRef.current;
    const overlayEl = blueBoxOverlayElRef.current;
    if (!chart || !series || !overlayEl) return;

    renderBlueBoxChartOverlay({
      chart,
      series,
      overlayEl,
      overlay: blueBoxOverlayDataRef.current,
      botIsActive: botIsActiveRef.current,
      intervalSecs: currentIntervalRef.current,
      lineRefs: blueBoxLineRefsRef,
    });

    setReadouts((prev) => ({
      ...prev,
      blueBoxStatus: blueBoxStatusLabel(blueBoxOverlayDataRef.current, botIsActiveRef.current),
    }));
  }, []);

  const zoomToRecentCandles = useCallback((dataLength) => {
    if (zoomTimeoutRef.current) {
      clearTimeout(zoomTimeoutRef.current);
      zoomTimeoutRef.current = null;
    }
    if (!chartRef.current || dataLength === 0) return;

    const applyZoom = () => {
      // Pair/timeframe switches clear data first — a delayed zoom from the
      // previous load must not run against an empty chart (throws on null.from).
      if (!chartRef.current || mockDataRef.current.length === 0) return;
      try {
        chartRef.current.timeScale().setVisibleLogicalRange({
          from: Math.max(0, dataLength - DEFAULT_VISIBLE_CANDLES),
          to: dataLength,
        });
      } catch (err) {
        console.warn('[CHART] Could not apply zoom range:', err);
      }
    };
    applyZoom();
    requestAnimationFrame(() => requestAnimationFrame(applyZoom));
    zoomTimeoutRef.current = setTimeout(applyZoom, 300);
  }, []);

  const refreshTrailingLockLine = useCallback((basePrice) => {
    const series = candleSeriesRef.current;
    if (!series || !basePrice || basePrice <= 0) return;
    if (trailingLockLineRef.current) {
      try {
        series.removePriceLine(trailingLockLineRef.current);
      } catch {
        /* line may already be detached */
      }
    }
    trailingLockLineRef.current = series.createPriceLine({
      price: basePrice * 1.002,
      color: '#3b82f6',
      lineWidth: 2,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: true,
      title: 'Lock +0.20%',
    });
  }, []);

  const resetPriceScale = useCallback(() => {
    chartRef.current?.priceScale('right').applyOptions({ autoScale: true });
  }, []);

  const updateReadouts = useCallback((bar, data) => {
    const now = new Date();
    const volSeries = calcVolumeSMA(data.slice(-(VOLUME_MA_PERIOD + 5)), VOLUME_MA_PERIOD);
    const clock = formatLiveClock(now);
    const rsiSeries = calcRSI(data, 14);
    const { macdLine } = calcMACD(data);
    const vwapSeries = calcVWAP(data);

    setReadouts((prev) => ({
      ...prev,
      vol: bar.volume,
      volMA: volSeries.length ? volSeries[volSeries.length - 1].value : bar.volume,
      rsi: rsiSeries.length ? rsiSeries[rsiSeries.length - 1].value : null,
      macd: macdLine.length ? macdLine[macdLine.length - 1].value : null,
      vwap: vwapSeries.length ? vwapSeries[vwapSeries.length - 1].value : null,
      lastUpdated: clock,
      liveClock: clock,
      chartCandleTime: bar?.time ? formatChartAxisTime(bar.time, currentIntervalRef.current) : prev.chartCandleTime,
    }));
  }, []);

  const applyIndicatorOverlays = useCallback((data) => {
    MA_PERIODS.forEach((period) => {
      maSeriesRef.current[period]?.setData(calcSMA(data, period));
    });
    const bb = calcBollinger(data, 20, 2);
    bollMidRef.current?.setData(bb.mid);
    bollUpperRef.current?.setData(bb.upper);
    bollLowerRef.current?.setData(bb.lower);
    vwapSeriesRef.current?.setData(calcVWAP(data));
    volumeSeriesRef.current?.setData(toVolumeBars(data));
    volumeMaSeriesRef.current?.setData(calcVolumeSMA(data, VOLUME_MA_PERIOD));
    rsiSeriesRef.current?.setData(calcRSI(data, 14));
    const macd = calcMACD(data);
    macdLineRef.current?.setData(macd.macdLine);
    macdSignalRef.current?.setData(macd.signalLine);
    macdHistRef.current?.setData(macd.hist);
  }, []);

  const applyAllOverlays = useCallback((data) => {
    applyIndicatorOverlays(data);
    pushCandlesToChart(data);
  }, [pushCandlesToChart, applyIndicatorOverlays]);

  // Pushes a full dataset (synthetic or real) into every series + the readouts.
  const applyDataset = useCallback(
    (data, { zoomToRecent = false } = {}) => {
      const cleaned = sanitizeCandleData(data, currentIntervalRef.current);
      mockDataRef.current = cleaned;
      const hadPendingFocus = Boolean(pendingTradeFocusRef.current);
      applyAllOverlays(cleaned);
      resetPriceScale();
      // Trade-row focus wins over default "zoom to recent" after a pair switch.
      if (hadPendingFocus) {
        tryApplyPendingTradeFocus();
      } else if (zoomToRecent) {
        zoomToRecentCandles(cleaned.length);
      }
      if (cleaned.length > 0 && !hadPendingFocus) {
        updateReadouts(cleaned[cleaned.length - 1], cleaned);
      }
      redrawBlueBoxOverlay();
    },
    [
      updateReadouts,
      applyAllOverlays,
      zoomToRecentCandles,
      resetPriceScale,
      redrawBlueBoxOverlay,
      tryApplyPendingTradeFocus,
    ]
  );

  // Kicks off the async real-history fetch and swaps it in once ready, unless
  // a newer switchSymbol/switchTimeframe call has already superseded this one.
  const loadRealHistoryInBackground = useCallback(
    (pairLabelArg, tfKey, basePrice) => {
      const myGeneration = ++loadGenerationRef.current;
      loadHistoricalData(pairLabelArg, tfKey, basePrice)
        .then(({ data, source }) => {
          if (myGeneration !== loadGenerationRef.current) return;
          setChartHistorySource(source);
          applyDataset(data, { zoomToRecent: true });
        })
        .catch((err) => {
          console.error(`[CHART] Failed to load history for ${pairLabelArg}:`, err);
        });
    },
    [applyDataset]
  );

  const disconnectFreeSource = useCallback(() => {
    if (freeSourceWsRef.current) {
      // Null out ALL handlers, not just onclose - otherwise a tick already in
      // the event queue can still fire onmessage after we close, delivering the
      // OLD pair's price into the NEW pair's chart and making the switch look
      // like it didn't take effect.
      freeSourceWsRef.current.onopen = null;
      freeSourceWsRef.current.onmessage = null;
      freeSourceWsRef.current.onerror = null;
      freeSourceWsRef.current.onclose = null;
      freeSourceWsRef.current.close();
      freeSourceWsRef.current = null;
    }
  }, []);

  const applyLiveCandleBar = useCallback(
    (bar, { fromKline = false } = {}) => {
      const mockData = mockDataRef.current;
      if (!mockData) return;
      const interval = currentIntervalRef.current || 60;
      const time = snapToChartInterval(
        normalizeChartCandleTime(bar.time) ?? 0,
        interval,
      );
      if (!time) return;

      const open = Number(bar.open);
      const high = Number(bar.high);
      const low = Number(bar.low);
      const close = Number(bar.close);
      if (![open, high, low, close].every(Number.isFinite)) return;

      const candle = {
        time,
        open,
        high: Math.max(high, open, close),
        low: Math.min(low, open, close),
        close,
        volume: Number(bar.volume) || 0,
      };

      let newCandle = false;
      if (mockData.length === 0) {
        mockData.push(candle);
        newCandle = true;
      } else {
        const last = mockData[mockData.length - 1];
        if (candle.time === last.time) {
          // Same bucket: kline replaces OHLC; trade tick only amends close/hi/lo.
          mockData[mockData.length - 1] = fromKline
            ? candle
            : {
                ...last,
                close: candle.close,
                high: Math.max(last.high, candle.close),
                low: Math.min(last.low, candle.close),
                volume: last.volume + (candle.volume || 0),
              };
        } else if (candle.time > last.time) {
          // Fill skipped minutes so the 1m axis stays even.
          let cursor = last.time + interval;
          let guard = 0;
          while (cursor < candle.time && guard < 120) {
            const bridge = last.close;
            mockData.push({
              time: cursor,
              open: bridge,
              high: bridge,
              low: bridge,
              close: bridge,
              volume: 0,
            });
            cursor += interval;
            guard += 1;
          }
          mockData.push(candle);
          newCandle = true;
          while (mockData.length > 400) mockData.shift();
        } else {
          // Older bar — ignore (stale WS / race).
          return;
        }
      }

      const updated = mockData[mockData.length - 1];
      try {
        candleSeriesRef.current?.update(decorateCandlestickSeries([updated])[0]);
      } catch {
        pushCandlesToChart(mockData);
      }
      updateReadouts(updated, mockData);

      // Throttle heavy MA/volume redraws — every tick setData was warping 1m bars.
      const now = performance.now();
      if (newCandle || now - overlayThrottleRef.current > 400) {
        overlayThrottleRef.current = now;
        applyIndicatorOverlays(mockData);
      }

      if (newCandle) {
        zoomToRecentCandles(mockData.length);
        redrawTradeFireOverlay();
      } else {
        try {
          chartRef.current?.timeScale().scrollToRealTime();
          volumeChartRef.current?.timeScale().scrollToRealTime();
          rsiChartRef.current?.timeScale().scrollToRealTime();
          macdChartRef.current?.timeScale().scrollToRealTime();
        } catch {
          /* chart may be mid-reset */
        }
      }
    },
    [updateReadouts, zoomToRecentCandles, pushCandlesToChart, redrawTradeFireOverlay, applyIndicatorOverlays]
  );

  const applyLivePriceTick = useCallback(
    (newClose) => {
      const mockData = mockDataRef.current;
      if (!mockData || mockData.length === 0) {
        console.warn('[CHART] No candle data available yet, skipping price tick');
        return;
      }
      const lastCandle = mockData[mockData.length - 1];
      if (!lastCandle || !lastCandle.time) return;

      if (lastCandle.close > 0) {
        const ratio = newClose / lastCandle.close;
        if (ratio > 2.5 || ratio < 0.4) {
          console.warn(`[CHART] Ignoring out-of-range tick ${newClose} vs last close ${lastCandle.close}`);
          return;
        }
      }

      const bucketTime =
        Math.floor(Date.now() / 1000 / currentIntervalRef.current) * currentIntervalRef.current;

      applyLiveCandleBar(
        {
          time: bucketTime,
          open: bucketTime > lastCandle.time ? lastCandle.close : lastCandle.open,
          high: newClose,
          low: newClose,
          close: newClose,
          volume: 0,
        },
        { fromKline: false },
      );
    },
    [applyLiveCandleBar]
  );

  const connectFreeSource = useCallback(
    (pairLabelArg) => {
      disconnectFreeSource();
      const bybitSymbol = getBybitSymbol(pairLabelArg);
      const tfKey = Object.entries(TIMEFRAME_SECONDS).find(
        ([, secs]) => secs === currentIntervalRef.current,
      )?.[0];
      const klineInterval = BYBIT_KLINE_INTERVAL[tfKey] || BYBIT_KLINE_INTERVAL['1M'];

      const scheduleRetry = () => {
        setTimeout(() => connectFreeSource(pairLabelArg), 2000);
      };

      if (!bybitSymbol) {
        console.warn(`[BYBIT PUBLIC] No linear symbol mapped for ${pairLabelArg}.`);
        setChartLiveSource('mock / synthetic (pair not on Bybit linear)');
        return;
      }

      setChartLiveSource(`Bybit kline WS (${bybitSymbol} ${klineInterval}m)`);
      const ws = new WebSocket(BYBIT_PUBLIC_WS_LINEAR);
      freeSourceWsRef.current = ws;

      ws.onopen = () => {
        const args = [bybitPublicKlineTopic(bybitSymbol, klineInterval)];
        // Trade stream only as fallback price pulse if kline is quiet.
        args.push(bybitPublicTradeTopic(bybitSymbol));
        ws.send(JSON.stringify({ op: 'subscribe', args }));
        debugLog(`[BYBIT PUBLIC] Subscribed to kline+trades ${bybitSymbol} @ ${klineInterval}`);
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.topic?.startsWith('kline.')) {
            const rows = msg.data;
            if (!Array.isArray(rows) || rows.length === 0) return;
            const mapped = mapBybitKlineWsBar(rows[rows.length - 1]);
            if (mapped) applyLiveCandleBar(mapped, { fromKline: true });
            return;
          }
          // Ignore trade ticks when kline is feeding — trades were causing gappy fat bars.
          if (msg.topic?.startsWith('publicTrade.')) {
            const hasCandles = mockDataRef.current.length > 0;
            const last = hasCandles ? mockDataRef.current[mockDataRef.current.length - 1] : null;
            const bucketNow =
              Math.floor(Date.now() / 1000 / currentIntervalRef.current) * currentIntervalRef.current;
            // Only use trades if we somehow have no current-minute candle yet.
            if (last && last.time === bucketNow) return;
            const trades = msg.data;
            if (!Array.isArray(trades) || trades.length === 0) return;
            const price = parseFloat(trades[trades.length - 1].p);
            if (!isNaN(price)) applyLivePriceTick(price);
          }
        } catch (err) {
          console.error('[BYBIT PUBLIC] Error parsing stream:', err);
        }
      };

      ws.onerror = (error) => {
        console.error(`[BYBIT PUBLIC] WebSocket error for ${pairLabelArg}:`, error);
      };

      ws.onclose = () => {
        console.warn(`[BYBIT PUBLIC] WebSocket closed for ${pairLabelArg}. Retrying in 2s...`);
        scheduleRetry();
      };
    },
    [applyLiveCandleBar, applyLivePriceTick, disconnectFreeSource]
  );

  const setChartDataSourceMode = useCallback((mode) => {
    tradingModeRef.current = mode;
    setChartSourceModeState(mode);
    // Chart live price always from public Bybit — mode only affects order execution.
  }, []);

  const switchSymbol = useCallback(
    (basePrice) => {
      entryPriceRef.current = basePrice;
      // Clear candles + fire overlays so previous pair's neon pattern cannot linger.
      pinnedTradeNeonRef.current = null;
      applyDataset([]);
      clearTradeFireOverlay(tradeFireOverlayElRef.current);
      tradeFireLookupRef.current = new Map();
      setReadouts((prev) => ({ ...prev, tradeFireTooltip: null }));
      refreshTrailingLockLine(basePrice);
      resetPriceScale();
      loadRealHistoryInBackground(pairLabelRef.current, timeframe, basePrice);
    },
    [applyDataset, loadRealHistoryInBackground, timeframe, refreshTrailingLockLine, resetPriceScale]
  );

  const switchTimeframe = useCallback(
    (tf, { persistBackend = true } = {}) => {
      if (!tf || !TIMEFRAME_SECONDS[tf]) return;
      setTimeframe(tf);
      currentIntervalRef.current = TIMEFRAME_SECONDS[tf] || 3600;
      const timeScaleOpts = buildTimeScaleOptions(currentIntervalRef.current);
      chartRef.current?.applyOptions({ timeScale: { ...darkThemeConfig.timeScale, ...timeScaleOpts } });
      volumeChartRef.current?.applyOptions({ timeScale: timeScaleOpts });
      rsiChartRef.current?.applyOptions({ timeScale: timeScaleOpts });
      macdChartRef.current?.applyOptions({ timeScale: timeScaleOpts });
      // Clear the chart rather than showing a fake synthetic placeholder.
      applyDataset([]);
      loadRealHistoryInBackground(pairLabelRef.current, tf, entryPriceRef.current);
      // Re-subscribe kline stream for the new interval (1m vs 5m etc.).
      connectFreeSource(pairLabelRef.current);

      // Sync trading engine TF so auto-size % matches chart (persists on VPS).
      if (persistBackend) {
        authFetch('/set-timeframe', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ seconds: TIMEFRAME_SECONDS[tf] || 60 }),
        }).catch((err) => console.warn('set-timeframe sync failed:', err));
      }

      try {
        localStorage.setItem(CHART_TIMEFRAME_STORAGE_KEY, tf);
      } catch {
        /* storage blocked */
      }
    },
    [applyDataset, loadRealHistoryInBackground, connectFreeSource]
  );

  // Init chart once on mount
  useEffect(() => {
    const chartContainer = chartContainerRef.current;
    const volumeContainer = volumeContainerRef.current;
    const rsiContainer = rsiContainerRef?.current;
    const macdContainer = macdContainerRef?.current;
    if (!chartContainer || !volumeContainer) return;

    if (getComputedStyle(chartContainer).position === 'static') {
      chartContainer.style.position = 'relative';
    }

    const chart = createChart(chartContainer, {
      width: chartContainer.clientWidth,
      height: chartContainer.clientHeight,
      ...darkThemeConfig,
      timeScale: {
        ...darkThemeConfig.timeScale,
        ...buildTimeScaleOptions(currentIntervalRef.current),
        visible: false,
      },
      localization: {
        timeFormatter: (time) => formatChartAxisTime(time, currentIntervalRef.current),
      },
    });
    chartRef.current = chart;

    const candleSeries = chart.addCandlestickSeries({
      upColor: '#22c55e',
      downColor: '#ef4444',
      borderVisible: false,
      wickUpColor: '#22c55e',
      wickDownColor: '#ef4444',
    });
    candleSeriesRef.current = candleSeries;

    const blueBoxLayer = document.createElement('div');
    blueBoxLayer.setAttribute('aria-hidden', 'true');
    blueBoxLayer.className = 'absolute inset-0 pointer-events-none z-[15] overflow-hidden';
    chartContainer.appendChild(blueBoxLayer);
    blueBoxOverlayElRef.current = blueBoxLayer;

    const tradeFireLayer = document.createElement('div');
    tradeFireLayer.setAttribute('aria-hidden', 'true');
    // Must stay pointer-events-none — a full-bleed layer steals wheel/drag from
    // lightweight-charts and kills zoom/pan. Hover uses chart crosshair instead.
    tradeFireLayer.className = 'absolute inset-0 pointer-events-none z-[20] overflow-visible';
    chartContainer.appendChild(tradeFireLayer);
    tradeFireOverlayElRef.current = tradeFireLayer;

    // MA(5,10,20,30) overlay lines
    maSeriesRef.current = {};
    MA_PERIODS.forEach((period) => {
      maSeriesRef.current[period] = chart.addLineSeries({
        color: MA_COLORS[period],
        lineWidth: 1.5,
        priceLineVisible: false,
        lastValueVisible: false,
      });
    });

    // Bollinger Bands (20, 2)
    bollUpperRef.current = chart.addLineSeries({
      color: 'rgba(148,163,184,0.7)',
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    bollMidRef.current = chart.addLineSeries({
      color: 'rgba(148,163,184,0.45)',
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    bollLowerRef.current = chart.addLineSeries({
      color: 'rgba(148,163,184,0.7)',
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      priceLineVisible: false,
      lastValueVisible: false,
    });

    // VWAP (daily reset)
    vwapSeriesRef.current = chart.addLineSeries({
      color: '#f97316',
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
    });

    const subPaneOpts = {
      ...darkThemeConfig,
      timeScale: buildTimeScaleOptions(currentIntervalRef.current),
      localization: {
        timeFormatter: (time) => formatChartAxisTime(time, currentIntervalRef.current),
      },
    };

    // Volume histogram sub-panel (own chart instance, time-synced with the main chart)
    const volumeChart = createChart(volumeContainer, {
      width: volumeContainer.clientWidth,
      height: volumeContainer.clientHeight,
      ...subPaneOpts,
    });
    volumeChartRef.current = volumeChart;
    const volumeSeries = volumeChart.addHistogramSeries({ priceFormat: { type: 'volume' }, lastValueVisible: false });
    volumeSeriesRef.current = volumeSeries;
    const volumeMaSeries = volumeChart.addLineSeries({ color: '#f59e0b', lineWidth: 1.5, lastValueVisible: false });
    volumeMaSeriesRef.current = volumeMaSeries;

    let rsiChart = null;
    if (rsiContainer) {
      rsiChart = createChart(rsiContainer, {
        width: rsiContainer.clientWidth,
        height: rsiContainer.clientHeight,
        ...subPaneOpts,
        timeScale: { ...buildTimeScaleOptions(currentIntervalRef.current), visible: false },
      });
      rsiChartRef.current = rsiChart;
      rsiSeriesRef.current = rsiChart.addLineSeries({
        color: '#a78bfa',
        lineWidth: 1.5,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      // Fixed 0–100 RSI scale with 30/70 guides via price lines after first data
      rsiSeriesRef.current.createPriceLine({
        price: 70,
        color: 'rgba(239,68,68,0.45)',
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: false,
        title: '',
      });
      rsiSeriesRef.current.createPriceLine({
        price: 30,
        color: 'rgba(34,197,94,0.45)',
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: false,
        title: '',
      });
    }

    let macdChart = null;
    if (macdContainer) {
      macdChart = createChart(macdContainer, {
        width: macdContainer.clientWidth,
        height: macdContainer.clientHeight,
        ...subPaneOpts,
      });
      macdChartRef.current = macdChart;
      macdHistRef.current = macdChart.addHistogramSeries({
        priceFormat: { type: 'price', precision: 4, minMove: 0.0001 },
        lastValueVisible: false,
      });
      macdLineRef.current = macdChart.addLineSeries({
        color: '#38bdf8',
        lineWidth: 1.5,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      macdSignalRef.current = macdChart.addLineSeries({
        color: '#f472b6',
        lineWidth: 1.5,
        priceLineVisible: false,
        lastValueVisible: false,
      });
    }

    const entryPrice = entryPriceRef.current;
    // Chart starts empty - no fake synthetic placeholder - until real data arrives below.
    mockDataRef.current = [];

    refreshTrailingLockLine(entryPrice);

    // Sync time scales between the main chart and indicator panes.
    // When data is cleared (pair/timeframe switch), lightweight-charts fires this
    // callback with range=null — passing that through crashes setVisibleLogicalRange.
    chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (!range || range.from == null || range.to == null) return;
      try {
        volumeChart.timeScale().setVisibleLogicalRange(range);
        rsiChart?.timeScale().setVisibleLogicalRange(range);
        macdChart?.timeScale().setVisibleLogicalRange(range);
      } catch (err) {
        console.warn('[CHART] Could not sync indicator chart zoom:', err);
      }
    });

    // Fetch real history for the initial pair using the persisted chart timeframe.
    loadRealHistoryInBackground(pairLabelRef.current, initialTimeframe, entryPrice);
    // Do NOT POST localStorage TF here — that was overwriting VPS-persisted TF
    // back to 1M on every login/refresh. Backend TF is adopted in a separate effect.

    chart.timeScale().subscribeVisibleLogicalRangeChange(() => {
      redrawBlueBoxOverlay();
      redrawTradeFireOverlayRef.current();
    });

    // Live crosshair OHLC readout + trade-fire pattern tooltip
    // own seriesData only carries {time,open,high,low,close} - it strips our custom
    // `volume` field - so look the full bar up in mockDataRef.current by time instead
    // of using param.seriesData.get() directly (that was producing "NaN" volume).
    chart.subscribeCrosshairMove((param) => {
      if (!param.time) {
        if (hoveredTradeFireTimeRef.current != null) {
          hoveredTradeFireTimeRef.current = null;
          setReadouts((prev) => ({ ...prev, tradeFireTooltip: null }));
          redrawTradeFireOverlayRef.current();
        }
        return;
      }
      const fullBar = mockDataRef.current.find((d) => d.time === param.time);
      if (fullBar) updateReadouts(fullBar, mockDataRef.current);

      const tip = tradeFireTooltipFromLookup(tradeFireLookupRef.current, param.time);
      const prevHover = hoveredTradeFireTimeRef.current;
      const nextHover = tip ? param.time : null;
      if (prevHover !== nextHover) {
        hoveredTradeFireTimeRef.current = nextHover;
        setReadouts((prev) => ({ ...prev, tradeFireTooltip: tip }));
        redrawTradeFireOverlayRef.current();
      }
    });

    const handleResize = () => {
      chart.applyOptions({ width: chartContainer.clientWidth, height: chartContainer.clientHeight });
      volumeChart.applyOptions({ width: volumeContainer.clientWidth, height: volumeContainer.clientHeight });
      if (rsiContainer && rsiChart) {
        rsiChart.applyOptions({ width: rsiContainer.clientWidth, height: rsiContainer.clientHeight });
      }
      if (macdContainer && macdChart) {
        macdChart.applyOptions({ width: macdContainer.clientWidth, height: macdContainer.clientHeight });
      }
      redrawBlueBoxOverlay();
      redrawTradeFireOverlayRef.current();
    };
    window.addEventListener('resize', handleResize);

    // Live Price Feed Wire (Real WebSocket Connection to Python AI / Bybit backend)
    let marketWs;
    let marketReconnectTimer;
    function connectMarketWS() {
      const ws = new WebSocket(backendWsUrl('/ws/market'));
      marketWs = ws;

      ws.onopen = () => {
        debugLog('[BACKEND WS] Connected to backend market feed');
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);

          if (data.trading_mode) setChartDataSourceMode(data.trading_mode);

          const publicWsLive = freeSourceWsRef.current?.readyState === WebSocket.OPEN;
          if (!publicWsLive && data.price != null && !Number.isNaN(Number(data.price))) {
            applyLivePriceTick(Number(data.price));
          }

          const wsPair = data.active_pair;
          if (
            data.lock_active &&
            (!wsPair || wsPair === pairLabelRef.current) &&
            entryPriceRef.current > 0
          ) {
            trailingLockLineRef.current?.applyOptions({
              price: entryPriceRef.current + entryPriceRef.current * (data.peak_pct / 100),
              title: `Lock peak (+${data.peak_pct.toFixed(2)}% gross)`,
              color: '#eab308',
            });
          }
        } catch (err) {
          console.error('[BACKEND WS] Error parsing message:', err);
        }
      };

      ws.onerror = (error) => {
        console.error('[BACKEND WS] WebSocket error:', error);
      };

      ws.onclose = () => {
        console.warn('[BACKEND WS] Connection closed. Reconnecting in 2s...');
        marketReconnectTimer = setTimeout(connectMarketWS, 2000);
      };
    }
    connectMarketWS();
    connectFreeSource(pairLabelRef.current);

    authFetch('/trading-mode')
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data) => {
        debugLog('[CHART] Trading mode retrieved:', data.mode);
        setChartDataSourceMode(data.mode);
      })
      .catch((err) => {
        console.warn('[CHART] Failed to fetch trading mode, defaulting to PAPER_TRADING:', err);
        setChartDataSourceMode('PAPER_TRADING');
      });

    return () => {
      window.removeEventListener('resize', handleResize);
      clearTimeout(marketReconnectTimer);
      if (zoomTimeoutRef.current) clearTimeout(zoomTimeoutRef.current);
      if (marketWs) {
        marketWs.onclose = null;
        marketWs.close();
      }
      disconnectFreeSource();
      clearBlueBoxChartGraphics({
        series: candleSeriesRef.current,
        overlayEl: blueBoxOverlayElRef.current,
        lineRefs: blueBoxLineRefsRef,
      });
      if (blueBoxLayer.parentNode) blueBoxLayer.parentNode.removeChild(blueBoxLayer);
      if (tradeFireLayer.parentNode) tradeFireLayer.parentNode.removeChild(tradeFireLayer);
      chart.remove();
      volumeChart.remove();
      rsiChart?.remove();
      macdChart?.remove();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // React to pair changes (skip the very first run since init already used the initial price)
  // switchSymbol / connectFreeSource are stored in refs so this effect always calls the
  // LATEST versions (switchSymbol is recreated whenever the timeframe changes). Without
  // this, the effect could call a stale switchSymbol and the chart wouldn't reload for the
  // newly selected pair - exactly the "pair changes in backend but chart doesn't update" bug.
  const switchSymbolRef = useRef(null);
  const connectFreeSourceRef = useRef(null);
  switchSymbolRef.current = switchSymbol;
  connectFreeSourceRef.current = connectFreeSource;

  useEffect(() => {
    if (skipFirstPairEffect.current) {
      skipFirstPairEffect.current = false;
      return;
    }
    if (!candleSeriesRef.current) return;
    debugLog(`[CHART] Switching candlestick data → ${pairLabel}`);
    switchSymbolRef.current?.(pairPrice);
    connectFreeSourceRef.current?.(pairLabel);
    // Only re-run when the pair label changes — price refresh alone must not wipe the chart.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pairLabel]);

  // React to trading-mode changes reported by the portfolio WebSocket
  useEffect(() => {
    if (externalTradingMode) setChartDataSourceMode(externalTradingMode);
  }, [externalTradingMode, setChartDataSourceMode]);

  const switchTimeframeRef = useRef(switchTimeframe);
  switchTimeframeRef.current = switchTimeframe;

  // Adopt VPS-persisted engine TF on login/refresh — never overwrite backend with
  // a stale localStorage default (that was snapping the engine back to 1M).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await authFetch('/bot/status');
        if (!res.ok || cancelled) return;
        const data = await res.json();
        const serverTf = timeframeFromSeconds(data.timeframe_seconds);
        if (!serverTf || cancelled) return;
        try {
          localStorage.setItem(CHART_TIMEFRAME_STORAGE_KEY, serverTf);
        } catch {
          /* storage blocked */
        }
        if (serverTf !== timeframeRef.current) {
          switchTimeframeRef.current(serverTf, { persistBackend: false });
        }
      } catch (err) {
        console.warn('backend timeframe sync failed:', err);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Live wall-clock in the chart header (updates every second even between ticks).
  useEffect(() => {
    const tick = () => {
      setReadouts((prev) => ({ ...prev, liveClock: formatLiveClock() }));
    };
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    redrawBlueBoxOverlay();
    redrawTradeFireOverlay();
  }, [botIsActive, blueBoxOverlay, timeframe, pairLabel, redrawBlueBoxOverlay, redrawTradeFireOverlay]);

  useEffect(() => {
    if (mockDataRef.current.length > 0) {
      pushCandlesToChart(mockDataRef.current);
    } else {
      clearTradeFireOverlay(tradeFireOverlayElRef.current);
      tradeFireLookupRef.current = new Map();
    }
  }, [entryCandles, patternNeon, pairLabel, pushCandlesToChart]);

  return {
    timeframe,
    switchTimeframe,
    focusTradeCandle,
    readouts,
    chartSourceMode,
    chartHistorySource,
    chartLiveSource,
  };
}
