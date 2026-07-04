import { useEffect, useRef, useState } from 'react';

export function formatUptime(seconds) {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = seconds % 60;
  return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

// Session uptime counter - only runs while the bot is actively trading.
export function useUptime(isActive) {
  const [seconds, setSeconds] = useState(0);
  const startTimeRef = useRef(null);
  const intervalRef = useRef(null);

  useEffect(() => {
    if (isActive) {
      startTimeRef.current = Date.now();
      intervalRef.current = setInterval(() => {
        setSeconds(Math.floor((Date.now() - startTimeRef.current) / 1000));
      }, 1000);
    } else {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
      startTimeRef.current = null;
      setSeconds(0);
    }

    return () => clearInterval(intervalRef.current);
  }, [isActive]);

  return { formatted: formatUptime(seconds), running: isActive };
}
