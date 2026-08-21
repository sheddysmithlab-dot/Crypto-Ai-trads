import SessionEngineFab from './SessionEngineFab';
import InfoTip from './InfoTip';

export default function ControlBar({
  botIsActive,
  botLoading,
  sessionEngineEnabled = false,
  sessionLoading = false,
  uptime,
  lastUpdated,
  onClick,
  onManualBuy,
  onManualSell,
  onOpenSessionModal,
}) {
  const busy = botLoading || sessionLoading;

  // Session Momentum ON → main button becomes Stop Session Engine (replaces AI START/STOP).
  const mainBtnColor = busy
    ? 'bg-amber-500 cursor-wait'
    : sessionEngineEnabled
    ? 'bg-red-600 hover:bg-red-700'
    : botIsActive
    ? 'bg-red-600 hover:bg-red-700'
    : 'bg-green-600 hover:bg-green-700';

  const sideBase =
    'shrink-0 w-12 sm:w-16 rounded-lg text-[9px] sm:text-[10px] font-black uppercase tracking-wide text-white transition-all duration-200 active:scale-95 flex flex-col items-center justify-center gap-0 py-1.5';
  const sideOff = 'bg-gray-300 dark:bg-gray-700 text-gray-500 cursor-not-allowed opacity-60';
  const locked = botIsActive || busy;

  return (
    <div className="relative shrink-0 z-50 bg-lightCard dark:bg-darkCard border-t border-gray-300 dark:border-gray-800 px-2 sm:px-3 py-1.5 shadow-[0_-4px_16px_rgba(0,0,0,0.25)]">
      {/* Main actions — compact height */}
      <div className="w-full max-w-4xl mx-auto flex items-stretch gap-1.5 sm:gap-2">
        <button
          type="button"
          className={`${sideBase} ${locked ? sideOff : 'bg-emerald-500 hover:bg-emerald-400'}`}
          onClick={onManualBuy}
          disabled={locked}
          title="Manual BUY (LONG)"
        >
          <i className="fas fa-arrow-up text-[10px]" />
          BUY
        </button>

        <button
          type="button"
          className={`flex-1 text-white py-2 sm:py-2.5 text-sm lg:text-base font-black tracking-widest rounded-lg uppercase flex items-center justify-center gap-2 transition-colors duration-200 active:scale-95 ${mainBtnColor}`}
          onClick={onClick}
          disabled={busy}
        >
          {busy ? (
            <>
              <i className="fas fa-spinner fa-spin" />
              PLEASE WAIT…
            </>
          ) : sessionEngineEnabled ? (
            <>
              <i className="fas fa-stop-circle" />
              <span className="text-center leading-tight">
                STOP SESSION
                <span className="hidden sm:inline"> MOMENTUM</span>
                <span className="sm:hidden block text-[9px] font-bold normal-case tracking-normal opacity-90">
                  Momentum Engine
                </span>
              </span>
            </>
          ) : botIsActive ? (
            <>
              <i className="fas fa-stop-circle" />
              AI ENGINE STOP
            </>
          ) : (
            <>
              <i className="fas fa-play" />
              AI ENGINE START
            </>
          )}
        </button>

        <button
          type="button"
          className={`${sideBase} ${locked ? sideOff : 'bg-orange-500 hover:bg-orange-400'}`}
          onClick={onManualSell}
          disabled={locked}
          title="Manual SELL (SHORT)"
        >
          <i className="fas fa-arrow-down text-[10px]" />
          SELL
        </button>
      </div>

      {/* Bottom strip: Session Engine locked to far-left corner */}
      <div className="mt-1 w-full flex items-center justify-between gap-2 min-h-[28px]">
        <div className="flex items-center gap-1.5 min-w-0">
          <SessionEngineFab enabled={sessionEngineEnabled} onClick={onOpenSessionModal} compact />
          <InfoTip text="Timed momentum windows (IST). When ON, the main button becomes Stop Session Momentum Engine." />
          <span className="inline-flex items-center gap-1.5 text-[10px] sm:text-xs text-gray-500 dark:text-gray-400 font-medium ml-1 truncate">
            <i className="fas fa-hourglass-start text-blue-500 shrink-0" />
            <span className="hidden sm:inline-flex items-center gap-1 shrink-0">
              Session <InfoTip text="How long this AI session has been running." />
            </span>
            <span className="text-gray-800 dark:text-gray-200 font-bold tabular-nums">{uptime.formatted}</span>
            <span
              className={`shrink-0 ${
                sessionEngineEnabled || botIsActive ? 'text-green-500' : 'text-gray-500'
              }`}
            >
              {sessionEngineEnabled
                ? botIsActive
                  ? '(Session · Trading)'
                  : '(Session · Waiting)'
                : botIsActive
                  ? '(Running)'
                  : '(Stopped)'}
            </span>
          </span>
        </div>
        <div className="flex items-center gap-1.5 text-[10px] sm:text-xs text-gray-500 dark:text-gray-400 font-medium shrink-0">
          <i className="fas fa-sync-alt text-gray-500" />
          <span className="hidden sm:inline">Updated:</span>
          <span className="text-gray-800 dark:text-gray-200 font-bold tabular-nums">{lastUpdated}</span>
        </div>
      </div>
    </div>
  );
}
