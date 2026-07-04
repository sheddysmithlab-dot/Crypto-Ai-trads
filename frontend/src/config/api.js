// Dynamic API/WebSocket base URLs - works whether the app is opened via
// localhost, 127.0.0.1, or a LAN IP, without any hardcoded host.
export const API_BASE =
  window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
    ? 'http://localhost:8000'
    : `http://${window.location.hostname}:8000`;

export const WS_BASE = API_BASE.replace('http://', 'ws://').replace('https://', 'wss://');
