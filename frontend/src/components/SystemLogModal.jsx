import { useCallback, useEffect, useRef, useState } from 'react';

const PANEL_STORAGE_KEY = 'systemLogPanel';
const MIN_W = 360;
const MIN_H = 320;
const MARGIN = 12;

function readStoredPanel() {
  try {
    const raw = sessionStorage.getItem(PANEL_STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function storePanel(panel) {
  try {
    sessionStorage.setItem(PANEL_STORAGE_KEY, JSON.stringify(panel));
  } catch {
    /* ignore quota errors */
  }
}

function defaultPanelSize() {
  const w = Math.min(896, Math.max(MIN_W, window.innerWidth - MARGIN * 2));
  const h = Math.min(Math.round(window.innerHeight * 0.85), Math.max(MIN_H, window.innerHeight - MARGIN * 2));
  return { w, h };
}

function centerPanel(size) {
  return {
    x: Math.max(MARGIN, Math.round((window.innerWidth - size.w) / 2)),
    y: Math.max(MARGIN, Math.round((window.innerHeight - size.h) / 2)),
  };
}

function clampPanel(pos, size) {
  const maxX = Math.max(MARGIN, window.innerWidth - size.w - MARGIN);
  const maxY = Math.max(MARGIN, window.innerHeight - size.h - MARGIN);
  const maxW = Math.max(MIN_W, window.innerWidth - pos.x - MARGIN);
  const maxH = Math.max(MIN_H, window.innerHeight - pos.y - MARGIN);
  return {
    pos: {
      x: Math.min(Math.max(MARGIN, pos.x), maxX),
      y: Math.min(Math.max(MARGIN, pos.y), maxY),
    },
    size: {
      w: Math.min(Math.max(MIN_W, size.w), maxW),
      h: Math.min(Math.max(MIN_H, size.h), maxH),
    },
  };
}

function useMovableResizablePanel(open) {
  const [pos, setPos] = useState({ x: MARGIN, y: MARGIN });
  const [size, setSize] = useState({ w: 896, h: 640 });
  const panelRef = useRef(null);
  const stateRef = useRef({ pos, size });
  stateRef.current = { pos, size };

  useEffect(() => {
    if (!open) return;
    const stored = readStoredPanel();
    if (stored?.w && stored?.h) {
      const clamped = clampPanel(
        { x: stored.x ?? MARGIN, y: stored.y ?? MARGIN },
        { w: stored.w, h: stored.h }
      );
      setPos(clamped.pos);
      setSize(clamped.size);
    } else {
      const nextSize = defaultPanelSize();
      setSize(nextSize);
      setPos(centerPanel(nextSize));
    }
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    const onResize = () => {
      const { pos: p, size: s } = stateRef.current;
      const clamped = clampPanel(p, s);
      setPos(clamped.pos);
      setSize(clamped.size);
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [open]);

  const persist = useCallback((nextPos, nextSize) => {
    storePanel({ x: nextPos.x, y: nextPos.y, w: nextSize.w, h: nextSize.h });
  }, []);

  const startDrag = useCallback((e) => {
    if (e.button !== 0 || e.target.closest('button')) return;
    e.preventDefault();
    const startX = e.clientX - stateRef.current.pos.x;
    const startY = e.clientY - stateRef.current.pos.y;
    let latestPos = stateRef.current.pos;
    const onMove = (ev) => {
      const clamped = clampPanel(
        { x: ev.clientX - startX, y: ev.clientY - startY },
        stateRef.current.size
      );
      latestPos = clamped.pos;
      setPos(clamped.pos);
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      persist(latestPos, stateRef.current.size);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  }, [persist]);

  const startResize = useCallback((e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const originX = e.clientX;
    const originY = e.clientY;
    const { w: startW, h: startH } = stateRef.current.size;
    const fixedPos = stateRef.current.pos;
    let latestSize = { w: startW, h: startH };
    const onMove = (ev) => {
      const clamped = clampPanel(fixedPos, {
        w: startW + (ev.clientX - originX),
        h: startH + (ev.clientY - originY),
      });
      latestSize = clamped.size;
      setSize(clamped.size);
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      persist(fixedPos, latestSize);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  }, [persist]);

  const resetLayout = useCallback(() => {
    const nextSize = defaultPanelSize();
    const nextPos = centerPanel(nextSize);
    setSize(nextSize);
    setPos(nextPos);
    persist(nextPos, nextSize);
  }, [persist]);

  return { panelRef, pos, size, startDrag, startResize, resetLayout };
}

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
  uvss: 'text-cyan-300',
  smc_vsa: 'text-cyan-300',
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
  const { panelRef, pos, size, startDrag, startResize, resetLayout } = useMovableResizablePanel(open);

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
  const scan = systemLogs?.last_taapi_scan;
  const scanEngine =
    scan?.engine === 'smc_vsa' ? 'Blue Box' : scan?.engine === 'uvss' ? 'UVSS' : 'Signal';
  const tradeFire = systemLogs?.last_trade_fire;
  const backendEntries = systemLogs?.entries || [];
  const backendNotifications = systemLogs?.notifications || [];

  const isPaper = (conn.bybit_mode || tradingMode) === 'PAPER_TRADING';
  const wsOk = apiStatus?.color === 'green';
  const bybitOk = isPaper || (conn.bybit_configured && (conn.bybit_mode === 'LIVE_TRADING' ? conn.bybit_connected : true));
  const aiOk = conn.ai_configured;
  const bybitTestnetOk = isPaper || conn.bybit_testnet_configured;

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

  const decision = scan?.decision || {};
  const bullish = scan?.bullish || [];
  const bearish = scan?.bearish || [];
  const costAware = scan?.cost_aware;

  return (
    <div className="fixed inset-0 z-[115] pointer-events-none">
      <div
        className="absolute inset-0 bg-transparent pointer-events-none"
        aria-hidden
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="system-log-title"
        className="modal-enter relative bg-[#0B0E11] rounded-2xl shadow-2xl border border-gray-700 flex flex-col overflow-hidden pointer-events-auto"
        style={{ left: pos.x, top: pos.y, width: size.w, height: size.h }}
        onClick={(e) => e.stopPropagation()}
      >
        <div
          className="flex items-center justify-between px-4 sm:px-6 py-4 border-b border-gray-800 shrink-0 cursor-move select-none touch-none"
          onPointerDown={startDrag}
        >
          <div className="min-w-0 pr-2">
            <h2 id="system-log-title" className="text-lg font-black text-white uppercase tracking-wide flex items-center gap-2">
              <i className="fas fa-grip-vertical text-gray-600 text-sm" title="Drag to move" />
              <i className="fas fa-terminal text-blue-400" />
              System Log
            </h2>
            <p className="text-xs text-gray-500 mt-0.5">Drag header to move · corner to resize</p>
          </div>
          <div className="flex items-center gap-1 shrink-0">
            <button
              type="button"
              onClick={resetLayout}
              className="p-2 rounded-lg hover:bg-gray-800 text-gray-400 hover:text-white transition"
              title="Reset size and position"
            >
              <i className="fas fa-compress-arrows-alt text-sm" />
            </button>
            <button
              type="button"
              onClick={onClose}
              className="p-2 rounded-lg hover:bg-gray-800 text-gray-400 hover:text-white transition"
              title="Close"
            >
              <i className="fas fa-times text-lg" />
            </button>
          </div>
        </div>

        <div className="flex flex-col flex-1 min-h-0">
          <div className="overflow-y-auto shrink-0 max-h-[38%] p-3 sm:p-4 space-y-2">
          {/* Connection grid */}
          <section className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-2">
            <div className="bg-[#161A1E] border border-gray-800 rounded-xl p-2">
              <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-2">Backend WebSockets</div>
              <StatusPill ok={wsOk} label={apiStatus?.label || 'UNKNOWN'} />
              <p className="text-[11px] text-gray-500 mt-2">market · portfolio · trades pipes</p>
            </div>
            <div className="bg-[#161A1E] border border-gray-800 rounded-xl p-2">
              <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">Bybit API</div>
              <StatusPill ok={bybitOk} label={conn.bybit_configured ? conn.bybit_mode || 'CONFIGURED' : 'NOT SET'} />
              <p className="text-[11px] text-gray-500 mt-2">
                {conn.bybit_environment || 'mainnet'}
                {conn.bybit_balance != null ? ` · $${Number(conn.bybit_balance).toLocaleString()}` : ''}
              </p>
              {conn.bybit_last_error ? (
                <p className="text-[10px] text-red-400 mt-1 truncate" title={conn.bybit_last_error}>{conn.bybit_last_error}</p>
              ) : null}
            </div>
            <div className="bg-[#161A1E] border border-gray-800 rounded-xl p-2">
              <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">AI Provider</div>
              <StatusPill ok={aiOk} label={conn.ai_configured ? 'CONNECTED' : 'NOT CONFIGURED'} />
              <p className="text-[11px] text-gray-500 mt-2 truncate" title={conn.ai_model}>
                {conn.ai_provider || '—'} / {conn.ai_model || '—'}
              </p>
            </div>
            <div className="bg-[#161A1E] border border-cyan-800/50 rounded-xl p-2">
              <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">Entry Engine</div>
              <StatusPill ok label="BLUE BOX ACTIVE" />
              <p className="text-[11px] text-gray-500 mt-2">15 patterns · VSA+Blue Box · Bybit klines</p>
            </div>
            <div className="bg-[#161A1E] border border-amber-700/40 rounded-xl p-2">
              <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">Bybit TESTNET</div>
              <StatusPill
                ok={bybitTestnetOk}
                label={isPaper ? 'PAPER MODE' : bybitTestnetOk ? 'KEYS SET' : 'KEYS MISSING'}
              />
              <p className="text-[11px] text-gray-500 mt-2">
                {isPaper ? 'Paper ledger — same Blue Box rules as TESTNET' : 'Bybit TESTNET — real orders, same rules as paper'}
              </p>
            </div>
          </section>

          {/* AI Agent + Chart */}
          <section className="grid grid-cols-1 lg:grid-cols-2 gap-2">
            <div className="bg-[#161A1E] border border-gray-800 rounded-xl p-3">
              <h3 className="text-xs font-bold text-white uppercase tracking-wider mb-2">
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
                  <dt className="text-gray-500">Max concurrent</dt>
                  <dd className="text-gray-200">
                    {agent.max_concurrent_trades ?? '—'}
                    {agent.risk_level_pct != null ? (
                      <span className="text-gray-500"> (risk {agent.risk_level_pct}%)</span>
                    ) : null}
                  </dd>
                </div>
                <div className="flex justify-between gap-2">
                  <dt className="text-gray-500">Mode</dt>
                  <dd className="text-gray-200">{tradingMode}</dd>
                </div>
              </dl>
              <p className="text-[10px] text-gray-600 mt-3 leading-relaxed">{agent.policy}</p>
            </div>

            <div className="bg-[#161A1E] border border-gray-800 rounded-xl p-3">
              <h3 className="text-xs font-bold text-white uppercase tracking-wider mb-2">
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

          {/* Last signal scan */}
          <section className="bg-[#161A1E] border border-gray-800 rounded-xl p-3">
            <h3 className="text-xs font-bold text-white uppercase tracking-wider mb-2">
              <i className={`fas ${scanEngine === 'Blue Box' ? 'fa-chart-bar' : 'fa-wave-square'} text-cyan-400 mr-1.5`} />
              Last {scanEngine} Scan
              {scan?.timestamp ? (
                <span className="text-gray-500 font-normal normal-case ml-2">{formatIso(scan.timestamp)}</span>
              ) : null}
            </h3>
            {!scan ? (
              <p className="text-sm text-gray-500">No candle scan yet. Start AI automation and wait for a closed candle.</p>
            ) : (
              <div className="space-y-2">
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
                  {decision.size_mult ? (
                    <span className="px-2 py-1 rounded bg-gray-800 text-gray-300">Size: <strong className="text-amber-400">{decision.size_mult}x</strong></span>
                  ) : null}
                  {decision.ema200 != null ? (
                    <span className="px-2 py-1 rounded bg-gray-800 text-gray-400">EMA200: {decision.ema200} · {decision.trend || '—'}</span>
                  ) : null}
                </div>
                {costAware ? (
                  <div className="rounded-lg border border-gray-700 bg-gray-900/50 p-3 text-xs space-y-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-gray-400 uppercase tracking-wide text-[10px] font-bold">Cost-aware gate</span>
                      <span
                        className={`px-2 py-0.5 rounded font-bold ${
                          costAware.would_block && !costAware.dry_run
                            ? 'bg-red-900/40 text-red-400'
                            : costAware.would_block && costAware.dry_run
                              ? 'bg-amber-900/40 text-amber-300'
                              : 'bg-green-900/40 text-green-400'
                        }`}
                      >
                        {costAware.would_block
                          ? costAware.dry_run
                            ? 'WOULD BLOCK (dry-run)'
                            : 'BLOCKED'
                          : 'PASS'}
                      </span>
                      {costAware.enabled === false ? (
                        <span className="text-gray-500">(disabled)</span>
                      ) : null}
                    </div>
                    <dl className="grid grid-cols-2 sm:grid-cols-3 gap-x-3 gap-y-1 font-mono text-[11px]">
                      <div>
                        <dt className="text-gray-500">Remaining edge</dt>
                        <dd className="text-gray-200">{costAware.remaining_edge_pct != null ? `${costAware.remaining_edge_pct}%` : '—'}</dd>
                      </div>
                      <div>
                        <dt className="text-gray-500">Entry hurdle (λ={costAware.lambda})</dt>
                        <dd className="text-gray-200">{costAware.entry_hurdle_pct != null ? `≥ ${costAware.entry_hurdle_pct}%` : '—'}</dd>
                      </div>
                      <div>
                        <dt className="text-gray-500">Candle range</dt>
                        <dd className="text-gray-200">{costAware.candle_range_pct != null ? `${costAware.candle_range_pct}%` : '—'}</dd>
                      </div>
                      <div>
                        <dt className="text-gray-500">Min range</dt>
                        <dd className="text-gray-200">{costAware.min_candle_range_pct != null ? `≥ ${costAware.min_candle_range_pct}%` : '—'}</dd>
                      </div>
                      <div>
                        <dt className="text-gray-500">Round-trip cost</dt>
                        <dd className="text-gray-200">{costAware.round_trip_cost_pct != null ? `${costAware.round_trip_cost_pct}%` : '—'}</dd>
                      </div>
                      <div>
                        <dt className="text-gray-500">Planned TP</dt>
                        <dd className="text-gray-200">{costAware.planned_gross_tp_pct != null ? `${costAware.planned_gross_tp_pct}%` : '—'}</dd>
                      </div>
                    </dl>
                    {costAware.block_reason ? (
                      <p className="text-[10px] text-amber-300/90">{costAware.block_reason}</p>
                    ) : null}
                  </div>
                ) : null}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
                  <div>
                    <div className="text-green-500 font-bold mb-1">Long rules ({bullish.length})</div>
                    <div className="text-gray-400 font-mono text-[11px] break-words">
                      {bullish.length ? bullish.join(', ') : '—'}
                    </div>
                  </div>
                  <div>
                    <div className="text-red-500 font-bold mb-1">Short rules ({bearish.length})</div>
                    <div className="text-gray-400 font-mono text-[11px] break-words">
                      {bearish.length ? bearish.join(', ') : '—'}
                    </div>
                  </div>
                </div>
              </div>
            )}
          </section>

          {/* Last trade fire */}
          <section className="bg-[#161A1E] border border-gray-800 rounded-xl p-3">
            <h3 className="text-xs font-bold text-white uppercase tracking-wider mb-2">
              <i className="fas fa-bolt text-amber-400 mr-1.5" />
              Last Trade Fire ({tradeFire?.mode || (isPaper ? 'PAPER_TRADING' : 'BYBIT_TESTNET')})
            </h3>
            {!tradeFire ? (
              <p className="text-sm text-gray-500">No trade fire attempted yet this session.</p>
            ) : (
              <dl className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
                <div>
                  <dt className="text-gray-500">Status</dt>
                  <dd
                    className={`font-bold ${
                      tradeFire.success
                        ? 'text-green-400'
                        : tradeFire.skipped || tradeFire.status === 'SKIPPED' || tradeFire.status === 'BLOCKED'
                          ? 'text-amber-400'
                          : 'text-red-400'
                    }`}
                  >
                    {tradeFire.status || (tradeFire.success ? 'FIRED' : 'FAILED')}
                  </dd>
                </div>
                <div>
                  <dt className="text-gray-500">Mode</dt>
                  <dd className="text-gray-200">{tradeFire.mode || '—'}</dd>
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
                {tradeFire.position_usd != null ? (
                  <div>
                    <dt className="text-gray-500">Size</dt>
                    <dd className="text-gray-200">
                      ${Number(tradeFire.position_usd).toLocaleString()} ({tradeFire.capital_pct || 10}% capital)
                    </dd>
                  </div>
                ) : null}
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
                {tradeFire.error ? (
                  <div
                    className={`col-span-2 sm:col-span-4 mt-1 rounded-lg border px-3 py-2 ${
                      tradeFire.skipped || tradeFire.status === 'SKIPPED' || tradeFire.status === 'BLOCKED'
                        ? 'border-amber-500/40 bg-amber-500/10'
                        : 'border-red-500/40 bg-red-500/10'
                    }`}
                  >
                    <dt
                      className={`text-[10px] uppercase font-bold ${
                        tradeFire.skipped || tradeFire.status === 'SKIPPED' || tradeFire.status === 'BLOCKED'
                          ? 'text-amber-400'
                          : 'text-red-400'
                      }`}
                    >
                      {tradeFire.skipped || tradeFire.status === 'SKIPPED' || tradeFire.status === 'BLOCKED'
                        ? 'Reason'
                        : 'Error'}
                    </dt>
                    <dd
                      className={`text-[11px] mt-1 break-words ${
                        tradeFire.skipped || tradeFire.status === 'SKIPPED' || tradeFire.status === 'BLOCKED'
                          ? 'text-amber-200'
                          : 'text-red-200'
                      }`}
                    >
                      {tradeFire.error}
                    </dd>
                  </div>
                ) : null}
              </dl>
            )}
          </section>

          {settingsStatus ? (
            <p className="text-[10px] text-gray-600 text-center pb-1">
              Settings: Bybit mainnet {settingsStatus.bybit_configured ? 'configured' : 'not set'} · TESTNET{' '}
              {conn.bybit_testnet_configured ? 'keys set' : 'keys missing'} · AI {settingsStatus.ai_provider} (
              {settingsStatus.ai_configured ? 'ready' : 'key missing'})
            </p>
          ) : null}
          </div>

          {/* Live log stream — fills remaining panel height */}
          <section className="bg-[#0d1117] border border-gray-800 rounded-xl overflow-hidden flex flex-col flex-1 min-h-[220px] mx-3 sm:mx-4 mb-3 sm:mb-4">
            <div className="px-4 py-2 border-b border-gray-800 flex justify-between items-center shrink-0">
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
            <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto p-3 font-mono text-[11px] space-y-1">
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
        </div>

        {/* Resize handle — bottom-right */}
        <div
          role="presentation"
          onPointerDown={startResize}
          className="absolute bottom-0 right-0 w-5 h-5 cursor-se-resize touch-none flex items-end justify-end p-1 text-gray-500 hover:text-gray-300"
          title="Drag to resize"
        >
          <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor" aria-hidden>
            <path d="M12 12H8V11H11V8H12V12ZM12 8H8V7H11V4H12V8ZM8 12H4V11H7V8H8V12Z" />
          </svg>
        </div>
      </div>
    </div>
  );
}
