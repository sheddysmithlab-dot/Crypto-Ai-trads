const HOW_IT_WORKS = [
  'Pick a coin pair and chart timeframe (1M, 5M, 15M, etc.).',
  'Press START AI AUTOMATION and set your risk level (max open trades).',
  'On each closed candle the bot runs SMC+VSA: 11 rule groups with a 200 EMA trend filter.',
  'VSA long (uptrend): R1 red big candle, R2 red hammer, R3 volume divergence, R5 green hammer.',
  'SMC long: R8 liquidity sweep spring, R10 buy absorption, R12 no-supply dry-up.',
  'VSA short (downtrend): R4 green big candle, R6 hidden selling, R7 volume climax.',
  'SMC short: R9 up-thrust sweep, R11 sell absorption.',
  'Size: 1x = 10% capital (R1, R4, R12). 2x = 20% (other rules). Multiple rules → highest size wins.',
  'BUY → LONG, SELL → SHORT. Opposite positions close before a flip.',
  'TAAPI.io is paused — all signals come from Bybit candle math only.',
  'PAPER simulates fills; testnet sends real orders. STOP ends automation.',
  'Bot keeps running on the cloud server after you close the browser.',
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
            SMC + VSA system in plain English. Not financial advice.
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
