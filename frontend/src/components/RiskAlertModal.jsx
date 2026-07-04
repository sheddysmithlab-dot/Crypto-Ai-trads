import { useEffect, useRef, useState } from 'react';

// Shown automatically when the backend's 2.5%+ cumulative stop-loss threshold
// is breached. Offers EMERGENCY EXIT (stay halted) or CONTINUE (resume trading
// with the ceiling raised by another 2.5%). Auto-defaults to Emergency Exit if
// the user doesn't respond within 30s.
export default function RiskAlertModal({ open, lossPct, currentThreshold, onExit, onContinue }) {
  const [seconds, setSeconds] = useState(30);
  const intervalRef = useRef(null);
  const onExitRef = useRef(onExit);
  onExitRef.current = onExit;

  // Tick the countdown down to 0.
  useEffect(() => {
    if (!open) return;
    setSeconds(30);
    intervalRef.current = setInterval(() => {
      setSeconds((s) => (s > 0 ? s - 1 : 0));
    }, 1000);
    return () => clearInterval(intervalRef.current);
  }, [open]);

  // React to the countdown hitting 0 in its own effect - calling onExit
  // directly from inside the setSeconds updater above would update App's
  // state while React is still committing this component's state.
  useEffect(() => {
    if (open && seconds === 0) {
      clearInterval(intervalRef.current);
      console.warn('[RULE 8] 30s countdown expired. Auto-executing Emergency Exit.');
      onExitRef.current();
    }
  }, [open, seconds]);

  if (!open) return null;

  const absPct = Math.abs(lossPct).toFixed(2);
  const nextThreshold = (currentThreshold + 2.5).toFixed(1);

  return (
    <div className="fixed inset-0 bg-black bg-opacity-70 z-[105] flex items-center justify-center backdrop-blur-sm">
      <div className="modal-enter bg-lightCard dark:bg-darkCard p-8 rounded-2xl shadow-2xl max-w-md w-full border border-red-500 text-center transform scale-95 transition-transform duration-300">
        <i className="fas fa-exclamation-triangle text-6xl text-red-500 mb-4 animate-bounce"></i>
        <h2 className="text-2xl font-black text-gray-900 dark:text-white mb-3">
          RULE 8: <span className="text-red-500">{absPct}%</span> Loss Limit Reached
        </h2>
        <p className="text-gray-600 dark:text-gray-300 mb-3 font-semibold">Choose Your Action</p>
        <p className="text-sm text-gray-500 dark:text-gray-400 mb-6 leading-relaxed">
          Your total capital has dropped <span className="font-bold text-red-500">{absPct}%</span> from the starting
          balance.
          <br />
          <br />
          <strong>Option 1:</strong> Permanently halt all trading.
          <br />
          <strong>Option 2:</strong> Continue with a raised stop-loss buffer to{' '}
          <span className="font-bold text-yellow-500">{nextThreshold}%</span>.
        </p>
        <div className="grid grid-cols-2 gap-3">
          <button
            className="bg-red-600 hover:bg-red-700 text-white font-bold py-3 rounded-lg uppercase tracking-wide text-xs sm:text-sm flex items-center justify-center"
            onClick={onExit}
          >
            <i className="fas fa-skull-crossbones mr-1.5"></i> Emergency Exit
          </button>
          <button
            className="bg-emerald-600 hover:bg-emerald-700 text-white font-bold py-3 rounded-lg uppercase tracking-wide text-xs sm:text-sm flex items-center justify-center"
            onClick={onContinue}
          >
            <i className="fas fa-play mr-1.5"></i> Continue
          </button>
        </div>
        <div className="text-xs text-gray-500 dark:text-gray-400 mt-4">
          Auto Emergency Exit in <span className="font-bold text-red-500">{seconds}</span>s if no response...
        </div>
      </div>
    </div>
  );
}
