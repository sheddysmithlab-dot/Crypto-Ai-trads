import NotificationsDropdown from './NotificationsDropdown';

const STATUS_COLOR = {
  green: 'text-green-500',
  yellow: 'text-yellow-500',
  red: 'text-red-500',
};
const DOT_COLOR = {
  green: 'bg-green-500',
  yellow: 'bg-yellow-500',
  red: 'bg-red-500',
};

export default function Header({
  totalCapital,
  dailyProfit,
  dailyProfitPct,
  tradesCount,
  apiStatus,
  tradingMode,
  notifications,
  unreadCount,
  markAllRead,
  onOpenPaperModal,
  onOpenSettings,
}) {
  const isProfit = dailyProfit >= 0;
  const capStr = totalCapital.toLocaleString('en-US', { minimumFractionDigits: 2 });
  const profitStr = `${isProfit ? '+' : '-'}$${Math.abs(dailyProfit).toLocaleString('en-US', {
    minimumFractionDigits: 2,
  })} (${isProfit ? '+' : ''}${dailyProfitPct.toFixed(2)}%)`;
  const isLive = tradingMode === 'LIVE_TRADING';

  return (
    <header className="bg-lightCard dark:bg-darkCard shadow-md px-3 py-2 flex justify-between items-center sticky top-0 z-50 border-b border-gray-200 dark:border-gray-800">
      <div className="flex items-center gap-2">
        <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-blue-500 to-indigo-600 flex items-center justify-center font-black text-white text-xs">
          Ai
        </div>
        <h1 className="text-sm lg:text-base font-bold tracking-wider">AI TRADING BOT</h1>
      </div>

      {/* Desktop Stats Strip */}
      <div className="hidden lg:flex space-x-6 text-sm">
        <div className="flex flex-col">
          <span className="text-gray-500 dark:text-gray-400 text-[10px] uppercase tracking-wider">Total Capital</span>
          <span className="font-bold text-sm">${capStr}</span>
        </div>
        <div className="flex flex-col">
          <span className="text-gray-500 dark:text-gray-400 text-[10px] uppercase tracking-wider">Daily Profit</span>
          <span className={`font-bold text-sm ${isProfit ? 'text-green-500' : 'text-red-500'}`}>{profitStr}</span>
        </div>
        <div className="flex flex-col">
          <span className="text-gray-500 dark:text-gray-400 text-[10px] uppercase tracking-wider">Open Positions</span>
          <span className="font-bold text-sm">
            {tradesCount} <span className="text-xs font-normal text-gray-400">(Active)</span>
          </span>
        </div>
        <div className="flex flex-col">
          <span className="text-gray-500 dark:text-gray-400 text-[10px] uppercase tracking-wider">API Status</span>
          <span className={`font-bold text-sm flex items-center ${STATUS_COLOR[apiStatus.color]}`}>
            <span className={`w-2 h-2 rounded-full mr-1.5 animate-pulse ${DOT_COLOR[apiStatus.color]}`}></span>{' '}
            {apiStatus.label}
          </span>
        </div>
      </div>

      {/* Right Icons */}
      <div className="flex items-center gap-2">
        <button
          id="trading-mode-badge"
          onClick={onOpenPaperModal}
          className={`hidden md:flex items-center px-2 py-1 rounded-full text-[10px] font-bold border hover:opacity-80 transition ${
            isLive
              ? 'bg-green-100 dark:bg-green-900/30 border-green-200 dark:border-green-700 text-green-700 dark:text-green-400'
              : 'bg-yellow-100 dark:bg-yellow-900/30 border-yellow-200 dark:border-yellow-700 text-yellow-700 dark:text-yellow-400'
          }`}
        >
          <i className={`fas ${isLive ? 'fa-bolt' : 'fa-file-invoice-dollar'} mr-1.5`}></i>
          <span>{isLive ? 'LIVE TRADING' : 'PAPER TRADING'}</span>
        </button>

        <NotificationsDropdown notifications={notifications} unreadCount={unreadCount} markAllRead={markAllRead} />

        <button
          id="settings-gear-btn"
          onClick={onOpenSettings}
          className="p-2 rounded-full hover:bg-gray-200 dark:hover:bg-gray-800 transition"
        >
          <i className="fas fa-cog text-lg text-gray-600 dark:text-gray-300"></i>
        </button>

        <div className="w-8 h-8 rounded-full bg-gray-300 dark:bg-gray-700 flex items-center justify-center overflow-hidden border border-gray-300 dark:border-gray-600">
          <i className="fas fa-user text-gray-500 dark:text-gray-300 text-sm"></i>
        </div>
      </div>
    </header>
  );
}
