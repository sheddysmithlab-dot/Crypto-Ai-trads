import { useEffect, useRef } from 'react';
import { backendWsUrl, preferHttpRealtime } from '../config/api';
import { subscribeRtLive } from '../config/rtLive';

/**
 * App-level market pipe for API-status health.
 * aitrads.in uses shared /rt/live HTTP poll; elsewhere WebSocket.
 */
export function useMarketFeed(setConnected) {
  const wsRef = useRef(null);
  const reconnectTimer = useRef(null);
  const stopped = useRef(false);

  useEffect(() => {
    stopped.current = false;

    if (preferHttpRealtime) {
      return subscribeRtLive((bundle, err) => {
        setConnected?.('market', Boolean(!err && bundle?.market));
      });
    }

    function connect() {
      if (stopped.current) return;
      const ws = new WebSocket(backendWsUrl('/ws/market'));
      wsRef.current = ws;

      ws.onopen = () => setConnected?.('market', true);

      ws.onmessage = () => {
        setConnected?.('market', true);
      };

      ws.onerror = () => {};

      ws.onclose = () => {
        setConnected?.('market', false);
        if (stopped.current) return;
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
}
