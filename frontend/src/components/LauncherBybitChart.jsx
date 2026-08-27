import { useEffect, useRef, useState } from 'react';
import { createChart, CrosshairMode } from 'lightweight-charts';
import {
  BYBIT_PUBLIC_WS_LINEAR,
  bybitPublicKlineTopic,
  fetchBybitDayKlines,
  barsForOneDay,
} from '../config/bybitPublic';
import { BYBIT_SYMBOL_MAP, fmtNum, getBybitSymbol } from '../data/pairs';
import { sanitizeCandleData } from '../utils/chartCandles';

const TF_INTERVAL = { '1M': '1', '5M': '5', '15M': '15', '1H': '60', '1D': 'D' };
const TF_SECONDS = { '1M': 60, '5M': 300, '15M': 900, '1H': 3600, '1D': 86400 };
const VISIBLE_TAIL = 60;

/**
 * Compact Bybit linear candlestick chart for the trade launcher.
 * On every symbol/TF open: fetch ~1 full day of klines, then live WS updates.
 */
export default function LauncherBybitChart({ symbol, timeframe }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const [status, setStatus] = useState('Loading…');
  const [lastPrice, setLastPrice] = useState(null);
  const [live, setLive] = useState(false);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return undefined;

    const bybitSymbol = getBybitSymbol(symbol) || BYBIT_SYMBOL_MAP[symbol];
    const interval = TF_INTERVAL[timeframe] || '1';
    const intervalSecs = TF_SECONDS[timeframe] || 60;
    let disposed = false;
    let ws = null;
    let ro = null;

    const chart = createChart(el, {
      layout: {
        background: { color: '#050806' },
        textColor: '#6ee7b7',
        fontSize: 10,
      },
      grid: {
        vertLines: { color: 'rgba(16,185,129,0.08)' },
        horzLines: { color: 'rgba(16,185,129,0.08)' },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: 'rgba(16,185,129,0.25)' },
      timeScale: {
        borderColor: 'rgba(16,185,129,0.25)',
        timeVisible: true,
        secondsVisible: false,
      },
      width: el.clientWidth,
      height: el.clientHeight || 260,
    });
    const series = chart.addCandlestickSeries({
      upColor: '#10b981',
      downColor: '#ef4444',
      borderUpColor: '#10b981',
      borderDownColor: '#ef4444',
      wickUpColor: '#34d399',
      wickDownColor: '#f87171',
    });
    chartRef.current = chart;
    seriesRef.current = series;

    ro = new ResizeObserver(() => {
      if (!containerRef.current || !chartRef.current) return;
      chartRef.current.applyOptions({
        width: containerRef.current.clientWidth,
        height: containerRef.current.clientHeight,
      });
    });
    ro.observe(el);

    async function boot() {
      if (!bybitSymbol) {
        setStatus('No Bybit linear market');
        setLive(false);
        return;
      }
      const dayBars = barsForOneDay(intervalSecs, timeframe);
      setStatus(`Loading ${bybitSymbol} · ~1 day (${dayBars} bars)…`);
      setLive(false);
      try {
        const raw = await fetchBybitDayKlines(bybitSymbol, interval, intervalSecs, timeframe);
        if (disposed) return;
        const data = sanitizeCandleData(raw, intervalSecs);
        series.setData(data.map(({ time, open, high, low, close }) => ({ time, open, high, low, close })));
        const n = data.length;
        try {
          chart.timeScale().setVisibleLogicalRange({
            from: Math.max(0, n - VISIBLE_TAIL),
            to: n,
          });
        } catch {
          chart.timeScale().fitContent();
        }
        const last = data[n - 1];
        if (last) setLastPrice(last.close);
        setStatus(`Bybit ${bybitSymbol} · ${timeframe} · ${n} bars (~1d)`);
        setLive(true);

        ws = new WebSocket(BYBIT_PUBLIC_WS_LINEAR);
        ws.onopen = () => {
          ws.send(JSON.stringify({ op: 'subscribe', args: [bybitPublicKlineTopic(bybitSymbol, interval)] }));
        };
        ws.onmessage = (ev) => {
          try {
            const msg = JSON.parse(ev.data);
            const row = msg?.data?.[0] || msg?.data;
            if (!row || msg?.topic !== bybitPublicKlineTopic(bybitSymbol, interval)) return;
            const bar = {
              time: Math.floor(Number(row.start) / 1000),
              open: parseFloat(row.open),
              high: parseFloat(row.high),
              low: parseFloat(row.low),
              close: parseFloat(row.close),
            };
            if (![bar.open, bar.high, bar.low, bar.close].every(Number.isFinite)) return;
            series.update(bar);
            setLastPrice(bar.close);
          } catch {
            /* ignore bad frames */
          }
        };
        ws.onerror = () => setLive(false);
        ws.onclose = () => setLive(false);
      } catch (err) {
        if (!disposed) {
          setStatus(`Bybit error: ${err.message || 'failed'}`);
          setLive(false);
        }
      }
    }

    boot();

    return () => {
      disposed = true;
      try {
        ws?.close();
      } catch {
        /* ignore */
      }
      ro?.disconnect();
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, [symbol, timeframe]);

  return (
    <div className="flex flex-col min-h-0 flex-1 border-t border-emerald-500/30">
      <div className="flex items-center justify-between gap-2 px-3 py-1.5 text-[10px] font-mono text-emerald-400/90">
        <span className="truncate flex items-center gap-1.5">
          <span
            className={`inline-block w-1.5 h-1.5 rounded-full ${live ? 'bg-emerald-400 animate-pulse' : 'bg-gray-500'}`}
          />
          {status}
        </span>
        {lastPrice != null ? (
          <span className="font-bold text-emerald-200 tabular-nums shrink-0">${fmtNum(lastPrice)}</span>
        ) : null}
      </div>
      <div ref={containerRef} className="w-full h-[min(42vh,320px)] min-h-[220px]" />
    </div>
  );
}
