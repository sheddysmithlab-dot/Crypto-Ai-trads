import { useCallback, useEffect, useState } from 'react';
import { authFetch } from '../config/api';

/**
 * Fresh AI Engine start/stop control.
 * Talks ONLY to /bot/start, /bot/stop, /bot/status — no old modal chain.
 */
export function useBotControl({ serverIsActive = false } = {}) {
  const [isActive, setIsActive] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // Mirror backend WS / portfolio flag when it arrives
  useEffect(() => {
    setIsActive(Boolean(serverIsActive));
  }, [serverIsActive]);

  const start = useCallback(async ({ watchlistPairs = [] } = {}) => {
    if (loading) return false;
    setLoading(true);
    setError(null);
    // Optimistic UI — flip green→red immediately
    setIsActive(true);
    try {
      if (watchlistPairs.length) {
        await authFetch('/set-watchlist', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ pairs: watchlistPairs }),
        });
      }
      const res = await authFetch('/bot/start', { method: 'POST' });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.status === 'error') {
        setIsActive(false);
        setError(data.message || 'Start failed');
        return false;
      }
      setIsActive(true);
      return true;
    } catch (err) {
      setIsActive(false);
      setError(err?.message || 'Network error');
      return false;
    } finally {
      setLoading(false);
    }
  }, [loading]);

  const stop = useCallback(async (mode = 'hold') => {
    if (loading) return false;
    setLoading(true);
    setError(null);
    // Optimistic UI — flip red→green immediately
    setIsActive(false);
    try {
      const res = await authFetch('/bot/stop', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.status === 'error') {
        setIsActive(true);
        setError(data.message || 'Stop failed');
        return false;
      }
      setIsActive(false);
      return true;
    } catch (err) {
      setIsActive(true);
      setError(err?.message || 'Network error');
      return false;
    } finally {
      setLoading(false);
    }
  }, [loading]);

  const toggle = useCallback(async (opts) => {
    return isActive ? stop() : start(opts);
  }, [isActive, start, stop]);

  return { isActive, loading, error, start, stop, toggle };
}
