import { useCallback, useMemo, useState } from 'react';

// Tracks all 3 real-time WebSocket pipes (market, portfolio, trades) so the
// header badge reflects the true combined connection state.
export function useApiStatus() {
  const [state, setState] = useState({ market: false, portfolio: false, trades: false });

  const setConnected = useCallback((name, connected) => {
    setState((prev) => ({ ...prev, [name]: connected }));
  }, []);

  const status = useMemo(() => {
    const values = Object.values(state);
    const allConnected = values.every(Boolean);
    const anyConnected = values.some(Boolean);
    if (allConnected) return { label: 'CONNECTED', color: 'green' };
    if (anyConnected) return { label: 'RECONNECTING', color: 'yellow' };
    return { label: 'DISCONNECTED', color: 'red' };
  }, [state]);

  return { status, setConnected };
}
