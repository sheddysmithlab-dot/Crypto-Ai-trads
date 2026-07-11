import { getPairMeta, fmtNum } from '../data/pairs';
import { formatTradeFireTime } from '../utils/time';

function isTradeWinning(trade) {
  if (trade.pnl != null) return trade.pnl >= 0;
  if (trade.gross_pnl_pct != null) return trade.gross_pnl_pct >= 0;
  if (trade.side === 'LONG' && trade.entry != null && trade.current != null) {
    return trade.current >= trade.entry;
  }
  if (trade.side === 'SHORT' && trade.entry != null && trade.current != null) {
    return trade.current <= trade.entry;
  }
  return trade.pnl >= 0;
}

function StatusIcon({ trade }) {
  const winning = isTradeWinning(trade);
  if (trade.status === 'sold') {
    return <i className="fas fa-check-double text-white/80" title="Sold"></i>;
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

export default function LiveTradesPanel({ trades, activeCount, activePair, onRequestClose }) {
  return (
    <div className="bg-lightCard dark:bg-darkCard rounded-xl shadow border border-gray-200 dark:border-gray-800 overflow-hidden flex-1 flex flex-col min-h-0">
      <div className="flex justify-between items-center px-3 py-2 border-b border-gray-200 dark:border-gray-800 shrink-0">
        <h2 className="font-bold text-xs uppercase tracking-wide">
          Live Trades <span className="text-blue-500">({activeCount} Active)</span>
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
              <th className="px-3 py-1.5 font-semibold">PnL (net)</th>
              <th className="px-3 py-1.5 font-semibold text-right">Status</th>
            </tr>
          </thead>
          <tbody>
            {trades.length === 0 ? (
              <tr>
                <td colSpan={7} className="text-center py-6 text-gray-500">
                  No active positions on {activePair}. Use "+ Add Position" to open a live trade.
                </td>
              </tr>
            ) : (
              trades.map((trade) => {
                const meta = getPairMeta(trade.pair);
                const isSold = trade.status === 'sold';
                const isProtected = trade.protected || trade.source === 'manual';
                const netPnl = trade.pnl ?? 0;
                const grossPnl = trade.gross_pnl_pct;
                const isProfit = isTradeWinning(trade);
                const rowBg = isSold
                  ? 'bg-white/5 dark:bg-white/5'
                  : isProfit
                    ? 'bg-green-50 dark:bg-green-900/20'
                    : 'bg-red-50 dark:bg-red-900/20';
                const pnlColor = isSold ? 'text-white/90' : isProfit ? 'text-green-500' : 'text-red-500';
                return (
                  <tr key={trade.id} className={`${rowBg} border-b border-gray-100 dark:border-gray-800 trade-row group`}>
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
                      {trade.side} {isSold ? '(SOLD)' : isProtected ? '(MANUAL)' : ''}
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
                      <span title={grossPnl != null ? `Gross: ${grossPnl >= 0 ? '+' : ''}${grossPnl.toFixed(2)}%` : undefined}>
                        {netPnl >= 0 ? '+' : ''}
                        {netPnl.toFixed(2)}%
                        <span className="text-[9px] font-normal text-gray-500 ml-0.5">net</span>
                      </span>
                      {grossPnl != null ? (
                        <div className="text-[9px] font-normal text-gray-500">
                          {grossPnl >= 0 ? '+' : ''}
                          {grossPnl.toFixed(2)}% gross
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
              })
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
          trades.map((trade) => {
            const meta = getPairMeta(trade.pair);
            const isSold = trade.status === 'sold';
            const isProtected = trade.protected || trade.source === 'manual';
            const isProfit = isTradeWinning(trade);
            const netPnl = trade.pnl ?? 0;
            const rowBg = isSold
              ? 'bg-white/5 dark:bg-white/5'
              : isProfit
                ? 'bg-green-50 dark:bg-green-900/20'
                : 'bg-red-50 dark:bg-red-900/20';
            const pnlColor = isSold ? 'text-white/90' : isProfit ? 'text-green-500' : 'text-red-500';
            return (
              <div key={trade.id} className={`${rowBg} p-2 flex items-center justify-between trade-row`}>
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
                      {trade.side} {isSold ? '(SOLD)' : isProtected ? '(MANUAL)' : ''}
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
                    Current: <span className="text-gray-800 dark:text-gray-200 font-mono">${fmtNum(trade.current)}</span>
                  </div>
                </div>
                <div className="flex items-center gap-1.5">
                  <span className={`font-bold ${pnlColor} text-xs`}>
                    {netPnl >= 0 ? '+' : ''}
                    {netPnl.toFixed(2)}%
                    {trade.gross_pnl_pct != null ? (
                      <span className="block text-[9px] font-normal text-gray-500">
                        {trade.gross_pnl_pct >= 0 ? '+' : ''}
                        {trade.gross_pnl_pct.toFixed(2)}% gross
                      </span>
                    ) : null}
                  </span>
                  <StatusIcon trade={trade} />
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
