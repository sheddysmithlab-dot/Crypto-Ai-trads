/** Shared exit-policy copy — matches backend path engine (main.py). */
export const EXIT_POLICY_SHORT =
  'Profit +0.5% peak-trail · Loss lock −0.5%…−0.7%';

export const EXIT_POLICY_CHART_OVERLAY =
  'Path exit · profit +0.5% trail · loss −0.5%…−0.7%';

export const EXIT_POLICY_SYSTEM_LOG =
  'Path exit: profit arm +0.5% peak-trail −0.1%; loss soft lock −0.5%, trail +0.2% in band, hard @ −0.7%';

export const EXIT_POLICY_MODAL =
  'Path exit on every open trade — profit arm +0.5% with peak trail; loss soft lock −0.5% with trail in −0.5…−0.7% band (hard exit @ −0.7%).';

/** Human label for trading mode in logs (never imply testnet when live). */
export function tradeFireModeLabel({ tradeFireMode, tradingMode, isPaper }) {
  if (tradeFireMode) return tradeFireMode;
  if (isPaper || tradingMode === 'PAPER_TRADING') return 'PAPER_TRADING';
  if (tradingMode === 'LIVE_TRADING') return 'LIVE_TRADING';
  return tradingMode || 'LIVE_TRADING';
}
