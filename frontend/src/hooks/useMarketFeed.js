import { useEffect, useRef } from 'react';
import { backendWsUrl } from '../config/api';

/**
 * App-level /ws/market connection for API-status health.
 * Chart still opens its own market socket for lock/price overlays; this pipe
 * keeps the header badge green even if the chart effect remounts.
 */
export function useMarketFeed(setConnected) {
  const wsRef = useRef(null);
  const reconnectTimer = useRef(null);
  const stopped = useRef(false);

  useEffect(() => {
    stopped.current = false;

    function connect() {
      if (stopped.current) return;
      const ws = new WebSocket(backendWsUrl('/ws/market'));
      wsRef.current = ws;

      ws.onopen = () => setConnected?.('market', true);

      ws.onmessage = () => {
        // Presence heartbeat only — chart owns price/lock rendering.
        setConnected?.('market', true);
      };

      ws.onerror = () => {
        // onclose follows; avoid double reconnect
      };

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
