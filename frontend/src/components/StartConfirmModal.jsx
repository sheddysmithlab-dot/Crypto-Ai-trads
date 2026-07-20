import { useEffect, useRef, useState } from 'react';

// "Emergency Exit & Continue" pre-start safety check.
// Opened immediately after the AI Agent Instructions popup closes (Step 4 of
// the wiring flow). This is the final confirmation before the bot actually
// starts - it shows the config the user just picked and asks one last time:
//   - Continue      -> apply /agent/config + POST /start-bot (handled by parent)
//   - Emergency Exit-> cancel, do NOT start (also the 30s auto-default)
// Visually mirrors the in-trading RULE 8 RiskAlertModal (warning icon, 30s
// countdown, two buttons) but the buttons are wired for pre-start semantics,
// not mid-trade loss resolution, so the two flows stay separate.
export default function StartConfirmModal({ open, config, activeCount = 0, onContinue, onExit }) {
  const [seconds, setSeconds] = useState(30);
  const intervalRef = useRef(null);
  const onExitRef = useRef(onExit);
  onExitRef.current = onExit;

  useEffect(() => {
    if (!open) return;
    setSeconds(30);
    intervalRef.current = setInterval(() => {
      setSeconds((s) => (s > 0 ? s - 1 : 0));
    }, 1000);
    return () => clearInterval(intervalRef.current);
  }, [open]);

  // Fire onExit when the countdown hits 0, in its own effect so we don't mutate
  // parent state while React is still committing this component's state.
  useEffect(() => {
    if (open && seconds === 0) {
      clearInterval(intervalRef.current);
      onExitRef.current();
    }
  }, [open, seconds]);

  if (!open) return null;

  const isRed = config?.isRed;
  const accent = isRed ? 'red' : 'green';
  const borderClass = isRed ? 'border-red-500' : 'border-green-500';
  const iconClass = isRed ? 'text-red-500' : 'text-green-500';
  const exitBtn = isRed ? 'bg-gray-700 hover:bg-gray-600' : 'bg-gray-700 hover:bg-gray-600';
  const continueBtn = isRed ? 'bg-red-600 hover:bg-red-500' : 'bg-emerald-600 hover:bg-emerald-500';

  return (
    <div className="fixed inset-0 bg-black bg-opacity-80 z-[113] flex items-center justify-center backdrop-blur-sm p-4">
      <div className={`modal-enter bg-[#0B0E11] p-8 rounded-2xl shadow-2xl max-w-md w-full border-2 ${borderClass} text-center`}>
        <i className={`fas fa-exclamation-triangle text-6xl ${iconClass} mb-4 animate-bounce`}></i>
        <h2 className="text-xl font-black text-white mb-2 uppercase tracking-wide">Final Safety Check</h2>
        <p className="text-sm text-gray-400 mb-5">
          AI Automation ko start karne se pehle confirm karein. Niche aapki chosen settings hain:
        </p>

        {activeCount > 0 ? (
          <div className="mb-5 rounded-lg border border-amber-500/60 bg-amber-500/10 px-4 py-3 text-left text-sm text-amber-200">
            <i className="fas fa-shield-alt mr-2"></i>
            <span className="font-bold">{activeCount} open position(s)</span> list me protected rahengi.
            Manual trades AI ke auto-sell se safe rahengi.
          </div>
        ) : null}

        <div className="grid grid-cols-2 gap-3 text-sm mb-6">
          <div className="bg-[#161A1E] border border-gray-700 rounded-lg px-3 py-2">
            <div className="text-[10px] uppercase tracking-wider text-gray-500">Stop Loss of Total Capital</div>
            <div className="font-bold text-white">{config?.stopLossPct}%</div>
          </div>
          <div className="bg-[#161A1E] border border-gray-700 rounded-lg px-3 py-2">
            <div className="text-[10px] uppercase tracking-wider text-gray-500">Daily Profit Target</div>
            <div className="font-bold text-white">{config?.dailyProfitPct ? `${config.dailyProfitPct}%` : 'Off'}</div>
          </div>
          <div className="bg-[#161A1E] border border-gray-700 rounded-lg px-3 py-2">
            <div className="text-[10px] uppercase tracking-wider text-gray-500">Risk</div>
            <div className={`font-bold ${isRed ? 'text-red-400' : 'text-green-400'}`}>{Math.round(config?.risk ?? 0)}%</div>
          </div>
          <div className="bg-[#161A1E] border border-gray-700 rounded-lg px-3 py-2">
            <div className="text-[10px] uppercase tracking-wider text-gray-500">Confidence</div>
            <div className={`font-bold ${isRed ? 'text-red-400' : 'text-green-400'}`}>{Math.round(config?.confidence ?? 0)}%</div>
          </div>
          <div className="col-span-2 bg-[#161A1E] border border-gray-700 rounded-lg px-3 py-2">
            <div className="text-[10px] uppercase tracking-wider text-gray-500">Concurrent Trades</div>
            <div className={`font-bold ${isRed ? 'text-red-400' : 'text-green-400'}`}>{config?.trades}</div>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <button
            className={`${exitBtn} text-white font-bold py-3 rounded-lg uppercase tracking-wide text-xs sm:text-sm flex items-center justify-center`}
            onClick={onExit}
          >
            <i className="fas fa-skull-crossbones mr-1.5"></i> Emergency Exit
          </button>
          <button
            className={`${continueBtn} text-white font-bold py-3 rounded-lg uppercase tracking-wide text-xs sm:text-sm flex items-center justify-center`}
            onClick={onContinue}
          >
            <i className="fas fa-play mr-1.5"></i> Continue
          </button>
        </div>

        <div className={`text-xs mt-4 ${accent === 'red' ? 'text-red-400' : 'text-gray-500 dark:text-gray-400'}`}>
          Auto <span className="font-bold">Emergency Exit</span> in <span className="font-bold">{seconds}</span>s if no response...
        </div>
      </div>
    </div>
  );
}
