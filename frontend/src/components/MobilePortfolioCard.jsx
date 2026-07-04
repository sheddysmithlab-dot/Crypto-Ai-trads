export default function MobilePortfolioCard({ totalCapital, dailyProfit, tradesCount }) {
  const isProfit = dailyProfit >= 0;
  const capStr = totalCapital.toLocaleString('en-US', { minimumFractionDigits: 2 });
  const pnlStr = `${isProfit ? '+' : '-'}$${Math.abs(dailyProfit).toLocaleString('en-US', { minimumFractionDigits: 2 })}`;

  return (
    <div className="lg:hidden bg-lightCard dark:bg-darkCard mx-3 mt-3 rounded-xl shadow border border-gray-200 dark:border-gray-800 p-4">
      <div className="text-xs font-bold text-gray-500 dark:text-gray-400 mb-2 uppercase tracking-wide">Portfolio</div>
      <div className="flex justify-between text-center">
        <div>
          <div className="text-[11px] text-gray-500 dark:text-gray-400">Total Capital</div>
          <div className="font-bold text-sm">${capStr}</div>
        </div>
        <div>
          <div className="text-[11px] text-gray-500 dark:text-gray-400">24h Profit</div>
          <div className={`font-bold text-sm ${isProfit ? 'text-green-500' : 'text-red-500'}`}>{pnlStr}</div>
        </div>
        <div>
          <div className="text-[11px] text-gray-500 dark:text-gray-400">Open Positions</div>
          <div className="font-bold text-sm">{tradesCount}</div>
        </div>
      </div>
    </div>
  );
}
