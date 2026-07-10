const HOW_IT_WORKS = [
  'Pick a coin pair and chart timeframe (1M, 5M, 15M, etc.).',
  'Press START AI AUTOMATION and set your risk level and optional daily profit goal.',
  'After each candle closes, the bot scans for bullish or bearish candlestick patterns.',
  'A BUY signal opens a LONG trade. A SELL signal opens a SHORT trade.',
  'Your risk % sets how many trades can run at the same time.',
  'Each auto trade uses a small share of your capital and a fixed take-profit target.',
  'A fee-aware filter can skip weak signals that may not cover trading costs.',
  'Before flipping direction, the bot closes the opposite side — it does not hedge both ways.',
  'PAPER mode simulates fills on your dashboard. LIVE / test mode sends real orders to the exchange test environment.',
  'Press STOP to end automation. Use System Log to see scans, blocks, and trade results.',
  'You can still open or close positions manually with + Add Position anytime.',
  'After you start automation, the bot keeps running on the cloud server. You can close the browser or turn off your PC. Log in again later and you will see the same running state.',
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
            Short overview in plain English. This is automated pattern trading — not financial advice.
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
