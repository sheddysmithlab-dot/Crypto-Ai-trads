import { useCallback, useEffect, useRef, useState } from 'react';
import { TRADING_PAIRS } from '../data/pairs';
import LauncherBybitChart from './LauncherBybitChart';

/** Cap = all mapped trading pairs (not a fixed 5). */
export const MAX_LAUNCHER_SLOTS = TRADING_PAIRS.length;

const POPUP_POS_KEY = 'ai_trading_bot_launcher_popup_pos';
const POPUP_W = 672; // ~42rem max width
const POPUP_H_EST = 480;

function formatTfLabel(tf) {
  if (!tf) return '1m';
  return tf === '1M' ? '1m' : String(tf).toLowerCase();
}

function readSavedPos() {
  try {
    const raw = localStorage.getItem(POPUP_POS_KEY);
    if (!raw) return null;
    const p = JSON.parse(raw);
    if (Number.isFinite(p?.x) && Number.isFinite(p?.y)) return { x: p.x, y: p.y };
  } catch {
    /* ignore */
  }
  return null;
}

function clampPos(x, y) {
  const maxX = Math.max(8, window.innerWidth - Math.min(POPUP_W, window.innerWidth - 16) - 8);
  const maxY = Math.max(8, window.innerHeight - POPUP_H_EST - 8);
  return {
    x: Math.min(Math.max(8, x), maxX),
    y: Math.min(Math.max(8, y), maxY),
  };
}

function defaultPos() {
  const x = window.innerWidth < 640 ? 12 : 24;
  const y = window.innerWidth < 640 ? 80 : 96;
  return clampPos(x, y);
}

/**
 * Trade launcher: minimized coin chips sit LEFT of "+" (all pairs allowed).
 * Popup is draggable; coin pick is popup-only (main chart unchanged).
 */
