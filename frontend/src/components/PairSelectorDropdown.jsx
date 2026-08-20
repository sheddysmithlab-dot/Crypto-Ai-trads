import { useMemo, useRef, useState } from 'react';
import { useClickOutside } from '../hooks/useClickOutside';

export default function PairSelectorDropdown({
  pairs,
  activePair,
  activePairLabel,
  selectPair,
  toggleStar,
  botIsActive = false,
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const wrapRef = useRef(null);

  useClickOutside(wrapRef, () => {
    setOpen(false);
    setQuery('');
  }, open);

  const filteredPairs = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return pairs;
    return pairs.filter((p) => p.symbol.toLowerCase().includes(q));
  }, [pairs, query]);

  function handleSelect(symbol) {
    if (botIsActive) return;
    selectPair(symbol);
    setOpen(false);
    setQuery('');
  }

  return (
    <div className="relative" ref={wrapRef}>
      <button
        className={`flex items-center gap-2 ${botIsActive ? 'opacity-60 cursor-not-allowed' : ''}`}
        onClick={() => {
          if (botIsActive) return;
          setOpen((o) => !o);
        }}
        disabled={botIsActive}
      >
        <div
          className="w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold text-white"
          style={{ background: activePair.color }}
        >
          {activePair.icon}
        </div>
        <span className="font-bold">{activePairLabel}</span>
        <i className="fas fa-chevron-down text-[10px] text-gray-400"></i>
      </button>

      {open && (
        <div className="absolute left-0 top-full mt-2 w-80 bg-lightCard dark:bg-darkCard border border-gray-200 dark:border-gray-800 rounded-xl shadow-2xl z-50 overflow-hidden backdrop-blur-sm">
          <div className="flex items-center gap-3 px-4 py-4 border-b border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-900/50">
            <i className="fas fa-chart-line text-blue-500 text-sm"></i>
            <div
              className="w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold text-white shrink-0"
              style={{ background: activePair.color }}
            >
              {activePair.icon}
            </div>
            <div className="flex-1 min-w-0">
              <div className="font-bold text-sm text-gray-900 dark:text-white leading-tight">{activePairLabel}</div>
              <div className="text-xs text-gray-500 dark:text-gray-400 leading-tight">{pairs.length} Bybit linear pairs</div>
            </div>
          </div>

          <div className="px-3 pt-3 pb-2">
            <div className="relative">
              <i className="fas fa-search absolute left-3 top-1/2 -translate-y-1/2 text-xs text-gray-400"></i>
              <input
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search SOL, DOGE, PEPE…"
                className="w-full pl-8 pr-3 py-2 text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 placeholder:text-gray-400 outline-none focus:border-blue-500"
                autoFocus
              />
            </div>
          </div>

          <div className="px-4 pt-1 pb-2 text-xs font-bold text-gray-500 dark:text-gray-400 uppercase tracking-widest flex items-center gap-2">
            <i className="fas fa-list text-xs"></i> Select Pair
          </div>

          <div className="max-h-80 overflow-y-auto pb-2 scroll-smooth">
            {filteredPairs.length === 0 && (
              <div className="px-4 py-6 text-center text-sm text-gray-500">No pair matches “{query}”</div>
            )}
            {filteredPairs.map((pair) => {
              const isActive = pair.symbol === activePair.symbol;
              return (
                <button
                  key={pair.symbol}
                  className={`w-full flex items-center gap-3 px-4 py-2.5 text-left transition-all ${
                    isActive
                      ? 'bg-blue-500/15 border-l-4 border-blue-500 dark:bg-blue-500/10'
                      : 'hover:bg-gray-100/5 dark:hover:bg-gray-800/30 border-l-4 border-transparent'
                  }`}
                  onClick={() => handleSelect(pair.symbol)}
                  disabled={botIsActive}
                >
                  <i
                    className={`text-xs cursor-pointer hover:scale-125 transition-transform ${
                      pair.starred ? 'fas text-yellow-400' : 'far text-gray-400 dark:text-gray-600'
                    }`}
                    onClick={(e) => {
                      e.stopPropagation();
                      toggleStar(pair.symbol);
                    }}
                  ></i>
                  <span
                    className="w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold text-white shrink-0 shadow-md"
                    style={{ background: pair.color }}
                  >
                    {pair.icon}
                  </span>
                  <div className="flex-1">
                    <span className={`text-sm font-bold ${isActive ? 'text-blue-600 dark:text-blue-400' : 'text-gray-900 dark:text-gray-200'}`}>
                      {pair.symbol}
                    </span>
                    <span className="text-gray-500 dark:text-gray-400 font-normal text-xs">/USDT</span>
                  </div>
                  {isActive && <i className="fas fa-check text-blue-500 text-sm ml-auto"></i>}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
