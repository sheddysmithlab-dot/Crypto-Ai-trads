import { useEffect, useMemo, useState } from 'react';
import { getPairMeta, fmtNum } from '../data/pairs';
import { formatTradeFireTime } from '../utils/time';

const PAGE_SIZE = 10;

function sideIsLong(trade) {
  const side = String(trade.side || 'LONG').trim().toUpperCase();
  return side === 'LONG' || side === 'BUY';
}

function priceDecimals(n) {
  const abs = Math.abs(n);
  if (abs > 0 && abs < 0.0001) return 8;
  if (abs > 0 && abs < 0.01) return 6;
  if (abs > 0 && abs < 1) return 4;
  return 2;
}

/** Same rounded price the Entry/Current cells print, so % and $ match the row. */
function shownPrice(n) {
  const text = fmtNum(n);
  if (!text || text === '—') return null;
  const x = Number(String(text).replace(/,/g, ''));
  return Number.isFinite(x) && x > 0 ? x : null;
}

function displayGrossPct(trade) {
  const entry = shownPrice(trade.entry);
  const current = shownPrice(trade.current);
  if (entry != null && entry > 0 && current != null && current > 0) {
    const raw = sideIsLong(trade)
      ? ((current - entry) / entry) * 100
      : ((entry - current) / entry) * 100;
    return Number(raw.toFixed(2));
  }
  if (trade.gross_pnl_pct != null && Number.isFinite(Number(trade.gross_pnl_pct))) {
    return Number(Number(trade.gross_pnl_pct).toFixed(2));
  }
  return null;
}

function feeCostUsd(usd) {
  const n = Number(usd);
  if (!Number.isFinite(n) || n === 0) return 0;
  return Math.abs(n);
}

function feePctCost(pct) {
  const p = Number(pct);
  if (!Number.isFinite(p) || p === 0) return 0;
  return Math.abs(p);
}

/** Fee is always a cost: negative dollars, subtracted from profit, added to a loss. */
function signedFeeUsd(usd) {
  const cost = feeCostUsd(usd);
  return cost > 0 ? -cost : 0;
}

function displayGrossUsd(trade) {
  const notional = Number(trade.position_size);
  const gross = displayGrossPct(trade);
  if (!Number.isFinite(notional) || notional <= 0 || gross == null) return null;
  return Number((notional * (gross / 100)).toFixed(2));
}

function paidFeeUsd(trade) {
  const entry = feeCostUsd(trade.entry_fee_usd);
  const exit = trade.status === 'sold' ? feeCostUsd(trade.exit_fee_usd) : 0;
  return entry + exit;
}

function displayNetPct(trade) {
  const gross = displayGrossPct(trade);
  if (gross == null) return null;
  const entry = feePctCost(trade.entry_fee_pct);
  const exit = trade.status === 'sold' ? feePctCost(trade.exit_fee_pct) : 0;
  return Number((gross - entry - exit).toFixed(2));
}

function displayNetUsd(trade) {
  const grossUsd = displayGrossUsd(trade);
  const fee = paidFeeUsd(trade);
  if (grossUsd == null) return null;
  // Printed-price dollar move, then always subtract |fee| — never add it.
  return Number((grossUsd - fee).toFixed(2));
}

function isTradeWinning(trade) {
  const n = displayNetPct(trade);
  if (n != null) return n >= 0;
  return Number(trade.pnl) >= 0;
}

function formatSignedPct(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return '—';
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
}

function formatMovePct(trade) {
  const n = displayNetPct(trade);
  if (n == null) return '+0.00%';
  return formatSignedPct(n);
}

