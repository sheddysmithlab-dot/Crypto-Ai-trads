/**
 * Session Momentum Engine stop — same Hold / Emergency / Cancel choices as Main AI Engine.
 */
export default function SessionStopConfirmModal({
  open,
  openCount = 0,
  loading = false,
  onHold,
  onEmergency,
  onCancel,
}) {
  if (!open) return null;

  return (
    <div className="fixed inset-0 bg-black/80 z-[116] flex items-center justify-center backdrop-blur-sm p-4">
      <div className="modal-enter bg-[#0B0E11] p-6 sm:p-8 rounded-2xl shadow-2xl max-w-md w-full border-2 border-amber-500 text-center">
        <i className="fas fa-hand text-5xl text-amber-400 mb-4" />
        <h2 className="text-lg sm:text-xl font-black text-white mb-2 uppercase tracking-wide">
          Stop Session Momentum Engine
        </h2>
        <p className="text-sm text-gray-300 mb-2">
          <span className="font-bold text-white">{openCount}</span> open trade
          {openCount === 1 ? '' : 's'} still live. How do you want to exit?
        </p>
        <p className="text-xs text-gray-500 mb-6">
          Both choices turn the button back to green AI ENGINE START. Timed IST windows stop.
          Next Main or Session start begins a fresh book from 0 without touching any held trades.
        </p>

        <div className="flex flex-col gap-3">
          <button
            type="button"
            disabled={loading}
            onClick={onHold}
            className="w-full bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white font-bold py-3.5 rounded-lg uppercase tracking-wide text-xs sm:text-sm flex items-center justify-center gap-2"
          >
            <i className="fas fa-pause-circle" />
            Hold &amp; exit
          </button>
          <p className="text-[10px] text-gray-500 -mt-1 px-1">
            Stop session schedule and new fires. Held trades keep path exit rules (profit +0.5% trail,
            loss lock −0.5%…−0.7%) until they close.
          </p>

          <button
            type="button"
            disabled={loading}
            onClick={onEmergency}
            className="w-full bg-red-600 hover:bg-red-500 disabled:opacity-50 text-white font-bold py-3.5 rounded-lg uppercase tracking-wide text-xs sm:text-sm flex items-center justify-center gap-2"
          >
            <i className="fas fa-skull-crossbones" />
            Emergency exit
          </button>
          <p className="text-[10px] text-gray-500 -mt-1 px-1">
            Close all open positions now, freeze this session book, and turn Session Momentum OFF.
          </p>

          <button
            type="button"
            disabled={loading}
            onClick={onCancel}
            className="w-full bg-gray-700 hover:bg-gray-600 disabled:opacity-50 text-white font-bold py-2.5 rounded-lg uppercase tracking-wide text-xs mt-1"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
