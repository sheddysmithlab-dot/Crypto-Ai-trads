import { fmtNum } from '../data/pairs';
import { formatTfMoveLabel } from '../hooks/useTfMoveStats';
import InfoTip from './InfoTip';

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

function StatCell({ label, tip, children }) {
  return (
    <div className="flex flex-col min-w-0 rounded-lg bg-gray-50 dark:bg-gray-800/50 px-3 py-2.5 border border-gray-200 dark:border-gray-700/80">
      <span className="flex items-center gap-1 text-gray-500 dark:text-gray-400 text-[10px] uppercase tracking-wider">
        <span className="truncate">{label}</span>
        {tip ? <InfoTip text={tip} /> : null}
      </span>
      <div className="font-bold text-sm mt-0.5 truncate">{children}</div>
    </div>
  );
}

export default function PortfolioModal({
  open,
  onClose,
  totalCapital,
  tradeValue = 0,
  dailyProfit,
  dailyProfitPct,
  dailyBrokerFee = 0,
  seasonProfit,
  seasonProfitPct,
  seasonActive,
  tradesCount,
  apiStatus,
  dayHigh,
  dayLow,
  tfMovePct = null,
  tfMoveLabel = null,
  chartTimeframe = '1M',
}) {
  if (!open) return null;

  const isProfit = dailyProfit >= 0;
  const isSeasonProfit = seasonProfit >= 0;
  const capStr = Number(totalCapital || 0).toLocaleString('en-US', { minimumFractionDigits: 2 });
  const tradeValStr = Number(tradeValue || 0).toLocaleString('en-US', { minimumFractionDigits: 2 });
  const profitStr = `${isProfit ? '+' : '-'}$${Math.abs(dailyProfit).toLocaleString('en-US', {
    minimumFractionDigits: 2,
  })} (${isProfit ? '+' : ''}${Number(dailyProfitPct || 0).toFixed(2)}%)`;
  const feeStr = `-$${Math.abs(Number(dailyBrokerFee) || 0).toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
  const seasonStr = `${isSeasonProfit ? '+' : '-'}$${Math.abs(seasonProfit).toLocaleString('en-US', {
    minimumFractionDigits: 2,
  })} (${isSeasonProfit ? '+' : ''}${Number(seasonProfitPct || 0).toFixed(2)}%)`;
  const tfMoveUp = tfMovePct != null && tfMovePct >= 0;
  const tfMoveStr =
    tfMovePct != null
      ? `${tfMoveUp ? '+' : ''}${Math.abs(tfMovePct) < 0.005 && tfMovePct !== 0 ? tfMovePct.toFixed(3) : tfMovePct.toFixed(2)}%`
      : '--';
  const tfMoveTitle = formatTfMoveLabel(chartTimeframe, tfMoveLabel);

  return (
    <div
      className="fixed inset-0 z-[80] flex items-start sm:items-center justify-center p-3 sm:p-6 bg-black/50 backdrop-blur-[2px]"
      onClick={onClose}
      role="presentation"
    >
      <div
        className="w-full max-w-2xl bg-white dark:bg-darkCard rounded-xl shadow-2xl border border-gray-200 dark:border-gray-700 overflow-hidden"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="portfolio-modal-title"
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-700">
          <h2 id="portfolio-modal-title" className="text-sm font-bold tracking-wide uppercase text-gray-800 dark:text-gray-100">
            <i className="fas fa-briefcase mr-2 text-blue-500" />
            Portfolio
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="w-8 h-8 rounded-full hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-500 dark:text-gray-400 transition"
            aria-label="Close portfolio"
          >
            <i className="fas fa-times" />
          </button>
        </div>

        <div className="p-4 grid grid-cols-2 sm:grid-cols-3 gap-2.5">
          <StatCell label="Total Capital" tip="Cash available for new trades.">
            <span className="text-gray-900 dark:text-white">${capStr}</span>
          </StatCell>
          <StatCell label="Trade Value" tip="Open trade size for this AI session. Freezes on STOP, resets on START.">
            <span className="text-amber-500">${tradeValStr}</span>
          </StatCell>
          <StatCell label="Session Profit (Gross)" tip="Session trade profit/loss before fees. Fees are shown on a separate line.">
            <span className={isProfit ? 'text-green-500' : 'text-red-500'}>{profitStr}</span>
          </StatCell>
          <StatCell
            label="Session Bybit Broker Fee"
            tip="Broker fees for this session. Shown separately from gross profit."
          >
            <span className="text-amber-400">{feeStr}</span>
          </StatCell>
          <StatCell label="AI Season Profit (Gross)" tip="Gross profit for the current AI season (fees not deducted).">
            <span className={isSeasonProfit ? 'text-green-500' : 'text-red-500'}>
              {seasonStr}
            </span>
          </StatCell>
          <StatCell label="Open Positions" tip="How many trades are open in this AI session.">
            <span className="text-gray-900 dark:text-white">
              {tradesCount}{' '}
              <span className="text-xs font-normal text-gray-400">(Active)</span>
            </span>
          </StatCell>
          <StatCell label="24H High / Low">
            {dayHigh != null ? (
              <span>
                <span className="text-green-500">{fmtNum(dayHigh)}</span>
                <span className="text-gray-400 font-normal"> / </span>
                <span className="text-red-500">{fmtNum(dayLow)}</span>
              </span>
            ) : (
              <span className="text-gray-400 font-normal">--</span>
            )}
          </StatCell>
          <StatCell label={`Market ${chartTimeframe}`} tip={`Average move per ${chartTimeframe} candle (${tfMoveTitle}).`}>
            <span
              className={tfMovePct == null ? 'text-gray-400' : tfMoveUp ? 'text-green-500' : 'text-red-500'}
              >
              {tfMoveStr}
              {tfMoveLabel ? (
                <span className="text-[10px] font-normal text-gray-400 ml-1.5">
                  {(formatTfMoveLabel(chartTimeframe, tfMoveLabel).split('·')[1] || tfMoveLabel).trim()}
                </span>
              ) : null}
            </span>
          </StatCell>
          <StatCell label="API Status">
            <span className={`flex items-center ${STATUS_COLOR[apiStatus?.color] || 'text-gray-400'}`}>
              <span
                className={`w-2 h-2 rounded-full mr-1.5 animate-pulse ${DOT_COLOR[apiStatus?.color] || 'bg-gray-400'}`}
              />
              {apiStatus?.label || '--'}
            </span>
          </StatCell>
        </div>
      </div>
    </div>
  );
}