/** Notional USD size put on this trade (position_size from backend). */
function formatTradeValue(trade) {
  const n = Number(trade.position_size);
  if (!Number.isFinite(n) || n <= 0) return '—';
  return `$${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

const TABLE_COLS = 13;

function fillLabel(trade) {
  const raw = String(trade.entry_liquidity || '').toLowerCase();
  if (raw === 'maker') return 'MAKER';
  if (raw === 'taker') return 'TAKER';
  return '—';
}

function formatFeeCell(usd, pct, { pending = false } = {}) {
  if (pending) return '—';
  const signed = signedFeeUsd(usd);
  if (signed >= 0) return '—';
  const p = Number(pct);
  const pctTxt = Number.isFinite(p) && p !== 0 ? ` (−${Math.abs(p).toFixed(4)}%)` : '';
  return `-$${Math.abs(signed).toFixed(4)}${pctTxt}`;
}

function formatUsdSigned(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return '—';
  return `${v >= 0 ? '+' : '-'}$${Math.abs(v).toFixed(2)}`;
}

function netColor(trade) {
  const n = displayNetUsd(trade);
  if (!Number.isFinite(n)) return 'text-gray-400';
  return n >= 0 ? 'text-green-500' : 'text-red-500';
}

function grossColor(trade) {
  const n = displayGrossPct(trade);
  if (!Number.isFinite(n)) return 'text-gray-400';
  return n >= 0 ? 'text-green-500' : 'text-red-500';
}

function exitReasonLabel(reason) {
  const raw = String(reason || '');
  if (raw.includes('PROFIT_LOCK')) return 'Profit book';
  if (raw.includes('LOSS_BAND')) return 'Hard stop';
  if (raw.includes('LOSS_RECOVERY')) return 'Loss trail';
  if (raw.includes('Manual')) return 'Manual close';
  return raw.split('|')[0].trim().slice(0, 28);
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

function slLockOn(trade) {
  return Boolean(trade.loss_protect || trade.is_stop_active || trade.status === 'sl_lock');
}

function pathExitHint(trade) {
  if (!trade || trade.status === 'sold') return null;
  if (trade.status === 'locked' && trade.sell_trigger_pct != null) {
    return `exit ≤ +${Number(trade.sell_trigger_pct).toFixed(2)}%`;
  }
  if (slLockOn(trade)) {
    const hard = trade.stop_level_pct != null ? Number(trade.stop_level_pct) : -0.7;
    const trail = trade.sell_trigger_pct != null ? Number(trade.sell_trigger_pct) : null;
    if (trail != null && trail < -0.25 && trail > hard + 0.001) {
      return `SL lock · fail ≤ ${trail.toFixed(2)}% · hard ${hard.toFixed(2)}%`;
    }
    return `SL lock · hard ${hard.toFixed(2)}% · unlock −0.25%`;
  }
  return 'SL arm −0.50 · hard −0.70 · unlock −0.25';
}

function StatusIcon({ trade }) {
  const winning = isTradeWinning(trade);
  if (trade.status === 'sold') {
    return <i className="fas fa-check-double text-white/80" title="Sold / booked"></i>;
  }
  if (trade.status === 'locked') {
    return (
      <i
        className="fas fa-lock text-blue-400"
        title="Profit trail lock — holds until exit floor. Trash still force-closes anytime."
      ></i>
    );
  }
  if (slLockOn(trade)) {
    return (
      <i
        className="fas fa-lock text-red-400"
        title="Loss lock on — failed bounce or −0.70% hard exit. Bounce to −0.25% clears the lock."
      ></i>
    );
  }
  return winning ? (
    <i className="fas fa-check-circle text-green-500" title="In Profit"></i>
  ) : (
    <i className="fas fa-exclamation-circle text-red-500" title="At Loss"></i>
  );
}

function TradeRowDesktop({ trade, onRequestClose, onSelectTrade }) {
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

  const fill = fillLabel(trade);
  const fillClass = fill === 'MAKER' ? 'text-violet-300' : 'text-gray-400';

  return (
    <tr
      className={`${rowBg} border-b border-gray-100 dark:border-gray-800 trade-row group cursor-pointer hover:ring-1 hover:ring-inset hover:ring-cyan-500/40`}
      title="Show this trade’s neon candle on the main chart"
      onClick={() => onSelectTrade?.(trade)}
    >
      <td className="px-3 py-1.5 font-semibold whitespace-nowrap">
        <span className="inline-flex items-center gap-1.5">
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
        </span>
      </td>
      <td className={`px-3 py-1.5 ${isSold ? 'text-white/80' : trade.side === 'LONG' ? 'text-green-500' : 'text-red-500'} font-bold text-[10px] whitespace-nowrap`}>
        {trade.side} {isSold ? '(EXIT)' : isProtected ? '(MANUAL)' : ''}
        {trade.exchange === 'bybit_linear' && !isSold ? (
          <span className="text-green-400 font-bold ml-1" title="Live Bybit mainnet position">⛓</span>
        ) : null}
        {trade.exchange === 'paper' && !isSold ? (
          <span className="text-blue-400 font-bold ml-1" title="Paper simulation (same rules as live)">📄</span>
        ) : null}
      </td>
      <td className="px-3 py-1.5 font-mono text-[10px] text-gray-400 whitespace-nowrap">
        {trade.timeframe_key || '—'}
      </td>
      <td className={`px-3 py-1.5 font-bold text-[10px] whitespace-nowrap ${fillClass}`} title="Entry liquidity">
        {fill}
      </td>
      <td className="px-3 py-1.5 font-mono text-[10px] text-gray-400 whitespace-nowrap" title="Trade fire time">
        {formatTradeFireTime(trade.opened_at)}
      </td>
      <td
        className="px-3 py-1.5 font-mono text-amber-500/90 font-semibold whitespace-nowrap"
        title="Notional trade value (USD size on this position)"
      >
        {formatTradeValue(trade)}
      </td>
      <td className="px-3 py-1.5 font-mono whitespace-nowrap">${fmtNum(trade.entry)}</td>
      <td className="px-3 py-1.5 font-mono whitespace-nowrap" title={isSold ? 'Exit price' : 'Live mark'}>
        ${fmtNum(trade.current)}
      </td>
      <td className={`px-3 py-1.5 font-bold font-mono whitespace-nowrap ${pnlColor}`} title="Net % after entry/exit fees.">
        {formatMovePct(trade)}
        {!isSold && pathExitHint(trade) ? (
          <div className={`text-[9px] font-normal ${slLockOn(trade) ? 'text-red-400' : trade.status === 'locked' ? 'text-blue-400' : 'text-gray-500'}`}>
            {pathExitHint(trade)}
          </div>
        ) : null}
        {isSold && trade.closed_reason ? (
          <div className="text-[9px] font-normal text-cyan-400/90 truncate max-w-[140px]" title={trade.closed_reason}>
            {exitReasonLabel(trade.closed_reason)}
          </div>
        ) : null}
      </td>
      <td className="px-3 py-1.5 font-mono text-[10px] text-red-400 whitespace-nowrap" title="Entry fee — always a cost (minus)">
        {formatFeeCell(trade.entry_fee_usd, trade.entry_fee_pct)}
      </td>
      <td className="px-3 py-1.5 font-mono text-[10px] text-red-400 whitespace-nowrap" title={isSold ? 'Exit fee — always a cost (minus)' : 'Exit fee not paid yet'}>
        {formatFeeCell(trade.exit_fee_usd, trade.exit_fee_pct, { pending: !isSold })}
      </td>
      <td className={`px-3 py-1.5 font-bold font-mono whitespace-nowrap ${grossColor(trade)}`} title="Gross % = printed Entry vs Current, before fees">
        {displayGrossPct(trade) == null ? '—' : formatSignedPct(displayGrossPct(trade))}
      </td>
      <td className="px-3 py-1.5">
        <div className="flex items-center justify-end gap-1.5">
          <StatusIcon trade={trade} />
          {!isSold && (
            <button
              type="button"
              className={`p-1 bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400 rounded hover:bg-red-200 dark:hover:bg-red-900/50 transition ${
                trade.status === 'locked'
                  ? 'opacity-100 ring-1 ring-red-400/50'
                  : 'opacity-100 sm:opacity-0 sm:group-hover:opacity-100'
              }`}
              title={
                trade.status === 'locked'
                  ? 'Force Close now (trailing lock does not block manual exit)'
                  : 'Force Close (confirmation required)'
              }
              onClick={(e) => {
                e.stopPropagation();
                onRequestClose(trade.id);
              }}
            >
              <i className="fas fa-trash text-[10px]"></i>
            </button>
          )}
        </div>
      </td>
    </tr>
  );
}

function TradeRowMobile({ trade, onSelectTrade, onRequestClose }) {
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
    <div
      className={`${rowBg} p-2 flex items-center justify-between trade-row cursor-pointer active:opacity-80`}
      title="Show this trade’s neon candle on the main chart"
      onClick={() => onSelectTrade?.(trade)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onSelectTrade?.(trade);
        }
      }}
    >
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
          <div className="text-[9px] text-amber-500/90 font-mono font-semibold mt-0.5" title="Notional trade value">
            Value: {formatTradeValue(trade)}
          </div>
          <div className="text-[9px] text-gray-400 font-mono mt-0.5">
            {trade.timeframe_key || '—'} · {fillLabel(trade)}
          </div>
          <div className="text-[9px] text-red-400 font-mono mt-0.5">
            Entry fee {formatFeeCell(trade.entry_fee_usd, trade.entry_fee_pct)}
          </div>
          <div className="text-[9px] text-red-400 font-mono">
            Exit fee {formatFeeCell(trade.exit_fee_usd, trade.exit_fee_pct, { pending: !isSold })}
          </div>
          <div className={`text-[9px] font-mono font-semibold ${grossColor(trade)}`}>
            Gross {displayGrossPct(trade) == null ? '—' : formatSignedPct(displayGrossPct(trade))}
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
        <span className={`font-bold font-mono ${pnlColor} text-xs`}>
          {formatMovePct(trade)}
          {!isSold && pathExitHint(trade) ? (
            <div className={`text-[8px] font-normal ${slLockOn(trade) ? 'text-red-400' : trade.status === 'locked' ? 'text-blue-400' : 'text-gray-500'}`}>
              {pathExitHint(trade)}
            </div>
          ) : null}
        </span>
        <StatusIcon trade={trade} />
        {!isSold && (
          <button
            type="button"
            className="p-1.5 bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400 rounded"
            title="Force Close (confirmation required)"
            onClick={(e) => {
              e.stopPropagation();
              onRequestClose?.(trade.id);
            }}
          >
            <i className="fas fa-trash text-[10px]"></i>
          </button>
        )}
      </div>
    </div>
  );
}

function SectionLabel({ children }) {
  return (
    <tr>
      <td colSpan={TABLE_COLS} className="px-3 py-1.5 text-[10px] uppercase tracking-wider text-gray-500 bg-gray-50 dark:bg-gray-900/40 font-semibold">
        {children}
      </td>
    </tr>
  );
}

export default function LiveTradesPanel({ trades, activeCount, activePair, onRequestClose, onSelectTrade }) {
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
    <div className="bg-lightCard dark:bg-darkCard rounded-xl shadow border border-gray-200 dark:border-gray-800 overflow-hidden shrink-0 flex flex-col min-h-[280px] sm:min-h-[320px]">
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
      <div className="hidden lg:flex flex-col min-h-[220px] max-h-[min(48vh,520px)] overflow-hidden">
        <div className="flex-1 min-h-0 overflow-x-auto overflow-y-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-gray-500 dark:text-gray-400 text-[10px] uppercase border-b border-gray-200 dark:border-gray-800">
                <th className="px-3 py-1.5 font-semibold">Asset</th>
                <th className="px-3 py-1.5 font-semibold">Side</th>
                <th className="px-3 py-1.5 font-semibold">TF</th>
                <th className="px-3 py-1.5 font-semibold" title="Entry fill: maker limit or taker market">Fill</th>
                <th className="px-3 py-1.5 font-semibold">Fired</th>
                <th className="px-3 py-1.5 font-semibold" title="Notional USD size on this trade">Value</th>
                <th className="px-3 py-1.5 font-semibold">Entry</th>
                <th className="px-3 py-1.5 font-semibold">Current</th>
                <th className="px-3 py-1.5 font-semibold" title="Net % after entry/exit fees">Net %</th>
                <th className="px-3 py-1.5 font-semibold">Entry fee</th>
                <th className="px-3 py-1.5 font-semibold">Exit fee</th>
                <th className="px-3 py-1.5 font-semibold" title="Price move % before fees (printed Entry vs Current)">Gross %</th>
                <th className="px-3 py-1.5 font-semibold text-right">Status</th>
              </tr>
            </thead>
            <tbody>
              {trades.length === 0 ? (
                <tr>
                  <td colSpan={TABLE_COLS} className="text-center py-6 text-gray-500">
                    No active positions on {activePair}. Use &quot;+ Add Position&quot; to open a live trade.
                  </td>
                </tr>
              ) : (
                <>
                  {activeAll.length > 0 ? <SectionLabel>Open</SectionLabel> : null}
                  {active.map((trade) => (
                    <TradeRowDesktop
                      key={trade.id}
                      trade={trade}
                      onRequestClose={onRequestClose}
                      onSelectTrade={onSelectTrade}
                    />
                  ))}
                  {activeAll.length > 0 ? (
                    <tr>
                      <td colSpan={TABLE_COLS} className="p-0 align-top">
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
                    <TradeRowDesktop
                      key={`sold-${trade.id}`}
                      trade={trade}
                      onRequestClose={onRequestClose}
                      onSelectTrade={onSelectTrade}
                    />
                  ))}
                  {closedAll.length > 0 ? (
                    <tr>
                      <td colSpan={TABLE_COLS} className="p-0 align-top">
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
      <div className="lg:hidden flex flex-col min-h-[220px] max-h-[min(48vh,520px)] overflow-hidden">
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
                <TradeRowMobile
                  key={trade.id}
                  trade={trade}
                  onSelectTrade={onSelectTrade}
                  onRequestClose={onRequestClose}
                />
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
                <TradeRowMobile key={`sold-${trade.id}`} trade={trade} onSelectTrade={onSelectTrade} />
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
