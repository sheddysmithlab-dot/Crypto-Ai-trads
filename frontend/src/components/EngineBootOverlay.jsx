import { useEffect, useMemo, useState } from 'react';

const INTRO_SEC = 10;
const ANALYSIS_SEC = 30;
const TOTAL_SEC = INTRO_SEC + ANALYSIS_SEC;

/**
 * Full-screen boot sequence after AI Engine START:
 * 0–10s  translucent engine diagram
 * 10–40s neon round analysis countdown
 * then hide — trading may begin (backend also enforces 40s)
 */
export default function EngineBootOverlay({
  active,
  warmupRemainingSec = 0,
  warmupTotalSec = TOTAL_SEC,
  introSec = INTRO_SEC,
  analysisSec = ANALYSIS_SEC,
}) {
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!active) return undefined;
    const id = setInterval(() => setTick((n) => n + 1), 200);
    return () => clearInterval(id);
  }, [active]);

  const remaining = Math.max(0, Number(warmupRemainingSec) || 0);
  const total = Math.max(1, Number(warmupTotalSec) || TOTAL_SEC);
  const intro = Math.max(1, Number(introSec) || INTRO_SEC);
  const analysis = Math.max(1, Number(analysisSec) || ANALYSIS_SEC);
  const elapsed = Math.max(0, total - remaining);
  const inIntro = remaining > analysis;
  const analysisLeft = Math.min(analysis, Math.max(0, remaining));
  const analysisProgress = 1 - analysisLeft / analysis;

  const show = active && remaining > 0.05;
  // tick forces re-render while parent WS updates remaining
  void tick;

  const ring = useMemo(() => {
    const size = 220;
    const stroke = 10;
    const r = (size - stroke) / 2;
    const c = 2 * Math.PI * r;
    const offset = c * (1 - analysisProgress);
    return { size, stroke, r, c, offset };
  }, [analysisProgress]);

  if (!show) return null;

  return (
    <div
      className="fixed inset-0 z-[90] flex items-center justify-center pointer-events-none"
      aria-live="polite"
      aria-label="AI Engine warmup"
    >
      <div className="absolute inset-0 bg-black/55" />

      {inIntro ? (
        <div className="relative z-[91] w-[min(92vw,920px)] max-h-[82vh] rounded-xl overflow-hidden border border-cyan-400/40 shadow-[0_0_40px_rgba(59,158,255,0.35)]">
          <img
            src="/engine-boot.jpg"
            alt="AI Engine initializing"
            className="w-full h-auto object-contain opacity-55"
            draggable={false}
          />
          <div className="absolute inset-0 bg-gradient-to-t from-black/70 via-transparent to-black/30" />
          <div className="absolute bottom-0 left-0 right-0 p-4 sm:p-6 text-center">
            <div className="text-cyan-300 text-xs sm:text-sm font-black tracking-[0.25em] uppercase mb-2">
              Initializing core…
            </div>
            <div className="mx-auto h-1.5 w-48 sm:w-64 rounded-full bg-white/15 overflow-hidden">
              <div
                className="h-full rounded-full bg-cyan-400 shadow-[0_0_12px_#22d3ee]"
                style={{ width: `${Math.min(100, (elapsed / intro) * 100)}%` }}
              />
            </div>
            <div className="mt-2 text-gray-300 text-[11px] font-mono">
              Intro {Math.ceil(Math.max(0, remaining - analysis))}s · then analysis
            </div>
          </div>
        </div>
      ) : (
        <div className="relative z-[91] flex flex-col items-center gap-4 px-4">
          <div className="text-orange-300 text-xs sm:text-sm font-black tracking-[0.2em] uppercase drop-shadow-[0_0_8px_rgba(255,138,31,0.8)]">
            Analysis countdown
          </div>
          <div className="relative" style={{ width: ring.size, height: ring.size }}>
            <svg width={ring.size} height={ring.size} className="-rotate-90">
              <circle
                cx={ring.size / 2}
                cy={ring.size / 2}
                r={ring.r}
                fill="none"
                stroke="rgba(255,255,255,0.12)"
                strokeWidth={ring.stroke}
              />
              <circle
                cx={ring.size / 2}
                cy={ring.size / 2}
                r={ring.r}
                fill="none"
                stroke="#3b9eff"
                strokeWidth={ring.stroke}
                strokeLinecap="round"
                strokeDasharray={ring.c}
                strokeDashoffset={ring.offset}
                style={{
                  filter: 'drop-shadow(0 0 10px #3b9eff) drop-shadow(0 0 18px rgba(255,138,31,0.55))',
                  transition: 'stroke-dashoffset 0.2s linear',
                }}
              />
            </svg>
            <div className="absolute inset-0 flex flex-col items-center justify-center">
              <div className="text-5xl sm:text-6xl font-black tabular-nums text-white drop-shadow-[0_0_14px_rgba(59,158,255,0.9)]">
                {Math.ceil(analysisLeft)}
              </div>
              <div className="text-[10px] sm:text-xs font-bold tracking-widest text-orange-300/90 uppercase mt-1">
                seconds
              </div>
            </div>
          </div>
          <div className="text-center text-gray-300 text-[11px] sm:text-xs max-w-sm leading-relaxed">
            Brain + order-flow warming up. New trades unlock when this hits zero.
          </div>
        </div>
      )}
    </div>
  );
}
