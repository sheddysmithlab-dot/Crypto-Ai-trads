const SECTIONS = [
  {
    title: 'AI Engine',
    items: [
      'START AI ENGINE scans watchlist pairs on your chart TF (1m–1D).',
      'Brain patterns + structure traps + order-flow trap (1M exec / 5M bias) → AI BUY/SELL/HOLD.',
      'Pattern scan uses the last closed candle; trade fires at the next candle open (not on the detect bar).',
      'Size by TF capital %: 1m 3% · 5m 7% · 15m 10% · 1h 15% · 1D 20%.',
    ],
  },
  {
    title: 'Stop-loss (per trade)',
    items: [
      'Continuous dump --- (3 adverse ticks, no bounce) → exit at −0.50%.',
      'Choppy path -+-+ (bounce in loss) → hold past 0.5%, exit at −0.70%.',
      'Hard floor always −0.70%.',
    ],
  },
  {
    title: 'Take-profit (per trade)',
    items: [
      'Continuous run +++ (clean climb) → hold past +0.50%, book at +0.70%.',
      'Choppy path -+-+ (dip while in profit) → book at +0.50%.',
      'Hard ceiling always +0.70%.',
    ],
  },
  {
    title: 'Controls',
    items: [
      'Manual BUY/SELL when AI is OFF; trash icon force-closes one position.',
      'STOP popup: Hold (keep TP/SL) or Emergency (close all). PAPER / Testnet supported.',
      'Session Momentum Engine: timed IST windows (mutually exclusive with main AI).',
    ],
  },
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
        className="modal-enter bg-[#0B0E11] rounded-2xl shadow-2xl max-w-lg w-full border border-gray-700 overflow-hidden"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-labelledby="bot-help-title"
        aria-modal="true"
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-800">
          <h2 id="bot-help-title" className="text-sm font-black tracking-widest text-white uppercase">
            How this bot works
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-gray-800 text-gray-400"
            aria-label="Close help"
          >
            <i className="fas fa-times" />
          </button>
        </div>

        <div className="px-5 py-4 max-h-[70vh] overflow-y-auto space-y-4">
          <p className="text-xs text-gray-400 leading-relaxed">
            Live AI candle brain + path stop-loss. Not financial advice.
          </p>

          {SECTIONS.map((sec) => (
            <section key={sec.title}>
              <h3 className="text-[10px] font-bold uppercase tracking-wider text-blue-400 mb-1.5">
                {sec.title}
              </h3>
              <ul className="space-y-1.5 text-sm text-gray-200 list-disc pl-4 leading-snug">
                {sec.items.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            </section>
          ))}
        </div>

        <div className="px-5 py-4 border-t border-gray-800">
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
