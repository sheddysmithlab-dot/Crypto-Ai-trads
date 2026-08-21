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
        className="inline-flex h-4 w-4 items-center justify-center rounded-full border border-sky-400/70 text-[9px] font-bold leading-none text-sky-300 hover:bg-sky-500/20 hover:text-sky-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400/60"
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
