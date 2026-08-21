import { useEffect, useState } from 'react';
import { authFetch } from '../config/api';
import InfoTip from './InfoTip';

const CHART_TFS = ['1M', '5M', '15M', '1H', '1D'];
const BASE_CAPITAL_RISK = 5;
const BASE_RISK = 50;

function fmtWindow(w) {
  if (!w) return '';
  const end =
    w.end === '01:30' || (w.start && w.end && w.end < w.start) ? `${w.end} (next day)` : w.end;
  return `${w.start} – ${end} IST`;
}

function clampPct(n) {
  return Math.max(0, Math.min(100, n));
}

function calcRisk(capitalRisk) {
  return clampPct(BASE_RISK + (capitalRisk - BASE_CAPITAL_RISK) * 5);
}

function Gauge({ label, value, colorVar }) {
  const r = 36;
  const c = 2 * Math.PI * r;
  const dash = (value / 100) * c;
  const glow = colorVar === 'green' ? '#22c55e' : '#f87171';
  return (
    <div className="flex flex-col items-center">
      <div className="relative w-24 h-24">
        <svg className="w-full h-full -rotate-90" viewBox="0 0 100 100">
          <circle cx="50" cy="50" r={r} fill="none" stroke="#1f2937" strokeWidth="7" />
          <circle
            cx="50"
            cy="50"
            r={r}
            fill="none"
            stroke={glow}
            strokeWidth="7"
            strokeLinecap="round"
            strokeDasharray={`${dash} ${c - dash}`}
            style={{
              filter: `drop-shadow(0 0 6px ${glow})`,
              transition: 'stroke-dasharray 0.3s ease, stroke 0.3s ease',
            }}
          />
        </svg>
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="text-lg font-black text-white">{Math.round(value)}%</span>
        </div>
      </div>
      <span className="mt-1.5 text-[10px] font-bold uppercase tracking-widest text-gray-400">
        {label}
      </span>
    </div>
  );
}

/**
 * Session Momentum Engine popup — English explanation + Start/Stop.
 * Start opens a setup step: chart timeframe + risk meter, then arms the engine.
 */
