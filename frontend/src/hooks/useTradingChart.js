import { useCallback, useEffect, useRef, useState } from 'react';
import { createChart, CrosshairMode, LineStyle } from 'lightweight-charts';
import { authFetch, backendWsUrl } from '../config/api';
import {
  BYBIT_PUBLIC_WS_LINEAR,
  bybitKlineUrl,
  bybitPublicTradeTopic,
  bybitRecentTradeUrl,
} from '../config/bybitPublic';
import { debugLog } from '../config/debug';
import { fmtNum, getBybitSymbol } from '../data/pairs';
import { formatChartAxisTime, formatLiveClock } from '../utils/time';
import { decorateCandlestickSeries, computeTradeFireMarkers } from '../utils/chartCandles';
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
    if (tfKey === '5M') {
      try {
        const data = await fetchBackend24hCandles(pairLabelArg);
        return { data, source: 'backend /chart/24h (Bybit 5m persisted)' };
      } catch (backendErr) {
        console.warn(`[CHART] Backend 24h snapshot unavailable for ${pairLabelArg}:`, backendErr);
      }
    }
    const data = bybitKline
      ? await fetchBybitHistory(bybitSymbol, bybitKline)
      : await fetchBybitRecentTradesAsCandles(bybitSymbol, intervalSecs);
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
  return {
    borderColor: '#1E2329',
    timeVisible: true,
    secondsVisible: intervalSeconds <= 60,
    tickMarkFormatter: (time) => formatChartAxisTime(time, intervalSeconds),
  };
}

