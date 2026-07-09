export default function MobilePortfolioCard({
  totalCapital,
  tradeValue = 0,
  dailyProfit,
  seasonProfit,
  seasonActive,
  tradesCount,
}) {
  const isProfit = dailyProfit >= 0;
  const isSeasonProfit = seasonProfit >= 0;
  const capStr = totalCapital.toLocaleString('en-US', { minimumFractionDigits: 2 });
  const tradeValStr = tradeValue.toLocaleString('en-US', { minimumFractionDigits: 2 });
  const pnlStr = `${isProfit ? '+' : '-'}$${Math.abs(dailyProfit).toLocaleString('en-US', { minimumFractionDigits: 2 })}`;
  const seasonStr = seasonActive
    ? `${isSeasonProfit ? '+' : '-'}$${Math.abs(seasonProfit).toLocaleString('en-US', { minimumFractionDigits: 2 })}`
    : '$0.00';

  return (
    <div className="lg:hidden bg-lightCard dark:bg-darkCard mx-3 mt-3 rounded-xl shadow border border-gray-200 dark:border-gray-800 p-4">
      <div className="text-xs font-bold text-gray-500 dark:text-gray-400 mb-2 uppercase tracking-wide">Portfolio</div>
      <div className="flex justify-between text-center">
        <div>
          <div className="text-[11px] text-gray-500 dark:text-gray-400">Total Capital</div>
          <div className="font-bold text-sm">${capStr}</div>
        </div>
        <div>
          <div className="text-[11px] text-gray-500 dark:text-gray-400">Trade Value</div>
          <div className="font-bold text-sm text-amber-500">${tradeValStr}</div>
        </div>
        <div>
          <div className="text-[11px] text-gray-500 dark:text-gray-400">Daily Profit</div>
          <div className={`font-bold text-sm ${isProfit ? 'text-green-500' : 'text-red-500'}`}>{pnlStr}</div>
        </div>
        <div>
          <div className="text-[11px] text-gray-500 dark:text-gray-400">AI Season</div>
          <div
            className={`font-bold text-sm ${
              !seasonActive ? 'text-gray-400' : isSeasonProfit ? 'text-green-500' : 'text-red-500'
            }`}
          >
            {seasonStr}
          </div>
        </div>
        <div>
          <div className="text-[11px] text-gray-500 dark:text-gray-400">Open</div>
          <div className="font-bold text-sm">{tradesCount}</div>
        </div>
      </div>
    </div>
  );
}
