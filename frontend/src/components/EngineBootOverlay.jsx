import { useEffect, useRef, useState } from 'react';
import bootVideo from '../assets/animation.mp4';

const INTRO_SEC = 10;
const ANALYSIS_SEC = 10;
const TOTAL_SEC = INTRO_SEC + ANALYSIS_SEC;

const RING_SIZE = 220;
const RING_STROKE = 10;
const RING_R = (RING_SIZE - RING_STROKE) / 2;
const RING_C = 2 * Math.PI * RING_R;

/**
 * Full-screen boot sequence after AI Engine START:
 * 0–10s  intro MP4 (autoplay, muted)
 * 10–20s smooth neon countdown ring (continuous, no tick jumps)
 * Blocks all background interaction; Cancel stops engine immediately (no confirm).
 */
export default function EngineBootOverlay({
  active,
  warmupRemainingSec = 0,
  warmupTotalSec = TOTAL_SEC,
  introSec = INTRO_SEC,
  analysisSec = ANALYSIS_SEC,
  onCancel,
  cancelLoading = false,
}) {
  const [videoOk, setVideoOk] = useState(true);
  const [smoothLeft, setSmoothLeft] = useState(0);
  const [smoothProgress, setSmoothProgress] = useState(0);
  const videoRef = useRef(null);
  const countdownEndRef = useRef(0);
  const countdownDurRef = useRef(ANALYSIS_SEC);

  const remainingWs = Math.max(0, Number(warmupRemainingSec) || 0);
  const total = Math.max(1, Number(warmupTotalSec) || TOTAL_SEC);
  const intro = Math.max(1, Number(introSec) || INTRO_SEC);
  const analysis = Math.max(1, Number(analysisSec) || ANALYSIS_SEC);
  const inIntro = remainingWs > analysis + 0.05;
  const show = Boolean(active && remainingWs > 0.05);
  const elapsedIntro = Math.max(0, Math.min(intro, total - remainingWs));

  // Lock body scroll while boot overlay is up
  useEffect(() => {
    if (!show) return undefined;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = prev;
    };
  }, [show]);

  // Start / restart intro video when intro phase is visible
  useEffect(() => {
    if (!show || !inIntro || !videoOk) return undefined;
    const el = videoRef.current;
    if (!el) return undefined;
    el.muted = true;
    el.currentTime = 0;
    const play = el.play();
    if (play && typeof play.catch === 'function') {
      play.catch(() => {
        /* Autoplay blocked — still show first frame */
      });
    }
    return () => {
      try {
        el.pause();
      } catch {
        /* ignore */
      }
    };
  }, [show, inIntro, videoOk]);

  // Smooth continuous countdown (rAF) — no discrete tick jumps
  useEffect(() => {
    if (!show || inIntro) {
      countdownEndRef.current = 0;
      setSmoothLeft(analysis);
      setSmoothProgress(0);
      return undefined;
    }
    const left = Math.min(analysis, Math.max(0, remainingWs));
    countdownDurRef.current = analysis;
    countdownEndRef.current = performance.now() + left * 1000;

    let raf = 0;
    const frame = (now) => {
      const end = countdownEndRef.current;
      const dur = countdownDurRef.current;
      const leftMs = Math.max(0, end - now);
      const leftSec = leftMs / 1000;
      const progress = dur > 0 ? 1 - leftSec / dur : 1;
      setSmoothLeft(leftSec);
      setSmoothProgress(Math.min(1, Math.max(0, progress)));
      if (leftMs > 0) raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
    // Seed once when countdown phase starts (not on every WS tick).
  }, [show, inIntro, analysis]); // remainingWs read only at phase enter


  if (!show) return null;

  const offset = RING_C * (1 - smoothProgress);
  const displaySec = Math.max(0, Math.ceil(smoothLeft));

  return (
    <div
      className="fixed inset-0 z-[200] flex flex-col items-center justify-center pointer-events-auto"
      role="dialog"
      aria-modal="true"
      aria-label="AI Engine warmup"
      onMouseDown={(e) => e.stopPropagation()}
      onClick={(e) => e.stopPropagation()}
      onTouchStart={(e) => e.stopPropagation()}
    >
      <div className="absolute inset-0 bg-black/70 backdrop-blur-[2px]" />

      {inIntro ? (
        <div className="relative z-[201] w-[min(96vw,980px)] max-h-[78vh] rounded-xl overflow-hidden border border-cyan-400/50 shadow-[0_0_48px_rgba(59,158,255,0.45)] bg-black">
          {videoOk ? (
            <video
              ref={videoRef}
              src={bootVideo}
              className="w-full max-h-[78vh] object-contain opacity-90"
              autoPlay
              muted
              playsInline
              loop
              preload="auto"
              controls={false}
              onError={() => setVideoOk(false)}
            />
          ) : (
            <div className="flex items-center justify-center min-h-[280px] text-cyan-300 font-bold tracking-widest uppercase text-sm p-8">
              AI Engine initializing…
            </div>
          )}
          <div className="absolute inset-0 bg-gradient-to-t from-black/75 via-transparent to-black/25 pointer-events-none" />
          <div className="absolute bottom-0 left-0 right-0 p-4 sm:p-5 text-center pointer-events-none">
            <div className="text-cyan-300 text-xs sm:text-sm font-black tracking-[0.25em] uppercase mb-2 drop-shadow-[0_0_8px_rgba(34,211,238,0.9)]">
              Initializing core…
            </div>
            <div className="mx-auto h-1.5 w-48 sm:w-64 rounded-full bg-white/15 overflow-hidden">
              <div
                className="h-full rounded-full bg-cyan-400 shadow-[0_0_12px_#22d3ee] transition-[width] duration-100 linear"
                style={{ width: `${Math.min(100, (elapsedIntro / intro) * 100)}%` }}
              />
            </div>
            <div className="mt-2 text-gray-200 text-[11px] font-mono">
              Intro {Math.ceil(Math.max(0, remainingWs - analysis))}s · then countdown
            </div>
          </div>
        </div>
      ) : (
        <div className="relative z-[201] flex flex-col items-center gap-4 px-4">
          <div className="text-orange-300 text-xs sm:text-sm font-black tracking-[0.2em] uppercase drop-shadow-[0_0_8px_rgba(255,138,31,0.8)]">
            Analysis countdown
          </div>
          {/* Round card — padding + overflow visible so neon glow is not clipped square */}
          <div
            className="relative rounded-full flex items-center justify-center overflow-visible bg-black/45 border border-cyan-400/35 shadow-[0_0_36px_rgba(59,158,255,0.35)]"
            style={{
              width: RING_SIZE + 56,
              height: RING_SIZE + 56,
            }}
          >
            <div
              className="relative overflow-visible"
              style={{ width: RING_SIZE, height: RING_SIZE }}
            >
              <svg
                width={RING_SIZE}
                height={RING_SIZE}
                className="-rotate-90 overflow-visible"
                style={{ overflow: 'visible' }}
              >
                <circle
                  cx={RING_SIZE / 2}
                  cy={RING_SIZE / 2}
                  r={RING_R}
                  fill="none"
                  stroke="rgba(255,255,255,0.12)"
                  strokeWidth={RING_STROKE}
                />
                <circle
                  cx={RING_SIZE / 2}
                  cy={RING_SIZE / 2}
                  r={RING_R}
                  fill="none"
                  stroke="#3b9eff"
                  strokeWidth={RING_STROKE}
                  strokeLinecap="butt"
                  strokeDasharray={RING_C}
                  strokeDashoffset={offset}
                  style={{
                    filter:
                      'drop-shadow(0 0 10px #3b9eff) drop-shadow(0 0 22px rgba(59,158,255,0.85)) drop-shadow(0 0 18px rgba(255,138,31,0.45))',
                  }}
                />
              </svg>
              <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
                <div className="text-5xl sm:text-6xl font-black tabular-nums text-white drop-shadow-[0_0_14px_rgba(59,158,255,0.9)]">
                  {displaySec}
                </div>
                <div className="text-[10px] sm:text-xs font-bold tracking-widest text-orange-300/90 uppercase mt-1">
                  seconds
                </div>
              </div>
            </div>
          </div>
          <div className="text-center text-gray-300 text-[11px] sm:text-xs max-w-sm leading-relaxed">
            Engine scanning now. Pattern detect → next candle END. New trades unlock at zero.
          </div>
        </div>
      )}

      <div className="relative z-[202] mt-5 flex flex-col items-center gap-2">
        <button
          type="button"
          className="pointer-events-auto px-6 py-2.5 rounded-lg text-sm font-black uppercase tracking-wider text-white bg-red-600 hover:bg-red-500 border border-red-400/60 shadow-[0_0_20px_rgba(239,68,68,0.45)] disabled:opacity-60 disabled:cursor-wait transition"
          disabled={cancelLoading}
          onClick={() => onCancel?.()}
        >
          {cancelLoading ? 'Stopping…' : 'Cancel — Stop Engine'}
        </button>
        <span className="text-[10px] text-gray-400 font-medium">
          Stops immediately · no confirmation
        </span>
      </div>
    </div>
  );
}
