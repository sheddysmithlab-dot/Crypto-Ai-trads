import { useCallback, useEffect, useRef, useState } from 'react';
import { createChart, CrosshairMode, LineStyle } from 'lightweight-charts';
import { API_BASE, WS_BASE } from '../config/api';
import { debugLog } from '../config/debug';
import { fmtNum, getBinanceSymbol } from '../data/pairs';

// Timeframe -> candle interval in seconds. Drives BOTH historical bucketing
// and live WebSocket tick bucketing so the chart genuinely reacts to the
// selected timeframe (not just a cosmetic label change).
const TIMEFRAME_SECONDS = { '10S': 10, '30S': 30, '1M': 60, '5M': 300, '15M': 900, '1H': 3600, '1D': 86400 };

// Binance's public klines REST endpoint only supports these standard
// granularities - our custom 10S/30S timeframes have no equivalent there,
// so those two always fall back to synthetic data.
const BINANCE_KLINE_INTERVAL = { '1M': '1m', '5M': '5m', '15M': '15m', '1H': '1h', '1D': '1d' };

const MA_PERIODS = [5, 10, 20, 30];
const MA_COLORS = { 5: '#facc15', 10: '#ec4899', 20: '#38bdf8', 30: '#a855f7' };
const VOLUME_MA_PERIOD = 20;
// Default zoom: only the most recent candles are visible, on both the main
// chart and the volume panel (they're time-synced), instead of the whole
// fetched history all at once.
const DEFAULT_VISIBLE_CANDLES = 10;

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

