import { useCallback, useEffect, useRef, useState } from 'react';
import { API_BASE, WS_BASE } from '../config/api';
import { debugLog } from '../config/debug';

// Live trades - populated exclusively from the backend /ws/trades feed.
// No dummy/mock trades; the table only ever reflects the backend AI Agent's
// real-time state for the single active trading pair (multiple stacked trades allowed).
export function useTrades(setConnected) {
  const [trades, setTrades] = useState([]);
  const [activePair, setActivePair] = useState('BTC/USDT');
  const wsRef = useRef(null);
  const reconnectTimer = useRef(null);

  useEffect(() => {
    function connect() {
      const ws = new WebSocket(`${WS_BASE}/ws/trades`);
      wsRef.current = ws;

      ws.onopen = () => setConnected('trades', true);

      ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        setActivePair(data.pair);
        setTrades(data.trades);
      };

      ws.onclose = () => {
        setConnected('trades', false);
        console.warn('Trades WebSocket closed, reconnecting...');
        reconnectTimer.current = setTimeout(connect, 2000);
      };
    }

    connect();
    return () => {
      clearTimeout(reconnectTimer.current);
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
      }
    };
  }, [setConnected]);

  const closeTrade = useCallback(async (id) => {
    try {
      await fetch(`${API_BASE}/close-trade`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id }),
      });
      debugLog(`Closed position #${id} via backend REST API.`);
    } catch (err) {
      console.error('Failed to close trade:', err);
    }
    // Next /ws/trades tick will refresh the table with the authoritative state
  }, []);

  const clearTrades = useCallback(() => setTrades([]), []);

  return { trades, activePair, closeTrade, clearTrades };
}