// Candlestick + MA overlay + volume panel. History and live ticks use Bybit
// public linear API/WebSocket (no API key). Backend WS syncs bot lock state.
export function useTradingChart({
  chartContainerRef,
  volumeContainerRef,
  pairLabel,
  pairPrice,
  externalTradingMode,
  setConnected,
  botIsActive = false,
  blueBoxOverlay = null,
  entryCandles = [],
}) {
  const chartRef = useRef(null);
  const volumeChartRef = useRef(null);
  const candleSeriesRef = useRef(null);
  const maSeriesRef = useRef({});
  const volumeSeriesRef = useRef(null);
  const volumeMaSeriesRef = useRef(null);
  const trailingLockLineRef = useRef(null);
  const blueBoxOverlayElRef = useRef(null);
  const tradeFireOverlayElRef = useRef(null);
  const tradeFireLookupRef = useRef(new Map());
  const hoveredTradeFireTimeRef = useRef(null);
  const onTradeFireHoverRef = useRef(() => {});
  const redrawTradeFireOverlayRef = useRef(() => {});
  const blueBoxLineRefsRef = useRef([]);
  const blueBoxOverlayDataRef = useRef(null);
  const entryCandlesRef = useRef([]);
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
  pairLabelRef.current = pairLabel;

  const [timeframe, setTimeframe] = useState(initialTimeframe);
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

  const redrawTradeFireOverlay = useCallback(() => {
    const chart = chartRef.current;
    const series = candleSeriesRef.current;
    const overlayEl = tradeFireOverlayElRef.current;
    if (!chart || !series || !overlayEl) return;

    const lookup = buildTradeFireLookup(
      entryCandlesRef.current,
      mockDataRef.current,
      currentIntervalRef.current,
    );
    tradeFireLookupRef.current = lookup;

    renderTradeFireOverlay({
      chart,
      series,
      overlayEl,
      lookup,
      intervalSecs: currentIntervalRef.current,
      hoveredTime: hoveredTradeFireTimeRef.current,
      onHover: (time) => onTradeFireHoverRef.current(time),
    });
  }, []);

  redrawTradeFireOverlayRef.current = redrawTradeFireOverlay;

  onTradeFireHoverRef.current = (time) => {
    if (hoveredTradeFireTimeRef.current === time) return;
    hoveredTradeFireTimeRef.current = time;
    const tip = time != null ? tradeFireTooltipFromLookup(tradeFireLookupRef.current, time) : null;
    setReadouts((prev) => ({ ...prev, tradeFireTooltip: tip }));
    redrawTradeFireOverlay();
  };

  const pushCandlesToChart = useCallback((data) => {
    const series = candleSeriesRef.current;
    if (!series || !data?.length) {
      series?.setData([]);
      clearTradeFireOverlay(tradeFireOverlayElRef.current);
      tradeFireLookupRef.current = new Map();
      return;
    }
    series.setData(decorateCandlestickSeries(data));
    const lookup = buildTradeFireLookup(entryCandlesRef.current, data, currentIntervalRef.current);
    tradeFireLookupRef.current = lookup;
    const fireMarkers = computeTradeFireMarkers();
    series.setMarkers(fireMarkers.length ? fireMarkers : computeExtremeMarkers(data));
    redrawTradeFireOverlay();
  }, [redrawTradeFireOverlay]);

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

    setReadouts((prev) => ({
      ...prev,
      vol: bar.volume,
      volMA: volSeries.length ? volSeries[volSeries.length - 1].value : bar.volume,
      lastUpdated: clock,
      liveClock: clock,
      chartCandleTime: bar?.time ? formatChartAxisTime(bar.time, currentIntervalRef.current) : prev.chartCandleTime,
    }));
  }, []);

  const applyAllOverlays = useCallback((data) => {
    MA_PERIODS.forEach((period) => {
      maSeriesRef.current[period]?.setData(calcSMA(data, period));
    });
    volumeSeriesRef.current?.setData(toVolumeBars(data));
    volumeMaSeriesRef.current?.setData(calcVolumeSMA(data, VOLUME_MA_PERIOD));
    pushCandlesToChart(data);
  }, [pushCandlesToChart]);

  // Pushes a full dataset (synthetic or real) into every series + the readouts.
  const applyDataset = useCallback(
    (data, { zoomToRecent = false } = {}) => {
      mockDataRef.current = data;
      applyAllOverlays(data);
      resetPriceScale();
      if (zoomToRecent) zoomToRecentCandles(data.length);
      if (data.length > 0) updateReadouts(data[data.length - 1], data);
      redrawBlueBoxOverlay();
    },
    [updateReadouts, applyAllOverlays, zoomToRecentCandles, resetPriceScale, redrawBlueBoxOverlay]
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

  const applyLivePriceTick = useCallback(
    (newClose) => {
      const mockData = mockDataRef.current;
      if (!mockData || mockData.length === 0) {
        console.warn('[CHART] No candle data available yet, skipping price tick');
        return;
      }
      const lastCandle = mockData[mockData.length - 1];
      if (!lastCandle || !lastCandle.time) {
        console.warn('[CHART] Last candle is invalid, regenerating chart data');
        return;
      }

      // Ignore ticks from the wrong pair (e.g. stale BTC feed after switching to ETH).
      if (lastCandle.close > 0) {
        const ratio = newClose / lastCandle.close;
        if (ratio > 2.5 || ratio < 0.4) {
          console.warn(`[CHART] Ignoring out-of-range tick ${newClose} vs last close ${lastCandle.close}`);
          return;
        }
      }

      const bucketTime = Math.floor(Date.now() / 1000 / currentIntervalRef.current) * currentIntervalRef.current;

      let updated;
      let newCandle = false;
      if (bucketTime > lastCandle.time) {
        newCandle = true;
        updated = { time: bucketTime, open: lastCandle.close, high: newClose, low: newClose, close: newClose, volume: Math.random() * 2 };
        mockData.push(updated);
        if (mockData.length > 200) mockData.shift();
      } else {
        updated = {
          ...lastCandle,
          close: newClose,
          high: Math.max(lastCandle.high, newClose),
          low: Math.min(lastCandle.low, newClose),
          volume: lastCandle.volume + Math.random() * 0.3,
        };
        mockData[mockData.length - 1] = updated;
      }

      candleSeriesRef.current.update(decorateCandlestickSeries([updated])[0]);
      updateReadouts(updated, mockData);
      applyAllOverlays(mockData);
      if (newCandle) {
        zoomToRecentCandles(mockData.length);
      } else {
        try {
          chartRef.current?.timeScale().scrollToRealTime();
          volumeChartRef.current?.timeScale().scrollToRealTime();
        } catch {
          /* chart may be mid-reset */
        }
      }
      redrawBlueBoxOverlay();
      redrawTradeFireOverlay();
    },
    [updateReadouts, applyAllOverlays, zoomToRecentCandles, redrawBlueBoxOverlay, redrawTradeFireOverlay]
  );

  const connectFreeSource = useCallback(
    (pairLabelArg) => {
      disconnectFreeSource();
      const bybitSymbol = getBybitSymbol(pairLabelArg);

      const scheduleRetry = () => {
        setTimeout(() => connectFreeSource(pairLabelArg), 2000);
      };

      if (!bybitSymbol) {
        console.warn(`[BYBIT PUBLIC] No linear symbol mapped for ${pairLabelArg}.`);
        setChartLiveSource('mock / synthetic (pair not on Bybit linear)');
        return;
      }

      setChartLiveSource(`Bybit public WebSocket (linear: ${bybitSymbol})`);
      const ws = new WebSocket(BYBIT_PUBLIC_WS_LINEAR);
      freeSourceWsRef.current = ws;

      ws.onopen = () => {
        ws.send(JSON.stringify({ op: 'subscribe', args: [bybitPublicTradeTopic(bybitSymbol)] }));
        debugLog(`[BYBIT PUBLIC] Subscribed to ${bybitSymbol} trades.`);
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (!msg.topic?.startsWith('publicTrade.')) return;
          const trades = msg.data;
          if (!Array.isArray(trades) || trades.length === 0) return;
          const price = parseFloat(trades[trades.length - 1].p);
          if (!isNaN(price)) applyLivePriceTick(price);
        } catch (err) {
          console.error('[BYBIT PUBLIC] Error parsing trade stream:', err);
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
    [applyLivePriceTick, disconnectFreeSource]
  );

  const setChartDataSourceMode = useCallback((mode) => {
    tradingModeRef.current = mode;
    setChartSourceModeState(mode);
    // Chart live price always from public Bybit — mode only affects order execution.
  }, []);

  const switchSymbol = useCallback(
    (basePrice) => {
      entryPriceRef.current = basePrice;
      // Clear the chart rather than showing a fake synthetic placeholder -
      // everything displayed should be real, wired data or nothing at all.
      applyDataset([]);
      refreshTrailingLockLine(basePrice);
      resetPriceScale();
      loadRealHistoryInBackground(pairLabelRef.current, timeframe, basePrice);
    },
    [applyDataset, loadRealHistoryInBackground, timeframe, refreshTrailingLockLine, resetPriceScale]
  );

  const switchTimeframe = useCallback(
    (tf) => {
      setTimeframe(tf);
      currentIntervalRef.current = TIMEFRAME_SECONDS[tf] || 3600;
      const timeScaleOpts = buildTimeScaleOptions(currentIntervalRef.current);
      chartRef.current?.applyOptions({ timeScale: { ...darkThemeConfig.timeScale, ...timeScaleOpts } });
      volumeChartRef.current?.applyOptions({ timeScale: timeScaleOpts });
      // Clear the chart rather than showing a fake synthetic placeholder.
      applyDataset([]);
      loadRealHistoryInBackground(pairLabelRef.current, tf, entryPriceRef.current);

      // Chart timeframe is display-only — do NOT call /set-timeframe here.
      // Backend trading engine keeps its own candle interval so open trades
      // and Blue Box state are unaffected by viewing a different chart zoom.
      try {
        localStorage.setItem(CHART_TIMEFRAME_STORAGE_KEY, tf);
      } catch {
        /* storage blocked */
      }
    },
    [applyDataset, loadRealHistoryInBackground]
  );

  // Init chart once on mount
  useEffect(() => {
    const chartContainer = chartContainerRef.current;
    const volumeContainer = volumeContainerRef.current;
    if (!chartContainer || !volumeContainer) return;

    if (getComputedStyle(chartContainer).position === 'static') {
      chartContainer.style.position = 'relative';
    }

    const chart = createChart(chartContainer, {
      width: chartContainer.clientWidth,
      height: chartContainer.clientHeight,
      timeScale: { ...darkThemeConfig.timeScale, ...buildTimeScaleOptions(currentIntervalRef.current), visible: false },
      localization: {
        timeFormatter: (time) => formatChartAxisTime(time, currentIntervalRef.current),
      },
      ...darkThemeConfig,
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
    tradeFireLayer.className = 'absolute inset-0 z-[20] overflow-visible';
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

    // Volume histogram sub-panel (own chart instance, time-synced with the main chart)
    const volumeChart = createChart(volumeContainer, {
      width: volumeContainer.clientWidth,
      height: volumeContainer.clientHeight,
      timeScale: buildTimeScaleOptions(currentIntervalRef.current),
      localization: {
        timeFormatter: (time) => formatChartAxisTime(time, currentIntervalRef.current),
      },
      ...darkThemeConfig,
    });
    volumeChartRef.current = volumeChart;
    const volumeSeries = volumeChart.addHistogramSeries({ priceFormat: { type: 'volume' }, lastValueVisible: false });
    volumeSeriesRef.current = volumeSeries;
    const volumeMaSeries = volumeChart.addLineSeries({ color: '#f59e0b', lineWidth: 1.5, lastValueVisible: false });
    volumeMaSeriesRef.current = volumeMaSeries;

    const entryPrice = entryPriceRef.current;
    // Chart starts empty - no fake synthetic placeholder - until real data arrives below.
    mockDataRef.current = [];

    refreshTrailingLockLine(entryPrice);

    // Sync time scales between the main chart and the volume panel.
    // When data is cleared (pair/timeframe switch), lightweight-charts fires this
    // callback with range=null — passing that through crashes setVisibleLogicalRange.
    chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (!range || range.from == null || range.to == null) return;
      try {
        volumeChart.timeScale().setVisibleLogicalRange(range);
      } catch (err) {
        console.warn('[CHART] Could not sync volume chart zoom:', err);
      }
    });

    // Fetch real history for the initial pair using the persisted chart timeframe.
    loadRealHistoryInBackground(pairLabelRef.current, initialTimeframe, entryPrice);

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
        setConnected?.('market', true);
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
        setConnected?.('market', false);
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
    debugLog(`[CHART] Regenerating candlestick data for ${pairLabel}`);
    switchSymbolRef.current?.(pairPrice);
    connectFreeSourceRef.current?.(pairLabel);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pairLabel, pairPrice]);

  // React to trading-mode changes reported by the portfolio WebSocket
  useEffect(() => {
    if (externalTradingMode) setChartDataSourceMode(externalTradingMode);
  }, [externalTradingMode, setChartDataSourceMode]);

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
  }, [botIsActive, blueBoxOverlay, timeframe, redrawBlueBoxOverlay, redrawTradeFireOverlay]);

  useEffect(() => {
    if (mockDataRef.current.length > 0) {
      pushCandlesToChart(mockDataRef.current);
    }
  }, [entryCandles, pushCandlesToChart]);

  return { timeframe, switchTimeframe, readouts, chartSourceMode, chartHistorySource, chartLiveSource };
}