export default function TradeLauncherPopup({
  slots = [],
  editorOpen,
  editingId,
  onOpenNew,
  onCloseEditor,
  onMinimizeToSlot,
  onRestoreSlot,
  onRemoveSlot,
  pairs = TRADING_PAIRS,
  activeSymbol,
  timeframe,
  botIsActive,
}) {
  const editing = editingId ? slots.find((s) => s.id === editingId) : null;
  const [symbol, setSymbol] = useState(editing?.symbol || 'BTC');
  const [coinOpen, setCoinOpen] = useState(false);
  const [pos, setPos] = useState(() => readSavedPos() || defaultPos());
  const [dragging, setDragging] = useState(false);
  const coinRef = useRef(null);
  const popupRef = useRef(null);
  const dragRef = useRef(null);

  const tf = timeframe || '1M';
  const locked = Boolean(botIsActive);
  const showEditor = editorOpen && !locked;

  const dockedSymbols = slots.map((s) => s.symbol);

  const isCoinDisabled = useCallback(
    (sym) => {
      if (sym === activeSymbol) return true;
      if (dockedSymbols.includes(sym)) {
        if (editing?.symbol === sym) return false;
        return true;
      }
      return false;
    },
    [activeSymbol, dockedSymbols, editing],
  );

  const pickDefaultSymbol = useCallback(() => {
    const candidate = pairs.find((p) => !isCoinDisabled(p.symbol));
    return candidate?.symbol || pairs[0]?.symbol || 'BTC';
  }, [pairs, isCoinDisabled]);

  // Auto-close popup when AI automation starts.
  useEffect(() => {
    if (locked && editorOpen) onCloseEditor?.();
  }, [locked, editorOpen, onCloseEditor]);

  useEffect(() => {
    if (!showEditor) return;
    if (editingId && editing) {
      setSymbol(editing.symbol);
    } else {
      setSymbol(pickDefaultSymbol());
    }
    setCoinOpen(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showEditor, editingId]);

  useEffect(() => {
    if (!dragging) return undefined;

    function onMove(e) {
      const d = dragRef.current;
      if (!d) return;
      const next = clampPos(d.originX + (e.clientX - d.startX), d.originY + (e.clientY - d.startY));
      setPos(next);
    }

    function onUp() {
      setDragging(false);
      setPos((current) => {
        try {
          localStorage.setItem(POPUP_POS_KEY, JSON.stringify(current));
        } catch {
          /* ignore */
        }
        return current;
      });
    }

    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
    return () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
    };
  }, [dragging]);

  useEffect(() => {
    if (!showEditor) return undefined;
    function onDoc(e) {
      if (coinRef.current && !coinRef.current.contains(e.target)) setCoinOpen(false);
    }
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [showEditor]);

  useEffect(() => {
    if (!showEditor) return undefined;
    function onResize() {
      setPos((prev) => clampPos(prev.x, prev.y));
    }
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [showEditor]);

  function startDrag(e) {
    if (e.button !== 0) return;
    if (e.target.closest('button')) return;
    e.preventDefault();
    dragRef.current = {
      startX: e.clientX,
      startY: e.clientY,
      originX: pos.x,
      originY: pos.y,
    };
    setDragging(true);
  }

  function handleMinimizeOnly() {
    if (locked) return;
    onMinimizeToSlot?.({ id: editingId, symbol, timeframe: tf });
  }

  const activePair = pairs.find((p) => p.symbol === symbol) || pairs[0];
  const atCap = slots.length >= MAX_LAUNCHER_SLOTS && !editingId;
  const plusDisabled = locked || (atCap && !editorOpen);

  return (
    <div
      className={`flex flex-wrap items-center gap-1.5 shrink-0 max-w-[min(100%,52rem)] ${locked ? 'opacity-45' : ''}`}
      title={locked ? 'Stop AI automation to change coins' : undefined}
    >
      {slots.map((slot) => {
        const meta = pairs.find((p) => p.symbol === slot.symbol) || {
          symbol: slot.symbol,
          icon: slot.symbol.charAt(0),
          color: '#10b981',
        };
        const isActiveEdit = showEditor && editingId === slot.id;
        return (
          <div key={slot.id} className="relative group flex items-center">
            <button
              type="button"
              disabled={locked}
              onClick={() => {
                if (locked) return;
                onRestoreSlot?.(slot.id);
              }}
              title={
                locked
                  ? 'Stop AI automation to edit coins'
                  : `Restore ${slot.symbol} ${slot.timeframe}`
              }
              className={`flex items-center gap-1.5 pl-1.5 pr-2 py-1 rounded-md border text-[11px] font-bold shadow-lg transition-colors ${
                locked
                  ? 'border-emerald-900/60 bg-black/70 text-emerald-800 cursor-not-allowed'
                  : isActiveEdit
                    ? 'border-emerald-300 bg-emerald-500/20 text-emerald-100'
                    : 'border-emerald-500/70 bg-black/90 text-emerald-300 hover:bg-emerald-500/10'
              }`}
            >
              <span
                className="w-4 h-4 rounded-full flex items-center justify-center text-[8px] text-white shrink-0"
                style={{ background: meta.color, opacity: locked ? 0.5 : 1 }}
              >
                {meta.icon}
              </span>
              <span>{slot.symbol}</span>
              <span className="text-emerald-600">·</span>
              <span>{timeframe || slot.timeframe || '1M'}</span>
              {botIsActive ? (
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
              ) : null}
            </button>
            {!locked ? (
              <button
                type="button"
                title="Remove"
                onClick={(e) => {
                  e.stopPropagation();
                  onRemoveSlot?.(slot.id);
                }}
                className="absolute -top-1.5 -right-1.5 w-4 h-4 rounded-full bg-black border border-emerald-500/60 text-emerald-400 text-[8px] leading-none opacity-0 group-hover:opacity-100 hover:text-red-400 hover:border-red-400"
              >
                ×
              </button>
            ) : null}
          </div>
        );
      })}

      <button
        type="button"
        title={
          locked
            ? 'Stop AI automation to add coins'
            : atCap
              ? `Max ${MAX_LAUNCHER_SLOTS} coins`
              : 'Add coin launcher'
        }
        disabled={plusDisabled}
        onClick={() => {
          if (locked) return;
          if (editorOpen && !editingId) onCloseEditor?.();
          else if (atCap) return;
          else onOpenNew?.();
        }}
        className={`w-8 h-8 flex items-center justify-center rounded border-2 border-dashed transition-colors shrink-0 ${
          plusDisabled
            ? 'border-emerald-900 text-emerald-900 cursor-not-allowed'
            : 'border-emerald-500 text-emerald-400 hover:bg-emerald-500/15 hover:text-emerald-300'
        }`}
      >
        <i className="fas fa-plus text-sm" />
      </button>

      {showEditor ? (
        <div
          ref={popupRef}
          className="fixed z-[80] w-[min(100vw-1.25rem,42rem)] max-h-[min(90vh,640px)] flex flex-col rounded-xl border border-emerald-500/80 bg-[#0b0f0c] shadow-[0_0_0_1px_rgba(16,185,129,0.25),0_24px_60px_rgba(0,0,0,0.7)] overflow-hidden"
          style={{ left: pos.x, top: pos.y }}
          role="dialog"
          aria-label="Trade launcher"
        >
          <div
            className="flex items-center justify-between gap-2 px-3 py-1.5 border-b border-emerald-500/30 bg-black/50 shrink-0 cursor-grab active:cursor-grabbing select-none"
            onMouseDown={startDrag}
            title="Drag to move"
          >
            <div className="flex items-center gap-2 min-w-0 pointer-events-none">
              <i className="fas fa-grip-vertical text-emerald-600/70 text-[10px] shrink-0" aria-hidden />
              <span
                className="w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold text-white shrink-0"
                style={{ background: activePair?.color || '#10b981' }}
              >
                {activePair?.icon || symbol?.charAt(0)}
              </span>
              <div className="min-w-0">
                <div className="text-sm font-bold text-emerald-100 truncate">
                  {symbol}/USDT <span className="text-emerald-600 font-normal">·</span> {tf}
                </div>
                <div className="text-[10px] text-emerald-500/80 font-mono truncate">
                  Preview only · main chart unchanged · {slots.length}/{MAX_LAUNCHER_SLOTS} docked
                </div>
              </div>
            </div>
            <div className="flex items-center gap-1 shrink-0">
              <button
                type="button"
                title="Minimize to chip"
                onClick={handleMinimizeOnly}
                className="w-7 h-7 flex items-center justify-center text-emerald-400/80 hover:text-emerald-300 hover:bg-emerald-500/10 rounded cursor-pointer"
              >
                <i className="fas fa-minus text-[10px]" />
              </button>
              <button
                type="button"
                title="Close"
                onClick={onCloseEditor}
                className="w-7 h-7 flex items-center justify-center text-emerald-400/80 hover:text-red-400 hover:bg-red-500/10 rounded cursor-pointer"
              >
                <i className="fas fa-times text-[11px]" />
              </button>
            </div>
          </div>

          <div className="flex items-stretch gap-2 p-3 shrink-0">
            <div className="relative flex-1 min-w-0" ref={coinRef}>
              <button
                type="button"
                onClick={() => setCoinOpen((v) => !v)}
                className="w-full h-10 px-2.5 flex items-center justify-between gap-1 rounded border border-emerald-500/50 bg-black/60 text-sm font-bold text-emerald-100 hover:border-emerald-400"
              >
                <span className="flex items-center gap-2 truncate">
                  <span
                    className="w-5 h-5 rounded-full flex items-center justify-center text-[9px] text-white shrink-0"
                    style={{ background: activePair?.color || '#10b981' }}
                  >
                    {activePair?.icon || symbol?.charAt(0)}
                  </span>
                  {symbol}
                </span>
                <i className="fas fa-chevron-down text-[9px] text-emerald-500 shrink-0" />
              </button>
              {coinOpen ? (
                <div className="absolute left-0 top-full mt-1 w-full max-h-56 overflow-y-auto rounded border border-emerald-500/60 bg-[#0d1210] shadow-2xl z-50">
                  {pairs.map((p) => {
                    const disabled = isCoinDisabled(p.symbol);
                    const isMain = p.symbol === activeSymbol;
                    const isDocked = dockedSymbols.includes(p.symbol) && editing?.symbol !== p.symbol;
                    let hint = '';
                    if (isMain) hint = 'Main chart';
                    else if (isDocked) hint = 'Already docked';
                    return (
                      <button
                        key={p.symbol}
                        type="button"
                        disabled={disabled}
                        title={disabled ? `${p.symbol} unavailable (${hint})` : `${p.symbol}/USDT`}
                        onClick={() => {
                          if (disabled) return;
                          setSymbol(p.symbol);
                          setCoinOpen(false);
                        }}
                        className={`w-full flex items-center gap-2 px-2.5 py-2 text-left text-sm font-semibold ${
                          disabled
                            ? 'opacity-40 cursor-not-allowed text-gray-500'
                            : p.symbol === symbol
                              ? 'bg-emerald-500/20 text-emerald-300'
                              : 'text-gray-200 hover:bg-emerald-500/10'
                        }`}
                      >
                        <span
                          className="w-5 h-5 rounded-full flex items-center justify-center text-[9px] text-white shrink-0"
                          style={{ background: p.color, opacity: disabled ? 0.45 : 1 }}
                        >
                          {p.icon}
                        </span>
                        {p.symbol}
                        <span className="text-[10px] text-gray-500 ml-auto">
                          {disabled ? hint : '/USDT'}
                        </span>
                      </button>
                    );
                  })}
                </div>
              ) : null}
            </div>

            <div
              className="w-[5rem] shrink-0 h-10 px-2 flex items-center justify-center rounded border border-emerald-500/30 bg-black/40 text-sm font-bold text-emerald-300/90 tabular-nums"
              title="Same as main chart timeframe"
            >
              {formatTfLabel(tf)}
            </div>
          </div>

          <LauncherBybitChart symbol={symbol} timeframe={tf} />
        </div>
      ) : null}
    </div>
  );
}
