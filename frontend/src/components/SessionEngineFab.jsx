/**
 * Session Engine control with a white neon star that rides the pill border.
 * Lives in the bottom-left corner of the control bar.
 */
export default function SessionEngineFab({ enabled = false, onClick, compact = false }) {
  return (
    <div className="session-engine-fab relative inline-flex">
      <div className="session-engine-fab__shell relative inline-flex">
        <svg
          className="session-engine-fab__ring"
          viewBox="0 0 100 40"
          preserveAspectRatio="none"
          aria-hidden="true"
        >
          <defs>
            <filter id="sessionStarNeon" x="-50%" y="-50%" width="200%" height="200%">
              <feGaussianBlur stdDeviation="1.2" result="blur" />
              <feMerge>
                <feMergeNode in="blur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
          </defs>
          <path
            id="sessionEngineBorderPath"
            d="M 20,2 H 80 A 18,18 0 0 1 80,38 H 20 A 18,18 0 0 1 20,2 Z"
            fill="none"
            stroke="rgba(255,255,255,0.12)"
            strokeWidth="0.6"
          />
          <g filter="url(#sessionStarNeon)">
            <text
              fill="#ffffff"
              fontSize="7"
              fontFamily="system-ui,Segoe UI,sans-serif"
              textAnchor="middle"
              dominantBaseline="central"
              style={{
                paintOrder: 'stroke',
                stroke: 'rgba(255,255,255,0.85)',
                strokeWidth: 0.35,
              }}
            >
              ★
              <animateMotion dur="2.6s" repeatCount="indefinite" rotate="0">
                <mpath href="#sessionEngineBorderPath" />
              </animateMotion>
            </text>
          </g>
        </svg>

        <button
          id="session-momentum-badge"
          type="button"
          onClick={onClick}
          className={`session-engine-fab__btn relative flex items-center rounded-full font-bold border-2 transition hover:opacity-90 active:scale-[0.98] ${
            compact
              ? 'gap-1.5 px-2.5 py-1 text-[9px] sm:text-[10px]'
              : 'gap-2 px-4 py-2 sm:px-5 sm:py-2.5 text-[10px] sm:text-xs'
          } ${
            enabled
              ? 'bg-cyan-100 dark:bg-cyan-900/40 border-cyan-300 dark:border-cyan-500 text-cyan-800 dark:text-cyan-200'
              : 'bg-gray-100 dark:bg-gray-900/80 border-gray-300 dark:border-gray-500 text-gray-700 dark:text-gray-200'
          }`}
          title="Session Momentum Engine — high-momentum market windows"
        >
          <i className={`fas fa-clock ${compact ? 'text-[10px]' : 'text-sm'}`} />
          <span className="tracking-wide whitespace-nowrap">
            {enabled ? 'SESSION ENGINE ON' : 'SESSION ENGINE'}
          </span>
        </button>
      </div>
    </div>
  );
}
