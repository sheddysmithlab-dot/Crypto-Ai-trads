import { useEffect, useState } from 'react';
import { authFetch } from '../config/api';
import InfoTip from './InfoTip';

// "AI Engine Instructions" pre-start popup.
// Total capital risk % drives:
//   risk meter   = 50 + (capitalRisk - 5) * 5   // default 5% -> 50/50
//   confidence   = 100 - risk
//   trade capacity = round(capitalRisk * 2)     // 5% -> 10 fires
// When session portfolio loss hits this %, backend auto Hold-stops (no new entries;
// open trades exit on their own TP/SL).

const BASE_CAPITAL_RISK = 5;
const BASE_RISK = 50;

function clampPct(n) {
  return Math.max(0, Math.min(100, n));
}

function calcRisk(capitalRisk) {
  return clampPct(BASE_RISK + (capitalRisk - BASE_CAPITAL_RISK) * 5);
}

function Gauge({ label, value, colorVar }) {
  const r = 42;
  const c = 2 * Math.PI * r;
  const dash = (value / 100) * c;
  const glow = colorVar === 'green' ? '#22c55e' : '#f87171';
  return (
    <div className="flex flex-col items-center">
      <div className="relative w-28 h-28">
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
          <span className="text-xl font-black text-white">{Math.round(value)}%</span>
        </div>
      </div>
      <span className="mt-2 text-[10px] font-bold uppercase tracking-widest text-gray-400">{label}</span>
    </div>
  );
}

export default function AgentInstructionsModal({ open, onClose, onStart }) {
  const [capitalRisk, setCapitalRisk] = useState(5);
  const [dailyProfit, setDailyProfit] = useState(0);

  useEffect(() => {
    if (!open) return;
    authFetch('/agent/config')
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (!data) return;
        if (typeof data.stop_loss_pct === 'number') setCapitalRisk(data.stop_loss_pct);
        if (typeof data.daily_profit_pct === 'number') setDailyProfit(data.daily_profit_pct);
      })
      .catch(() => {});
  }, [open]);

  if (!open) return null;

  const risk = calcRisk(capitalRisk);
  const confidence = 100 - risk;
  const trades = Math.max(1, Math.floor(capitalRisk * 2 + 0.5));
  const isRed = risk > 50;
  const colorVar = isRed ? 'red' : 'green';
  const glowColor = isRed ? 'rgba(239,68,68,0.7)' : 'rgba(34,197,94,0.7)';
  const solidColor = isRed ? 'bg-red-600 hover:bg-red-500' : 'bg-green-500 hover:bg-green-400';
  const borderGlow = isRed
    ? 'border-red-500 shadow-[0_0_30px_rgba(239,68,68,0.45)]'
    : 'border-green-500 shadow-[0_0_30px_rgba(34,197,94,0.4)]';
  const connectorColor = isRed ? 'bg-red-500' : 'bg-green-500';

  function handleStartClick() {
    onStart({
      stopLossPct: capitalRisk,
      dailyProfitPct: dailyProfit,
      risk,
      confidence,
      trades,
      isRed,
    });
  }

  function handleCapitalRiskChange(e) {
    const v = parseFloat(e.target.value);
    setCapitalRisk(Number.isFinite(v) ? Math.min(100, Math.max(0.5, v)) : 0.5);
  }

  function handleDailyProfitChange(e) {
    const v = parseFloat(e.target.value);
    setDailyProfit(Number.isFinite(v) && v >= 0 ? v : 0);
  }

  return (
    <div
      className="fixed inset-0 z-[112] flex items-center justify-center backdrop-blur-sm p-4 bg-black bg-opacity-80"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className={`bg-[#0B0E13] border-2 rounded-2xl max-w-md w-full ${borderGlow}`}>
        <div className="flex justify-between items-center px-6 py-4 border-b border-gray-800">
          <h2 className="text-sm font-black tracking-widest text-white uppercase">AI Engine Instructions</h2>
          <button
            className="w-8 h-8 rounded-lg bg-[#161A1E] border border-gray-700 text-gray-400 hover:text-white flex items-center justify-center"
            onClick={onClose}
          >
            <i className="fas fa-times" />
          </button>
        </div>

        <div className="px-6 py-6 space-y-6">
          <div className="flex items-center justify-between relative">
            <Gauge label="Risk" value={risk} colorVar={colorVar} />
            <div className={`flex-1 h-0.5 mx-2 ${connectorColor}`} style={{ boxShadow: `0 0 8px ${glowColor}` }} />
            <Gauge label="Confidence" value={confidence} colorVar={colorVar} />
          </div>

          <div className="space-y-3">
            <div className="flex items-center justify-between bg-[#161A1E] border border-gray-700 rounded-lg px-4 py-3">
              <span className="flex items-center gap-1.5 text-xs font-semibold text-gray-300">
                Total capital risk %
                <InfoTip text="Max session portfolio loss you allow. Example: 5% also sets trade capacity to 10 (2 trades per 1%). If loss hits this %, AI Hold-stops — no new trades; open trades exit on their own TP/SL." />
              </span>
              <div className="flex items-center gap-1">
                <input
                  type="number"
                  min="0.5"
                  max="100"
                  step="0.5"
                  value={capitalRisk}
                  onChange={handleCapitalRiskChange}
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
                  onChange={handleDailyProfitChange}
                  className="w-16 bg-transparent text-right text-sm font-bold text-white focus:outline-none"
                />
                <span className="text-sm font-bold text-gray-400">%</span>
              </div>
            </div>
          </div>

          <p className="text-[11px] text-gray-400 leading-relaxed text-center mt-2">
            Per-trade: TP <span className="text-emerald-400 font-bold">+0.7%</span> continuous +++ /{' '}
            <span className="text-emerald-400 font-bold">+0.5%</span> choppy · SL{' '}
            <span className="text-amber-400 font-bold">−0.5%</span> continuous /{' '}
            <span className="text-amber-400 font-bold">−0.7%</span> choppy. Hit capital risk % → auto Hold
            stop.
          </p>

          <p className="text-center text-xs text-gray-400">
            Trade fire capacity:{' '}
            <span className={`font-black ${isRed ? 'text-red-400' : 'text-green-400'}`}>{trades}</span>
            {' '}(= risk × 2). Emergency STOP closes everything.
          </p>

          <button
            type="button"
            className={`w-full ${solidColor} ${isRed ? 'text-white' : 'text-black'} font-bold py-3.5 rounded-lg text-sm uppercase tracking-widest transition-all active:scale-95`}
            style={{ boxShadow: `0 0 20px ${glowColor}` }}
            onClick={handleStartClick}
          >
            Continue to Safety Check
          </button>
        </div>
      </div>
    </div>
  );
}
