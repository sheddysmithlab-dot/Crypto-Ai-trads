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