// Pulls REAL historical OHLCV candles from Binance's free public REST API
// (no key needed, CORS-open) so the chart shows genuine price history
// instead of a synthetic random walk. Falls back to generateMockData when
// the pair/timeframe has no Binance equivalent (10S/30S, or an unlisted pair).
async function fetchRealHistory(binanceSymbol, klineInterval, limit = 150) {
  const url = `https://api.binance.com/api/v3/klines?symbol=${binanceSymbol.toUpperCase()}&interval=${klineInterval}&limit=${limit}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const raw = await res.json();
  if (!Array.isArray(raw) || raw.length === 0) throw new Error('Empty klines response');
  return raw.map((k) => ({
    time: Math.floor(k[0] / 1000),
    open: parseFloat(k[1]),
    high: parseFloat(k[2]),
    low: parseFloat(k[3]),
    close: parseFloat(k[4]),
    volume: parseFloat(k[5]),
  }));
}

// Binance has no kline granularity below 1 minute, so for the custom 10S/30S
// timeframes we bucket the most recent REAL trades ourselves instead of
// falling back to fully synthetic data. Covers a few real minutes of actual
// price action (exact span depends on how actively that pair is trading).
async function fetchRealTradesAsCandles(binanceSymbol, intervalSeconds, limit = 1000) {
  const url = `https://api.binance.com/api/v3/aggTrades?symbol=${binanceSymbol.toUpperCase()}&limit=${limit}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const trades = await res.json();
  if (!Array.isArray(trades) || trades.length === 0) throw new Error('Empty trades response');

  const buckets = new Map();
  for (const t of trades) {
    const price = parseFloat(t.p);
    const qty = parseFloat(t.q);
    const bucketTime = Math.floor(t.T / 1000 / intervalSeconds) * intervalSeconds;
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

async function loadHistoricalData(pairLabelArg, tfKey, basePrice) {
  const binanceSymbol = getBinanceSymbol(pairLabelArg);
  if (!binanceSymbol) return generateMockData(basePrice, TIMEFRAME_SECONDS[tfKey] || 3600);

  const klineInterval = BINANCE_KLINE_INTERVAL[tfKey];
  try {
    if (klineInterval) return await fetchRealHistory(binanceSymbol, klineInterval);
    return await fetchRealTradesAsCandles(binanceSymbol, TIMEFRAME_SECONDS[tfKey]);
  } catch (err) {
    console.warn(`[CHART] Failed to fetch real history for ${pairLabelArg} (${tfKey}), using synthetic data:`, err);
  }
  return generateMockData(basePrice, TIMEFRAME_SECONDS[tfKey] || 3600);
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
  timeScale: { borderColor: '#1E2329' },
};

// Candlestick + MA(5,10,20,30) overlay chart with a Volume histogram
// sub-panel, real Binance historical candles + live public feed (paper
// trading), and the backend/Bybit feed (live trading). Defaults to showing
// only the last 10 candles, zoomed in, instead of the whole history at once.
export function useTradingChart({ chartContainerRef, volumeContainerRef, pairLabel, pairPrice, externalTradingMode, setConnected }) {
  const chartRef = useRef(null);
  const volumeChartRef = useRef(null);
  const candleSeriesRef = useRef(null);
  const maSeriesRef = useRef({});
  const volumeSeriesRef = useRef(null);
  const volumeMaSeriesRef = useRef(null);
  const trailingLockLineRef = useRef(null);
  const mockDataRef = useRef([]);
  const entryPriceRef = useRef(pairPrice);
  const currentIntervalRef = useRef(TIMEFRAME_SECONDS['30S']);
  const tradingModeRef = useRef(null);
  const freeSourceWsRef = useRef(null);
  const pairLabelRef = useRef(pairLabel);
  const skipFirstPairEffect = useRef(true);
  // Bumped on every switchSymbol/switchTimeframe call so a slow, superseded
  // real-history fetch can't clobber a newer switch when it finally resolves.
  const loadGenerationRef = useRef(0);
  pairLabelRef.current = pairLabel;

  const [timeframe, setTimeframe] = useState('30S');
  const [readouts, setReadouts] = useState({
    vol: 0,
    volMA: 0,
    lastUpdated: '--:-- UTC',
  });

  const zoomToRecentCandles = useCallback((dataLength) => {
    if (!chartRef.current || dataLength === 0) return;
    const applyZoom = () => {
      chartRef.current?.timeScale().setVisibleLogicalRange({
        from: Math.max(0, dataLength - DEFAULT_VISIBLE_CANDLES),
        to: dataLength,
      });
    };
    // A single call right after setData() can be a no-op if the chart's canvas
    // hasn't been laid out at its final size yet (e.g. on first mount, before
    // fonts/CSS finish settling) - double rAF plus a short delayed re-apply
    // makes the initial zoom land reliably instead of silently falling back
    // to showing the whole dataset zoomed out.
    applyZoom();
    requestAnimationFrame(() => requestAnimationFrame(applyZoom));
    setTimeout(applyZoom, 300);
  }, []);

  const updateReadouts = useCallback((bar, data) => {
    const now = new Date();
    const volSeries = calcVolumeSMA(data.slice(-(VOLUME_MA_PERIOD + 5)), VOLUME_MA_PERIOD);

    setReadouts((prev) => ({
      ...prev,
      vol: bar.volume,
      volMA: volSeries.length ? volSeries[volSeries.length - 1].value : bar.volume,
      lastUpdated: `${String(now.getUTCHours()).padStart(2, '0')}:${String(now.getUTCMinutes()).padStart(2, '0')} UTC`,
    }));
  }, []);

  const applyAllOverlays = useCallback((data) => {
    MA_PERIODS.forEach((period) => {
      maSeriesRef.current[period]?.setData(calcSMA(data, period));
    });
    volumeSeriesRef.current?.setData(toVolumeBars(data));
    volumeMaSeriesRef.current?.setData(calcVolumeSMA(data, VOLUME_MA_PERIOD));
    candleSeriesRef.current?.setMarkers(computeExtremeMarkers(data));
  }, []);

  // Pushes a full dataset (synthetic or real) into every series + the readouts.
  const applyDataset = useCallback(
    (data, { zoomToRecent = false } = {}) => {
      mockDataRef.current = data;
      candleSeriesRef.current?.setData(data);
      applyAllOverlays(data);
      if (zoomToRecent) zoomToRecentCandles(data.length);
      updateReadouts(data[data.length - 1], data);
    },
    [updateReadouts, applyAllOverlays, zoomToRecentCandles]
  );

  // Kicks off the async real-history fetch and swaps it in once ready, unless
  // a newer switchSymbol/switchTimeframe call has already superseded this one.
  const loadRealHistoryInBackground = useCallback(
    (pairLabelArg, tfKey, basePrice) => {
      const myGeneration = ++loadGenerationRef.current;
      loadHistoricalData(pairLabelArg, tfKey, basePrice).then((data) => {
        if (myGeneration !== loadGenerationRef.current) return; // superseded - drop it
        applyDataset(data, { zoomToRecent: true });
      });
    },
    [applyDataset]
  );

  const disconnectFreeSource = useCallback(() => {
    if (freeSourceWsRef.current) {
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

      const bucketTime = Math.floor(Date.now() / 1000 / currentIntervalRef.current) * currentIntervalRef.current;

      let updated;
      if (bucketTime > lastCandle.time) {
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

      candleSeriesRef.current.update(updated);
      updateReadouts(updated, mockData);
      applyAllOverlays(mockData);
    },
    [updateReadouts, applyAllOverlays]
  );

  const connectFreeSource = useCallback(
    (pairLabelArg) => {
      disconnectFreeSource();
      const binanceSymbol = getBinanceSymbol(pairLabelArg);
      if (!binanceSymbol) {
        console.warn(`[FREE SOURCE] No free public feed mapped for ${pairLabelArg}; falling back to backend simulated feed.`);
        return;
      }

      const ws = new WebSocket(`wss://stream.binance.com:9443/ws/${binanceSymbol}@trade`);
      freeSourceWsRef.current = ws;

      ws.onopen = () => {
        debugLog(`[FREE SOURCE] Connected to Binance public feed for ${pairLabelArg} (auto-selected, no API key needed).`);
      };

      ws.onmessage = (event) => {
        if (tradingModeRef.current !== 'PAPER_TRADING') return; // stale message after switching to live
        try {
          const data = JSON.parse(event.data);
          const price = parseFloat(data.p);
          if (!isNaN(price)) applyLivePriceTick(price);
        } catch (err) {
          console.error('[FREE SOURCE] Error parsing price data:', err);
        }
      };

      ws.onerror = (error) => {
        console.error(`[FREE SOURCE] Binance WebSocket error for ${pairLabelArg}:`, error);
        console.warn('[FREE SOURCE] Free API connection failed. Using backend mock data instead.');
      };

      ws.onclose = () => {
        console.warn(`[FREE SOURCE] Binance WebSocket closed for ${pairLabelArg}. Will retry in 2s...`);
        if (tradingModeRef.current === 'PAPER_TRADING') {
          setTimeout(() => connectFreeSource(pairLabelArg), 2000);
        }
      };
    },
    [applyLivePriceTick, disconnectFreeSource]
  );

  const setChartDataSourceMode = useCallback(
    (mode) => {
      if (mode === tradingModeRef.current) return;
      tradingModeRef.current = mode;

      if (mode === 'LIVE_TRADING') {
        debugLog('[CHART SOURCE] Bybit connected -> free source stopped, chart now follows Backend/Bybit feed.');
        disconnectFreeSource();
      } else {
        debugLog('[CHART SOURCE] Paper Trading -> switching chart to free public crypto feed.');
        connectFreeSource(pairLabelRef.current);
      }
    },
    [connectFreeSource, disconnectFreeSource]
  );

  const switchSymbol = useCallback(
    (basePrice) => {
      entryPriceRef.current = basePrice;
      // Instant synthetic placeholder so the chart never sits blank...
      applyDataset(generateMockData(basePrice, currentIntervalRef.current), { zoomToRecent: true });
      trailingLockLineRef.current.applyOptions({ price: basePrice * 1.0008, title: 'Lock +0.08', color: '#3b82f6' });
      // ...then swap in real Binance history for this pair once it arrives.
      loadRealHistoryInBackground(pairLabelRef.current, timeframe, basePrice);
    },
    [applyDataset, loadRealHistoryInBackground, timeframe]
  );

  const switchTimeframe = useCallback(
    (tf) => {
      setTimeframe(tf);
      currentIntervalRef.current = TIMEFRAME_SECONDS[tf] || 3600;
      // Instant synthetic placeholder, then swap in real history for the new timeframe.
      applyDataset(generateMockData(entryPriceRef.current, currentIntervalRef.current), { zoomToRecent: true });
      loadRealHistoryInBackground(pairLabelRef.current, tf, entryPriceRef.current);

      // RULE 2: Dynamic Timeframe Syncing - tell the backend AI Agent to read
      // volume/price data on this exact interval from now on.
      fetch(`${API_BASE}/set-timeframe`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ seconds: currentIntervalRef.current }),
      }).catch((err) => console.error('Failed to sync timeframe with backend:', err));
    },
    [applyDataset, loadRealHistoryInBackground]
  );

  // Init chart once on mount
  useEffect(() => {
    const chartContainer = chartContainerRef.current;
    const volumeContainer = volumeContainerRef.current;
    if (!chartContainer || !volumeContainer) return;

    const chart = createChart(chartContainer, {
      width: chartContainer.clientWidth,
      height: chartContainer.clientHeight,
      timeScale: { ...darkThemeConfig.timeScale, visible: false },
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
      ...darkThemeConfig,
    });
    volumeChartRef.current = volumeChart;
    const volumeSeries = volumeChart.addHistogramSeries({ priceFormat: { type: 'volume' }, lastValueVisible: false });
    volumeSeriesRef.current = volumeSeries;
    const volumeMaSeries = volumeChart.addLineSeries({ color: '#f59e0b', lineWidth: 1.5, lastValueVisible: false });
    volumeMaSeriesRef.current = volumeMaSeries;

    const entryPrice = entryPriceRef.current;
    // Instant synthetic placeholder so something renders immediately...
    const placeholderData = generateMockData(entryPrice, currentIntervalRef.current);
    mockDataRef.current = placeholderData;
    candleSeries.setData(placeholderData);
    applyAllOverlays(placeholderData);

    trailingLockLineRef.current = candleSeries.createPriceLine({
      price: entryPrice * 1.0008,
      color: '#3b82f6',
      lineWidth: 2,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: true,
      title: 'Lock +0.08',
    });

    // Sync time scales between the main chart and the volume panel
    chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      volumeChart.timeScale().setVisibleLogicalRange(range);
    });

    zoomToRecentCandles(placeholderData.length);
    updateReadouts(placeholderData[placeholderData.length - 1], placeholderData);

    // ...then swap in real Binance history for the initial pair/timeframe once it arrives
    // (30S has no Binance kline equivalent, so this stays on synthetic data until the user
    // picks a timeframe Binance actually supports).
    loadRealHistoryInBackground(pairLabelRef.current, '30S', entryPrice);

    // RULE 2: Sync the default timeframe to the backend on load too, not just on every
    // subsequent switchTimeframe click, so a fresh page load and a fresh backend start
    // agree on the candle interval from the very first tick.
    fetch(`${API_BASE}/set-timeframe`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ seconds: currentIntervalRef.current }),
    }).catch((err) => console.error('Failed to sync initial timeframe with backend:', err));

    // Live crosshair OHLC readout (hover to inspect any candle). lightweight-charts'
    // own seriesData only carries {time,open,high,low,close} - it strips our custom
    // `volume` field - so look the full bar up in mockDataRef.current by time instead
    // of using param.seriesData.get() directly (that was producing "NaN" volume).
    chart.subscribeCrosshairMove((param) => {
      if (!param.time) return;
      const fullBar = mockDataRef.current.find((d) => d.time === param.time);
      if (fullBar) updateReadouts(fullBar, mockDataRef.current);
    });

    const handleResize = () => {
      chart.applyOptions({ width: chartContainer.clientWidth, height: chartContainer.clientHeight });
      volumeChart.applyOptions({ width: volumeContainer.clientWidth, height: volumeContainer.clientHeight });
    };
    window.addEventListener('resize', handleResize);

    // Live Price Feed Wire (Real WebSocket Connection to Python AI / Bybit backend)
    let marketWs;
    let marketReconnectTimer;
    function connectMarketWS() {
      const ws = new WebSocket(`${WS_BASE}/ws/market`);
      marketWs = ws;

      ws.onopen = () => {
        debugLog('[BACKEND WS] Connected to backend market feed');
        setConnected?.('market', true);
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);

          if (data.trading_mode) setChartDataSourceMode(data.trading_mode);

          if (tradingModeRef.current === 'LIVE_TRADING') {
            applyLivePriceTick(data.price);
          }

          if (data.lock_active) {
            trailingLockLineRef.current.applyOptions({
              price: entryPriceRef.current + entryPriceRef.current * (data.peak_pct / 100),
              title: `Active Lock Peak (+${data.peak_pct.toFixed(2)}%)`,
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

    // Determine the initial chart data source (Paper -> free feed, Live -> backend/Bybit)
    fetch(`${API_BASE}/trading-mode`)
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
      if (marketWs) {
        marketWs.onclose = null;
        marketWs.close();
      }
      disconnectFreeSource();
      chart.remove();
      volumeChart.remove();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // React to pair changes (skip the very first run since init already used the initial price)
  useEffect(() => {
    if (skipFirstPairEffect.current) {
      skipFirstPairEffect.current = false;
      return;
    }
    if (!candleSeriesRef.current) return;
    debugLog(`[CHART] Regenerating candlestick data for ${pairLabel}`);
    switchSymbol(pairPrice);
    if (tradingModeRef.current === 'PAPER_TRADING') {
      debugLog(`[FREE SOURCE] Re-subscribing to Binance feed for ${pairLabel}`);
      connectFreeSource(pairLabel);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pairLabel, pairPrice]);

  // React to trading-mode changes reported by the portfolio WebSocket
  useEffect(() => {
    if (externalTradingMode) setChartDataSourceMode(externalTradingMode);
  }, [externalTradingMode, setChartDataSourceMode]);

  return { timeframe, switchTimeframe, readouts };
}
