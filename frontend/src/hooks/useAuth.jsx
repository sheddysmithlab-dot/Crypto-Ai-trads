import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { API_BASE, authFetch, clearAuthToken, getAuthToken, setAuthToken } from '../config/api';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [status, setStatus] = useState('loading');
  const [username, setUsername] = useState('');

  const checkSession = useCallback(async () => {
    const token = getAuthToken();
    if (!token) {
      setStatus('guest');
      setUsername('');
      return;
    }
    try {
      const res = await authFetch('/auth/session', { skipAuthRedirect: true });
      const data = await res.json();
      if (res.ok && data.authenticated) {
        setUsername(data.username || '');
        setStatus('authed');
        return;
      }
    } catch {
      // fall through to guest
    }
    clearAuthToken();
    setUsername('');
    setStatus('guest');
  }, []);

  useEffect(() => {
    checkSession();
  }, [checkSession]);

  const login = useCallback(async (user, password) => {
    const res = await fetch(`${API_BASE}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: user, password }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.message || 'Invalid username or password.');
    }
    setAuthToken(data.token);
    setUsername(data.username || user);
    setStatus('authed');
  }, []);

  const logout = useCallback(async () => {
    try {
      await authFetch('/auth/logout', { method: 'POST', skipAuthRedirect: true });
    } catch {
      // still clear local session
    }
    clearAuthToken();
    setUsername('');
    setStatus('guest');
  }, []);

  const value = useMemo(
    () => ({ status, username, login, logout, checkSession }),
    [status, username, login, logout, checkSession]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
