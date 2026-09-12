/** Shared exit-policy copy — matches backend TF ladder (timeframe_profiles.py). */

export const EXIT_LADDER = {
  '0S': { profit: 0.3, trail: 0.3, soft: 0, hard: 1.0, capitalPct: 0.5 },
  '1M': { profit: 0.5, trail: 0.1, soft: 0.5, hard: 0.7, capitalPct: 1.5 },
  '5M': { profit: 0.7, trail: 0.15, soft: 0.7, hard: 1.0, capitalPct: 3 },
  '15M': { profit: 1.0, trail: 0.2, soft: 1.0, hard: 1.4, capitalPct: 7 },
  '1H': { profit: 1.5, trail: 0.25, soft: 1.5, hard: 2.0, capitalPct: 12 },
  '1D': { profit: 2.5, trail: 0.4, soft: 2.5, hard: 3.0, capitalPct: 20 },
};

export function exitLadderFor(tf) {
  const key = String(tf || '1M').toUpperCase();
  return EXIT_LADDER[key] || EXIT_LADDER['1M'];
}

function fmtPct(n) {
  return Number(n).toFixed(2);
}

export function exitOverlayFor(tf) {
  const key = String(tf || '1M').toUpperCase();
  if (key === '0S') {
    return '0S tick · 7-coin batch · P+fees > L+fees · trail +0.30/−0.30 · hard −1.00%';
  }
  const row = exitLadderFor(tf);
  const label = String(tf || '1M');
  return (
    `${label} maker entry · profit +${fmtPct(row.profit)} trail ${fmtPct(row.trail)} · ` +
    `SL −${fmtPct(row.soft)} hard −${fmtPct(row.hard)}`
  );
}

export const EXIT_POLICY_SHORT =
  'maker entry / taker exit · 1m +0.50/0.10/−0.70 · 5m +0.70/0.15/−1.00 · 15m +1.00/0.20/−1.40 · 1h +1.50/0.25/−2.00 · 1D +2.50/0.40/−3.00';

export const EXIT_POLICY_CHART_OVERLAY = exitOverlayFor('1M');

export const EXIT_POLICY_SYSTEM_LOG =
  'All TF entry maker limit (post-only); exit market/taker. ' +
  'Ladder: 1m profit +0.50 trail 0.10 soft −0.50 hard −0.70 size 1.5% · ' +
  '5m +0.70/0.15/−0.70/−1.00 size 3% · 15m +1.00/0.20/−1.00/−1.40 size 7% · ' +
  '1h +1.50/0.25/−1.50/−2.00 size 12% · 1D +2.50/0.40/−2.50/−3.00 size 20%.';

export const EXIT_POLICY_MODAL =
  'Maker entry + taker exit on every TF. Size and stops widen 1m < 5m < 15m < 1h < 1D. ' +
  '1m +0.50 trail 0.10 soft −0.50 hard −0.70 size 1.5%. ' +
  '5m +0.70 / 0.15 / −0.70 / −1.00 / 3%. ' +
  '15m +1.00 / 0.20 / −1.00 / −1.40 / 7%. ' +
  '1h +1.50 / 0.25 / −1.50 / −2.00 / 12%. ' +
  '1D +2.50 / 0.40 / −2.50 / −3.00 / 20%.';

/** Human label for trading mode in logs (never imply testnet when live). */
export function tradeFireModeLabel({ tradeFireMode, tradingMode, isPaper }) {
  if (tradeFireMode) return tradeFireMode;
  if (isPaper || tradingMode === 'PAPER_TRADING') return 'PAPER_TRADING';
  if (tradingMode === 'LIVE_TRADING') return 'LIVE_TRADING';
  return tradingMode || 'LIVE_TRADING';
}
