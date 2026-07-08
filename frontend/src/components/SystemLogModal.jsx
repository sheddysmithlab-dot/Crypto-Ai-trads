import { useEffect, useRef } from 'react';

function StatusPill({ ok, label }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wide ${
        ok ? 'bg-emerald-900/40 text-emerald-300 border border-emerald-700/50' : 'bg-red-900/40 text-red-300 border border-red-700/50'
      }`}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${ok ? 'bg-emerald-400' : 'bg-red-400'}`} />
      {label}
    </span>
  );
}

function formatTime(ts) {
  if (!ts) return '—';
  const d = typeof ts === 'number' ? new Date(ts * 1000) : new Date(ts);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function formatIso(ts) {
  if (!ts) return '—';
  const d = typeof ts === 'number' ? new Date(ts * 1000) : new Date(ts);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toISOString().replace('T', ' ').slice(0, 19);
}

const CATEGORY_COLOR = {
  taapi: 'text-purple-300',
  ai: 'text-blue-300',
  bybit: 'text-amber-300',
  trade: 'text-green-300',
  chart: 'text-cyan-300',
  system: 'text-gray-300',
  frontend: 'text-pink-300',
};

export default function SystemLogModal({
  open,
  onClose,
  apiStatus,
  tradingMode,
  chartSourceMode,
  chartHistorySource,
  chartLiveSource,
  timeframe,
  activePair,
  lastUpdated,
  settingsStatus,
  systemLogs,
  actionLogs,
  onRefresh,
}) {
  const scrollRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    onRefresh?.();
    const timer = setInterval(() => onRefresh?.(), 2500);
    return () => clearInterval(timer);
  }, [open, onRefresh]);

  useEffect(() => {
    if (open && scrollRef.current) {
      scrollRef.current.scrollTop = 0;
    }
  }, [open, systemLogs?.entries?.length, actionLogs?.length]);

  if (!open) return null;

  const conn = systemLogs?.connections || {};
  const agent = systemLogs?.agent || {};
  const taapi = systemLogs?.last_taapi_scan;
  const tradeFire = systemLogs?.last_trade_fire;
  const backendEntries = systemLogs?.entries || [];
  const backendNotifications = systemLogs?.notifications || [];

  const wsOk = apiStatus?.color === 'green';
  const bybitOk = conn.bybit_configured && (conn.bybit_mode === 'LIVE_TRADING' ? conn.bybit_connected : true);
  const aiOk = conn.ai_configured || conn.ai_provider === 'none';
  const taapiOk = conn.taapi_configured;

  const mergedLogs = [
    ...(actionLogs || []).map((row) => ({
      id: `fe-${row.timestamp}`,
      category: 'frontend',
      message: row.message,
      timestamp: new Date(row.timestamp).getTime() / 1000,
    })),
    ...backendEntries,
  ]
    .sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0))
    .slice(0, 80);

  const decision = taapi?.decision || {};
  const bullish = taapi?.bullish || [];
  const bearish = taapi?.bearish || [];

  return (
    <div
      className="fixed inset-0 bg-black/80 z-[115] flex items-center justify-center backdrop-blur-sm p-3 sm:p-4"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="modal-enter bg-[#0B0E11] rounded-2xl shadow-2xl border border-gray-700 w-full max-w-4xl max-h-[92vh] flex flex-col overflow-hidden">
        <div className="flex items-center justify-between px-4 sm:px-6 py-4 border-b border-gray-800 shrink-0">
          <div>
            <h2 className="text-lg font-black text-white uppercase tracking-wide flex items-center gap-2">
              <i className="fas fa-terminal text-blue-400" />
              System Log
            </h2>
            <p className="text-xs text-gray-500 mt-0.5">Live transparency — TAAPI, AI, Bybit, trades, chart sources</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-2 rounded-lg hover:bg-gray-800 text-gray-400 hover:text-white transition"
            title="Close"
          >
            <i className="fas fa-times text-lg" />
          </button>
        </div>

        <div className="overflow-y-auto flex-1 p-4 sm:p-6 space-y-4">
          {/* Connection grid */}
          <section className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            <div className="bg-[#161A1E] border border-gray-800 rounded-xl p-3">
              <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-2">Backend WebSockets</div>
              <StatusPill ok={wsOk} label={apiStatus?.label || 'UNKNOWN'} />
              <p className="text-[11px] text-gray-500 mt-2">market · portfolio · trades pipes</p>
            </div>
            <div className="bg-[#161A1E] border border-gray-800 rounded-xl p-3">
              <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-2">Bybit API</div>
              <StatusPill ok={bybitOk} label={conn.bybit_configured ? conn.bybit_mode || 'CONFIGURED' : 'NOT SET'} />
              <p className="text-[11px] text-gray-500 mt-2">
                {conn.bybit_environment || 'mainnet'}
                {conn.bybit_balance != null ? ` · $${Number(conn.bybit_balance).toLocaleString()}` : ''}
              </p>
              {conn.bybit_last_error ? (
                <p className="text-[10px] text-red-400 mt-1 truncate" title={conn.bybit_last_error}>{conn.bybit_last_error}</p>
              ) : null}
            </div>
            <div className="bg-[#161A1E] border border-gray-800 rounded-xl p-3">
              <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-2">AI Provider</div>
              <StatusPill ok={aiOk} label={conn.ai_configured ? 'CONNECTED' : 'NOT CONFIGURED'} />
              <p className="text-[11px] text-gray-500 mt-2 truncate" title={conn.ai_model}>
                {conn.ai_provider || '—'} / {conn.ai_model || '—'}
              </p>
            </div>
            <div className="bg-[#161A1E] border border-gray-800 rounded-xl p-3">
              <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-2">TAAPI.io</div>
              <StatusPill ok={taapiOk} label={taapiOk ? 'CONFIGURED' : 'MISSING KEY'} />
              <p className="text-[11px] text-gray-500 mt-2">exchange: {conn.taapi_exchange || 'binance'}</p>
            </div>
          </section>

          {/* AI Agent + Chart */}
          <section className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <div className="bg-[#161A1E] border border-gray-800 rounded-xl p-4">
              <h3 className="text-xs font-bold text-white uppercase tracking-wider mb-3">
                <i className="fas fa-robot text-blue-400 mr-1.5" />
                AI Agent
              </h3>
              <dl className="space-y-1.5 text-xs">
                <div className="flex justify-between gap-2">
                  <dt className="text-gray-500">Status</dt>
                  <dd className={agent.is_active ? 'text-green-400 font-bold' : 'text-gray-400'}>
                    {agent.is_active ? 'RUNNING' : 'STOPPED'}
                  </dd>
                </div>
                <div className="flex justify-between gap-2">
                  <dt className="text-gray-500">Pair / TF</dt>
                  <dd className="text-gray-200 font-mono">{activePair} · {timeframe} ({agent.timeframe_key})</dd>
                </div>
                <div className="flex justify-between gap-2">
                  <dt className="text-gray-500">Price</dt>
                  <dd className="text-gray-200 font-mono">${agent.current_price ?? '—'}</dd>
                </div>
                <div className="flex justify-between gap-2">
                  <dt className="text-gray-500">Open positions</dt>
                  <dd className="text-gray-200">{agent.open_trades ?? 0}</dd>
                </div>
                <div className="flex justify-between gap-2">
                  <dt className="text-gray-500">Mode</dt>
                  <dd className="text-gray-200">{tradingMode}</dd>
                </div>
              </dl>
              <p className="text-[10px] text-gray-600 mt-3 leading-relaxed">{agent.policy}</p>
            </div>

            <div className="bg-[#161A1E] border border-gray-800 rounded-xl p-4">
              <h3 className="text-xs font-bold text-white uppercase tracking-wider mb-3">
                <i className="fas fa-chart-line text-cyan-400 mr-1.5" />
                Chart Data
              </h3>
              <dl className="space-y-1.5 text-xs">
                <div className="flex justify-between gap-2">
                  <dt className="text-gray-500">Trading mode</dt>
                  <dd className="text-gray-200">{chartSourceMode || tradingMode}</dd>
                </div>
                <div className="flex justify-between gap-2">
                  <dt className="text-gray-500">History source</dt>
                  <dd className="text-gray-200 text-right max-w-[60%] truncate" title={chartHistorySource}>{chartHistorySource}</dd>
                </div>
                <div className="flex justify-between gap-2">
                  <dt className="text-gray-500">Live ticks</dt>
                  <dd className="text-gray-200 text-right max-w-[60%] truncate" title={chartLiveSource}>{chartLiveSource}</dd>
                </div>
                <div className="flex justify-between gap-2">
                  <dt className="text-gray-500">Last updated</dt>
                  <dd className="text-gray-200">{lastUpdated}</dd>
                </div>
              </dl>
            </div>
          </section>

          {/* TAAPI last scan */}
          <section className="bg-[#161A1E] border border-gray-800 rounded-xl p-4">
            <h3 className="text-xs font-bold text-white uppercase tracking-wider mb-3">
              <i className="fas fa-wave-square text-purple-400 mr-1.5" />
              Last TAAPI Scan
              {taapi?.timestamp ? (
                <span className="text-gray-500 font-normal normal-case ml-2">{formatIso(taapi.timestamp)}</span>
              ) : null}
            </h3>
            {!taapi ? (
              <p className="text-sm text-gray-500">No candle scan yet. Start AI automation and wait for a closed candle.</p>
            ) : (
              <div className="space-y-3">
                <div className="flex flex-wrap gap-2 text-xs">
                  <span className="px-2 py-1 rounded bg-gray-800 text-gray-300">
                    Decision: <strong className={decision.action === 'BUY' ? 'text-green-400' : decision.action === 'SELL' ? 'text-red-400' : 'text-yellow-400'}>{decision.action}</strong>
                  </span>
                  {decision.pattern ? (
                    <span className="px-2 py-1 rounded bg-gray-800 text-gray-300">Pattern: <strong className="text-white">{decision.pattern}</strong></span>
                  ) : null}
                  {decision.reason ? (
                    <span className="px-2 py-1 rounded bg-gray-800 text-gray-400">{decision.reason}</span>
                  ) : null}
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
                  <div>
                    <div className="text-green-500 font-bold mb-1">Bullish ({bullish.length})</div>
                    <div className="text-gray-400 font-mono text-[11px] break-words">
                      {bullish.length ? bullish.join(', ') : '—'}
                    </div>
                  </div>
                  <div>
                    <div className="text-red-500 font-bold mb-1">Bearish ({bearish.length})</div>
                    <div className="text-gray-400 font-mono text-[11px] break-words">
                      {bearish.length ? bearish.join(', ') : '—'}
                    </div>
                  </div>
                </div>
              </div>
            )}
          </section>

          {/* Last trade fire */}
          <section className="bg-[#161A1E] border border-gray-800 rounded-xl p-4">
            <h3 className="text-xs font-bold text-white uppercase tracking-wider mb-3">
              <i className="fas fa-bolt text-amber-400 mr-1.5" />
              Last Trade Fire (Bybit TESTNET)
            </h3>
            {!tradeFire ? (
              <p className="text-sm text-gray-500">No TESTNET order attempted yet this session.</p>
            ) : (
              <dl className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
                <div>
                  <dt className="text-gray-500">Status</dt>
                  <dd className={tradeFire.success ? 'text-green-400 font-bold' : 'text-red-400 font-bold'}>
                    {tradeFire.success ? 'FIRED' : 'FAILED'}
                  </dd>
                </div>
                <div>
                  <dt className="text-gray-500">Action</dt>
                  <dd className="text-gray-200">{tradeFire.action}</dd>
                </div>
                <div>
                  <dt className="text-gray-500">Symbol</dt>
                  <dd className="text-gray-200 font-mono">{tradeFire.symbol}</dd>
                </div>
                <div>
                  <dt className="text-gray-500">Qty</dt>
                  <dd className="text-gray-200">{tradeFire.qty}</dd>
                </div>
                <div className="col-span-2">
                  <dt className="text-gray-500">Pattern</dt>
                  <dd className="text-gray-200">{tradeFire.pattern || '—'}</dd>
                </div>
                <div>
                  <dt className="text-gray-500">SL / TP</dt>
                  <dd className="text-gray-200 font-mono text-[11px]">{tradeFire.sl} / {tradeFire.tp}</dd>
                </div>
                <div>
                  <dt className="text-gray-500">Time</dt>
                  <dd className="text-gray-200">{formatTime(tradeFire.timestamp)}</dd>
                </div>
              </dl>
            )}
          </section>

          {/* Live log stream */}
          <section className="bg-[#0d1117] border border-gray-800 rounded-xl overflow-hidden">
            <div className="px-4 py-2 border-b border-gray-800 flex justify-between items-center">
              <h3 className="text-xs font-bold text-white uppercase tracking-wider">
                <i className="fas fa-list text-gray-400 mr-1.5" />
                Live Event Log
              </h3>
              <button
                type="button"
                onClick={() => onRefresh?.()}
                className="text-[10px] text-blue-400 hover:text-blue-300 uppercase font-bold"
              >
                <i className="fas fa-sync-alt mr-1" />
                Refresh
              </button>
            </div>
            <div ref={scrollRef} className="max-h-56 overflow-y-auto p-3 font-mono text-[11px] space-y-1">
              {mergedLogs.length === 0 && backendNotifications.length === 0 ? (
                <p className="text-gray-600">Waiting for events…</p>
              ) : (
                mergedLogs.map((row) => (
                  <div key={row.id} className="flex gap-2 text-gray-400 hover:bg-white/5 px-1 py-0.5 rounded">
                    <span className="text-gray-600 shrink-0">{formatTime(row.timestamp)}</span>
                    <span className={`shrink-0 uppercase font-bold w-14 ${CATEGORY_COLOR[row.category] || 'text-gray-400'}`}>
                      [{row.category}]
                    </span>
                    <span className="text-gray-300 break-words">{row.message}</span>
                  </div>
                ))
              )}
            </div>
          </section>

          {settingsStatus ? (
            <p className="text-[10px] text-gray-600 text-center">
              Settings: Bybit {settingsStatus.bybit_configured ? 'configured' : 'not set'} · AI {settingsStatus.ai_provider} ({settingsStatus.ai_configured ? 'ready' : 'key missing'})
            </p>
          ) : null}
        </div>
      </div>
    </div>
  );
}
