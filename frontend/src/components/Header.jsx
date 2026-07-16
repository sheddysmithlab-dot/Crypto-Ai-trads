import { useRef, useState } from 'react';
import NotificationsDropdown from './NotificationsDropdown';
import BotHelpModal from './BotHelpModal';
import { fmtNum } from '../data/pairs';
import { useClickOutside } from '../hooks/useClickOutside';

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
  tradeValue = 0,
  dailyProfit,
  dailyProfitPct,
  seasonProfit,
  seasonProfitPct,
  seasonActive,
  tradesCount,
  apiStatus,
  tradingMode,
  dayHigh,
  dayLow,
  notifications,
  unreadCount,
  markAllRead,
  onOpenPaperModal,
  onOpenSettings,
  onOpenLog,
  onLogout,
  username,
}) {
  const [profileOpen, setProfileOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const profileRef = useRef(null);
  useClickOutside(profileRef, () => setProfileOpen(false), profileOpen);

  const isProfit = dailyProfit >= 0;
  const isSeasonProfit = seasonProfit >= 0;
  const capStr = totalCapital.toLocaleString('en-US', { minimumFractionDigits: 2 });
  const tradeValStr = tradeValue.toLocaleString('en-US', { minimumFractionDigits: 2 });
  const profitStr = `${isProfit ? '+' : '-'}$${Math.abs(dailyProfit).toLocaleString('en-US', {
    minimumFractionDigits: 2,
  })} (${isProfit ? '+' : ''}${dailyProfitPct.toFixed(2)}%)`;
  const seasonStr = seasonActive
    ? `${isSeasonProfit ? '+' : '-'}$${Math.abs(seasonProfit).toLocaleString('en-US', {
        minimumFractionDigits: 2,
      })} (${isSeasonProfit ? '+' : ''}${seasonProfitPct.toFixed(2)}%)`
    : '$0.00 (0.00%)';
  const isLive = tradingMode === 'LIVE_TRADING';

  return (
    <header className="bg-lightCard dark:bg-darkCard shadow-md px-3 py-2 flex justify-between items-center sticky top-0 z-50 border-b border-gray-200 dark:border-gray-800">
      <div className="flex items-center gap-2">
        <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-blue-500 to-indigo-600 flex items-center justify-center font-black text-white text-xs">
          Ai
        </div>
        <h1 className="text-sm lg:text-base font-bold tracking-wider">AI TRADING BOT</h1>
        <button
          type="button"
          onClick={() => setHelpOpen(true)}
          className="w-5 h-5 rounded-full border border-gray-400 dark:border-gray-500 text-gray-500 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-800 hover:text-blue-500 dark:hover:text-blue-400 text-[11px] font-bold leading-none flex items-center justify-center transition"
          title="How does this bot work?"
          aria-label="How does this bot work?"
        >
          ?
        </button>
      </div>

      {/* Desktop Stats Strip */}
      <div className="hidden lg:flex space-x-6 text-sm">
        <div className="flex flex-col">
          <span className="text-gray-500 dark:text-gray-400 text-[10px] uppercase tracking-wider">Total Capital</span>
          <span className="font-bold text-sm" title="Available cash for new trades (10% sizing base)">${capStr}</span>
        </div>
        <div className="flex flex-col">
          <span className="text-gray-500 dark:text-gray-400 text-[10px] uppercase tracking-wider">Trade Value</span>
          <span className="font-bold text-sm text-amber-500" title="Total open notional exposure">${tradeValStr}</span>
        </div>
        <div className="flex flex-col">
          <span className="text-gray-500 dark:text-gray-400 text-[10px] uppercase tracking-wider">Daily Profit</span>
          <span className={`font-bold text-sm ${isProfit ? 'text-green-500' : 'text-red-500'}`}>{profitStr}</span>
        </div>
        <div className="flex flex-col">
          <span className="text-gray-500 dark:text-gray-400 text-[10px] uppercase tracking-wider">AI Season Profit</span>
          <span
            className={`font-bold text-sm ${
              !seasonActive ? 'text-gray-400' : isSeasonProfit ? 'text-green-500' : 'text-red-500'
            }`}
          >
            {seasonStr}
          </span>
        </div>
        <div className="flex flex-col">
          <span className="text-gray-500 dark:text-gray-400 text-[10px] uppercase tracking-wider">Open Positions</span>
          <span className="font-bold text-sm">
            {tradesCount} <span className="text-xs font-normal text-gray-400">(Active)</span>
          </span>
        </div>
        <div className="flex flex-col">
          <span className="text-gray-500 dark:text-gray-400 text-[10px] uppercase tracking-wider">24H High / Low</span>
          <span className="font-bold text-sm">
            {dayHigh != null ? (
              <>
                <span className="text-green-500">{fmtNum(dayHigh)}</span>
                <span className="text-gray-400 font-normal"> / </span>
                <span className="text-red-500">{fmtNum(dayLow)}</span>
              </>
            ) : (
              <span className="text-gray-400 font-normal">--</span>
            )}
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
          title="Settings"
        >
          <i className="fas fa-cog text-lg text-gray-600 dark:text-gray-300"></i>
        </button>

        <button
          id="log-btn"
          onClick={onOpenLog}
          className="p-2 rounded-full hover:bg-gray-200 dark:hover:bg-gray-800 transition"
          title="System log"
        >
          <i className="fas fa-file-alt text-lg text-gray-600 dark:text-gray-300"></i>
        </button>

        <div className="relative" ref={profileRef}>
          <button
            type="button"
            onClick={() => setProfileOpen((open) => !open)}
            className="w-8 h-8 rounded-full bg-gray-300 dark:bg-gray-700 flex items-center justify-center overflow-hidden border border-gray-300 dark:border-gray-600 hover:ring-2 hover:ring-blue-500/40 transition"
            title="Profile"
            aria-expanded={profileOpen}
            aria-haspopup="true"
          >
            <i className="fas fa-user text-gray-500 dark:text-gray-300 text-sm"></i>
          </button>

          {profileOpen && (
            <div className="absolute right-0 mt-2 w-52 bg-white dark:bg-darkRow rounded-lg shadow-xl border border-gray-200 dark:border-gray-700 overflow-hidden z-50">
              <div className="px-3 py-3 border-b border-gray-200 dark:border-gray-700">
                <div className="text-[10px] uppercase tracking-wider text-gray-500 dark:text-gray-400">Username</div>
                <div className="text-sm font-bold text-gray-800 dark:text-gray-100 truncate mt-0.5" title={username}>
                  {username || 'User'}
                </div>
              </div>
              <button
                type="button"
                onClick={() => {
                  setProfileOpen(false);
                  onLogout?.();
                }}
                className="w-full px-3 py-2.5 text-left text-sm font-semibold text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 flex items-center gap-2 transition"
              >
                <i className="fas fa-right-from-bracket"></i>
                Logout
              </button>
            </div>
          )}
        </div>
      </div>

      <BotHelpModal open={helpOpen} onClose={() => setHelpOpen(false)} />
    </header>
  );
}
