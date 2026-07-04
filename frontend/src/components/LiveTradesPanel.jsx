import { getPairMeta, fmtNum } from '../data/pairs';

function StatusIcon({ trade }) {
  if (trade.status === 'locked') {
    return <i className="fas fa-lock text-blue-400" title="Trailing Lock Active"></i>;
  }
  return trade.pnl >= 0 ? (
    <i className="fas fa-check-circle text-green-500" title="In Profit"></i>
  ) : (
    <i className="fas fa-exclamation-circle text-red-500" title="At Loss"></i>
  );
}

export default function LiveTradesPanel({ trades, activePair, closeTrade }) {
  return (
    <div className="bg-lightCard dark:bg-darkCard rounded-xl shadow border border-gray-200 dark:border-gray-800 overflow-hidden">
      <div className="flex justify-between items-center px-3 py-2 border-b border-gray-200 dark:border-gray-800">
        <h2 className="font-bold text-xs uppercase tracking-wide">
          Live Trades <span className="text-blue-500">({trades.length} Active)</span>
        </h2>
      </div>

      {/* Desktop Table */}
      <div className="hidden lg:block overflow-x-auto max-h-64 overflow-y-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-gray-500 dark:text-gray-400 text-[10px] uppercase border-b border-gray-200 dark:border-gray-800">
              <th className="px-3 py-1.5 font-semibold">Asset</th>
              <th className="px-3 py-1.5 font-semibold">Side</th>
              <th className="px-3 py-1.5 font-semibold">Entry</th>
              <th className="px-3 py-1.5 font-semibold">Current</th>
              <th className="px-3 py-1.5 font-semibold">PnL (%)</th>
              <th className="px-3 py-1.5 font-semibold text-right">Status</th>
            </tr>
          </thead>
          <tbody>
            {trades.length === 0 ? (
              <tr>
                <td colSpan={6} className="text-center py-6 text-gray-500">
                  No active positions on {activePair}. Use "+ Add Position" to open a live trade.
                </td>
              </tr>
            ) : (
              trades.map((trade) => {
                const meta = getPairMeta(trade.pair);
                const isProfit = trade.pnl >= 0;
                const rowBg = isProfit ? 'bg-green-50 dark:bg-green-900/20' : 'bg-red-50 dark:bg-red-900/20';
                const pnlColor = isProfit ? 'text-green-500' : 'text-red-500';
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
                    </td>
                    <td className={`px-3 py-1.5 ${trade.side === 'LONG' ? 'text-green-500' : 'text-red-500'} font-bold text-[10px]`}>
                      {trade.side}
                    </td>
                    <td className="px-3 py-1.5 font-mono">${fmtNum(trade.entry)}</td>
                    <td className="px-3 py-1.5 font-mono">${fmtNum(trade.current)}</td>
                    <td className={`px-3 py-1.5 font-bold ${pnlColor}`}>
                      {isProfit ? '+' : ''}
                      {trade.pnl.toFixed(2)}%
                    </td>
                    <td className="px-3 py-1.5">
                      <div className="flex items-center justify-end gap-1.5">
                        <StatusIcon trade={trade} />
                        <button
                          className="p-1 bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400 rounded hover:bg-red-200 dark:hover:bg-red-900/50 transition opacity-0 group-hover:opacity-100"
                          title="Force Close"
                          onClick={() => closeTrade(trade.id)}
                        >
                          <i className="fas fa-trash text-[10px]"></i>
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* Mobile List */}
      <div className="lg:hidden divide-y divide-gray-100 dark:divide-gray-800 max-h-64 overflow-y-auto">
        {trades.length === 0 ? (
          <div className="text-center py-6 text-gray-500 text-sm">No active positions. All trades closed.</div>
        ) : (
          trades.map((trade) => {
            const meta = getPairMeta(trade.pair);
            const isProfit = trade.pnl >= 0;
            const rowBg = isProfit ? 'bg-green-50 dark:bg-green-900/20' : 'bg-red-50 dark:bg-red-900/20';
            const pnlColor = isProfit ? 'text-green-500' : 'text-red-500';
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
                    <div className={`text-[10px] ${trade.side === 'LONG' ? 'text-green-500' : 'text-red-500'} font-bold`}>
                      {trade.side}
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
                    {isProfit ? '+' : ''}
                    {trade.pnl.toFixed(2)}%
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
