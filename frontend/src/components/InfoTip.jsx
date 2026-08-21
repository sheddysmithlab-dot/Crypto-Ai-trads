import { useEffect, useId, useRef, useState } from 'react';

/**
 * Compact ring "i" info control. Click opens a short toast-style tip so users
 * understand a field without developer/setup walls of text.
 */
export default function InfoTip({ text, label = 'More info', className = '' }) {
  const [open, setOpen] = useState(false);
  const tipId = useId();
  const rootRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    function onDoc(e) {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false);
    }
    function onKey(e) {
      if (e.key === 'Escape') setOpen(false);
    }
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    const timer = window.setTimeout(() => setOpen(false), 6000);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
      window.clearTimeout(timer);
    };
  }, [open]);

  if (!text) return null;

  return (
    <span ref={rootRef} className={`relative inline-flex items-center align-middle ${className}`}>
      <button
        type="button"
        className="inline-flex h-3.5 w-3.5 items-center justify-center rounded-full border border-gray-500/25 text-[8px] font-semibold leading-none text-gray-400/35 opacity-40 hover:opacity-75 hover:border-gray-400/40 hover:text-gray-300/70 hover:bg-transparent focus:outline-none focus-visible:opacity-80 focus-visible:ring-1 focus-visible:ring-gray-500/30 transition-opacity"
        aria-label={label}
        aria-expanded={open}
        aria-controls={tipId}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
      >
        i
      </button>
      {open ? (
        <span
          id={tipId}
          role="status"
          className="absolute left-1/2 top-full z-[130] mt-2 w-56 -translate-x-1/2 rounded-lg border border-sky-500/40 bg-[#0f172a] px-3 py-2 text-[11px] font-normal normal-case tracking-normal text-sky-50 shadow-xl shadow-black/50 leading-snug sm:w-64"
        >
          {text}
        </span>
      ) : null}
    </span>
  );
}
