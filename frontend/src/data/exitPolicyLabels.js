/** Shared exit-policy copy — matches backend path engine (main.py). */
export const EXIT_POLICY_SHORT =
  '1m hard +0.34%/−0.40% · else profit +0.5% trail · loss −0.5%…−0.7%';

export const EXIT_POLICY_CHART_OVERLAY =
  'Path exit · 1m hard TP +0.34% / SL −0.40%';

export const EXIT_POLICY_SYSTEM_LOG =
  'Path exit: 1m hard TP +0.34% / SL −0.40% (no trail); else profit arm +0.5% peak-trail −0.1%; soft −0.5% hard −0.7% unlock −0.2%';

export const EXIT_POLICY_MODAL =
  'Path exit — 1m: hard TP +0.34% and hard SL −0.40% (no trail). Other TFs: profit arm +0.5% with peak trail; soft lock −0.5%, hard −0.7%, unlock −0.2%.';

/** Human label for trading mode in logs (never imply testnet when live). */
export function tradeFireModeLabel({ tradeFireMode, tradingMode, isPaper }) {
  if (tradeFireMode) return tradeFireMode;
  if (isPaper || tradingMode === 'PAPER_TRADING') return 'PAPER_TRADING';
  if (tradingMode === 'LIVE_TRADING') return 'LIVE_TRADING';
  return tradingMode || 'LIVE_TRADING';
}
