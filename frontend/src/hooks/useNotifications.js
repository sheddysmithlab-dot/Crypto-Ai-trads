import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { backendWsUrl } from '../config/api';

export function timeAgo(unixSeconds) {
  const seconds = Math.floor(Date.now() / 1000 - unixSeconds);
  if (seconds < 5) return 'just now';
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ago`;
}

export const NOTIF_ICON_CLASS = {
  success: 'fas fa-check-circle text-green-500',
  warning: 'fas fa-exclamation-triangle text-yellow-500',
  error: 'fas fa-times-circle text-red-500',
  info: 'fas fa-info-circle text-blue-400',
};

// Live bell notification feed (real backend events, not dummy text).
export function useNotifications() {
  const [notifications, setNotifications] = useState([]);
  const [lastSeenId, setLastSeenId] = useState(0);
  const reconnectTimer = useRef(null);

  useEffect(() => {
    function connect() {
      const ws = new WebSocket(backendWsUrl('/ws/notifications'));
      ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        setNotifications(data.notifications || []);
      };
      ws.onclose = () => {
        reconnectTimer.current = setTimeout(connect, 2000);
      };
      return ws;
    }
    const ws = connect();
    return () => {
      clearTimeout(reconnectTimer.current);
      ws.onclose = null;
      ws.close();
    };
  }, []);

  const unreadCount = useMemo(
    () => notifications.filter((n) => n.id > lastSeenId).length,
    [notifications, lastSeenId]
  );

  const markAllRead = useCallback(() => {
    if (notifications.length) setLastSeenId(notifications[notifications.length - 1].id);
  }, [notifications]);

  return { notifications, unreadCount, markAllRead };
}
