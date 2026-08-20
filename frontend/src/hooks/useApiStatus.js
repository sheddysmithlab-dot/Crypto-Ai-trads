import { useCallback, useMemo, useRef, useState } from 'react';

// Tracks all 3 real-time WebSocket pipes (market, portfolio, trades) so the
// header badge reflects the true combined connection state.
// Brief reconnect blips (< DISCONNECT_GRACE_MS) do not flip the badge to
// RECONNECTING — that was the main false "RECONNECTING" flash on Hostinger.
const DISCONNECT_GRACE_MS = 4000;

export function useApiStatus() {
  const [state, setState] = useState({ market: false, portfolio: false, trades: false });
  const pendingFalse = useRef({});

  const setConnected = useCallback((name, connected) => {
    if (connected) {
      if (pendingFalse.current[name]) {
        clearTimeout(pendingFalse.current[name]);
        delete pendingFalse.current[name];
      }
      setState((prev) => (prev[name] ? prev : { ...prev, [name]: true }));
      return;
    }

    // Delay marking disconnected so short reconnect cycles stay green.
    if (pendingFalse.current[name]) return;
    pendingFalse.current[name] = setTimeout(() => {
      delete pendingFalse.current[name];
      setState((prev) => (prev[name] ? { ...prev, [name]: false } : prev));
    }, DISCONNECT_GRACE_MS);
  }, []);

  const status = useMemo(() => {
    const values = Object.values(state);
    const allConnected = values.every(Boolean);
    const anyConnected = values.some(Boolean);
    const connectedCount = values.filter(Boolean).length;
    if (allConnected) return { label: 'CONNECTED', color: 'green', pipes: state };
    // 2 of 3 live = still usable (market pipe is chart-coupled); don't alarm.
    if (connectedCount >= 2) return { label: 'CONNECTED', color: 'green', pipes: state };
    if (anyConnected) return { label: 'RECONNECTING', color: 'yellow', pipes: state };
    return { label: 'DISCONNECTED', color: 'red', pipes: state };
  }, [state]);

  return { status, setConnected };
}
