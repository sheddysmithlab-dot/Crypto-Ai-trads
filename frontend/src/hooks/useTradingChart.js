import { useCallback, useEffect, useRef, useState } from 'react';
import { createChart, CrosshairMode, LineStyle } from 'lightweight-charts';
import { API_BASE, WS_BASE } from '../config/api';
import { debugLog } from '../config/debug';
import { fmtNum, getBinanceSymbol } from '../data/pairs';

// Timeframe -> candle interval in seconds. Drives BOTH historical bucketing
// and live WebSocket tick bucketing so the chart genuinely reacts to the
// selected timeframe (not just a cosmetic label change).
const TIMEFRAME_SECONDS = { '10S': 10, '30S': 30, '1M': 60, '5M': 300, '15M': 900, '1H': 3600, '1D': 86400 };

const MA_PERIODS = [5, 10, 20, 30];
const MA_COLORS = { 5: '#facc15', 10: '#ec4899', 20: '#38bdf8', 30: '#a855f7' };
const VOLUME_MA_PERIOD = 20;

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

function calcRSI(data, period = 14) {
  let gains = 0,
    losses = 0;
  const result = [];
  for (let i = 1; i < data.length; i++) {
    const change = data[i].close - data[i - 1].close;
    const gain = Math.max(change, 0);
    const loss = Math.max(-change, 0);

    if (i <= period) {
      gains += gain;
      losses += loss;
      if (i === period) {
        const avgGain = gains / period;
        const avgLoss = losses / period;
        const rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
        const rsi = avgLoss === 0 ? 100 : 100 - 100 / (1 + rs);
        result.push({ time: data[i].time, value: rsi });
      }
    } else {
      const prevAvgGain = gains / period;
      const prevAvgLoss = losses / period;
      const avgGain = (prevAvgGain * (period - 1) + gain) / period;
      const avgLoss = (prevAvgLoss * (period - 1) + loss) / period;
      gains = avgGain * period;
      losses = avgLoss * period;
      const rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
      const rsi = avgLoss === 0 ? 100 : 100 - 100 / (1 + rs);
      result.push({ time: data[i].time, value: rsi });
    }
  }
  return result;
}

const darkThemeConfig = {
  layout: { background: { type: 'solid', color: '#161A1E' }, textColor: '#9ca3af' },
  grid: { vertLines: { color: '#1E2329' }, horzLines: { color: '#1E2329' } },
  crosshair: { mode: CrosshairMode.Normal },
  rightPriceScale: { borderColor: '#1E2329' },
  timeScale: { borderColor: '#1E2329' },
};

