import { useCallback, useState } from 'react';
import { authFetch } from '../config/api';

/**
 * Fresh paper-trading capital wiring — only /paper/status + /paper/set-capital.
 */
export function usePaperTrading() {
  const [capital, setCapital] = useState(null);
  const [mode, setMode] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await authFetch('/paper/status');
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.status === 'error') {
        setError(data.message || data.detail || 'Failed to load paper status');
        return null;
      }
      const cap = Number(data.capital);
      setCapital(Number.isFinite(cap) ? cap : 0);
      setMode(data.mode || null);
      return data;
    } catch (err) {
      setError(err?.message || 'Network error');
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  const setPaperCapital = useCallback(async (amount) => {
    setLoading(true);
    setError(null);
    try {
      const res = await authFetch('/paper/set-capital', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ amount: Number(amount) }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.status === 'error') {
        setError(data.message || 'Failed to set paper capital');
        return { ok: false, message: data.message || 'Failed to set paper capital' };
      }
      const cap = Number(data.capital);
      setCapital(Number.isFinite(cap) ? cap : Number(amount));
      setMode(data.mode || 'PAPER_TRADING');
      return { ok: true, capital: cap, message: data.message };
    } catch (err) {
      const message = err?.message || 'Network error';
      setError(message);
      return { ok: false, message };
    } finally {
      setLoading(false);
    }
  }, []);

  return { capital, mode, loading, error, refresh, setPaperCapital };
}
