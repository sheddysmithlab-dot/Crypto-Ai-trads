const SECTIONS = [
  {
    title: 'AI Engine',
    items: [
      'START AI ENGINE scans watchlist pairs on your chart TF (1m–1D).',
      'Engine runs on the VPS — closing the browser does NOT stop trading. Only AI ENGINE STOP does.',
      'Momentum gate: only coins with MARKET avg% above TF floor (1M>0.03, 5M>0.05, 15M>0.15, 1H>0.35, 1D>5) auto-enter watchlist; re-scan every 7 candles.',
      'HARD RULE: 7th-candle / watchlist refresh·replace·add·edit NEVER closes or hides related OPEN trades — they keep their own TP/SL until exit.',
      'Brain patterns + structure traps + order-flow trap (1M exec / 5M bias) → AI BUY/SELL/HOLD.',
      'Pattern scan uses the last closed candle. On 1m and 5m: lock after AI confirm, then fire as soon as the next bar turns green (LONG) or red (SHORT) live — do not wait for candle close; max 5 bars then skip. On 15m+: fire at next candle open. First detect per pair is skipped.',
      'Size by TF capital %: 1m/5m 1.5% · 15m 10% · 1h 15% · 1D 20%.',
      '1m/5m scalp: OF≥75 (trap≥90), dual profit lock +0.50→+0.40 then +0.65→+0.55. 1m only: max 3 open per chart + 5-bar spacing.',
      '1m/5m: AI Engine soft-restarts every 1 hour (fresh momentum scan + confirm pipeline; open trades kept).',
    ],
  },
  {
    title: 'Stop-loss (per trade)',
    items: [
      '−0.70% → LOSS LOCK + HOLD (no trail exit).',
      'Sell only when price recovers inside −0.50% (gross ≥ −0.50%); otherwise keep holding.',
      'No Bybit reconcile fake exits — positions only close via path SL or profit lock.',
    ],
  },
  {
    title: 'Take-profit (per trade)',
    items: [
      '1m/5m dual lock: +0.50%→exit floor +0.40%; if run continues, +0.65%→floor +0.55%; then +0.20 steps / 0.10 trail.',
      'Other TFs (15m+): +0.50% lock → floor +0.40%; then +0.20 steps / 0.10 trail.',
      'Hard ceiling not fixed — stepped locks keep trailing while trend runs.',
    ],
  },
  {
    title: 'Controls',
    items: [
      'Manual BUY/SELL open LONG/SHORT on the main chart coin (works even while AI/session runs).',
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