// Candlestick + MA(5,10,20,30) overlay chart, Volume histogram sub-panel,
// RSI sub-panel, live OHLC/Change/Range readouts, the free Binance public
// feed (paper trading), and the backend/Bybit feed (live trading).
export function useTradingChart({
  chartContainerRef,
  volumeContainerRef,
  rsiContainerRef,
  pairLabel,
  pairPrice,
  externalTradingMode,
  setConnected,
}) {
  const chartRef = useRef(null);
  const volumeChartRef = useRef(null);
  const rsiChartRef = useRef(null);
  const candleSeriesRef = useRef(null);
  const maSeriesRef = useRef({});
  const volumeSeriesRef = useRef(null);
  const volumeMaSeriesRef = useRef(null);
  const rsiSeriesRef = useRef(null);
  const trailingLockLineRef = useRef(null);
  const mockDataRef = useRef([]);
  const entryPriceRef = useRef(pairPrice);
  const currentIntervalRef = useRef(TIMEFRAME_SECONDS['1H']);
  const tradingModeRef = useRef(null);
  const freeSourceWsRef = useRef(null);
  const pairLabelRef = useRef(pairLabel);
  const skipFirstPairEffect = useRef(true);
  pairLabelRef.current = pairLabel;

  const [timeframe, setTimeframe] = useState('1H');
  const [readouts, setReadouts] = useState({
    open: pairPrice,
    high: pairPrice,
    low: pairPrice,
    close: pairPrice,
    changeAbs: 0,
    changePct: 0,
    rangeAbs: 0,
    rangePct: 0,
    isUp: true,
    ma5: pairPrice,
    ma10: pairPrice,
    ma20: pairPrice,
    ma30: pairPrice,
    vol: 0,
    volMA: 0,
    rsi: 50,
    lastUpdated: '--:-- UTC',
    label: `Candlestick · 1H · ${pairLabel}`,
  });

  const updateReadouts = useCallback((bar, data) => {
    const changeAbs = bar.close - bar.open;
    const changePct = (changeAbs / bar.open) * 100;
    const rangeAbs = bar.high - bar.low;
    const rangePct = (rangeAbs / bar.low) * 100;
    const now = new Date();

    const maValues = {};
    MA_PERIODS.forEach((period) => {
      const series = calcSMA(data.slice(-(period + 5)), period);
      maValues[`ma${period}`] = series.length ? series[series.length - 1].value : bar.close;
    });

    const volSeries = calcVolumeSMA(data.slice(-(VOLUME_MA_PERIOD + 5)), VOLUME_MA_PERIOD);

    setReadouts((prev) => ({
      ...prev,
      open: bar.open,
      high: bar.high,
      low: bar.low,
      close: bar.close,
      changeAbs,
      changePct,
      rangeAbs,
      rangePct,
      isUp: bar.close >= bar.open,
      ...maValues,
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

      const rsiTail = calcRSI(mockData.slice(-30), 14);
      if (rsiTail.length) {
        rsiSeriesRef.current.update(rsiTail[rsiTail.length - 1]);
        setReadouts((prev) => ({ ...prev, rsi: rsiTail[rsiTail.length - 1].value }));
      }
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
      const mockData = generateMockData(basePrice, currentIntervalRef.current);
      mockDataRef.current = mockData;

      candleSeriesRef.current.setData(mockData);
      applyAllOverlays(mockData);

      const rsiData = calcRSI(mockData, 14);
      rsiSeriesRef.current.setData(rsiData);

      trailingLockLineRef.current.applyOptions({
        price: basePrice * 1.0008,
        title: 'Lock +0.08',
        color: '#3b82f6',
      });

      updateReadouts(mockData[mockData.length - 1], mockData);
      if (rsiData.length) {
        setReadouts((prev) => ({ ...prev, rsi: rsiData[rsiData.length - 1].value }));
      }
    },
    [updateReadouts, applyAllOverlays]
  );

  const switchTimeframe = useCallback(
    (tf) => {
      setTimeframe(tf);
      currentIntervalRef.current = TIMEFRAME_SECONDS[tf] || 3600;
      const mockData = generateMockData(entryPriceRef.current, currentIntervalRef.current);
      mockDataRef.current = mockData;

      candleSeriesRef.current.setData(mockData);
      applyAllOverlays(mockData);

      const rsiData = calcRSI(mockData, 14);
      rsiSeriesRef.current.setData(rsiData);

      chartRef.current.timeScale().fitContent();
      updateReadouts(mockData[mockData.length - 1], mockData);
      setReadouts((prev) => ({
        ...prev,
        rsi: rsiData.length ? rsiData[rsiData.length - 1].value : prev.rsi,
        label: `Candlestick · ${tf} · ${pairLabelRef.current}`,
      }));

      // RULE 2: Dynamic Timeframe Syncing - tell the backend AI Agent to read
      // volume/price data on this exact interval from now on.
      fetch(`${API_BASE}/set-timeframe`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ seconds: currentIntervalRef.current }),
      }).catch((err) => console.error('Failed to sync timeframe with backend:', err));
    },
    [updateReadouts, applyAllOverlays]
  );

  // Init chart once on mount
  useEffect(() => {
    const chartContainer = chartContainerRef.current;
    const volumeContainer = volumeContainerRef.current;
    const rsiContainer = rsiContainerRef.current;
    if (!chartContainer || !volumeContainer || !rsiContainer) return;

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
    const volumeSeries = volumeChart.addHistogramSeries({ priceFormat: { type: 'volume' } });
    volumeSeriesRef.current = volumeSeries;
    const volumeMaSeries = volumeChart.addLineSeries({ color: '#f59e0b', lineWidth: 1.5, lastValueVisible: false });
    volumeMaSeriesRef.current = volumeMaSeries;

    // RSI Sub-panel Chart (separate synced chart instance)
    const rsiChart = createChart(rsiContainer, {
      width: rsiContainer.clientWidth,
      height: rsiContainer.clientHeight,
      ...darkThemeConfig,
    });
    rsiChartRef.current = rsiChart;

    const rsiSeries = rsiChart.addLineSeries({ color: '#a855f7', lineWidth: 2 });
    rsiSeriesRef.current = rsiSeries;
    rsiChart.addLineSeries({ color: '#4b5563', lineWidth: 1, lineStyle: LineStyle.Dashed }).setData([]);

    const entryPrice = entryPriceRef.current;
    const mockData = generateMockData(entryPrice, currentIntervalRef.current);
    mockDataRef.current = mockData;
    candleSeries.setData(mockData);
    applyAllOverlays(mockData);

    const rsiData = calcRSI(mockData, 14);
    rsiSeries.setData(rsiData);
    rsiSeries.createPriceLine({ price: 70, color: '#4b5563', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: '70' });
    rsiSeries.createPriceLine({ price: 30, color: '#4b5563', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: '30' });

    trailingLockLineRef.current = candleSeries.createPriceLine({
      price: entryPrice * 1.0008,
      color: '#3b82f6',
      lineWidth: 2,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: true,
      title: 'Lock +0.08',
    });

    // Sync time scales between the main chart, volume panel, and RSI panel
    chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      volumeChart.timeScale().setVisibleLogicalRange(range);
      rsiChart.timeScale().setVisibleLogicalRange(range);
    });

    updateReadouts(mockData[mockData.length - 1], mockData);
    if (rsiData.length) setReadouts((prev) => ({ ...prev, rsi: rsiData[rsiData.length - 1].value }));

    // Live crosshair OHLC readout (hover to inspect any candle). Reads mockDataRef.current
    // (not the local mockData variable) so it stays correct after switchSymbol/switchTimeframe
    // replace the underlying array with a new one.
    chart.subscribeCrosshairMove((param) => {
      if (!param.time) return;
      const bar = param.seriesData.get(candleSeries);
      if (bar) updateReadouts(bar, mockDataRef.current);
    });

    const handleResize = () => {
      chart.applyOptions({ width: chartContainer.clientWidth, height: chartContainer.clientHeight });
      volumeChart.applyOptions({ width: volumeContainer.clientWidth, height: volumeContainer.clientHeight });
      rsiChart.applyOptions({ width: rsiContainer.clientWidth, height: rsiContainer.clientHeight });
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
      rsiChart.remove();
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
    setReadouts((prev) => ({ ...prev, label: `Candlestick · ${timeframe} · ${pairLabel}` }));
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
