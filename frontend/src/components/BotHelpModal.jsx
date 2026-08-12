const HOW_IT_WORKS = [
  'Pick a coin pair and chart timeframe (1M, 5M, 15M, etc.).',
  'Press START AI AUTOMATION — scanner runs on your watchlist closed candles.',
  '1M/5M = Scalp mode: near 15m S/R + prefer liquidity sweeps; news/panic gates block bad entries.',
  '1M/5M exits: book 50% at 1R, move SL to breakeven, trail the rest toward ~2R.',
  '15M+ = Fire Engine v3.1 (patterns + structure + indicators → SL + 1:2 TP).',
  'You can still manual-close or emergency sell-all anytime. PAPER / testnet as configured.',
];

export default function BotHelpModal({ open, onClose }) {
  if (!open) return null;

  return (
    <div
      className="fixed inset-0 bg-black/70 z-[108] flex items-center justify-center backdrop-blur-sm p-4"
      onClick={onClose}
      role="presentation"
    >
      <div
        className="modal-enter bg-lightCard dark:bg-darkCard rounded-2xl shadow-2xl max-w-lg w-full border border-gray-200 dark:border-gray-700 overflow-hidden"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-labelledby="bot-help-title"
        aria-modal="true"
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200 dark:border-gray-800">
          <h2 id="bot-help-title" className="text-sm font-black tracking-widest text-gray-900 dark:text-white uppercase">
            How this bot works
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-800 text-gray-500 dark:text-gray-400"
            aria-label="Close help"
          >
            <i className="fas fa-times" />
          </button>
        </div>

        <div className="px-5 py-4 max-h-[70vh] overflow-y-auto">
          <p className="text-sm text-gray-600 dark:text-gray-300 mb-4 leading-relaxed">
            1M/5M scalp (HTF + sweeps + dynamic exits) or Fire Engine on higher TFs. Not financial advice.
          </p>
          <ul className="space-y-2.5 text-sm text-gray-700 dark:text-gray-200 list-disc pl-5 leading-relaxed">
            {HOW_IT_WORKS.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>

        <div className="px-5 py-4 border-t border-gray-200 dark:border-gray-800">
          <button
            type="button"
            onClick={onClose}
            className="w-full py-2.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-sm font-bold uppercase tracking-wider"
          >
            Got it
          </button>
        </div>
      </div>
    </div>
  );
}
