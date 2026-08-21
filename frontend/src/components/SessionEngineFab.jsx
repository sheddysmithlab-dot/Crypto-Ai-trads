/**
 * Floating Session Engine control — lower-left, with a white neon star
 * that rides the button border continuously.
 */
export default function SessionEngineFab({ enabled = false, onClick }) {
  return (
    <div className="session-engine-fab fixed z-40 left-3 bottom-28 sm:left-4 sm:bottom-32">
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
          {/* Rounded-rect path matching the pill (rx≈20 on a 100×40 box). */}
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
          className={`session-engine-fab__btn relative flex items-center gap-2 px-4 py-2.5 sm:px-5 sm:py-3 rounded-full text-xs sm:text-sm font-bold border-2 transition hover:opacity-90 active:scale-[0.98] ${
            enabled
              ? 'bg-cyan-100 dark:bg-cyan-900/40 border-cyan-300 dark:border-cyan-500 text-cyan-800 dark:text-cyan-200'
              : 'bg-gray-100 dark:bg-gray-900/80 border-gray-300 dark:border-gray-500 text-gray-700 dark:text-gray-200'
          }`}
          title="Session Momentum Engine — high-momentum market windows"
        >
          <i className="fas fa-clock text-sm sm:text-base" />
          <span className="tracking-wide whitespace-nowrap">
            {enabled ? 'SESSION ENGINE ON' : 'SESSION ENGINE'}
          </span>
        </button>
      </div>
    </div>
  );
}
