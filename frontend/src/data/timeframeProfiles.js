/** Chart TF → expected win/lose display rates + auto trade capital %. */
export const TIMEFRAME_PROFILES = {
  '0S': { winRate: 20, loseRate: 80, capitalPct: 0.5 },
  '1M': { winRate: 30, loseRate: 70, capitalPct: 1.5 },
  '5M': { winRate: 40, loseRate: 60, capitalPct: 3 },
  '15M': { winRate: 60, loseRate: 40, capitalPct: 7 },
  '1H': { winRate: 70, loseRate: 30, capitalPct: 12 },
  '1D': { winRate: 80, loseRate: 20, capitalPct: 20 },
};

export function getTimeframeProfile(tf) {
  return (
    TIMEFRAME_PROFILES[tf] || {
      winRate: 50,
      loseRate: 50,
      capitalPct: 7,
    }
  );
}
