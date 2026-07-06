// Only two states ever for the main button: green START AI AUTOMATION
// (inactive) or red STOP AI AUTOMATION (active) - no transient
// "starting/stopping/halted" states in between.
// BUY/SELL flank buttons are the OPPOSITE of automation: enabled by default
// (manual trading while automation is off), disabled once START is clicked
// (automation is running, so manual entry/exit is locked out to avoid
// conflicting with the bot). Their underlying working policy is unchanged.
export default function ControlBar({ botIsActive, uptime, lastUpdated, onClick, onManualBuy, onManualSell }) {
  const colorClasses = botIsActive
    ? 'bg-red-600 hover:bg-red-700 active:bg-red-800 shadow-[0_0_20px_rgba(220,38,38,0.6)] hover:shadow-[0_0_25px_rgba(220,38,38,0.8)]'
    : 'bg-green-600 hover:bg-green-700 active:bg-green-800 shadow-[0_0_20px_rgba(34,197,94,0.6)] hover:shadow-[0_0_25px_rgba(34,197,94,0.8)]';

  const sideButtonBase =
    'shrink-0 w-14 sm:w-20 rounded-xl text-[10px] sm:text-xs font-black uppercase tracking-wide text-white transition-all duration-200 transform active:scale-95 flex flex-col items-center justify-center gap-0.5 py-2';
  const sideButtonDisabled = 'bg-gray-300 dark:bg-gray-700 text-gray-500 dark:text-gray-400 cursor-not-allowed opacity-60';

  return (
    <div className="bg-lightCard dark:bg-darkCard border-t border-gray-300 dark:border-gray-800 px-4 py-4 shrink-0 flex flex-col items-center justify-center space-y-3">
      <div className="w-full max-w-4xl flex items-stretch gap-2">
        <button
          className={`${sideButtonBase} ${botIsActive ? sideButtonDisabled : 'bg-emerald-500 hover:bg-emerald-400 active:bg-emerald-600'}`}
          onClick={onManualBuy}
          disabled={botIsActive}
          title="Manual BUY - opens a new 1% margin position (100x leverage)"
        >
          <i className="fas fa-arrow-up"></i>
          BUY
        </button>

        <button
          className={`flex-1 text-white transition-all duration-200 transform active:scale-95 py-3.5 text-base lg:text-lg font-black tracking-widest rounded-xl uppercase flex items-center justify-center gap-3 ${colorClasses}`}
          onClick={onClick}
        >
          <i className={`fas ${botIsActive ? 'fa-stop-circle' : 'fa-play'} mr-1`}></i>
          {botIsActive ? (
            <>
              STOP AI AUTOMATION <span className="font-bold">(ACTIVE)</span>
            </>
          ) : (
            <>
              START AI AUTOMATION <span className="font-bold">(INACTIVE)</span>
            </>
          )}
        </button>

        <button
          className={`${sideButtonBase} ${botIsActive ? sideButtonDisabled : 'bg-orange-500 hover:bg-orange-400 active:bg-orange-600'}`}
          onClick={onManualSell}
          disabled={botIsActive}
          title="Manual SELL - closes your best-performing (or least-losing) manually-opened trade"
        >
          <i className="fas fa-arrow-down"></i>
          SELL
        </button>
      </div>

      <div className="w-full max-w-4xl flex flex-col sm:flex-row items-center justify-between text-xs text-gray-500 dark:text-gray-400 font-medium gap-3 px-2">
        <div className="flex items-center gap-2">
          <i className="fas fa-hourglass-start text-blue-500"></i>
          <span>Session:</span>
          <span className="text-gray-800 dark:text-gray-200 font-bold">{uptime.formatted}</span>
          <span className={uptime.running ? 'text-green-500' : 'text-gray-500 dark:text-gray-400'}>
            {uptime.running ? '(Running)' : '(Stopped)'}
          </span>
        </div>

        <span className="hidden sm:inline text-gray-400 dark:text-gray-500">•</span>

        <div className="flex items-center gap-2">
          <i className="fas fa-sync-alt text-gray-500"></i>
          <span>Updated:</span>
          <span className="text-gray-800 dark:text-gray-200 font-bold">{lastUpdated}</span>
        </div>
      </div>
    </div>
  );
}
