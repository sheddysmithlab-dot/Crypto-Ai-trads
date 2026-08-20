export default function ControlBar({
  botIsActive,
  botLoading,
  sessionEngineEnabled = false,
  uptime,
  lastUpdated,
  onClick,
  onManualBuy,
  onManualSell,
}) {
  const mainBtnColor = botLoading
    ? 'bg-amber-500 cursor-wait'
    : botIsActive
    ? 'bg-red-600 hover:bg-red-700'
    : sessionEngineEnabled
    ? 'bg-gray-600 hover:bg-gray-500'
    : 'bg-green-600 hover:bg-green-700';

  const sideBase =
    'shrink-0 w-14 sm:w-20 rounded-xl text-[10px] sm:text-xs font-black uppercase tracking-wide text-white transition-all duration-200 active:scale-95 flex flex-col items-center justify-center gap-0.5 py-2';
  const sideOff = 'bg-gray-300 dark:bg-gray-700 text-gray-500 cursor-not-allowed opacity-60';
  const locked = botIsActive || botLoading;

  return (
    <div className="bg-lightCard dark:bg-darkCard border-t border-gray-300 dark:border-gray-800 px-4 py-4 shrink-0 flex flex-col items-center justify-center space-y-3">
      <div className="w-full max-w-4xl flex items-stretch gap-2">
        <button
          type="button"
          className={`${sideBase} ${locked ? sideOff : 'bg-emerald-500 hover:bg-emerald-400'}`}
          onClick={onManualBuy}
          disabled={locked}
          title="Manual BUY (LONG)"
        >
          <i className="fas fa-arrow-up" />
          BUY
        </button>

        <button
          type="button"
          className={`flex-1 text-white py-3.5 text-base lg:text-lg font-black tracking-widest rounded-xl uppercase flex items-center justify-center gap-3 transition-colors duration-200 active:scale-95 ${mainBtnColor}`}
          onClick={onClick}
          disabled={botLoading}
        >
          {botLoading ? (
            <>
              <i className="fas fa-spinner fa-spin" />
              PLEASE WAIT…
            </>
          ) : botIsActive ? (
            <>
              <i className="fas fa-stop-circle" />
              AI ENGINE STOP
            </>
          ) : sessionEngineEnabled ? (
            <>
              <i className="fas fa-play" />
              AI ENGINE START
              <span className="hidden sm:inline text-xs font-bold normal-case tracking-normal opacity-80">
                (Session Engine ON)
              </span>
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
          <i className="fas fa-arrow-down" />
          SELL
        </button>
      </div>

      <div className="w-full max-w-4xl flex flex-col sm:flex-row items-center justify-between text-xs text-gray-500 dark:text-gray-400 font-medium gap-3 px-2">
        <div className="flex items-center gap-2">
          <i className="fas fa-hourglass-start text-blue-500" />
          <span>Session:</span>
          <span className="text-gray-800 dark:text-gray-200 font-bold">{uptime.formatted}</span>
          <span className={botIsActive ? 'text-green-500' : 'text-gray-500'}>
            {botIsActive ? '(Running)' : '(Stopped)'}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <i className="fas fa-sync-alt text-gray-500" />
          <span>Updated:</span>
          <span className="text-gray-800 dark:text-gray-200 font-bold">{lastUpdated}</span>
        </div>
      </div>
    </div>
  );
}
