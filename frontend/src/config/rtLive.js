import { authFetch, preferHttpRealtime } from '../config/api';

const POLL_MS = 1000;
const listeners = new Set();
let timer = null;
let inFlight = false;
let lastBundle = null;

async function pollOnce() {
  if (inFlight) return;
  inFlight = true;
  try {
    const res = await authFetch('/rt/live');
    if (!res.ok) throw new Error(`rt/live ${res.status}`);
    const data = await res.json();
    lastBundle = data;
    listeners.forEach((fn) => {
      try {
        fn(data, null);
      } catch {
        // ignore subscriber errors
      }
    });
  } catch (err) {
    listeners.forEach((fn) => {
      try {
        fn(null, err);
      } catch {
        // ignore
      }
    });
  } finally {
    inFlight = false;
  }
}

function ensurePolling() {
  if (timer != null) return;
  pollOnce();
  timer = setInterval(pollOnce, POLL_MS);
}

function stopIfIdle() {
  if (listeners.size > 0) return;
  if (timer != null) {
    clearInterval(timer);
    timer = null;
  }
}

/**
 * Shared /rt/live poller for aitrads.in (Hostinger cannot proxy WebSockets).
 * Returns unsubscribe. No-op when preferHttpRealtime is false.
 */
export function subscribeRtLive(onUpdate) {
  if (!preferHttpRealtime) {
    return () => {};
  }
  listeners.add(onUpdate);
  if (lastBundle) {
    try {
      onUpdate(lastBundle, null);
    } catch {
      // ignore
    }
  }
  ensurePolling();
  return () => {
    listeners.delete(onUpdate);
    stopIfIdle();
  };
}

export { preferHttpRealtime };
