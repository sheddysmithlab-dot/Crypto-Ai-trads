import PairSelectorDropdown from './PairSelectorDropdown';
import { fmtNum } from '../data/pairs';

const TIMEFRAMES = ['1M', '5M', '15M', '1H', '1D'];

export default function ChartPanel({ pairSelector, chartContainerRef, rsiContainerRef, timeframe, switchTimeframe, readouts }) {
  return (
    <div className="bg-lightCard dark:bg-darkCard rounded-xl shadow border border-gray-200 dark:border-gray-800 overflow-hidden">
      {/* Chart Header */}
      <div className="flex justify-between items-center px-3 py-2 border-b border-gray-200 dark:border-gray-800">
        <PairSelectorDropdown
          pairs={pairSelector.pairs}
          activePair={pairSelector.activePair}
          activePairLabel={pairSelector.activePairLabel}
          selectPair={pairSelector.selectPair}
          toggleStar={pairSelector.toggleStar}
        />
        <div className="flex gap-1 text-xs font-semibold">
          {TIMEFRAMES.map((tf) => (
            <button
              key={tf}
              className={`px-2.5 py-1 rounded ${
                tf === timeframe ? 'bg-blue-600 text-white' : 'text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800'
              }`}
              onClick={() => switchTimeframe(tf)}
            >
              {tf}
            </button>
          ))}
        </div>
      </div>

      {/* OHLC / Volume / EMA Readouts */}
      <div className="px-4 pt-2 text-[11px] text-gray-500 dark:text-gray-400 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono">
        <span className="font-sans font-semibold text-gray-700 dark:text-gray-300">{readouts.label}</span>
        <span>
          O<span className="text-gray-700 dark:text-gray-200">{fmtNum(readouts.open)}</span>
        </span>
        <span>
          H<span className="text-gray-700 dark:text-gray-200">{fmtNum(readouts.high)}</span>
        </span>
        <span>
          L<span className="text-gray-700 dark:text-gray-200">{fmtNum(readouts.low)}</span>
        </span>
        <span>
          C
          <span className={`font-bold ${readouts.isUp ? 'text-green-500' : 'text-red-500'}`}>
            {fmtNum(readouts.close)} ({readouts.changePct >= 0 ? '+' : ''}
            {readouts.changePct.toFixed(2)}%)
          </span>
        </span>
      </div>
      <div className="px-4 text-[11px] text-gray-500 dark:text-gray-400 font-mono">
        Volume <span className="text-blue-400">{fmtNum(readouts.vol)}</span>
      </div>
      <div className="px-4 pb-2 text-[11px] text-gray-500 dark:text-gray-400 font-mono">
        EMA (20, 50) <span className="text-yellow-500">{readouts.ema.toFixed(3)}</span>
      </div>

      {/* Candlestick Chart */}
      <div ref={chartContainerRef} className="w-full h-48 lg:h-64 relative">
        <div className="absolute left-2 bottom-1.5 flex items-center gap-1 pointer-events-none opacity-30 select-none z-10">
          <div className="w-3.5 h-3.5 rounded bg-gradient-to-br from-blue-500 to-indigo-600 flex items-center justify-center font-black text-white text-[7px]">
            Ai
          </div>
          <span className="text-[9px] font-bold tracking-wide text-gray-500 dark:text-gray-400">AI TRADING BOT</span>
        </div>
      </div>

      {/* RSI Sub-panel */}
      <div className="px-3 pt-1.5 text-[10px] text-gray-500 dark:text-gray-400 font-mono border-t border-gray-200 dark:border-gray-800">
        RSI (14) <span className="text-purple-400 font-bold">{readouts.rsi.toFixed(2)}</span>
      </div>
      <div ref={rsiContainerRef} className="w-full h-14 lg:h-16 pb-1.5"></div>
    </div>
  );
}
