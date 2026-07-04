export default function ControlBar({ phase, botIsActive, uptime, lastUpdated, onClick }) {
  const disabled = phase === 'starting' || phase === 'stopping';

  let icon = 'fa-play';
  let colorClasses =
    'bg-green-600 hover:bg-green-700 active:bg-green-800 shadow-[0_0_20px_rgba(34,197,94,0.6)] hover:shadow-[0_0_25px_rgba(34,197,94,0.8)]';
  let content = (
    <>
      START TRADING <span className="font-bold">(INACTIVE)</span>
    </>
  );

  if (phase === 'starting') {
    icon = 'fa-spinner fa-spin';
    content = 'STARTING...';
  } else if (phase === 'stopping') {
    icon = 'fa-spinner fa-spin';
    colorClasses = 'bg-red-600';
    content = 'STOPPING...';
  } else if (phase === 'halted') {
    icon = 'fa-check-circle';
    colorClasses = 'bg-gray-600';
    content = 'SYSTEM HALTED';
  } else if (botIsActive) {
    icon = 'fa-stop-circle';
    colorClasses =
      'bg-red-600 hover:bg-red-700 active:bg-red-800 shadow-[0_0_20px_rgba(220,38,38,0.6)] hover:shadow-[0_0_25px_rgba(220,38,38,0.8)]';
    content = (
      <>
        STOP TRADING <span className="font-bold">(ACTIVE)</span>
      </>
    );
  }

  return (
    <div className="bg-lightCard dark:bg-darkCard border-t border-gray-300 dark:border-gray-800 px-4 py-4 shrink-0 flex flex-col items-center justify-center space-y-3">
      <button
        className={`w-full max-w-4xl text-white transition-all duration-200 transform active:scale-95 py-3.5 text-base lg:text-lg font-black tracking-widest rounded-xl uppercase flex items-center justify-center gap-3 ${colorClasses}`}
        disabled={disabled}
        onClick={onClick}
      >
        <i className={`fas ${icon} mr-1`}></i> {content}
      </button>

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
