import { useEffect } from 'react';

function fmtWindow(w) {
  if (!w) return '';
  const end = w.end === '01:30' || (w.start && w.end && w.end < w.start)
    ? `${w.end} (next day)`
    : w.end;
  return `${w.start} – ${end} IST`;
}

/**
 * Session Momentum Engine popup — English explanation + Start/Stop.
 * When this engine is ON, the Main AI Engine stays OFF.
 */
export default function SessionMomentumModal({
  open,
  onClose,
  enabled,
  loading,
  status,
  mainEngineActive,
  onRefresh,
  onStart,
  onStop,
}) {
  useEffect(() => {
    if (open) onRefresh?.();
  }, [open, onRefresh]);

  if (!open) return null;

  const windows = Array.isArray(status?.windows) ? status.windows : [];
  const active = Array.isArray(status?.active_windows) ? status.active_windows : [];
  const next = status?.next || {};
  const nowIst = status?.now_ist || '—';
  const inWindow = Boolean(status?.in_window);

  async function handleToggle() {
    if (loading) return;
    if (enabled) {
      await onStop();
    } else {
      await onStart();
    }
  }

  return (
    <div
      className="fixed inset-0 bg-black/75 z-[120] flex items-center justify-center backdrop-blur-sm p-4"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="bg-[#0B0E11] border border-gray-800 rounded-2xl shadow-2xl max-w-xl w-full max-h-[90vh] overflow-y-auto">
        <div className="flex justify-between items-start px-6 pt-6">
          <div>
            <div className="text-xs font-bold text-cyan-400 uppercase tracking-widest mb-1">
              Timed Momentum Engine
            </div>
            <h2 className="text-xl font-bold text-white">Session Momentum Engine</h2>
          </div>
          <button
            type="button"
            className="w-8 h-8 rounded-lg bg-[#161A1E] border border-gray-700 text-gray-400 hover:text-white flex items-center justify-center"
            onClick={onClose}
          >
            <i className="fas fa-times" />
          </button>
        </div>

        <div className="px-6 py-5 space-y-4 text-sm text-gray-300 leading-relaxed">
          <p>
            Global markets do not move evenly through the day. Liquidity and volatility spike when
            major sessions open — especially the <span className="text-white font-semibold">US cash open</span>,
            Asian morning flow, and the <span className="text-white font-semibold">London–New York overlap</span>.
            Those windows produce the strongest momentum bursts of the trading day.
          </p>

          <p>
            If you want the bot to trade only when that momentum is typically highest, start this
            engine. It will automatically arm trading during the scheduled IST windows below and
            pause when the window ends.
          </p>

          <div className="rounded-xl border border-amber-700/40 bg-amber-900/20 px-4 py-3 text-amber-200 text-xs">
            <strong className="block text-amber-300 mb-1">Important — mutual exclusivity</strong>
            Starting the Session Momentum Engine turns the <strong>Main AI Engine OFF</strong>.
            Only one control mode can run at a time. You can switch back anytime by stopping this
            engine and using <strong>AI ENGINE START</strong> on the main control bar.
          </div>

          <div>
            <h3 className="text-xs font-bold uppercase tracking-wider text-gray-400 mb-2">
              High-momentum windows (Mon–Fri, Asia/Kolkata)
            </h3>
            <ul className="space-y-2">
              {(windows.length
                ? windows
                : [
                    { key: 'morning_momentum', label: 'Morning Momentum', start: '05:30', end: '08:30' },
                    { key: 'peak_overlap', label: 'Peak Overlap Window', start: '18:30', end: '23:30' },
                    { key: 'us_core', label: 'US Core Session', start: '19:30', end: '01:30' },
                  ]
              ).map((w) => (
                <li
                  key={w.key || w.label}
                  className="flex items-start justify-between gap-3 bg-[#161A1E] border border-gray-800 rounded-lg px-3 py-2.5"
                >
                  <div>
                    <div className="text-white font-semibold text-sm">{w.label}</div>
                    <div className="text-[11px] text-gray-500 mt-0.5">
                      {w.key === 'morning_momentum' && 'Asian / early European liquidity wake-up'}
                      {w.key === 'peak_overlap' && 'London–NY overlap — peak volume & momentum'}
                      {w.key === 'us_core' && 'US session core — high directional moves'}
                      {!['morning_momentum', 'peak_overlap', 'us_core'].includes(w.key) && 'Scheduled momentum window'}
                    </div>
                  </div>
                  <div className="text-cyan-300 font-mono text-xs whitespace-nowrap pt-0.5">
                    {fmtWindow(w)}
                  </div>
                </li>
              ))}
            </ul>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
            <div className="rounded-lg border border-gray-800 bg-[#161A1E] px-3 py-2">
              <div className="text-gray-500 uppercase tracking-wider mb-1">Now (IST)</div>
              <div className="text-white font-semibold">{nowIst}</div>
            </div>
            <div className="rounded-lg border border-gray-800 bg-[#161A1E] px-3 py-2">
              <div className="text-gray-500 uppercase tracking-wider mb-1">Window status</div>
              <div className={inWindow ? 'text-emerald-400 font-semibold' : 'text-gray-400 font-semibold'}>
                {inWindow ? `IN WINDOW${active.length ? ` — ${active.join(', ')}` : ''}` : 'Outside window'}
              </div>
            </div>
            <div className="rounded-lg border border-gray-800 bg-[#161A1E] px-3 py-2 sm:col-span-2">
              <div className="text-gray-500 uppercase tracking-wider mb-1">Next transition</div>
              <div className="text-white font-semibold">
                {next?.at_ist
                  ? `${next.at_ist}${next.will_be_active ? ' → trading arms' : ' → trading pauses'}`
                  : '—'}
              </div>
            </div>
          </div>

          {mainEngineActive && !enabled ? (
            <p className="text-xs text-orange-300 border border-orange-700/40 bg-orange-900/20 rounded-lg px-3 py-2">
              Main AI Engine is currently ON. Starting Session Momentum Engine will stop the main engine first.
            </p>
          ) : null}

          <button
            type="button"
            disabled={loading}
            onClick={handleToggle}
            className={`w-full py-3 rounded-xl text-sm font-black uppercase tracking-widest transition disabled:opacity-60 ${
              enabled
                ? 'bg-red-600 hover:bg-red-500 text-white'
                : 'bg-cyan-500 hover:bg-cyan-400 text-black'
            }`}
          >
            {loading ? (
              <>
                <i className="fas fa-spinner fa-spin mr-2" />
                Please wait…
              </>
            ) : enabled ? (
              <>
                <i className="fas fa-stop-circle mr-2" />
                Stop Session Momentum Engine
              </>
            ) : (
              <>
                <i className="fas fa-play mr-2" />
                Start Session Momentum Engine
              </>
            )}
          </button>

          <p className="text-[11px] text-gray-500 text-center pb-1">
            Status: {enabled ? 'ENABLED' : 'DISABLED'}
            {enabled && inWindow ? ' · actively trading this window' : ''}
            {enabled && !inWindow ? ' · waiting for next window' : ''}
          </p>
        </div>
      </div>
    </div>
  );
}
