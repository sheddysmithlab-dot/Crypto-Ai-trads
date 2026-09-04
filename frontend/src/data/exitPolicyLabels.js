/** Shared exit-policy copy — matches backend path engine (main.py). */
export const EXIT_POLICY_SHORT =
  'profit +0.85% peak-trail · loss soft −0.75%…−1.00% (all TFs)';

export const EXIT_POLICY_CHART_OVERLAY =
  'Path exit · profit +0.85% trail / loss −0.75%…−1.00%';

export const EXIT_POLICY_SYSTEM_LOG =
  'Path exit (all TFs incl. 1m): profit arm +0.85% peak-trail −0.15%; soft −0.75% hard −1.00% unlock −0.2%';

export const EXIT_POLICY_MODAL =
  'Path exit — all TFs (incl. 1m): profit arm +0.85% with peak trail −0.15%; soft lock −0.75%, hard −1.00%, unlock −0.2%.';

/** Human label for trading mode in logs (never imply testnet when live). */
export function tradeFireModeLabel({ tradeFireMode, tradingMode, isPaper }) {
  if (tradeFireMode) return tradeFireMode;
  if (isPaper || tradingMode === 'PAPER_TRADING') return 'PAPER_TRADING';
  if (tradingMode === 'LIVE_TRADING') return 'LIVE_TRADING';
  return tradingMode || 'LIVE_TRADING';
}
