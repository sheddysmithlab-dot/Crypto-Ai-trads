// In production (Render), the frontend and backend are separate services on
// separate domains, so the backend URL is baked in at build time via
// VITE_API_BASE_URL. Locally (npm run dev), fall back to hostname-based
// detection so it works whether opened via localhost, 127.0.0.1, or a LAN IP.
const configuredBase = import.meta.env.VITE_API_BASE_URL?.replace(/\/+$/, '');

export const API_BASE =
  configuredBase ||
  (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
    ? 'http://localhost:8000'
    : `http://${window.location.hostname}:8000`);

export const WS_BASE = API_BASE.replace('http://', 'ws://').replace('https://', 'wss://');
