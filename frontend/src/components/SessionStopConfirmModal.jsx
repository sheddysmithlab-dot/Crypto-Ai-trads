/**
 * Confirm before stopping Session Momentum Engine from the main control bar.
 */
export default function SessionStopConfirmModal({
  open,
  loading = false,
  inWindow = false,
  onConfirm,
  onCancel,
}) {
  if (!open) return null;

  return (
    <div className="fixed inset-0 bg-black/80 z-[116] flex items-center justify-center backdrop-blur-sm p-4">
      <div className="modal-enter bg-[#0B0E11] p-6 sm:p-8 rounded-2xl shadow-2xl max-w-md w-full border-2 border-cyan-500 text-center">
        <i className="fas fa-clock text-5xl text-cyan-400 mb-4" />
        <h2 className="text-lg sm:text-xl font-black text-white mb-2 uppercase tracking-wide">
          Stop Session Momentum Engine?
        </h2>
        <p className="text-sm text-gray-300 mb-2">
          Timed high-momentum windows will turn off. The main AI Engine stays OFF until you start it
          separately.
        </p>
        <p className="text-xs text-gray-500 mb-6">
          {inWindow
            ? 'You are currently inside an active window — stopping ends schedule-driven trading now.'
            : 'Engine is waiting for the next window — stopping cancels the schedule.'}
        </p>

        <div className="flex flex-col gap-3">
          <button
            type="button"
            disabled={loading}
            onClick={onConfirm}
            className="w-full bg-red-600 hover:bg-red-500 disabled:opacity-50 text-white font-bold py-3.5 rounded-lg uppercase tracking-wide text-xs sm:text-sm flex items-center justify-center gap-2"
          >
            {loading ? (
              <>
                <i className="fas fa-spinner fa-spin" />
                Stopping…
              </>
            ) : (
              <>
                <i className="fas fa-stop-circle" />
                Yes, stop session engine
              </>
            )}
          </button>

          <button
            type="button"
            disabled={loading}
            onClick={onCancel}
            className="w-full bg-gray-700 hover:bg-gray-600 disabled:opacity-50 text-white font-bold py-2.5 rounded-lg uppercase tracking-wide text-xs"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
