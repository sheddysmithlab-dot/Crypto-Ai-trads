import PairSelectorDropdown from './PairSelectorDropdown';
import { fmtNum } from '../data/pairs';

const TIMEFRAMES = ['1M', '5M', '15M', '1H', '1D'];

export default function ChartPanel({ pairSelector, chartContainerRef, volumeContainerRef, timeframe, switchTimeframe, readouts }) {
  return (
    <div className="bg-lightCard dark:bg-darkCard rounded-xl shadow border border-gray-200 dark:border-gray-800 overflow-hidden shrink-0">
      {/* Chart Header */}
      <div className="flex justify-between items-center px-3 py-2 border-b border-gray-200 dark:border-gray-800">
        <PairSelectorDropdown
          pairs={pairSelector.pairs}
          activePair={pairSelector.activePair}
          activePairLabel={pairSelector.activePairLabel}
          selectPair={pairSelector.selectPair}
          toggleStar={pairSelector.toggleStar}
        />
        <div className="flex flex-wrap justify-end gap-1 text-xs font-semibold">
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

      {/* Candlestick Chart - zoomed by default to the last 10 candles */}
      <div ref={chartContainerRef} className="w-full h-80 lg:h-[28rem] relative">
        <div className="absolute left-2 bottom-1.5 flex items-center gap-1 pointer-events-none opacity-30 select-none z-10">
          <div className="w-3.5 h-3.5 rounded bg-gradient-to-br from-blue-500 to-indigo-600 flex items-center justify-center font-black text-white text-[7px]">
            Ai
          </div>
          <span className="text-[9px] font-bold tracking-wide text-gray-500 dark:text-gray-400">AI TRADING BOT</span>
        </div>
      </div>

      {/* Volume Sub-panel */}
      <div className="px-3 pt-1.5 text-[10px] text-gray-500 dark:text-gray-400 font-mono border-t border-gray-200 dark:border-gray-800 flex flex-wrap items-baseline gap-x-3">
        <span className="font-sans font-semibold text-gray-700 dark:text-gray-300">Volume(20)</span>
        <span>
          MA20: <span className="text-yellow-500 font-bold">{fmtNum(readouts.volMA)}</span>
        </span>
        <span>
          VOL:{pairSelector.activePair.symbol}: <span className="text-blue-400 font-bold">{fmtNum(readouts.vol)}</span>
        </span>
      </div>
      <div ref={volumeContainerRef} className="w-full h-16 lg:h-24 pb-1.5"></div>
    </div>
  );
}
