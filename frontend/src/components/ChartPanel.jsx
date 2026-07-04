import PairSelectorDropdown from './PairSelectorDropdown';
import { fmtNum } from '../data/pairs';

const TIMEFRAMES = ['10S', '30S', '1M', '5M', '15M', '1H', '1D'];
const MA_COLORS = { 5: '#facc15', 10: '#ec4899', 20: '#38bdf8', 30: '#a855f7' };

export default function ChartPanel({
  pairSelector,
  chartContainerRef,
  volumeContainerRef,
  rsiContainerRef,
  timeframe,
  switchTimeframe,
  readouts,
}) {
  const changeSign = readouts.changeAbs >= 0 ? '+' : '';

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

      {/* Candlestick Chart with an in-chart OHLC/Change/Range + MA legend overlay */}
      <div ref={chartContainerRef} className="w-full h-52 lg:h-72 relative">
        <div className="absolute top-1.5 left-2 z-10 pointer-events-none select-none font-mono text-[10px] leading-tight space-y-1 pr-2">
          <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5 text-gray-500 dark:text-gray-400">
            <span className="font-sans font-semibold text-gray-700 dark:text-gray-300">{readouts.label}</span>
            <span>
              O= <span className="text-gray-700 dark:text-gray-200">{fmtNum(readouts.open)}</span>
            </span>
            <span>
              H= <span className="text-gray-700 dark:text-gray-200">{fmtNum(readouts.high)}</span>
            </span>
            <span>
              L= <span className="text-gray-700 dark:text-gray-200">{fmtNum(readouts.low)}</span>
            </span>
            <span>
              C= <span className="text-gray-700 dark:text-gray-200">{fmtNum(readouts.close)}</span>
            </span>
            <span>
              Change=
              <span className={`font-bold ${readouts.isUp ? 'text-green-500' : 'text-red-500'}`}>
                {' '}
                {changeSign}
                {readouts.changeAbs.toFixed(2)} ({changeSign}
                {readouts.changePct.toFixed(2)}%)
              </span>
            </span>
            <span>
              Range=
              <span className="text-gray-700 dark:text-gray-200"> {readouts.rangeAbs.toFixed(2)} ({readouts.rangePct.toFixed(2)}%)</span>
            </span>
          </div>
          <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5">
            <span className="text-gray-500 dark:text-gray-400">MA(5,10,20,30)</span>
            <span style={{ color: MA_COLORS[5] }}>MA5: {fmtNum(readouts.ma5)}</span>
            <span style={{ color: MA_COLORS[10] }}>MA10: {fmtNum(readouts.ma10)}</span>
            <span style={{ color: MA_COLORS[20] }}>MA20: {fmtNum(readouts.ma20)}</span>
            <span style={{ color: MA_COLORS[30] }}>MA30: {fmtNum(readouts.ma30)}</span>
          </div>
        </div>

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
      <div ref={volumeContainerRef} className="w-full h-14 lg:h-20 pb-1.5"></div>

      {/* RSI Sub-panel */}
      <div className="px-3 pt-1.5 text-[10px] text-gray-500 dark:text-gray-400 font-mono border-t border-gray-200 dark:border-gray-800">
        RSI (14) <span className="text-purple-400 font-bold">{readouts.rsi.toFixed(2)}</span>
      </div>
      <div ref={rsiContainerRef} className="w-full h-14 lg:h-16 pb-1.5"></div>
    </div>
  );
}
