/**
 * Header Live / Paper mode swap.
 * Disabled while AI Engine is running (parent should not open, but guard here too).
 */
export default function TradingModeModal({
  open,
  onClose,
  tradingMode,
  engineActive = false,
  bybitConfigured = null,
  switching = false,
  error = null,
  onSelectPaper,
  onSelectLive,
}) {
  if (!open) return null;

  const isLive = tradingMode === 'LIVE_TRADING';
  const isPaper = tradingMode === 'PAPER_TRADING';
  const locked = Boolean(engineActive) || switching;

  return (
    <div
      className="fixed inset-0 bg-black/70 z-[110] flex items-center justify-center backdrop-blur-sm p-4"
      onClick={(e) => e.target === e.currentTarget && !switching && onClose()}
    >
      <div className="bg-[#0B0E11] border border-gray-800 rounded-2xl shadow-2xl max-w-md w-full overflow-hidden">
        <div className="flex justify-between items-start px-6 pt-6">
          <div>
            <div className="text-xs font-bold text-gray-500 uppercase tracking-widest mb-1">
              Execution mode
            </div>
            <h2 className="text-xl font-bold text-white">Live / Paper Trading</h2>
          </div>
          <button
            type="button"
            className="w-8 h-8 rounded-lg bg-[#161A1E] border border-gray-700 text-gray-400 hover:text-white flex items-center justify-center disabled:opacity-40"
            onClick={onClose}
            disabled={switching}
            aria-label="Close"
          >
            <i className="fas fa-times" />
          </button>
        </div>

        <div className="px-6 py-5 space-y-3">
          {engineActive ? (
            <div className="text-xs rounded-lg px-4 py-3 border bg-red-900/30 border-red-600/50 text-red-300">
              AI Engine is running — stop it before switching Live / Paper.
            </div>
          ) : (
            <p className="text-sm text-gray-400">
              Choose how orders are placed. Chart data stays on public Bybit either way.
            </p>
          )}

          <button
            type="button"
            disabled={locked}
            onClick={onSelectLive}
            className={`w-full text-left rounded-xl border px-4 py-3.5 transition disabled:opacity-50 disabled:cursor-not-allowed ${
              isLive
                ? 'border-green-500 bg-green-900/25 ring-1 ring-green-500/40'
                : 'border-gray-700 bg-[#161A1E] hover:border-green-600/60'
            }`}
          >
            <div className="flex items-center gap-3">
              <span className="w-9 h-9 rounded-full bg-green-900/40 border border-green-700/50 flex items-center justify-center text-green-400">
                <i className="fas fa-bolt" />
              </span>
              <div className="min-w-0 flex-1">
                <div className="text-sm font-bold text-green-400">LIVE TRADING</div>
                <div className="text-xs text-gray-400 mt-0.5">
                  Real Bybit orders · account equity
                  {bybitConfigured === false ? ' · add API keys in Settings first' : ''}
                </div>
              </div>
              {isLive && <i className="fas fa-check text-green-400" />}
            </div>
          </button>

          <button
            type="button"
            disabled={locked}
            onClick={onSelectPaper}
            className={`w-full text-left rounded-xl border px-4 py-3.5 transition disabled:opacity-50 disabled:cursor-not-allowed ${
              isPaper
                ? 'border-yellow-500 bg-yellow-900/25 ring-1 ring-yellow-500/40'
                : 'border-gray-700 bg-[#161A1E] hover:border-yellow-600/60'
            }`}
          >
            <div className="flex items-center gap-3">
              <span className="w-9 h-9 rounded-full bg-yellow-900/40 border border-yellow-700/50 flex items-center justify-center text-yellow-400">
                <i className="fas fa-file-invoice-dollar" />
              </span>
              <div className="min-w-0 flex-1">
                <div className="text-sm font-bold text-yellow-400">PAPER TRADING</div>
                <div className="text-xs text-gray-400 mt-0.5">
                  Built-in simulation · virtual capital · no real funds
                </div>
              </div>
              {isPaper && <i className="fas fa-check text-yellow-400" />}
            </div>
          </button>

          {error ? (
            <div className="text-xs rounded-lg px-4 py-3 border bg-red-900/30 border-red-600/50 text-red-300">
              {error}
            </div>
          ) : null}

          {switching ? (
            <div className="text-xs text-center text-gray-400 py-1">Switching mode…</div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
