// Routine diagnostic logs are only printed in dev (`npm run dev`), keeping the
// browser console clean for end users in a production build.
export function debugLog(...args) {
  if (import.meta.env.DEV) console.log(...args);
}
