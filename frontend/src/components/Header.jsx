import { useRef, useState } from 'react';
import NotificationsDropdown from './NotificationsDropdown';
import BotHelpModal from './BotHelpModal';
import PortfolioModal from './PortfolioModal';
import { formatTfMoveLabel } from '../hooks/useTfMoveStats';
import { useClickOutside } from '../hooks/useClickOutside';

export default function Header({
  totalCapital,
  tradeValue = 0,
  dailyProfit,
  dailyProfitPct,
  dailyBrokerFee = 0,
  seasonProfit,
  seasonProfitPct,
  seasonActive,
  tradesCount,
  exitedPnlUsd = 0,
  apiStatus,
  tradingMode,
  dayHigh,
  dayLow,
  tfMovePct = null,
  tfMoveLabel = null,
  chartTimeframe = '1M',
  notifications,
  unreadCount,
  markAllRead,
  onOpenPaperModal,
  onOpenSettings,
  onOpenLog,
  onOpenStatement,
  onLogout,
  username,
}) {
  const [profileOpen, setProfileOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [portfolioOpen, setPortfolioOpen] = useState(false);
  const profileRef = useRef(null);
  useClickOutside(profileRef, () => setProfileOpen(false), profileOpen);

  const isLive = tradingMode === 'LIVE_TRADING';
  const exitedPnl = Number(exitedPnlUsd) || 0;
  const exitedProfit = exitedPnl >= 0;
  const exitedPnlStr = `${exitedProfit ? '+' : '-'}$${Math.abs(exitedPnl).toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
  const tfMoveUp = tfMovePct != null && tfMovePct >= 0;
  const tfMoveStr =
    tfMovePct != null
      ? `${tfMoveUp ? '+' : ''}${Math.abs(tfMovePct) < 0.005 && tfMovePct !== 0 ? tfMovePct.toFixed(3) : tfMovePct.toFixed(2)}%`
      : '--';
  const tfMoveTitle = formatTfMoveLabel(chartTimeframe, tfMoveLabel);

  return (
    <header className="bg-lightCard dark:bg-darkCard shadow-md px-3 py-2 flex justify-between items-center sticky top-0 z-50 border-b border-gray-200 dark:border-gray-800 gap-2">
      <div className="flex items-center gap-2 shrink-0">
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

      {/* Center: Exited + Market momentum (chart TF) */}
      <div className="flex-1 flex items-center justify-center gap-4 sm:gap-8 min-w-0">
        <div className="flex flex-col items-center min-w-0">
          <span className="text-gray-500 dark:text-gray-400 text-[9px] sm:text-[10px] uppercase tracking-wider whitespace-nowrap">
            Exited (booked)
          </span>
          <span
            className={`font-bold text-sm sm:text-base tabular-nums ${
              exitedProfit ? 'text-green-500' : 'text-red-500'
            }`}
            title="Closed trades this AI session — gross $ (fees not deducted)"
          >
            {exitedPnlStr}
          </span>
        </div>
        <div className="w-px h-7 bg-gray-300 dark:bg-gray-700 shrink-0" aria-hidden="true" />
        <div className="flex flex-col items-center min-w-0" title={tfMoveTitle}>
          <span className="text-gray-500 dark:text-gray-400 text-[9px] sm:text-[10px] uppercase tracking-wider whitespace-nowrap">
            Market {chartTimeframe}
          </span>
          <span
            className={`font-bold text-sm sm:text-base tabular-nums ${
              tfMovePct == null ? 'text-gray-400' : tfMoveUp ? 'text-green-500' : 'text-red-500'
            }`}
          >
            {tfMoveStr}
          </span>
        </div>
      </div>

      {/* Right Icons */}
      <div className="flex items-center gap-2 shrink-0">
        <button
          id="portfolio-btn"
          type="button"
          onClick={() => setPortfolioOpen(true)}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-full text-[10px] sm:text-xs font-bold border border-blue-300 dark:border-blue-700 bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 hover:opacity-90 transition"
          title="Portfolio details"
        >
          <i className="fas fa-briefcase" />
          <span className="hidden sm:inline">PORTFOLIO</span>
        </button>

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
            <div className="absolute right-0 mt-2 w-56 bg-white dark:bg-darkRow rounded-lg shadow-xl border border-gray-200 dark:border-gray-700 overflow-hidden z-50">
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
                  onOpenStatement?.();
                }}
                className="w-full px-3 py-2.5 text-left text-sm font-semibold text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-800 flex items-center gap-2 transition"
              >
                <i className="fas fa-file-invoice-dollar text-blue-500"></i>
                Trading Statement
              </button>
              <button
                type="button"
                onClick={() => {
                  setProfileOpen(false);
                  onLogout?.();
                }}
                className="w-full px-3 py-2.5 text-left text-sm font-semibold text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 flex items-center gap-2 transition border-t border-gray-200 dark:border-gray-700"
              >
                <i className="fas fa-right-from-bracket"></i>
                Logout
              </button>
            </div>
          )}
        </div>
      </div>

      <BotHelpModal open={helpOpen} onClose={() => setHelpOpen(false)} />
      <PortfolioModal
        open={portfolioOpen}
        onClose={() => setPortfolioOpen(false)}
        totalCapital={totalCapital}
        tradeValue={tradeValue}
        dailyProfit={dailyProfit}
        dailyProfitPct={dailyProfitPct}
        dailyBrokerFee={dailyBrokerFee}
        seasonProfit={seasonProfit}
        seasonProfitPct={seasonProfitPct}
        seasonActive={seasonActive}
        tradesCount={tradesCount}
        apiStatus={apiStatus}
        dayHigh={dayHigh}
        dayLow={dayLow}
        tfMovePct={tfMovePct}
        tfMoveLabel={tfMoveLabel}
        chartTimeframe={chartTimeframe}
      />
    </header>
  );
}
