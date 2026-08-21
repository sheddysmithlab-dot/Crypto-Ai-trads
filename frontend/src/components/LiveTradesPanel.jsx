import { useEffect, useMemo, useState } from 'react';
import { getPairMeta, fmtNum } from '../data/pairs';
import { formatTradeFireTime } from '../utils/time';

const PAGE_SIZE = 10;

function isTradeWinning(trade) {
  // Prefer backend gross % — rounded display prices on PEPE can flip sign wrongly.
  if (trade.gross_pnl_pct != null && Number.isFinite(Number(trade.gross_pnl_pct))) {
    return Number(trade.gross_pnl_pct) >= 0;
  }
  if (trade.pnl != null && Number.isFinite(Number(trade.pnl))) {
    return Number(trade.pnl) >= 0;
  }
  if (trade.side === 'LONG' && trade.entry != null && trade.current != null) {
    return Number(trade.current) >= Number(trade.entry);
  }
  if (trade.side === 'SHORT' && trade.entry != null && trade.current != null) {
    return Number(trade.current) <= Number(trade.entry);
  }
  return false;
}

function formatMovePct(trade) {
  // Prefer backend gross % so PEPE 4dp display rounding cannot flip ±1% every tick.
  if (trade.gross_pnl_pct != null && Number.isFinite(Number(trade.gross_pnl_pct))) {
    const n = Number(trade.gross_pnl_pct);
    return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`;
  }
  if (trade.pnl != null && Number.isFinite(Number(trade.pnl))) {
    const n = Number(trade.pnl);
    return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`;
  }
  const entry = Number(trade.entry);
  const current = Number(trade.current);
  if (Number.isFinite(entry) && entry > 0 && Number.isFinite(current)) {
    let pct;
    if (trade.side === 'SHORT') {
      pct = ((entry - current) / entry) * 100;
    } else {
      pct = ((current - entry) / entry) * 100;
    }
    return `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`;
  }
  return '+0.00%';
}

function sortLatestFirst(list) {
  // Newest first, oldest last — prefer close time for exits, else open time / id.
  return [...list].sort((a, b) => {
    const ta = Number(a.closed_at || a.opened_at || 0);
    const tb = Number(b.closed_at || b.opened_at || 0);
    if (tb !== ta) return tb - ta;
    return Number(b.id || 0) - Number(a.id || 0);
  });
}

function pageSlice(list, page) {
  const start = (page - 1) * PAGE_SIZE;
  return list.slice(start, start + PAGE_SIZE);
}

function totalPages(count) {
  return Math.max(1, Math.ceil(count / PAGE_SIZE));
}