export default function SessionMomentumModal({
  open,
  onClose,
  enabled,
  loading,
  status,
  mainEngineActive,
  chartTimeframe = '1M',
  onRefresh,
  onStart,
  onStop,
}) {
  const [setupOpen, setSetupOpen] = useState(false);
  const [chartTf, setChartTf] = useState('1M');
  const [capitalRisk, setCapitalRisk] = useState(5);
  const [dailyProfit, setDailyProfit] = useState(0);
  const [setupBusy, setSetupBusy] = useState(false);

  useEffect(() => {
    if (open) onRefresh?.();
  }, [open, onRefresh]);

  useEffect(() => {
    if (!open) {
      setSetupOpen(false);
      return;
    }
    setChartTf(String(chartTimeframe || '1M').toUpperCase());
  }, [open, chartTimeframe]);

  useEffect(() => {
    if (!setupOpen) return;
    authFetch('/agent/config')
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (!data) return;
        if (typeof data.stop_loss_pct === 'number') setCapitalRisk(data.stop_loss_pct);
        if (typeof data.daily_profit_pct === 'number') setDailyProfit(data.daily_profit_pct);
      })
      .catch(() => {});
  }, [setupOpen]);

  if (!open) return null;

  const windows = Array.isArray(status?.windows) ? status.windows : [];
  const active = Array.isArray(status?.active_windows) ? status.active_windows : [];
  const next = status?.next || {};
  const nowIst = status?.now_ist || '—';
  const inWindow = Boolean(status?.in_window);

  const risk = calcRisk(capitalRisk);
  const confidence = 100 - risk;
  const trades = Math.max(1, Math.floor(capitalRisk * 2 + 0.5));
  const isRed = risk > 50;
  const colorVar = isRed ? 'red' : 'green';

  async function handleToggle() {
    if (loading || setupBusy) return;
    if (enabled) {
      await onStop();
      return;
    }
    // Start → ask timeframe + risk first
    setSetupOpen(true);
  }

  async function handleConfirmSetup() {
    if (setupBusy || loading) return;
    setSetupBusy(true);
    try {
      const result = await onStart({
        timeframe: chartTf,
        stopLossPct: capitalRisk,
        dailyProfitPct: dailyProfit,
        risk,
        confidence,
        trades,
      });
      if (result?.ok !== false) {
        setSetupOpen(false);
      }
    } finally {
      setSetupBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 bg-black/75 z-[120] flex items-center justify-center backdrop-blur-sm p-4"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="bg-[#0B0E11] border border-gray-800 rounded-2xl shadow-2xl max-w-xl w-full max-h-[90vh] overflow-y-auto">
        <div className="flex justify-between items-start px-6 pt-6">
          <div>
            <div className="text-xs font-bold text-cyan-400 uppercase tracking-widest mb-1">
              Timed Momentum Engine
            </div>
            <h2 className="text-xl font-bold text-white">
              {setupOpen ? 'Session setup' : 'Session Momentum Engine'}
            </h2>
          </div>
          <button
            type="button"
            className="w-8 h-8 rounded-lg bg-[#161A1E] border border-gray-700 text-gray-400 hover:text-white flex items-center justify-center"
            onClick={onClose}
          >
            <i className="fas fa-times" />
          </button>
        </div>

        {setupOpen ? (
          <div className="px-6 py-5 space-y-5 text-sm text-gray-300">
            <p className="text-xs text-gray-400 leading-relaxed">
              Choose the chart timeframe this session will scan on, and set the risk meter (same
              capital-risk controls as Main AI Engine).
            </p>

            <div>
              <div className="text-xs font-bold uppercase tracking-wider text-gray-400 mb-2 flex items-center gap-1.5">
                Chart timeframe
                <InfoTip text="Bot scans and fires on this candle size for the whole session (1M = fastest, 1D = slowest)." />
              </div>
              <div className="flex flex-wrap gap-2">
                {CHART_TFS.map((tf) => {
                  const on = chartTf === tf;
                  return (
                    <button
                      key={tf}
                      type="button"
                      onClick={() => setChartTf(tf)}
                      className={`min-w-[3.25rem] px-3 py-2 rounded-lg text-xs font-black tracking-wide border transition ${
                        on
                          ? 'bg-cyan-500 border-cyan-400 text-black'
                          : 'bg-[#161A1E] border-gray-700 text-gray-300 hover:border-gray-500'
                      }`}
                    >
                      {tf}
                    </button>
                  );
                })}
              </div>
              <p className="text-[11px] text-gray-500 mt-2">
                Selected: <span className="text-cyan-300 font-semibold">{chartTf}</span> chart
              </p>
            </div>

            <div className="flex items-center justify-center gap-6 py-2">
              <Gauge label="Risk" value={risk} colorVar={colorVar} />
              <div className={`w-10 h-0.5 ${isRed ? 'bg-red-500' : 'bg-green-500'}`} />
              <Gauge label="Confidence" value={confidence} colorVar={colorVar} />
            </div>

            <div className="space-y-3">
              <div className="flex items-center justify-between bg-[#161A1E] border border-gray-700 rounded-lg px-4 py-3">
                <span className="flex items-center gap-1.5 text-xs font-semibold text-gray-300">
                  Total capital risk %
                  <InfoTip text="Max session portfolio loss you allow. Also sets trade capacity ≈ risk × 2." />
                </span>
                <div className="flex items-center gap-1">
                  <input
                    type="number"
                    min="0.5"
                    max="100"
                    step="0.5"
                    value={capitalRisk}
                    onChange={(e) => {
                      const v = parseFloat(e.target.value);
                      setCapitalRisk(
                        Number.isFinite(v) ? Math.min(100, Math.max(0.5, v)) : 0.5,
                      );
                    }}
                    className="w-16 bg-transparent text-right text-sm font-bold text-white focus:outline-none"
                  />
                  <span className="text-sm font-bold text-gray-400">%</span>
                </div>
              </div>

              <div className="flex items-center justify-between bg-[#161A1E] border border-gray-700 rounded-lg px-4 py-3">
                <span className="flex items-center gap-1.5 text-xs font-semibold text-gray-300">
                  Daily profit target <span className="text-gray-500">(optional)</span>
                  <InfoTip text="Optional goal. When session profit reaches this %, new auto entries pause. Leave 0 to disable." />
                </span>
                <div className="flex items-center gap-1">
                  <input
                    type="number"
                    min="0"
                    step="1"
                    value={dailyProfit}
                    onChange={(e) => {
                      const v = parseFloat(e.target.value);
                      setDailyProfit(Number.isFinite(v) && v >= 0 ? v : 0);
                    }}
                    className="w-16 bg-transparent text-right text-sm font-bold text-white focus:outline-none"
                  />
                  <span className="text-sm font-bold text-gray-400">%</span>
                </div>
              </div>
            </div>

            <p className="text-[11px] text-gray-500 text-center">
              Trade fire capacity ≈{' '}
              <span className={`font-bold ${isRed ? 'text-red-400' : 'text-green-400'}`}>
                {trades}
              </span>{' '}
              (= risk × 2)
            </p>

            <div className="flex gap-2 pt-1">
              <button
                type="button"
                disabled={setupBusy || loading}
                onClick={() => setSetupOpen(false)}
                className="flex-1 py-3 rounded-xl text-sm font-bold uppercase tracking-wider border border-gray-700 text-gray-300 hover:bg-gray-900 transition disabled:opacity-60"
              >
                Back
              </button>
              <button
                type="button"
                disabled={setupBusy || loading}
                onClick={handleConfirmSetup}
                className="flex-[1.4] py-3 rounded-xl text-sm font-black uppercase tracking-widest bg-cyan-500 hover:bg-cyan-400 text-black transition disabled:opacity-60"
              >
                {setupBusy || loading ? (
                  <>
                    <i className="fas fa-spinner fa-spin mr-2" />
                    Starting…
                  </>
                ) : (
                  <>
                    <i className="fas fa-play mr-2" />
                    Confirm &amp; Start
                  </>
                )}
              </button>
            </div>
          </div>
        ) : (
          <div className="px-6 py-5 space-y-4 text-sm text-gray-300 leading-relaxed">
            <p>
              Global markets do not move evenly through the day. Liquidity and volatility spike when
              major sessions open — especially the{' '}
              <span className="text-white font-semibold">US cash open</span>, Asian morning flow, and
              the <span className="text-white font-semibold">London–New York overlap</span>. Those
              windows produce the strongest momentum bursts of the trading day.
            </p>

            <p>
              If you want the bot to trade only when that momentum is typically highest, start this
              engine. It will automatically arm trading during the scheduled IST windows below and
              pause when the window ends.
            </p>

            <div className="rounded-xl border border-amber-700/40 bg-amber-900/20 px-4 py-3 text-amber-200 text-xs">
              <strong className="block text-amber-300 mb-1">Important — mutual exclusivity</strong>
              Starting the Session Momentum Engine turns the <strong>Main AI Engine OFF</strong>. Only
              one control mode can run at a time. You can switch back anytime by stopping this engine
              and using <strong>AI ENGINE START</strong> on the main control bar.
            </div>

            <div>
              <h3 className="text-xs font-bold uppercase tracking-wider text-gray-400 mb-2">
                High-momentum windows (Mon–Fri, Asia/Kolkata)
              </h3>
              <ul className="space-y-2">
                {(windows.length
                  ? windows
                  : [
                      { key: 'morning_momentum', label: 'Morning Momentum', start: '05:30', end: '08:30' },
                      { key: 'peak_overlap', label: 'Peak Overlap Window', start: '18:30', end: '23:30' },
                      { key: 'us_core', label: 'US Core Session', start: '19:30', end: '01:30' },
                    ]
                ).map((w) => (
                  <li
                    key={w.key || w.label}
                    className="flex items-start justify-between gap-3 bg-[#161A1E] border border-gray-800 rounded-lg px-3 py-2.5"
                  >
                    <div>
                      <div className="text-white font-semibold text-sm">{w.label}</div>
                      <div className="text-[11px] text-gray-500 mt-0.5">
                        {w.key === 'morning_momentum' && 'Asian / early European liquidity wake-up'}
                        {w.key === 'peak_overlap' && 'London–NY overlap — peak volume & momentum'}
                        {w.key === 'us_core' && 'US session core — high directional moves'}
                        {!['morning_momentum', 'peak_overlap', 'us_core'].includes(w.key) &&
                          'Scheduled momentum window'}
                      </div>
                    </div>
                    <div className="text-cyan-300 font-mono text-xs whitespace-nowrap pt-0.5">
                      {fmtWindow(w)}
                    </div>
                  </li>
                ))}
              </ul>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
              <div className="rounded-lg border border-gray-800 bg-[#161A1E] px-3 py-2">
                <div className="text-gray-500 uppercase tracking-wider mb-1">Now (IST)</div>
                <div className="text-white font-semibold">{nowIst}</div>
              </div>
              <div className="rounded-lg border border-gray-800 bg-[#161A1E] px-3 py-2">
                <div className="text-gray-500 uppercase tracking-wider mb-1">Window status</div>
                <div
                  className={
                    inWindow ? 'text-emerald-400 font-semibold' : 'text-gray-400 font-semibold'
                  }
                >
                  {inWindow
                    ? `IN WINDOW${active.length ? ` — ${active.join(', ')}` : ''}`
                    : 'Outside window'}
                </div>
              </div>
              <div className="rounded-lg border border-gray-800 bg-[#161A1E] px-3 py-2 sm:col-span-2">
                <div className="text-gray-500 uppercase tracking-wider mb-1">Next transition</div>
                <div className="text-white font-semibold">
                  {next?.at_ist
                    ? `${next.at_ist}${next.will_be_active ? ' → trading arms' : ' → trading pauses'}`
                    : '—'}
                </div>
              </div>
            </div>

            {mainEngineActive && !enabled ? (
              <p className="text-xs text-orange-300 border border-orange-700/40 bg-orange-900/20 rounded-lg px-3 py-2">
                Main AI Engine is currently ON. Starting Session Momentum Engine will stop the main
                engine first.
              </p>
            ) : null}

            <button
              type="button"
              disabled={loading}
              onClick={handleToggle}
              className={`w-full py-3 rounded-xl text-sm font-black uppercase tracking-widest transition disabled:opacity-60 ${
                enabled
                  ? 'bg-red-600 hover:bg-red-500 text-white'
                  : 'bg-cyan-500 hover:bg-cyan-400 text-black'
              }`}
            >
              {loading ? (
                <>
                  <i className="fas fa-spinner fa-spin mr-2" />
                  Please wait…
                </>
              ) : enabled ? (
                <>
                  <i className="fas fa-stop-circle mr-2" />
                  Stop Session Momentum Engine
                </>
              ) : (
                <>
                  <i className="fas fa-play mr-2" />
                  Start Session Momentum Engine
                </>
              )}
            </button>

            <p className="text-[11px] text-gray-500 text-center pb-1">
              Status: {enabled ? 'ENABLED' : 'DISABLED'}
              {enabled && inWindow ? ' · actively trading this window' : ''}
              {enabled && !inWindow ? ' · waiting for next window' : ''}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
