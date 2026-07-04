import { useRef, useState } from 'react';
import { useClickOutside } from '../hooks/useClickOutside';
import { NOTIF_ICON_CLASS, timeAgo } from '../hooks/useNotifications';

export default function NotificationsDropdown({ notifications, unreadCount, markAllRead }) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);

  useClickOutside(wrapRef, () => setOpen(false), open);

  function toggle() {
    const next = !open;
    setOpen(next);
    if (next) markAllRead();
  }

  return (
    <div className="relative" ref={wrapRef}>
      <button
        id="notif-btn"
        className="p-2 rounded-full hover:bg-gray-200 dark:hover:bg-gray-800 transition relative"
        onClick={toggle}
      >
        <i className="fas fa-bell text-lg text-gray-600 dark:text-gray-300"></i>
        {unreadCount > 0 && (
          <span className="absolute top-0.5 right-0.5 bg-red-500 text-white text-[9px] w-3.5 h-3.5 rounded-full flex items-center justify-center font-bold">
            {unreadCount > 9 ? '9+' : unreadCount}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 mt-2 w-72 bg-white dark:bg-darkRow rounded-lg shadow-xl border border-gray-200 dark:border-gray-700 overflow-hidden z-50">
          <div className="p-3 bg-gray-50 dark:bg-gray-900 border-b border-gray-200 dark:border-gray-700 font-bold text-sm">
            Live Notifications
          </div>
          <div className="max-h-72 overflow-y-auto divide-y divide-gray-100 dark:divide-gray-700">
            {notifications.length === 0 ? (
              <div className="p-4 text-sm text-gray-400 text-center">Waiting for live events...</div>
            ) : (
              [...notifications]
                .reverse()
                .map((n) => (
                  <div key={n.id} className="p-3 text-sm flex items-start gap-2">
                    <span className="mt-0.5">
                      <i className={NOTIF_ICON_CLASS[n.type] || NOTIF_ICON_CLASS.info}></i>
                    </span>
                    <div className="flex-1">
                      <div className="text-gray-700 dark:text-gray-200">{n.message}</div>
                      <div className="text-[10px] text-gray-400 mt-0.5">{timeAgo(n.timestamp)}</div>
                    </div>
                  </div>
                ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