function PaginationBar({ page, total, onChange, label }) {
  if (total <= 1) return null;
  const pages = Array.from({ length: total }, (_, i) => i + 1);

  return (
    <div className="flex items-center justify-between gap-2 px-3 py-1.5 border-t border-gray-100 dark:border-gray-800 bg-gray-50/80 dark:bg-gray-900/30">
      <span className="text-[10px] text-gray-500 uppercase tracking-wider shrink-0">{label}</span>
      <div className="flex items-center gap-1 flex-wrap justify-end">
        <button
          type="button"
          disabled={page <= 1}
          onClick={() => onChange(page - 1)}
          className="px-2 py-0.5 rounded text-[10px] font-bold border border-gray-300 dark:border-gray-600 disabled:opacity-40 hover:bg-gray-200 dark:hover:bg-gray-800 transition"
          title="Previous page"
        >
          ‹ Prev
        </button>
        {pages.map((n) => (
          <button
            key={n}
            type="button"
            onClick={() => onChange(n)}
            className={`min-w-[1.5rem] px-1.5 py-0.5 rounded text-[10px] font-bold border transition ${
              n === page
                ? 'bg-blue-600 border-blue-600 text-white'
                : 'border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-800'
            }`}
          >
            {n}
          </button>
        ))}
        <button
          type="button"
          disabled={page >= total}
          onClick={() => onChange(page + 1)}
          className="px-2 py-0.5 rounded text-[10px] font-bold border border-gray-300 dark:border-gray-600 disabled:opacity-40 hover:bg-gray-200 dark:hover:bg-gray-800 transition"
          title="Next page"
        >
          Next ›
        </button>
      </div>
    </div>
  );
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
      <td className={`px-3 py-1.5 font-bold font-mono ${pnlColor}`}>
        {formatMovePct(trade)}
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
        <span className={`font-bold font-mono ${pnlColor} text-xs`}>{formatMovePct(trade)}</span>
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
  const activeAll = useMemo(
    () => sortLatestFirst(trades.filter((t) => t.status !== 'sold')),
    [trades],
  );
  const closedAll = useMemo(
    () => sortLatestFirst(trades.filter((t) => t.status === 'sold')),
    [trades],
  );

  const [livePage, setLivePage] = useState(1);
  const [exitPage, setExitPage] = useState(1);

  const livePages = totalPages(activeAll.length);
  const exitPages = totalPages(closedAll.length);

  useEffect(() => {
    if (livePage > livePages) setLivePage(livePages);
  }, [livePage, livePages]);

  useEffect(() => {
    if (exitPage > exitPages) setExitPage(exitPages);
  }, [exitPage, exitPages]);

  const active = pageSlice(activeAll, livePage);
  const closed = pageSlice(closedAll, exitPage);

  return (
    <div className="bg-lightCard dark:bg-darkCard rounded-xl shadow border border-gray-200 dark:border-gray-800 overflow-hidden flex-1 flex flex-col min-h-0">
      <div className="flex justify-between items-center px-3 py-2 border-b border-gray-200 dark:border-gray-800 shrink-0">
        <h2 className="font-bold text-xs uppercase tracking-wide">
          Live Trades <span className="text-blue-500">({activeCount} Active)</span>
          {closedAll.length > 0 ? (
            <span className="text-gray-500 font-semibold normal-case ml-2">· {closedAll.length} exited</span>
          ) : null}
        </h2>
        <span className="text-[10px] text-gray-400">10 / page</span>
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
                <th className="px-3 py-1.5 font-semibold">P&L</th>
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
                  {activeAll.length > 0 ? <SectionLabel>Open</SectionLabel> : null}
                  {active.map((trade) => (
                    <TradeRowDesktop key={trade.id} trade={trade} onRequestClose={onRequestClose} />
                  ))}
                  {activeAll.length > 0 ? (
                    <tr>
                      <td colSpan={7} className="p-0 align-top">
                        <PaginationBar
                          page={livePage}
                          total={livePages}
                          onChange={setLivePage}
                          label={`Open · page ${livePage}/${livePages}`}
                        />
                      </td>
                    </tr>
                  ) : null}
                  {closedAll.length > 0 ? <SectionLabel>Exited (booked)</SectionLabel> : null}
                  {closed.map((trade) => (
                    <TradeRowDesktop key={`sold-${trade.id}`} trade={trade} onRequestClose={onRequestClose} />
                  ))}
                  {closedAll.length > 0 ? (
                    <tr>
                      <td colSpan={7} className="p-0 align-top">
                        <PaginationBar
                          page={exitPage}
                          total={exitPages}
                          onChange={setExitPage}
                          label={`Exited · page ${exitPage}/${exitPages}`}
                        />
                      </td>
                    </tr>
                  ) : null}
                </>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Mobile List */}
      <div className="lg:hidden flex-1 min-h-0 flex flex-col overflow-hidden">
        <div className="flex-1 min-h-0 overflow-y-auto divide-y divide-gray-100 dark:divide-gray-800">
          {trades.length === 0 ? (
            <div className="text-center py-6 text-gray-500 text-sm">No active positions. All trades closed.</div>
          ) : (
            <>
              {activeAll.length > 0 ? (
                <div className="px-3 py-1.5 text-[10px] uppercase tracking-wider text-gray-500 bg-gray-50 dark:bg-gray-900/40 font-semibold">
                  Open
                </div>
              ) : null}
              {active.map((trade) => (
                <TradeRowMobile key={trade.id} trade={trade} />
              ))}
              {activeAll.length > 0 ? (
                <PaginationBar
                  page={livePage}
                  total={livePages}
                  onChange={setLivePage}
                  label={`Open · page ${livePage}/${livePages}`}
                />
              ) : null}
              {closedAll.length > 0 ? (
                <div className="px-3 py-1.5 text-[10px] uppercase tracking-wider text-gray-500 bg-gray-50 dark:bg-gray-900/40 font-semibold">
                  Exited (booked)
                </div>
              ) : null}
              {closed.map((trade) => (
                <TradeRowMobile key={`sold-${trade.id}`} trade={trade} />
              ))}
              {closedAll.length > 0 ? (
                <PaginationBar
                  page={exitPage}
                  total={exitPages}
                  onChange={setExitPage}
                  label={`Exited · page ${exitPage}/${exitPages}`}
                />
              ) : null}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
