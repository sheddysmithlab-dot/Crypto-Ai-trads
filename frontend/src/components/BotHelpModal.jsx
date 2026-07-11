const PHASES = [
  {
    title: 'System Role',
    lines: [
      'Autonomous AI Trading Agent — scan charts, react to structure, do NOT predict.',
      'Missing any confirmation (e.g. sweep but no BOS) → cancel setup, wait.',
    ],
  },
  {
    title: 'Phase 1 — Zones & Setup',
    lines: [
      'HTF range: Discount 0–50% = LONG only | Premium 50–100% = SHORT only',
      'London trap: Asian breakout → liquidity grab → reversal → retest',
      'Measured move: strong trend → wait 30–70% pullback',
    ],
  },
  {
    title: 'Phase 2 — Confirmation',
    lines: [
      'BUY: Sweep low → CHoCH → BOS → mitigate to demand / order block',
      'SELL: Sweep high → CHoCH → BOS → mitigate to supply',
      'Candles: Hammer / Engulfing / Morning Star (buy) | Shooting star / rejection (sell)',
      'Hold on Doji or Spinning Top',
    ],
  },
  {
    title: 'Phase 3 — Execution',
    lines: [
      'Retest + pin bar at broken level → execute',
      'SL behind structure (reference; profit book via +0.15% stepped lock)',
      'Log every trade in System Log',
    ],
  },
  {
    title: 'Active Engine (this bot)',
    lines: [
      'Blue Box sweep + displacement (BB-L / BB-S)',
      'VSA + SMC: L1–L4, S1–S3, L5/S4 momentum',
      'Marubozu pullback: MBZ-L / MBZ-S',
      'BUY → LONG | SELL → SHORT | Opposite closes before flip',
      'PAPER simulates; TESTNET real orders. STOP ends automation.',
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
        className="modal-enter bg-lightCard dark:bg-darkCard rounded-2xl shadow-2xl max-w-lg w-full border border-gray-200 dark:border-gray-700 overflow-hidden"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-labelledby="bot-help-title"
        aria-modal="true"
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200 dark:border-gray-800">
          <h2 id="bot-help-title" className="text-sm font-black tracking-widest text-gray-900 dark:text-white uppercase">
            AI Agent — System Role
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

        <div className="px-5 py-4 max-h-[70vh] overflow-y-auto space-y-4">
          <p className="text-sm text-gray-600 dark:text-gray-300 leading-relaxed">
            Rule-based SMC framework. Full doc: System Log → System Role. Not financial advice.
          </p>
          {PHASES.map((phase) => (
            <section key={phase.title}>
              <h3 className="text-xs font-bold uppercase tracking-wider text-blue-500 dark:text-blue-400 mb-1.5">
                {phase.title}
              </h3>
              <ul className="space-y-1 text-sm text-gray-700 dark:text-gray-200 list-disc pl-5 leading-relaxed">
                {phase.lines.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            </section>
          ))}
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
