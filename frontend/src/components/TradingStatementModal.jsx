import { useEffect, useState } from 'react';
import { authFetch } from '../config/api';

function fmtUsd(n) {
  const v = Number(n) || 0;
  const sign = v > 0 ? '+' : v < 0 ? '-' : '';
  return `${sign}$${Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function fmtPct(n) {
  const v = Number(n) || 0;
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
}

function fmtTime(epoch) {
  if (!epoch) return '—';
  try {
    return new Date(Number(epoch) * 1000).toLocaleString('en-IN', {
      timeZone: 'Asia/Kolkata',
      day: '2-digit',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return '—';
  }
}

export default function TradingStatementModal({ open, onClose }) {
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState(null);
  const [seasons, setSeasons] = useState([]);
  const [filter, setFilter] = useState('all'); // all | sold | active
  const [view, setView] = useState('trades'); // trades | seasons
  const [seasonId, setSeasonId] = useState(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    async function load() {
      setLoading(true);
      try {
        const seasonsRes = await authFetch('/trades/seasons?limit=50');
        const seasonsJson = await seasonsRes.json().catch(() => ({}));
        if (!cancelled && Array.isArray(seasonsJson.seasons)) {
          setSeasons(seasonsJson.seasons);
        }

        if (view === 'seasons') {
          if (!cancelled) setData({ ok: true, enabled: Boolean(seasonsJson.enabled), rows: [] });
          return;
        }

        const parts = [`limit=300`];
        if (filter !== 'all') parts.push(`status=${filter}`);
        if (seasonId) parts.push(`season_id=${seasonId}`);
        const res = await authFetch(`/trades/statement?${parts.join('&')}`);
        const json = await res.json();
        if (!cancelled) setData(json);
      } catch {
        if (!cancelled) {
          setData({ ok: false, enabled: false, rows: [], summary: {}, message: 'Could not load statement' });
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [open, filter, view, seasonId]);

  if (!open) return null;

  const summary = data?.summary || {};
  const rows = data?.rows || [];
  const dbOk = Boolean(data?.ok);
  const dbEnabled = Boolean(data?.enabled);

  return (
    <div
      className="fixed inset-0 bg-black/70 z-[120] flex items-center justify-center backdrop-blur-sm p-3"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="bg-[#0B0E11] border border-gray-800 rounded-2xl shadow-2xl w-full max-w-5xl max-h-[90vh] flex flex-col overflow-hidden">
        <div className="flex items-start justify-between px-5 pt-5 pb-3 border-b border-gray-800">
          <div>
            <div className="text-[10px] font-bold text-blue-400 uppercase tracking-widest">Account</div>
            <h2 className="text-lg font-bold text-white mt-0.5">Trading Statement</h2>
            <p className="text-[11px] text-gray-500 mt-1">
              Season-wise history from MySQL ·{' '}
              {dbEnabled ? (dbOk ? 'DB connected' : data?.message || 'DB error') : 'MySQL not configured yet'}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="w-8 h-8 rounded-lg bg-[#161A1E] border border-gray-700 text-gray-400 hover:text-white"
          >
            <i className="fas fa-times"></i>
          </button>
        </div>

        <div className="px-5 py-3 flex flex-wrap gap-2 border-b border-gray-800 items-center">
          {[
            { id: 'trades', label: 'Trades' },
            { id: 'seasons', label: 'Seasons' },
          ].map((f) => (
            <button
              key={f.id}
              type="button"
              onClick={() => setView(f.id)}
              className={`px-3 py-1.5 rounded-lg text-[11px] font-bold uppercase tracking-wide border transition ${
                view === f.id
                  ? 'bg-indigo-600/30 border-indigo-500 text-indigo-300'
                  : 'bg-[#161A1E] border-gray-700 text-gray-400 hover:text-white'
              }`}
            >
              {f.label}
            </button>
          ))}

          {view === 'trades' ? (
            <>
              {[
                { id: 'all', label: 'All' },
                { id: 'sold', label: 'Closed' },
                { id: 'active', label: 'Open' },
              ].map((f) => (
                <button
                  key={f.id}
                  type="button"
                  onClick={() => setFilter(f.id)}
                  className={`px-3 py-1.5 rounded-lg text-[11px] font-bold uppercase tracking-wide border transition ${
                    filter === f.id
                      ? 'bg-blue-600/30 border-blue-500 text-blue-300'
                      : 'bg-[#161A1E] border-gray-700 text-gray-400 hover:text-white'
                  }`}
                >
                  {f.label}
                </button>
              ))}
              <select
                value={seasonId || ''}
                onChange={(e) => setSeasonId(e.target.value ? Number(e.target.value) : null)}
                className="ml-auto px-2 py-1.5 rounded-lg text-[11px] bg-[#161A1E] border border-gray-700 text-gray-300"
              >
                <option value="">All seasons</option>
                {seasons.map((s) => (
                  <option key={s.id} value={s.id}>
                    #{s.id} · {fmtTime(s.started_at)} · {fmtUsd(s.net_pnl_usd)}
                  </option>
                ))}
              </select>
            </>
          ) : null}
        </div>

        {view === 'trades' ? (
          <div className="px-5 py-3 grid grid-cols-2 sm:grid-cols-4 gap-2 border-b border-gray-800">
            <div className="bg-[#161A1E] rounded-lg px-3 py-2">
              <div className="text-[10px] text-gray-500 uppercase">Trades</div>
              <div className="text-sm font-bold text-white">{summary.total_trades ?? 0}</div>
            </div>
            <div className="bg-[#161A1E] rounded-lg px-3 py-2">
              <div className="text-[10px] text-gray-500 uppercase">W / L</div>
              <div className="text-sm font-bold">
                <span className="text-green-400">{summary.wins ?? 0}</span>
                <span className="text-gray-600"> / </span>
                <span className="text-red-400">{summary.losses ?? 0}</span>
              </div>
            </div>
            <div className="bg-[#161A1E] rounded-lg px-3 py-2">
              <div className="text-[10px] text-gray-500 uppercase">Net P&L</div>
              <div
                className={`text-sm font-bold ${(summary.net_pnl_usd || 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}
              >
                {fmtUsd(summary.net_pnl_usd)}
              </div>
            </div>
            <div className="bg-[#161A1E] rounded-lg px-3 py-2">
              <div className="text-[10px] text-gray-500 uppercase">Fees</div>
              <div className="text-sm font-bold text-amber-400">
                -{fmtUsd(summary.fees_usd).replace(/^[+-]/, '')}
              </div>
            </div>
          </div>
        ) : null}

        <div className="flex-1 overflow-auto px-2 sm:px-4 py-3">
          {loading ? (
            <div className="text-center text-gray-500 text-sm py-12">Loading statement…</div>
          ) : !dbEnabled ? (
            <div className="text-center text-gray-400 text-sm py-10 px-4 leading-relaxed">
              MySQL abhi connect nahi hai.
              <br />
              Hostinger hPanel → MySQL Databases → DB banao, schema.sql run karo, phir VPS{' '}
              <code className="text-blue-400">backend/.env</code> mein MYSQL_* keys set karo.
            </div>
          ) : view === 'seasons' ? (
            seasons.length === 0 ? (
              <div className="text-center text-gray-500 text-sm py-12">No AI seasons saved yet.</div>
            ) : (
              <table className="w-full text-left text-[11px] sm:text-xs">
                <thead className="text-gray-500 uppercase tracking-wider sticky top-0 bg-[#0B0E11]">
                  <tr>
                    <th className="px-2 py-2 font-semibold">Season</th>
                    <th className="px-2 py-2 font-semibold">Started</th>
                    <th className="px-2 py-2 font-semibold">Ended</th>
                    <th className="px-2 py-2 font-semibold">Trades</th>
                    <th className="px-2 py-2 font-semibold">W / L</th>
                    <th className="px-2 py-2 font-semibold">Net $</th>
                    <th className="px-2 py-2 font-semibold">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {seasons.map((s) => {
                    const net = Number(s.net_pnl_usd) || 0;
                    return (
                      <tr
                        key={s.id}
                        className="border-t border-gray-800/80 hover:bg-white/[0.03] cursor-pointer"
                        onClick={() => {
                          setSeasonId(s.id);
                          setView('trades');
                        }}
                        title="View trades in this season"
                      >
                        <td className="px-2 py-2 font-bold text-indigo-300">#{s.id}</td>
                        <td className="px-2 py-2 text-gray-400 whitespace-nowrap">{fmtTime(s.started_at)}</td>
                        <td className="px-2 py-2 text-gray-400 whitespace-nowrap">{fmtTime(s.ended_at)}</td>
                        <td className="px-2 py-2 text-white">{s.trade_count ?? 0}</td>
                        <td className="px-2 py-2">
                          <span className="text-green-400">{s.win_count ?? 0}</span>
                          <span className="text-gray-600"> / </span>
                          <span className="text-red-400">{s.loss_count ?? 0}</span>
                        </td>
                        <td className={`px-2 py-2 font-semibold ${net >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                          {fmtUsd(net)}
                        </td>
                        <td className="px-2 py-2">
                          <span
                            className={`px-1.5 py-0.5 rounded text-[10px] font-bold uppercase ${
                              s.status === 'active'
                                ? 'bg-emerald-900/40 text-emerald-300'
                                : 'bg-gray-700 text-gray-200'
                            }`}
                          >
                            {s.status}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )
          ) : rows.length === 0 ? (
            <div className="text-center text-gray-500 text-sm py-12">No trades in statement yet.</div>
          ) : (
            <table className="w-full text-left text-[11px] sm:text-xs">
              <thead className="text-gray-500 uppercase tracking-wider sticky top-0 bg-[#0B0E11]">
                <tr>
                  <th className="px-2 py-2 font-semibold">Time</th>
                  <th className="px-2 py-2 font-semibold">Season</th>
                  <th className="px-2 py-2 font-semibold">Pair</th>
                  <th className="px-2 py-2 font-semibold">Side</th>
                  <th className="px-2 py-2 font-semibold">Entry → Exit</th>
                  <th className="px-2 py-2 font-semibold">Gross %</th>
                  <th className="px-2 py-2 font-semibold">Net $</th>
                  <th className="px-2 py-2 font-semibold">Status</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const net = Number(r.net_pnl_usd) || 0;
                  const sold = r.status === 'sold';
                  return (
                    <tr key={r.id || r.trade_uid} className="border-t border-gray-800/80 hover:bg-white/[0.03]">
                      <td className="px-2 py-2 text-gray-400 whitespace-nowrap">
                        {fmtTime(r.closed_at || r.opened_at)}
                      </td>
                      <td className="px-2 py-2 text-indigo-300 font-semibold">
                        {r.season_id != null ? `#${r.season_id}` : '—'}
                      </td>
                      <td className="px-2 py-2 font-semibold text-white">{r.pair}</td>
                      <td className={`px-2 py-2 font-bold ${r.side === 'LONG' ? 'text-green-400' : 'text-red-400'}`}>
                        {r.side}
                        {r.source === 'manual' ? (
                          <span className="text-amber-400 font-normal ml-1">M</span>
                        ) : null}
                      </td>
                      <td className="px-2 py-2 text-gray-300 whitespace-nowrap">
                        {Number(r.entry_price).toLocaleString()}
                        {sold ? ` → ${Number(r.exit_price || 0).toLocaleString()}` : ''}
                      </td>
                      <td
                        className={`px-2 py-2 font-semibold ${
                          (r.gross_pnl_pct || 0) >= 0 ? 'text-green-400' : 'text-red-400'
                        }`}
                      >
                        {sold ? fmtPct(r.gross_pnl_pct) : '—'}
                      </td>
                      <td className={`px-2 py-2 font-semibold ${net >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                        {sold ? fmtUsd(net) : '—'}
                      </td>
                      <td className="px-2 py-2">
                        <span
                          className={`px-1.5 py-0.5 rounded text-[10px] font-bold uppercase ${
                            sold ? 'bg-gray-700 text-gray-200' : 'bg-emerald-900/40 text-emerald-300'
                          }`}
                        >
                          {r.status}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
