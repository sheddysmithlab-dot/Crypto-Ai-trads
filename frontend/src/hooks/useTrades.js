import { useCallback, useEffect, useRef, useState } from 'react';
import { authFetch, backendWsUrl, preferHttpRealtime } from '../config/api';
import { subscribeRtLive } from '../config/rtLive';
import { debugLog } from '../config/debug';

function applyTradesData(data, setters) {
  const { setActivePair, setTrades, setEntryCandles, setPatternNeon, setActiveCount } = setters;
  setActivePair(data.pair);
  setTrades(data.trades);
  setEntryCandles(data.entry_candles || []);
  setPatternNeon(data.pattern_neon || []);
  setActiveCount(data.active_count ?? (data.trades || []).filter((t) => t.status !== 'sold').length);
}

// Live trades — WS normally; on aitrads.in HTTP /rt/live via Hostinger proxy.
export function useTrades(setConnected) {
  const [trades, setTrades] = useState([]);
  const [activeCount, setActiveCount] = useState(0);
  const [activePair, setActivePair] = useState('BTC/USDT');
  const [entryCandles, setEntryCandles] = useState([]);
  const [patternNeon, setPatternNeon] = useState([]);
  const wsRef = useRef(null);
  const reconnectTimer = useRef(null);
  const stopped = useRef(false);

  useEffect(() => {
    stopped.current = false;
    const setters = { setActivePair, setTrades, setEntryCandles, setPatternNeon, setActiveCount };

    if (preferHttpRealtime) {
      return subscribeRtLive((bundle, err) => {
        if (err || !bundle?.trades) {
          setConnected('trades', false);
          return;
        }
        setConnected('trades', true);
        applyTradesData(bundle.trades, setters);
      });
    }

    function connect() {
      if (stopped.current) return;
      const ws = new WebSocket(backendWsUrl('/ws/trades'));
      wsRef.current = ws;

      ws.onopen = () => setConnected('trades', true);

      ws.onmessage = (event) => {
        setConnected('trades', true);
        applyTradesData(JSON.parse(event.data), setters);
      };

      ws.onclose = () => {
        setConnected('trades', false);
        if (stopped.current) return;
        console.warn('Trades WebSocket closed, reconnecting...');
        reconnectTimer.current = setTimeout(connect, 2000);
      };
    }

    connect();
    return () => {
      stopped.current = true;
      clearTimeout(reconnectTimer.current);
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [setConnected]);

  const closeTrade = useCallback(async (id, confirmed = false) => {
    try {
      const res = await authFetch('/close-trade', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id, confirmed }),
      });
      const data = await res.json();
      if (data.status === 'error') {
        console.error('Failed to close trade:', data.message);
        return data;
      }
      debugLog(`Closed position #${id} via backend REST API.`);
      return data;
    } catch (err) {
      console.error('Failed to close trade:', err);
      throw err;
    }
  }, []);

  return { trades, activeCount, activePair, closeTrade, entryCandles, patternNeon };
}
