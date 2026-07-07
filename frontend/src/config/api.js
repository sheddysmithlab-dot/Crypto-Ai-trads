// In production (Render), the frontend and backend are separate services on
// separate domains, so the backend URL is baked in at build time via
// VITE_API_BASE_URL. Locally (npm run dev), fall back to hostname-based
// detection so it works whether opened via localhost, 127.0.0.1, or a LAN IP.
const configuredBase = import.meta.env.VITE_API_BASE_URL?.replace(/\/+$/, '');

// Match the page's own protocol for the same-host fallback, so a page served
// over HTTPS never tries to open an insecure ws:// socket (browsers block
// that outright as mixed content, instead of failing gracefully).
const pageProtocol = window.location.protocol === 'https:' ? 'https' : 'http';

export const API_BASE =
  configuredBase ||
  (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
    ? 'http://localhost:8000'
    : `${pageProtocol}://${window.location.hostname}:8000`);

export const WS_BASE = API_BASE.replace('http://', 'ws://').replace('https://', 'wss://');

const AUTH_TOKEN_KEY = 'dashboard_auth_token';

export function getAuthToken() {
  try {
    return sessionStorage.getItem(AUTH_TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setAuthToken(token) {
  try {
    sessionStorage.setItem(AUTH_TOKEN_KEY, token);
  } catch {
    // ignore storage failures
  }
}

export function clearAuthToken() {
  try {
    sessionStorage.removeItem(AUTH_TOKEN_KEY);
  } catch {
    // ignore
  }
}

export function backendWsUrl(path) {
  const token = getAuthToken();
  if (!token) return `${WS_BASE}${path}`;
  const joiner = path.includes('?') ? '&' : '?';
  return `${WS_BASE}${path}${joiner}token=${encodeURIComponent(token)}`;
}

export async function authFetch(path, options = {}) {
  const { skipAuthRedirect, headers: extraHeaders, ...rest } = options;
  const headers = { ...(extraHeaders || {}) };
  const token = getAuthToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch(`${API_BASE}${path}`, { ...rest, headers });
  if (res.status === 401 && token && !skipAuthRedirect) {
    clearAuthToken();
    window.location.reload();
  }
  return res;
}
