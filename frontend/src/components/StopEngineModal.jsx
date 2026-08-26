/**
 * Shown when user presses AI ENGINE STOP (always — even with 0 open trades).
 * Hold = keep positions (auto TP/SL), stop new fires.
 * Emergency = close everything now.
 *
 * Closing the browser does NOT stop the VPS engine — only these buttons do.
 */
export default function StopEngineModal({
  open,
  openCount = 0,
  loading = false,
  onHold,
  onEmergency,
  onCancel,
}) {
  if (!open) return null;

  const hasOpen = openCount > 0;

  return (
    <div className="fixed inset-0 bg-black/80 z-[115] flex items-center justify-center backdrop-blur-sm p-4">
      <div className="modal-enter bg-[#0B0E11] p-6 sm:p-8 rounded-2xl shadow-2xl max-w-md w-full border-2 border-amber-500 text-center">
        <i className="fas fa-hand text-5xl text-amber-400 mb-4" />
        <h2 className="text-lg sm:text-xl font-black text-white mb-2 uppercase tracking-wide">
          Stop AI Engine
        </h2>
        <p className="text-sm text-gray-300 mb-2">
          {hasOpen ? (
            <>
              <span className="font-bold text-white">{openCount}</span> open trade
              {openCount === 1 ? '' : 's'} still live. How do you want to exit?
            </>
          ) : (
            <>No open trades. Confirm to halt the VPS scanner (browser close alone does not stop it).</>
          )}
        </p>
        <p className="text-xs text-gray-500 mb-6">
          Engine runs on the server without your browser. Only confirm here if you really want it OFF.
        </p>

        <div className="flex flex-col gap-3">
          <button
            type="button"
            disabled={loading}
            onClick={onHold}
            className="w-full bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white font-bold py-3.5 rounded-lg uppercase tracking-wide text-xs sm:text-sm flex items-center justify-center gap-2"
          >
            <i className="fas fa-pause-circle" />
            {hasOpen ? 'Hold & exit' : 'Stop scanner (Hold)'}
          </button>
          <p className="text-[10px] text-gray-500 -mt-1 px-1">
            {hasOpen
              ? 'Stop new fires. Held trades keep path SL/TP and update portfolio until they close.'
              : 'Turn engine OFF on VPS. You can START again anytime from this page.'}
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
            {hasOpen
              ? 'Close all open positions now and freeze this session book.'
              : 'Same as stop when flat — ends the current session book.'}
          </p>

          <button
            type="button"
            disabled={loading}
            onClick={onCancel}
            className="w-full bg-gray-700 hover:bg-gray-600 disabled:opacity-50 text-white font-bold py-2.5 rounded-lg uppercase tracking-wide text-xs mt-1"
          >
            Keep running
          </button>
        </div>
      </div>
    </div>
  );
}
