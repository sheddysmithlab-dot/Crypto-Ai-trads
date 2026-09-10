const SECTIONS = [
  {
    title: 'AI Engine',
    items: [
      'START AI ENGINE scans watchlist pairs on your chart TF (1m–1D).',
      'Engine runs on the VPS — closing the browser does NOT stop trading. Only AI ENGINE STOP does.',
      'Momentum gate: only coins with MARKET avg% above TF floor (1M>0.055, 5M>0.085, 15M>0.25, 1H>0.55, 1D>7) auto-enter watchlist; re-scan hourly.',
      'HARD RULE: 7th-candle / watchlist refresh·replace·add·edit NEVER closes or hides related OPEN trades — they keep their own TP/SL until exit.',
      'Brain 5-step pipeline: (1) pattern detect → (2) trap scan → (3) candle-1 confirm/pullback → (4) 10th-man ALLOW/VETO → (5) fire or skip.',
      'Step2/4: if structure trap fights the pattern, take the opposite (trap) side — do not skip into the trap. Dual-gate must still pass on the flipped side.',
      '1m only: if pattern OF side score < 30, reverse the trade (fade) — do not skip. Other TFs still skip weak scores.',
      'Every TF: Step3 = impulse ≥2×trail then trail% pullback post-only maker. Opposite color skips only before impulse lock (after lock, pullback fill allowed). Miss / move ≥ profit / 10th-man VETO = skip (no taker chase). Exit market/taker.',
      'Size ladder: 1m 1.5% · 5m 3% · 15m 7% · 1h 12% · 1D 20% · Bybit $5 minimum notional.',
      'Exit ladder: 1m +0.50 trail 0.10 soft −0.50 hard −0.70 · 5m +0.70/0.15/−0.70/−1.00 · 15m +1.00/0.20/−1.00/−1.40 · 1h +1.50/0.25/−1.50/−2.00 · 1D +2.50/0.40/−2.50/−3.00.',
      '1m/5m/15m: AI Engine soft-restarts every 1 hour (fresh momentum scan + 5-step pipeline; open trades kept). 15m fire = same scalp pack as 1m; book-profit/hard-stop stay on 15m ladder.',
    ],
  },
  {
    title: 'Stop-loss (per trade)',
    items: [
      'Soft lock is the TF soft stop (1m −0.50 … 1D −2.50). Bounce up is not an exit — lock stays until hard, failed bounce, or unlock.',
      'Failed bounce: drop the TF trail from the bounce high → exit. Unlock is half the soft stop so a clean bounce can run to profit.',
      'Hard floor is the TF hard stop (1m −0.70 … 1D −3.00) → instant exit.',
    ],
  },
  {
    title: 'Take-profit (per trade)',
    items: [
      'Profit arms at +0.50, trails −0.10 until +0.65, then −0.20. No +1% hard exit.',
      '1m batch: when 10 trades net ≥ +0.25% (after fees), all 10 exit together.',
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
