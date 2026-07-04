import { useEffect } from 'react';

export function useClickOutside(refs, onOutside, active = true) {
  useEffect(() => {
    if (!active) return;
    function handleClick(e) {
      const list = Array.isArray(refs) ? refs : [refs];
      const inside = list.some((r) => r.current && r.current.contains(e.target));
      if (!inside) onOutside();
    }
    document.addEventListener('click', handleClick);
    return () => document.removeEventListener('click', handleClick);
  }, [refs, onOutside, active]);
}
