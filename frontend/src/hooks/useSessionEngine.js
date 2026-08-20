import { useCallback, useEffect, useState } from 'react';
import { authFetch } from '../config/api';

/**
 * Session Momentum Engine — timed high-momentum market windows (IST).
 * Mutually exclusive with Main AI Engine.
 */
export function useSessionEngine({ serverSchedule = null } = {}) {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (serverSchedule && typeof serverSchedule === 'object') {
      setStatus((prev) => ({ ...(prev || {}), ...serverSchedule }));
    }
  }, [serverSchedule]);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await authFetch('/session-engine/status');
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.status === 'error') {
        setError(data.message || data.detail || 'Failed to load session engine');
        return null;
      }
      setStatus(data);
      return data;
    } catch (err) {
      setError(err?.message || 'Network error');
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  const start = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await authFetch('/session-engine/start', { method: 'POST' });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.status === 'error') {
        setError(data.message || 'Failed to start Session Momentum Engine');
        return { ok: false, message: data.message };
      }
      setStatus(data.schedule || data);
      return { ok: true, data };
    } catch (err) {
      setError(err?.message || 'Network error');
      return { ok: false, message: err?.message };
    } finally {
      setLoading(false);
    }
  }, []);

  const stop = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await authFetch('/session-engine/stop', { method: 'POST' });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.status === 'error') {
        setError(data.message || 'Failed to stop Session Momentum Engine');
        return { ok: false, message: data.message };
      }
      setStatus(data.schedule || data);
      return { ok: true, data };
    } catch (err) {
      setError(err?.message || 'Network error');
      return { ok: false, message: err?.message };
    } finally {
      setLoading(false);
    }
  }, []);

  const enabled = Boolean(status?.enabled);

  return { status, enabled, loading, error, refresh, start, stop };
}
