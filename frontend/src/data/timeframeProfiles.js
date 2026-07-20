/** Chart TF → expected win/lose display rates + auto trade capital %. */
export const TIMEFRAME_PROFILES = {
  '1M': { winRate: 30, loseRate: 70, capitalPct: 3 },
  '5M': { winRate: 50, loseRate: 50, capitalPct: 7 },
  '15M': { winRate: 60, loseRate: 40, capitalPct: 10 },
  '1H': { winRate: 70, loseRate: 30, capitalPct: 15 },
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
