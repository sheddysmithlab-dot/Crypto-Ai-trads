const HOW_IT_WORKS = [
  'Pick a coin pair and chart timeframe (1M, 5M, 15M, etc.).',
  'Press START AI AUTOMATION and set your risk level (max open trades).',
  'On each closed candle: Blue Box traps, VSA rules (L1–S4), Marubozu pullback, and momentum.',
  'Blue Box: sweep below L20 or above H20, then displacement in the next 1–2 bars.',
  'VSA long (200 EMA uptrend): L1 exhaustion, L2 hammer, L4 absorption. L3 spring + L5 momentum bypass EMA.',
  'VSA short (200 EMA downtrend): S1 exhaustion, S3 absorption. S2 up-thrust + S4 momentum bypass EMA.',
  'Marubozu: EMA50/200 trend + 2–4 bar pullback + large-body marubozu candle.',
  'BUY → LONG, SELL → SHORT. Fired trades stay open — no auto-close on flip or profit lock; use STOP or force-close.',
  'PAPER simulates fills; testnet sends real orders. STOP ends automation.',
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
            Blue Box + Marubozu continuation in plain English. Not financial advice.
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
