import { useEffect, useState } from 'react';
import { authFetch } from '../config/api';

function fmtTime(epoch) {
  if (!epoch) return '—';
  try {
    const ms = Number(epoch) > 1e12 ? Number(epoch) : Number(epoch) * 1000;
    return new Date(ms).toLocaleString('en-IN', {
      timeZone: 'Asia/Kolkata',
      day: '2-digit',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  } catch {
    return '—';
  }
}

function decisionTone(decision) {
  const d = String(decision || '').toUpperCase();
  if (d === 'FIRE') return 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40';
  if (d === 'SKIP') return 'bg-amber-500/15 text-amber-300 border-amber-500/40';
  if (d === 'DELAY') return 'bg-sky-500/15 text-sky-300 border-sky-500/40';
  return 'bg-gray-500/15 text-gray-300 border-gray-600';
}

function outcomeTone(outcome) {
  const o = String(outcome || '').toLowerCase();
  if (o === 'win') return 'text-emerald-400';
  if (o === 'loss') return 'text-red-400';
  if (o === 'breakeven') return 'text-yellow-400';
  if (o === 'skipped') return 'text-gray-400';
  return 'text-gray-500';
}

function pct(n) {
  if (n == null || Number.isNaN(Number(n))) return '—';
  return `${(Number(n) * 100).toFixed(1)}%`;
}

export default function AiTrainingModal({ open, onClose }) {
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState(null);
  const [tab, setTab] = useState('log'); // log | observations | playbook
  const [decisionFilter, setDecisionFilter] = useState('all');

  useEffect(() => {
    if (!open) return undefined;
    let cancelled = false;
    async function load() {
      setLoading(true);
      try {
        const parts = ['limit=120'];
        if (decisionFilter !== 'all') parts.push(`decision=${decisionFilter}`);
        const res = await authFetch(`/settings/ai-training?${parts.join('&')}`);
        const json = await res.json().catch(() => ({}));
        if (!cancelled) setData(json);
      } catch {
        if (!cancelled) {
          setData({
            ok: false,
            message: 'Could not load AI training feed',
            observations: [],
            events: [],
            rules: [],
            summary: {},
          });
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    const timer = setInterval(load, 15000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [open, decisionFilter]);

  if (!open) return null;

  const summary = data?.summary || {};
  const events = data?.events || [];
  const observations = data?.observations || [];
  const rules = data?.rules || [];
  const dbOk = Boolean(data?.ok);

  return (
    <div
      className="fixed inset-0 bg-black/70 z-[120] flex items-center justify-center backdrop-blur-sm p-3"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="bg-[#0B0E11] border border-violet-900/50 rounded-2xl shadow-2xl shadow-violet-950/40 w-full max-w-5xl max-h-[90vh] flex flex-col overflow-hidden">
        <div className="flex items-start justify-between px-5 pt-5 pb-3 border-b border-gray-800">
          <div className="flex items-start gap-3 min-w-0">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-violet-500 to-indigo-600 flex items-center justify-center shrink-0 shadow-lg shadow-violet-500/20">
              <i className="fas fa-brain text-white text-base" />
            </div>
            <div className="min-w-0">
              <div className="text-[10px] font-bold text-violet-400 uppercase tracking-widest">
                Cursor AI · Self-improve
              </div>
              <h2 className="text-lg font-bold text-white mt-0.5">AI Observation & Training</h2>
              <p className="text-[11px] text-gray-500 mt-1 truncate">
                Provider: {data?.ai_provider || '—'}
                {data?.cursor_unlimited ? ' · unlimited agent' : ''}
                {' · '}
                {dbOk
                  ? 'MySQL connected'
                  : data?.mysql?.message || data?.message || 'MySQL offline'}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="w-8 h-8 rounded-lg bg-[#161A1E] border border-gray-700 text-gray-400 hover:text-white shrink-0"
            aria-label="Close"
          >
            <i className="fas fa-times" />
          </button>
        </div>

        <div className="px-5 py-3 flex flex-wrap gap-2 border-b border-gray-800">
          {[
            { id: 'log', label: 'Training log', icon: 'fa-list' },
            { id: 'observations', label: 'Observations', icon: 'fa-eye' },
            { id: 'playbook', label: 'Playbook', icon: 'fa-book' },
          ].map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => setTab(t.id)}
              className={`px-3 py-1.5 rounded-lg text-[11px] font-bold uppercase tracking-wide border transition flex items-center gap-1.5 ${
                tab === t.id
                  ? 'bg-violet-600/30 border-violet-500 text-violet-200'
                  : 'bg-[#161A1E] border-gray-700 text-gray-400 hover:text-white'
              }`}
            >
              <i className={`fas ${t.icon}`} />
              {t.label}
            </button>
          ))}

          {tab === 'log' && (
            <div className="ml-auto flex flex-wrap gap-1.5">
              {[
                { id: 'all', label: 'All' },
                { id: 'FIRE', label: 'Fire' },
                { id: 'SKIP', label: 'Skip' },
                { id: 'DELAY', label: 'Delay' },
              ].map((f) => (
                <button
                  key={f.id}
                  type="button"
                  onClick={() => setDecisionFilter(f.id)}
                  className={`px-2.5 py-1 rounded-md text-[10px] font-bold uppercase border ${
                    decisionFilter === f.id
                      ? 'bg-indigo-600/30 border-indigo-500 text-indigo-200'
                      : 'bg-[#161A1E] border-gray-700 text-gray-500 hover:text-gray-300'
                  }`}
                >
                  {f.label}
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="px-5 py-2.5 flex flex-wrap gap-3 border-b border-gray-800/80 text-[11px]">
          <span className="text-gray-500">
            Events <b className="text-gray-200">{summary.events ?? 0}</b>
            {summary.pending > 0 ? (
              <span className="text-violet-400 ml-1">(pending {summary.pending})</span>
            ) : null}
          </span>
          <span className="text-emerald-500/90">
            Fire <b>{summary.fire ?? 0}</b>
          </span>
          <span className="text-amber-500/90">
            Skip <b>{summary.skip ?? 0}</b>
          </span>
          <span className="text-emerald-400">
            Wins <b>{summary.wins ?? 0}</b>
          </span>
          <span className="text-red-400">
            Losses <b>{summary.losses ?? 0}</b>
          </span>
          <span className="text-violet-400 ml-auto">
            Observations <b>{summary.observations ?? 0}</b>
          </span>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          {loading && !data ? (
            <div className="text-center text-gray-500 py-16 text-sm">
              <i className="fas fa-spinner fa-spin mr-2" />
              Loading AI feed…
            </div>
          ) : null}

          {tab === 'log' && (
            <div className="space-y-2">
              {!events.length && !loading ? (
                <div className="text-center text-gray-500 py-12 text-sm">
                  No training events yet. Fires and skips will appear here as the engine runs.
                </div>
              ) : null}
              {events.map((ev) => (
                <div
                  key={ev.id || ev.event_uid}
                  className="rounded-xl border border-gray-800 bg-[#12161B] px-3.5 py-3"
                >
                  <div className="flex flex-wrap items-center gap-2 gap-y-1.5">
                    <span
                      className={`px-2 py-0.5 rounded border text-[10px] font-black tracking-wide ${decisionTone(
                        ev.decision
                      )}`}
                    >
                      {String(ev.decision || '—').toUpperCase()}
                    </span>
                    <span className="text-sm font-bold text-white">{ev.pair || '—'}</span>
                    <span className="text-[11px] text-gray-400 uppercase">{ev.tf || '—'}</span>
                    <span className="text-[11px] text-gray-500">{ev.side || ''}</span>
                    <span className="text-[11px] text-violet-300/90 capitalize">{ev.family || ''}</span>
                    {ev.pattern ? (
                      <span className="text-[11px] text-gray-400 truncate max-w-[180px]" title={ev.pattern}>
                        {ev.pattern}
                      </span>
                    ) : null}
                    <span className={`text-[11px] font-semibold ml-auto ${outcomeTone(ev.outcome)}`}>
                      {ev.outcome ? String(ev.outcome).toUpperCase() : 'OPEN'}
                    </span>
                    <span className="text-[10px] text-gray-600 tabular-nums">{fmtTime(ev.created_at)}</span>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-3 text-[11px] text-gray-500">
                    {ev.score != null ? (
                      <span>
                        Score <b className="text-gray-300">{Number(ev.score).toFixed(0)}</b>
                      </span>
                    ) : null}
                    {ev.confidence != null ? (
                      <span>
                        Conf <b className="text-gray-300">{Number(ev.confidence).toFixed(0)}</b>
                      </span>
                    ) : null}
                    {ev.mfe_pct != null ? (
                      <span>
                        MFE <b className="text-emerald-400/90">{Number(ev.mfe_pct).toFixed(2)}%</b>
                      </span>
                    ) : null}
                    {ev.mae_pct != null ? (
                      <span>
                        MAE <b className="text-red-400/90">{Number(ev.mae_pct).toFixed(2)}%</b>
                      </span>
                    ) : null}
                    {ev.net_pnl_usd != null ? (
                      <span>
                        Net{' '}
                        <b className={Number(ev.net_pnl_usd) >= 0 ? 'text-emerald-400' : 'text-red-400'}>
                          ${Number(ev.net_pnl_usd).toFixed(2)}
                        </b>
                      </span>
                    ) : null}
                    {ev.trade_id != null ? <span>Trade #{ev.trade_id}</span> : null}
                  </div>
                  {ev.lesson ? (
                    <p className="mt-2 text-[12px] text-violet-200/80 leading-relaxed border-t border-gray-800/80 pt-2">
                      <i className="fas fa-lightbulb text-violet-400 mr-1.5 text-[10px]" />
                      {ev.lesson}
                    </p>
                  ) : null}
                  {Array.isArray(ev.fault_tags) && ev.fault_tags.length > 0 ? (
                    <div className="mt-2 flex flex-wrap gap-1">
                      {ev.fault_tags.map((tag) => (
                        <span
                          key={tag}
                          className="px-1.5 py-0.5 rounded text-[9px] font-bold uppercase bg-red-500/10 text-red-300/90 border border-red-500/20"
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          )}

          {tab === 'observations' && (
            <div className="space-y-2">
              {!observations.length && !loading ? (
                <div className="text-center text-gray-500 py-12 text-sm">
                  No AI observations yet. Lessons appear after family training samples.
                </div>
              ) : null}
              {observations.map((obs, idx) => (
                <div
                  key={`${obs.family}-${obs.timeframe_key}-${idx}`}
                  className="rounded-xl border border-violet-900/40 bg-gradient-to-br from-[#14101C] to-[#12161B] px-4 py-3"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-bold text-violet-200 capitalize">{obs.family}</span>
                    <span className="text-[11px] text-gray-400 uppercase">{obs.timeframe_key}</span>
                    {obs.locked ? (
                      <span className="text-[9px] font-bold uppercase text-amber-400 border border-amber-500/30 px-1.5 py-0.5 rounded">
                        Locked
                      </span>
                    ) : null}
                    <span className="text-[10px] text-gray-600 ml-auto">v{obs.version || 1}</span>
                  </div>
                  {obs.lesson ? (
                    <p className="mt-2 text-[13px] text-gray-200 leading-relaxed">{obs.lesson}</p>
                  ) : (
                    <p className="mt-2 text-[12px] text-gray-500 italic">No lesson text yet.</p>
                  )}
                  <div className="mt-2.5 flex flex-wrap gap-3 text-[11px] text-gray-500">
                    <span>
                      Samples <b className="text-gray-300">{obs.sample_count || 0}</b>
                    </span>
                    <span>
                      Win rate <b className="text-gray-300">{pct(obs.win_rate)}</b>
                    </span>
                    {obs.min_of_score != null ? (
                      <span>
                        OF floor <b className="text-gray-300">{Number(obs.min_of_score).toFixed(0)}</b>
                      </span>
                    ) : null}
                    {obs.updated_at ? <span className="ml-auto text-[10px]">{obs.updated_at}</span> : null}
                  </div>
                </div>
              ))}
            </div>
          )}

          {tab === 'playbook' && (
            <div className="overflow-x-auto">
              {!rules.length && !loading ? (
                <div className="text-center text-gray-500 py-12 text-sm">No family rules in MySQL.</div>
              ) : (
                <table className="w-full text-left text-[11px]">
                  <thead>
                    <tr className="text-gray-500 border-b border-gray-800">
                      <th className="py-2 pr-3 font-semibold">Family</th>
                      <th className="py-2 pr-3 font-semibold">TF</th>
                      <th className="py-2 pr-3 font-semibold">OF</th>
                      <th className="py-2 pr-3 font-semibold">Brain</th>
                      <th className="py-2 pr-3 font-semibold">R:R</th>
                      <th className="py-2 pr-3 font-semibold">Samples</th>
                      <th className="py-2 pr-3 font-semibold">WR</th>
                      <th className="py-2 font-semibold">Ver</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rules.map((r) => (
                      <tr key={r.id || `${r.family}-${r.timeframe_key}`} className="border-b border-gray-900/80 text-gray-300">
                        <td className="py-2 pr-3 capitalize text-violet-200">{r.family}</td>
                        <td className="py-2 pr-3 uppercase text-gray-400">{r.timeframe_key}</td>
                        <td className="py-2 pr-3 tabular-nums">{r.min_of_score ?? '—'}</td>
                        <td className="py-2 pr-3 tabular-nums">{r.min_brain_score ?? '—'}</td>
                        <td className="py-2 pr-3 tabular-nums">{r.min_rr ?? '—'}</td>
                        <td className="py-2 pr-3 tabular-nums">{r.sample_count ?? 0}</td>
                        <td className="py-2 pr-3 tabular-nums">{pct(r.win_rate)}</td>
                        <td className="py-2 tabular-nums">{r.version ?? 1}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
