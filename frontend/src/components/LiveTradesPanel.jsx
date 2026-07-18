import { getPairMeta, fmtNum } from '../data/pairs';
import { formatTradeFireTime } from '../utils/time';

function isTradeWinning(trade) {
  if (trade.pnl != null) return trade.pnl >= 0;
  if (trade.side === 'LONG' && trade.entry != null && trade.current != null) {
    return trade.current >= trade.entry;
  }
  if (trade.side === 'SHORT' && trade.entry != null && trade.current != null) {
    return trade.current <= trade.entry;
  }
  return false;
}

function formatTotalPnl(pnl) {
  const n = Number(pnl) || 0;
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`;
}

function StatusIcon({ trade }) {
  const winning = isTradeWinning(trade);
  if (trade.status === 'sold') {
    return <i className="fas fa-check-double text-white/80" title="Sold / booked"></i>;
  }
  if (trade.status === 'locked') {
    return <i className="fas fa-lock text-blue-400" title="Trailing Lock Active"></i>;
  }
  return winning ? (
    <i className="fas fa-check-circle text-green-500" title="In Profit"></i>
  ) : (
    <i className="fas fa-exclamation-circle text-red-500" title="At Loss"></i>
  );
}

function TradeRowDesktop({ trade, onRequestClose }) {
  const meta = getPairMeta(trade.pair);
  const isSold = trade.status === 'sold';
  const isProtected = trade.protected || trade.source === 'manual';
  const totalPnl = trade.pnl ?? 0;
  const isProfit = isTradeWinning(trade);
  const rowBg = isSold
    ? 'bg-white/5 dark:bg-white/5 opacity-90'
    : isProfit
      ? 'bg-green-50 dark:bg-green-900/20'
      : 'bg-red-50 dark:bg-red-900/20';
  const pnlColor = isSold ? 'text-white/90' : isProfit ? 'text-green-500' : 'text-red-500';

  return (
    <tr className={`${rowBg} border-b border-gray-100 dark:border-gray-800 trade-row group`}>
      <td className="px-3 py-1.5 font-semibold flex items-center gap-1.5">
        <span
          className="w-4 h-4 rounded-full flex items-center justify-center text-[9px] font-bold text-white"
          style={{ background: meta.color }}
        >
          {meta.icon}
        </span>
        {trade.pair}
        {isProtected && !isSold ? (
          <span className="text-[9px] text-amber-400 font-bold" title="Manual position — AI cannot auto-close">
            <i className="fas fa-shield-alt"></i>
          </span>
        ) : null}
      </td>
      <td className={`px-3 py-1.5 ${isSold ? 'text-white/80' : trade.side === 'LONG' ? 'text-green-500' : 'text-red-500'} font-bold text-[10px]`}>
        {trade.side} {isSold ? '(EXIT)' : isProtected ? '(MANUAL)' : ''}
        {trade.exchange === 'bybit_linear_testnet' && !isSold ? (
          <span className="text-amber-400 font-bold ml-1" title="Real Bybit TESTNET position">⛓</span>
        ) : null}
        {trade.exchange === 'paper' && !isSold ? (
          <span className="text-blue-400 font-bold ml-1" title="Paper simulation (same rules as live)">📄</span>
        ) : null}
      </td>
      <td className="px-3 py-1.5 font-mono text-[10px] text-gray-400 whitespace-nowrap" title="Trade fire time">
        {formatTradeFireTime(trade.opened_at)}
      </td>
      <td className="px-3 py-1.5 font-mono">${fmtNum(trade.entry)}</td>
      <td className="px-3 py-1.5 font-mono">${fmtNum(trade.current)}</td>
      <td className={`px-3 py-1.5 font-bold ${pnlColor}`}>
        <span title="Total P&L after entry + exit broker fees">
          {formatTotalPnl(totalPnl)}
          <span className="text-[9px] font-normal text-gray-500 ml-0.5">total</span>
        </span>
        {!isSold && trade.status === 'locked' && trade.sell_trigger_pct != null ? (
          <div className="text-[9px] font-normal text-blue-400">
            exit ≤ +{Number(trade.sell_trigger_pct).toFixed(2)}%
          </div>
        ) : null}
        {isSold && trade.closed_reason ? (
          <div className="text-[9px] font-normal text-cyan-400/90 truncate max-w-[180px]" title={trade.closed_reason}>
            {trade.closed_reason}
          </div>
        ) : null}
      </td>
      <td className="px-3 py-1.5">
        <div className="flex items-center justify-end gap-1.5">
          <StatusIcon trade={trade} />
          {!isSold && (
            <button
              className="p-1 bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400 rounded hover:bg-red-200 dark:hover:bg-red-900/50 transition opacity-0 group-hover:opacity-100"
              title="Force Close (confirmation required)"
              onClick={() => onRequestClose(trade.id)}
            >
              <i className="fas fa-trash text-[10px]"></i>
            </button>
          )}
        </div>
      </td>
    </tr>
  );
}

function TradeRowMobile({ trade }) {
  const meta = getPairMeta(trade.pair);
  const isSold = trade.status === 'sold';
  const isProtected = trade.protected || trade.source === 'manual';
  const isProfit = isTradeWinning(trade);
  const totalPnl = trade.pnl ?? 0;
  const rowBg = isSold
    ? 'bg-white/5 dark:bg-white/5 opacity-90'
    : isProfit
      ? 'bg-green-50 dark:bg-green-900/20'
      : 'bg-red-50 dark:bg-red-900/20';
  const pnlColor = isSold ? 'text-white/90' : isProfit ? 'text-green-500' : 'text-red-500';

  return (
    <div className={`${rowBg} p-2 flex items-center justify-between trade-row`}>
      <div className="flex items-center gap-1.5">
        <span
          className="w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold text-white"
          style={{ background: meta.color }}
        >
          {meta.icon}
        </span>
        <div>
          <div className="font-semibold text-xs">{trade.pair}</div>
          <div className={`text-[10px] ${isSold ? 'text-white/80' : trade.side === 'LONG' ? 'text-green-500' : 'text-red-500'} font-bold`}>
            {trade.side} {isSold ? '(EXIT)' : isProtected ? '(MANUAL)' : ''}
          </div>
          <div className="text-[9px] text-gray-500 font-mono mt-0.5">
            Fired: {formatTradeFireTime(trade.opened_at)}
          </div>
        </div>
      </div>
      <div className="text-right text-[10px] text-gray-500 dark:text-gray-400">
        <div>
          Entry: <span className="text-gray-800 dark:text-gray-200 font-mono">${fmtNum(trade.entry)}</span>
        </div>
        <div>
          {isSold ? 'Exit' : 'Current'}:{' '}
          <span className="text-gray-800 dark:text-gray-200 font-mono">${fmtNum(trade.current)}</span>
        </div>
      </div>
      <div className="flex items-center gap-1.5">
        <span className={`font-bold ${pnlColor} text-xs`} title="Total P&L after broker fees">
          {formatTotalPnl(totalPnl)}
          <span className="block text-[9px] font-normal text-gray-500">total</span>
        </span>
        <StatusIcon trade={trade} />
      </div>
    </div>
  );
}

function SectionLabel({ children }) {
  return (
    <tr>
      <td colSpan={7} className="px-3 py-1.5 text-[10px] uppercase tracking-wider text-gray-500 bg-gray-50 dark:bg-gray-900/40 font-semibold">
        {children}
      </td>
    </tr>
  );
}

export default function LiveTradesPanel({ trades, activeCount, activePair, onRequestClose }) {
  const active = trades.filter((t) => t.status !== 'sold');
  const closed = trades.filter((t) => t.status === 'sold');

  return (
    <div className="bg-lightCard dark:bg-darkCard rounded-xl shadow border border-gray-200 dark:border-gray-800 overflow-hidden flex-1 flex flex-col min-h-0">
      <div className="flex justify-between items-center px-3 py-2 border-b border-gray-200 dark:border-gray-800 shrink-0">
        <h2 className="font-bold text-xs uppercase tracking-wide">
          Live Trades <span className="text-blue-500">({activeCount} Active)</span>
          {closed.length > 0 ? (
            <span className="text-gray-500 font-semibold normal-case ml-2">· {closed.length} exited</span>
          ) : null}
        </h2>
      </div>

      {/* Desktop Table */}
      <div className="hidden lg:flex flex-1 min-h-0 flex-col overflow-hidden">
        <div className="flex-1 min-h-0 overflow-x-auto overflow-y-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-gray-500 dark:text-gray-400 text-[10px] uppercase border-b border-gray-200 dark:border-gray-800">
                <th className="px-3 py-1.5 font-semibold">Asset</th>
                <th className="px-3 py-1.5 font-semibold">Side</th>
                <th className="px-3 py-1.5 font-semibold">Fired</th>
                <th className="px-3 py-1.5 font-semibold">Entry</th>
                <th className="px-3 py-1.5 font-semibold">Current</th>
                <th className="px-3 py-1.5 font-semibold">Total</th>
                <th className="px-3 py-1.5 font-semibold text-right">Status</th>
              </tr>
            </thead>
            <tbody>
              {trades.length === 0 ? (
                <tr>
                  <td colSpan={7} className="text-center py-6 text-gray-500">
                    No active positions on {activePair}. Use &quot;+ Add Position&quot; to open a live trade.
                  </td>
                </tr>
              ) : (
                <>
                  {active.length > 0 ? <SectionLabel>Open</SectionLabel> : null}
                  {active.map((trade) => (
                    <TradeRowDesktop key={trade.id} trade={trade} onRequestClose={onRequestClose} />
                  ))}
                  {closed.length > 0 ? <SectionLabel>Exited (booked)</SectionLabel> : null}
                  {closed.map((trade) => (
                    <TradeRowDesktop key={`sold-${trade.id}`} trade={trade} onRequestClose={onRequestClose} />
                  ))}
                </>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Mobile List */}
      <div className="lg:hidden flex-1 min-h-0 overflow-y-auto divide-y divide-gray-100 dark:divide-gray-800">
        {trades.length === 0 ? (
          <div className="text-center py-6 text-gray-500 text-sm">No active positions. All trades closed.</div>
        ) : (
          <>
            {active.map((trade) => (
              <TradeRowMobile key={trade.id} trade={trade} />
            ))}
            {closed.length > 0 ? (
              <div className="px-3 py-1.5 text-[10px] uppercase tracking-wider text-gray-500 bg-gray-50 dark:bg-gray-900/40 font-semibold">
                Exited (booked)
              </div>
            ) : null}
            {closed.map((trade) => (
              <TradeRowMobile key={`sold-${trade.id}`} trade={trade} />
            ))}
          </>
        )}
      </div>
    </div>
  );
}
