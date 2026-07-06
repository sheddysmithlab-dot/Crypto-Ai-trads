import { useState } from 'react';

// "AI Agent Instructions" pre-start popup.
// Entry point: the green START AI AUTOMATION button in the ControlBar opens this
// (the bot is NOT started yet). The user picks a stop-loss % (and an optional
// daily profit target). Two neon gauges - RISK and CONFIDENCE - update live off
// the stop-loss value, and the whole popup recolours green->red as risk crosses
// 50%. Clicking START AI AUTOMATION applies the config + starts the bot via the
// parent's onStart; in the red zone a confirm step runs first (the "emergency
// exit / continue" forward wiring - here reused as a high-risk proceed gate).
//
// Algorithm (per spec):
//   risk       = 45 + (stopLoss - 3) * 5     // base 3% -> 45%, +5% per +1% SL
//   confidence = 100 - risk                  // base 3% -> 55%
//   trades     = round(stopLoss * 1.5)       // half-up; 3% -> 5
// Everything is displayed as a strict integer - no decimals on the card.

const BASE_STOP_LOSS = 3;
const BASE_RISK = 45;

function clampPct(n) {
  return Math.max(0, Math.min(100, n));
}

function calcRisk(stopLoss) {
  return clampPct(BASE_RISK + (stopLoss - BASE_STOP_LOSS) * 5);
}

function Gauge({ label, value, colorVar }) {
  // Circular neon progress meter. value is 0-100.
  const r = 42;
  const c = 2 * Math.PI * r;
  const dash = (value / 100) * c;
  const glow = colorVar === 'green' ? '#22c55e' : '#ef4444';
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
            style={{ filter: `drop-shadow(0 0 6px ${glow})`, transition: 'stroke-dasharray 0.3s ease, stroke 0.3s ease' }}
          />
        </svg>
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="text-2xl font-black text-white">{Math.round(value)}%</span>
        </div>
      </div>
      <span className="mt-2 text-[10px] font-bold uppercase tracking-widest text-gray-400">{label}</span>
    </div>
  );
}

