import { timeAgo } from '../hooks/useNotifications';

const STATUS_STYLES = {
  active: {
    dot: 'bg-green-400',
    border: 'border-green-500/35',
    text: 'text-green-100',
    pulse: false,
  },
  scanning: {
    dot: 'bg-cyan-400',
    border: 'border-cyan-500/40',
    text: 'text-cyan-100',
    pulse: true,
  },
  match: {
    dot: 'bg-emerald-400',
    border: 'border-emerald-500/40',
    text: 'text-emerald-100',
    pulse: true,
  },
  no_match: {
    dot: 'bg-gray-400',
    border: 'border-gray-600/50',
    text: 'text-gray-300',
    pulse: false,
  },
  trade: {
    dot: 'bg-blue-400',
    border: 'border-blue-500/40',
    text: 'text-blue-100',
    pulse: false,
  },
};

function styleFor(status) {
  return STATUS_STYLES[status] || STATUS_STYLES.scanning;
}

export default function AgentChatStrip({ isActive, lines }) {
  if (!isActive) return null;

  const recent = Array.isArray(lines) ? lines.slice(-4) : [];
  if (!recent.length) {
    return (
      <div className="rounded-xl border border-cyan-500/25 bg-gray-900/70 dark:bg-black/40 px-3 py-2.5 flex items-center gap-2.5 text-sm">
        <span className="w-2 h-2 rounded-full bg-cyan-400 animate-pulse shrink-0" />
        <p className="text-cyan-100/90 font-medium">
          AI agent is running — waiting for the next closed candle to scan patterns…
        </p>
      </div>
    );
  }

  const latest = recent[recent.length - 1];
  const latestStyle = styleFor(latest.status);
  const older = recent.slice(0, -1);

  return (
    <div
      className={`rounded-xl border ${latestStyle.border} bg-gray-900/75 dark:bg-black/45 px-3 py-2.5 space-y-1.5`}
      aria-live="polite"
      aria-label="AI agent pattern scan activity"
    >
      <div className="flex items-start gap-2.5 min-w-0">
        <span
          className={`w-2 h-2 rounded-full mt-1.5 shrink-0 ${latestStyle.dot} ${
            latestStyle.pulse ? 'animate-pulse' : ''
          }`}
          title="AI agent activity"
        />
        <div className="flex-1 min-w-0">
          <p className={`text-sm font-semibold leading-snug ${latestStyle.text}`}>{latest.message}</p>
          <p className="text-[10px] text-gray-500 mt-0.5 font-mono">
            AI pattern engine · {timeAgo(latest.timestamp)}
          </p>
        </div>
      </div>

      {older.length > 0 ? (
        <ul className="pl-4 border-l border-gray-700/80 ml-1 space-y-1">
          {[...older].reverse().map((line) => {
            const s = styleFor(line.status);
            return (
              <li key={line.id} className="text-[11px] text-gray-500 leading-snug truncate">
                <span className={`inline-block w-1 h-1 rounded-full mr-1.5 align-middle ${s.dot}`} />
                {line.message}
                <span className="text-gray-600 ml-1">· {timeAgo(line.timestamp)}</span>
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}
