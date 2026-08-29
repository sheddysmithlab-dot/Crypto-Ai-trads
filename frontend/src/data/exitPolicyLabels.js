/** Shared exit-policy copy — matches backend path engine (main.py). */
export const EXIT_POLICY_SHORT =
  'Profit +0.5% peak-trail · 1m loss −0.3%…−0.4% · else −0.5%…−0.7%';

export const EXIT_POLICY_CHART_OVERLAY =
  'Path exit · profit +0.5% trail · 1m loss −0.3%…−0.4%';

export const EXIT_POLICY_SYSTEM_LOG =
  'Path exit: profit arm +0.5% peak-trail −0.1%; 1m soft −0.3% hard −0.4% unlock −0.1%; else soft −0.5% hard −0.7% unlock −0.2%';

export const EXIT_POLICY_MODAL =
  'Path exit — profit arm +0.5% with peak trail. 1m: soft lock −0.3%, hard −0.4%, unlock −0.1%. Other TFs: soft −0.5%, hard −0.7%, unlock −0.2%.';

/** Human label for trading mode in logs (never imply testnet when live). */
export function tradeFireModeLabel({ tradeFireMode, tradingMode, isPaper }) {
  if (tradeFireMode) return tradeFireMode;
  if (isPaper || tradingMode === 'PAPER_TRADING') return 'PAPER_TRADING';
  if (tradingMode === 'LIVE_TRADING') return 'LIVE_TRADING';
  return tradingMode || 'LIVE_TRADING';
}