export default function AgentInstructionsModal({ open, onClose, onStart }) {
  const [stopLoss, setStopLoss] = useState(3);
  const [dailyProfit, setDailyProfit] = useState(10);
  const [confirming, setConfirming] = useState(false);
  const [starting, setStarting] = useState(false);

  if (!open) return null;

  const risk = calcRisk(stopLoss);
  const confidence = 100 - risk;
  const trades = Math.round(stopLoss * 1.5);
  const isRed = risk > 50;
  const colorVar = isRed ? 'red' : 'green';
  const glowColor = isRed ? 'rgba(239,68,68,0.7)' : 'rgba(34,197,94,0.7)';
  const solidColor = isRed ? 'bg-red-600 hover:bg-red-500' : 'bg-green-500 hover:bg-green-400';
  const borderGlow = isRed
    ? 'border-red-500 shadow-[0_0_30px_rgba(239,68,68,0.45)]'
    : 'border-green-500 shadow-[0_0_30px_rgba(34,197,94,0.45)]';
  const connectorColor = isRed ? 'bg-red-500' : 'bg-green-500';

  function handleStartClick() {
    if (isRed) {
      // Red zone -> forward wiring: show the emergency-exit/continue style gate.
      setConfirming(true);
      return;
    }
    runStart();
  }

  async function runStart() {
    setStarting(true);
    try {
      await onStart({ stopLossPct: stopLoss, dailyProfitPct: dailyProfit });
    } finally {
      setStarting(false);
      setConfirming(false);
    }
  }

  function handleStopLossChange(e) {
    const v = parseFloat(e.target.value);
    setStopLoss(Number.isFinite(v) ? v : 0);
    setConfirming(false);
  }

  function handleDailyProfitChange(e) {
    const v = parseFloat(e.target.value);
    setDailyProfit(Number.isFinite(v) && v >= 0 ? v : 0);
  }

  return (
    <div
      className="fixed inset-0 bg-black bg-opacity-80 z-[112] flex items-center justify-center backdrop-blur-sm p-4"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className={`bg-[#0B0E11] border-2 rounded-2xl max-w-md w-full ${borderGlow}`}>
        {/* Header */}
        <div className="flex justify-between items-center px-6 py-4 border-b border-gray-800">
          <h2 className="text-sm font-black tracking-widest text-white uppercase">AI Agent Instructions</h2>
          <button
            className="w-8 h-8 rounded-lg bg-[#161A1E] border border-gray-700 text-gray-400 hover:text-white flex items-center justify-center"
            onClick={onClose}
          >
            <i className="fas fa-times"></i>
          </button>
        </div>

        <div className="px-6 py-6 space-y-6">
          {/* Gauges */}
          <div className="flex items-center justify-between relative">
            <Gauge label="Risk" value={risk} colorVar={colorVar} />
            <div className={`flex-1 h-0.5 mx-2 ${connectorColor}`} style={{ boxShadow: `0 0 8px ${glowColor}` }} />
            <Gauge label="Confidence" value={confidence} colorVar={colorVar} />
          </div>

          {/* Inputs */}
          <div className="space-y-3">
            <div className="flex items-center justify-between bg-[#161A1E] border border-gray-700 rounded-lg px-4 py-3">
              <span className="text-xs font-semibold text-gray-300">Stop loss of total capital</span>
              <div className="flex items-center gap-1">
                <input
                  type="number"
                  min="0.5"
                  step="0.5"
                  value={stopLoss}
                  onChange={handleStopLossChange}
                  className="w-16 bg-transparent text-right text-sm font-bold text-white focus:outline-none"
                />
                <span className="text-sm font-bold text-gray-400">%</span>
              </div>
            </div>

            <div className="flex items-center justify-between bg-[#161A1E] border border-gray-700 rounded-lg px-4 py-3">
              <span className="text-xs font-semibold text-gray-300">Capital profit of the day <span className="text-gray-500">(optional)</span></span>
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

          {/* Trades info */}
          <p className="text-center text-xs text-gray-400">
            Ai bot can do <span className={`font-black ${isRed ? 'text-red-400' : 'text-green-400'}`}>{trades}</span> trades at a time as per your stop loss.
          </p>

          {/* Action area */}
          {confirming ? (
            <div className="space-y-3">
              <div className="text-center text-sm text-red-400 font-bold">
                <i className="fas fa-exclamation-triangle mr-1.5"></i>
                High risk: {Math.round(risk)}% risk / {Math.round(confidence)}% confidence. Proceed anyway?
              </div>
              <div className="grid grid-cols-2 gap-3">
                <button
                  className="bg-gray-700 hover:bg-gray-600 text-white font-bold py-3 rounded-lg uppercase tracking-wide text-xs"
                  onClick={() => setConfirming(false)}
                  disabled={starting}
                >
                  <i className="fas fa-times mr-1.5"></i> Emergency Exit
                </button>
                <button
                  className="bg-red-600 hover:bg-red-500 text-white font-bold py-3 rounded-lg uppercase tracking-wide text-xs"
                  onClick={runStart}
                  disabled={starting}
                >
                  {starting ? 'Starting...' : (<><i className="fas fa-play mr-1.5"></i> Continue</>)}
                </button>
              </div>
            </div>
          ) : (
            <button
              className={`w-full ${solidColor} ${isRed ? 'text-white' : 'text-black'} font-black py-3.5 rounded-lg text-sm uppercase tracking-widest transition-all active:scale-95 disabled:opacity-60`}
              style={{ boxShadow: `0 0 20px ${glowColor}` }}
              onClick={handleStartClick}
              disabled={starting}
            >
              {starting ? 'Starting...' : 'Start AI Automation'}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
