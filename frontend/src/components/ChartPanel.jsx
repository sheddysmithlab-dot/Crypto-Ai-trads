import { useState } from 'react';
import PairSelectorDropdown from './PairSelectorDropdown';
import { fmtNum } from '../data/pairs';
import { formatTradeFireTime } from '../utils/time';
import { TIMEFRAME_PROFILES, getTimeframeProfile } from '../data/timeframeProfiles';

const TIMEFRAMES = ['1M', '5M', '15M', '1H', '1D'];

export default function ChartPanel({
  pairSelector,
  chartContainerRef,
  volumeContainerRef,
  timeframe,
  switchTimeframe,
  readouts,
  botIsActive,
}) {
  const [hoverTf, setHoverTf] = useState(null);
  const activeProfile = getTimeframeProfile(timeframe);
  const displayTf = hoverTf || timeframe;
  const displayProfile = getTimeframeProfile(displayTf);

  return (
    <div className="bg-lightCard dark:bg-darkCard rounded-xl shadow border border-gray-200 dark:border-gray-800 overflow-hidden shrink-0">
      {/* Chart Header */}
      <div className="flex justify-between items-center px-3 py-2 border-b border-gray-200 dark:border-gray-800 gap-2">
        <PairSelectorDropdown
          pairs={pairSelector.pairs}
          activePair={pairSelector.activePair}
          activePairLabel={pairSelector.activePairLabel}
          selectPair={pairSelector.selectPair}
          toggleStar={pairSelector.toggleStar}
        />
        <div className="flex flex-wrap justify-end items-center gap-2 text-xs font-semibold">
          {readouts.tradeFireTooltip ? (
            <div
              className={`flex flex-col gap-0.5 px-2 py-1 rounded border text-[10px] font-bold uppercase tracking-wide ${
                readouts.tradeFireTooltip.side === 'SHORT'
                  ? 'border-fuchsia-500/50 bg-fuchsia-500/10 text-fuchsia-200'
                  : 'border-lime-500/50 bg-lime-500/10 text-lime-200'
              }`}
              title="Pattern-detected trade fire candle"
            >
              <span className="flex items-center gap-1">
                <span className="inline-block w-1.5 h-1.5 rounded-full bg-current animate-pulse" />
                {readouts.tradeFireTooltip.pattern}
              </span>
              <span className="text-[9px] font-mono font-normal normal-case tracking-normal text-gray-400">
                {formatTradeFireTime(
                  readouts.tradeFireTooltip.opened_at || readouts.tradeFireTooltip.signal_candle_time,
                )}
              </span>
            </div>
          ) : null}
          {botIsActive && readouts.blueBoxStatus ? (
            <div
              className="flex items-center gap-1.5 px-2 py-1 rounded border border-cyan-500/40 bg-cyan-500/10 text-[10px] font-bold uppercase tracking-wide text-cyan-300"
              title="AI candle brain: detect → Bible → ML cost-aware → fire"
            >
              <span className="inline-block w-1.5 h-1.5 rounded-full bg-cyan-400 animate-pulse" />
              {readouts.blueBoxStatus}
            </div>
          ) : null}
          <div className="flex items-center gap-1.5 px-2 py-1 rounded bg-gray-100 dark:bg-gray-800/80 text-[10px] font-mono text-gray-600 dark:text-gray-300 tabular-nums">
            <span className="inline-block w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" title="Live clock" />
            <span title="Live local time">{readouts.liveClock}</span>
            {readouts.chartCandleTime && readouts.chartCandleTime !== '—' ? (
              <span className="text-gray-400" title="Current candle time">
                · {readouts.chartCandleTime}
              </span>
            ) : null}
          </div>
          {TIMEFRAMES.map((tf) => {
            const p = TIMEFRAME_PROFILES[tf];
            return (
              <button
                key={tf}
                type="button"
                className={`px-2.5 py-1 rounded ${
                  tf === timeframe
                    ? 'bg-blue-600 text-white'
                    : 'text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800'
                }`}
                onClick={() => switchTimeframe(tf)}
                onMouseEnter={() => setHoverTf(tf)}
                onMouseLeave={() => setHoverTf(null)}
                title={`${tf}: Win ${p.winRate}% / Lose ${p.loseRate}% · Trade ${p.capitalPct}% of capital`}
              >
                {tf}
              </button>
            );
          })}
        </div>
      </div>

      {/* TF profile strip — updates on hover/select */}
      <div className="px-3 py-1.5 border-b border-gray-200 dark:border-gray-800 flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] font-semibold uppercase tracking-wide">
        <span className="text-gray-500 dark:text-gray-400 normal-case tracking-normal">
          {displayTf} profile
          {hoverTf && hoverTf !== timeframe ? (
            <span className="text-blue-400 ml-1">(hover)</span>
          ) : null}
        </span>
        <span className="text-green-500">
          Win <span className="font-black">{displayProfile.winRate}%</span>
        </span>
        <span className="text-red-500">
          Lose <span className="font-black">{displayProfile.loseRate}%</span>
        </span>
        <span className="text-amber-500">
          Trade value <span className="font-black">{displayProfile.capitalPct}%</span>
          <span className="text-gray-500 dark:text-gray-400 font-normal normal-case tracking-normal ml-1">
            of capital
          </span>
        </span>
        <span className="text-gray-500 dark:text-gray-400 font-normal normal-case tracking-normal ml-auto">
          Active {timeframe}: {activeProfile.capitalPct}% size · W{activeProfile.winRate}/L{activeProfile.loseRate}
        </span>
      </div>

      {/* Candlestick Chart - default zoom shows the last 40 candles */}
      <div ref={chartContainerRef} className="w-full h-80 lg:h-[28rem] relative">
        {botIsActive ? (
          <div className="absolute top-2 right-2 z-20 pointer-events-none flex flex-col items-end gap-1">
            <div className="px-2 py-1 rounded-md bg-cyan-950/80 border border-cyan-500/50 text-[9px] font-black uppercase tracking-widest text-cyan-200 shadow-lg">
              Candle Brain
            </div>
            <div className="text-[8px] text-cyan-300/80 font-mono text-right leading-tight">
              detect → Bible → ML gate
              <br />
              <span className="text-yellow-400">—</span> EMA50 · <span className="text-purple-400">—</span> EMA200
              <br />
              <span className="text-lime-400">⚡</span> neon trade fire
            </div>
          </div>
        ) : null}
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
      <div ref={volumeContainerRef} className="w-full h-32 lg:h-44 pb-1.5"></div>
    </div>
  );
}
