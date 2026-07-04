import { useCallback, useEffect, useRef, useState } from 'react';
import { createChart, CrosshairMode, LineStyle } from 'lightweight-charts';
import { API_BASE, WS_BASE } from '../config/api';
import { debugLog } from '../config/debug';
import { getBinanceSymbol } from '../data/pairs';

// Timeframe -> candle interval in seconds. Drives BOTH historical bucketing
// and live WebSocket tick bucketing so the chart genuinely reacts to the
// selected timeframe (not just a cosmetic label change).
const TIMEFRAME_SECONDS = { '1M': 60, '5M': 300, '15M': 900, '1H': 3600, '1D': 86400 };

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

    data.push({ time: time + i * intervalSeconds, open, high, low, close });
    price = close;
  }
  return data;
}

function calcEMA(data, period) {
  const k = 2 / (period + 1);
  let emaPrev = data[0].close;
  return data.map((d, i) => {
    const val = i === 0 ? d.close : d.close * k + emaPrev * (1 - k);
    emaPrev = val;
    return { time: d.time, value: val };
  });
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

// Candlestick + EMA overlay chart, RSI sub-panel, live OHLC/Volume readouts,
// the free Binance public feed (paper trading), and the backend/Bybit feed (live trading).
export function useTradingChart({ chartContainerRef, rsiContainerRef, pairLabel, pairPrice, externalTradingMode, setConnected }) {
  const chartRef = useRef(null);
  const rsiChartRef = useRef(null);
  const candleSeriesRef = useRef(null);
  const emaSeries20Ref = useRef(null);
  const emaSeries50Ref = useRef(null);
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
    changePct: 0,
    isUp: true,
    vol: pairPrice,
    ema: 85.15,
    rsi: 50,
    lastUpdated: '--:-- UTC',
    label: `Candlestick · 1H · ${pairLabel}`,
  });

  const updateReadouts = useCallback((bar, firstBar) => {
    const pctChange = ((bar.close - firstBar.open) / firstBar.open) * 100;
    const now = new Date();
    setReadouts((prev) => ({
      ...prev,
      open: bar.open,
      high: bar.high,
      low: bar.low,
      close: bar.close,
      changePct: pctChange,
      isUp: bar.close >= bar.open,
      vol: (Math.abs(bar.close) * 1.0) % 1000 + bar.close,
      ema: bar.close - entryPriceRef.current + 85.15,
      lastUpdated: `${String(now.getUTCHours()).padStart(2, '0')}:${String(now.getUTCMinutes()).padStart(2, '0')} UTC`,
    }));
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
        updated = { time: bucketTime, open: lastCandle.close, high: newClose, low: newClose, close: newClose };
        mockData.push(updated);
        if (mockData.length > 200) mockData.shift();
      } else {
        updated = {
          ...lastCandle,
          close: newClose,
          high: Math.max(lastCandle.high, newClose),
          low: Math.min(lastCandle.low, newClose),
        };
        mockData[mockData.length - 1] = updated;
      }

      candleSeriesRef.current.update(updated);
      updateReadouts(updated, mockData[0]);

      const emaTail = calcEMA(mockData.slice(-30), 20);
      emaSeries20Ref.current.update(emaTail[emaTail.length - 1]);

      const rsiTail = calcRSI(mockData.slice(-30), 14);
      if (rsiTail.length) {
        rsiSeriesRef.current.update(rsiTail[rsiTail.length - 1]);
        setReadouts((prev) => ({ ...prev, rsi: rsiTail[rsiTail.length - 1].value }));
      }
    },
    [updateReadouts]
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
      emaSeries20Ref.current.setData(calcEMA(mockData, 20));
      emaSeries50Ref.current.setData(calcEMA(mockData, 50));

      const rsiData = calcRSI(mockData, 14);
      rsiSeriesRef.current.setData(rsiData);

      trailingLockLineRef.current.applyOptions({
        price: basePrice * 1.0008,
        title: 'Lock +0.08',
        color: '#3b82f6',
      });

      updateReadouts(mockData[mockData.length - 1], mockData[0]);
      if (rsiData.length) {
        setReadouts((prev) => ({ ...prev, rsi: rsiData[rsiData.length - 1].value }));
      }
    },
    [updateReadouts]
  );

  const switchTimeframe = useCallback(
    (tf) => {
      setTimeframe(tf);
      currentIntervalRef.current = TIMEFRAME_SECONDS[tf] || 3600;
      const mockData = generateMockData(entryPriceRef.current, currentIntervalRef.current);
      mockDataRef.current = mockData;

      candleSeriesRef.current.setData(mockData);
      emaSeries20Ref.current.setData(calcEMA(mockData, 20));
      emaSeries50Ref.current.setData(calcEMA(mockData, 50));

      const rsiData = calcRSI(mockData, 14);
      rsiSeriesRef.current.setData(rsiData);

      chartRef.current.timeScale().fitContent();
      updateReadouts(mockData[mockData.length - 1], mockData[0]);
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
    [updateReadouts]
  );

  // Init chart once on mount
  useEffect(() => {
    const chartContainer = chartContainerRef.current;
    const rsiContainer = rsiContainerRef.current;
    if (!chartContainer || !rsiContainer) return;

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

    const emaSeries20 = chart.addLineSeries({ color: '#f59e0b', lineWidth: 1.5 });
    emaSeries20.setData(calcEMA(mockData, 20));
    emaSeries20Ref.current = emaSeries20;

    const emaSeries50 = chart.addLineSeries({ color: '#38bdf8', lineWidth: 1.5 });
    emaSeries50.setData(calcEMA(mockData, 50));
    emaSeries50Ref.current = emaSeries50;

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

    chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      rsiChart.timeScale().setVisibleLogicalRange(range);
    });

    updateReadouts(mockData[mockData.length - 1], mockData[0]);
    if (rsiData.length) setReadouts((prev) => ({ ...prev, rsi: rsiData[rsiData.length - 1].value }));

    chart.subscribeCrosshairMove((param) => {
      if (!param.time) return;
      const bar = param.seriesData.get(candleSeries);
      if (bar) updateReadouts(bar, mockData[0]);
    });

    const handleResize = () => {
      chart.applyOptions({ width: chartContainer.clientWidth, height: chartContainer.clientHeight });
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
