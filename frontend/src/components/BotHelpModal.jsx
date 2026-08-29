const SECTIONS = [
  {
    title: 'AI Engine',
    items: [
      'START AI ENGINE scans watchlist pairs on your chart TF (1m–1D).',
      'Engine runs on the VPS — closing the browser does NOT stop trading. Only AI ENGINE STOP does.',
      'Momentum gate: only coins with MARKET avg% above TF floor (1M>0.03, 5M>0.05, 15M>0.15, 1H>0.35, 1D>5) auto-enter watchlist; re-scan every 7 candles.',
      'HARD RULE: 7th-candle / watchlist refresh·replace·add·edit NEVER closes or hides related OPEN trades — they keep their own TP/SL until exit.',
      'Brain patterns + structure traps + order-flow trap (1M exec / 5M bias) → AI BUY/SELL/HOLD.',
      'Pattern scan uses the last closed candle. On 1m: lock after AI confirm, skip 1st green/red tick, fire on 2nd (max 3 bars). On 5m: fire on 1st green/red tick. On 15m+: fire at next candle open. First detect per pair is skipped. 1m next fire earliest N+3.',
      'Size: engine risk % set at START (typical 7%) · Bybit $5 minimum notional per trade.',
      '1m path exit: profit +0.5% peak-trail · loss soft −0.3% hard −0.4% unlock −0.1%. 5m+: loss soft −0.5%…−0.7%.',
      '1m/5m: AI Engine soft-restarts every 1 hour (fresh momentum scan + confirm pipeline; open trades kept).',
    ],
  },
  {
    title: 'Stop-loss (per trade)',
    items: [
      '1m: soft lock @ −0.30%; hard exit @ −0.40%; unlock @ −0.10% → profit book.',
      'Other TFs: soft lock @ −0.50%; hard exit @ −0.70%; unlock @ −0.20%.',
      'Loss zone: 0.20% upward trail — sell line = best recovery + 0.20.',
      'Deeper than hard floor → HOLD until trail or unlock (hard exit fires at band).',
    ],
  },
  {
    title: 'Take-profit (per trade)',
    items: [
      'Arm @ +0.50%; lock follows peak profit; trail 0.10% (peak +0.73% → floor +0.63%).',
      'All TFs: continuous ratchet — bottom lock always peak − 0.10%.',
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
