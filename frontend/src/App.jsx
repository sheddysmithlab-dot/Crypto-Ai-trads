import { useCallback, useEffect, useRef, useState } from 'react';
import { useAuth } from './hooks/useAuth.jsx';
import { authFetch } from './config/api';
import { debugLog } from './config/debug';
import { useApiStatus } from './hooks/useApiStatus';
import { useMarketFeed } from './hooks/useMarketFeed';
import { usePairSelector } from './hooks/usePairSelector';
import { useTrades } from './hooks/useTrades';
import { useNotifications } from './hooks/useNotifications';
import { usePortfolio } from './hooks/usePortfolio';
import { useTradingChart } from './hooks/useTradingChart';
import { useUptime } from './hooks/useUptime';
import { useDayStats } from './hooks/useDayStats';
import { useTfMoveStats } from './hooks/useTfMoveStats';
import { useBotControl } from './hooks/useBotControl';
import { usePaperTrading } from './hooks/usePaperTrading';
import { useSessionEngine } from './hooks/useSessionEngine';

import Header from './components/Header';
import ChartPanel from './components/ChartPanel';
import LiveTradesPanel from './components/LiveTradesPanel';
import ControlBar from './components/ControlBar';
import PaperTradingModal from './components/PaperTradingModal';
import SessionMomentumModal from './components/SessionMomentumModal';
import AlertModal from './components/AlertModal';
import SettingsModal from './components/SettingsModal';
import AgentInstructionsModal from './components/AgentInstructionsModal';
import StartConfirmModal from './components/StartConfirmModal';
import TermsConditionsModal from './components/TermsConditionsModal';
import StopEngineModal from './components/StopEngineModal';
import SessionStopConfirmModal from './components/SessionStopConfirmModal';
import EngineBootOverlay from './components/EngineBootOverlay';
import TradeExitConfirmModal from './components/TradeExitConfirmModal';
import SystemLogModal from './components/SystemLogModal';
import AgentChatStrip from './components/AgentChatStrip';
import TradingStatementModal from './components/TradingStatementModal';
import { MAX_LAUNCHER_SLOTS } from './components/TradeLauncherPopup';
import { TRADING_PAIRS } from './data/pairs';

const ORIGINAL_SYMBOLS = new Set(TRADING_PAIRS.map((p) => p.symbol));
const LAUNCHER_SLOTS_KEY = 'ai_trading_bot_launcher_slots';

function readSavedLauncherSlots() {
  try {
    const raw = localStorage.getItem(LAUNCHER_SLOTS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((s) => s && typeof s.symbol === 'string' && s.symbol.trim())
      .slice(0, MAX_LAUNCHER_SLOTS)
      .map((s, i) => ({
        id: String(s.id || `${s.symbol}-${i}`),
        symbol: String(s.symbol).trim().toUpperCase(),
        timeframe: String(s.timeframe || '1M').toUpperCase(),
      }));
  } catch {
    return [];
  }
}

function persistLauncherSlots(slots) {
  try {
    localStorage.setItem(LAUNCHER_SLOTS_KEY, JSON.stringify(slots || []));
  } catch {
    /* ignore quota / private mode */
  }
}

export default function App() {
  const { logout, username } = useAuth();
  const { status: apiStatus, setConnected } = useApiStatus();
  useMarketFeed(setConnected);
  const pairSelector = usePairSelector();
  const { trades, activeCount, activePair: activeTradesPair, closeTrade, entryCandles, patternNeon } = useTrades(setConnected);
  const { notifications, unreadCount, markAllRead } = useNotifications();

  const [alertOpen, setAlertOpen] = useState(false);
  const [paperModalOpen, setPaperModalOpen] = useState(false);
  const [sessionModalOpen, setSessionModalOpen] = useState(false);
  const [sessionStopConfirmOpen, setSessionStopConfirmOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [agentModalOpen, setAgentModalOpen] = useState(false);
  const [startConfirmOpen, setStartConfirmOpen] = useState(false);
  const [termsOpen, setTermsOpen] = useState(false);
  const [stopConfirmOpen, setStopConfirmOpen] = useState(false);
  const [pendingConfig, setPendingConfig] = useState(null);
  const [logModalOpen, setLogModalOpen] = useState(false);
  const [statementOpen, setStatementOpen] = useState(false);
  const [settingsStatus, setSettingsStatus] = useState(null);
  const [systemLogs, setSystemLogs] = useState(null);
  const [actionLogs, setActionLogs] = useState([]);
  const [exitConfirm, setExitConfirm] = useState({ open: false, type: null, tradeId: null });
  const [launcherSlots, setLauncherSlots] = useState(() => readSavedLauncherSlots());
  const [launcherEditorOpen, setLauncherEditorOpen] = useState(false);
  const [launcherEditingId, setLauncherEditingId] = useState(null);

  const portfolio = usePortfolio(setConnected);
  const {
    capital: paperCapital,
    loading: paperLoading,
    refresh: refreshPaperStatus,
    setPaperCapital,
  } = usePaperTrading();

  // Load true paper capital from backend once (do not trust $0 from empty WS).
  useEffect(() => {
    refreshPaperStatus();
  }, [refreshPaperStatus]);

  const serverBotActive = portfolio.isActive;
  const {
    isActive: effectiveBotActive,
    loading: botLoading,
    start: startBotEngine,
    stop: stopBotEngine,
  } = useBotControl({ serverIsActive: serverBotActive });

  const {
    status: sessionStatus,
    enabled: sessionEngineEnabled,
    loading: sessionLoading,
    refresh: refreshSessionEngine,
    start: startSessionEngine,
    stop: stopSessionEngine,
  } = useSessionEngine({ serverSchedule: portfolio.sessionSchedule });

  const uptime = useUptime(effectiveBotActive);
  const dayStats = useDayStats(pairSelector.activePairLabel);

  async function fetchSettingsStatus() {
    try {
      const res = await authFetch('/settings/status');
      if (!res.ok) return;
      const data = await res.json();
      setSettingsStatus(data);
    } catch (err) {
      console.warn('Failed to fetch settings status for log modal:', err);
    }
  }

  const fetchSystemLogs = useCallback(async () => {
    try {
      const res = await authFetch('/system/logs');
      if (!res.ok) return;
      const data = await res.json();
      setSystemLogs(data);
    } catch (err) {
      console.warn('Failed to fetch system logs:', err);
    }
  }, []);

  useEffect(() => {
    if (logModalOpen) {
      fetchSettingsStatus();
      fetchSystemLogs();
    }
  }, [logModalOpen, fetchSystemLogs]);

  const chartContainerRef = useRef(null);
  const volumeContainerRef = useRef(null);
  const { timeframe, switchTimeframe, focusTradeCandle, readouts, chartSourceMode, chartHistorySource, chartLiveSource } = useTradingChart({
    chartContainerRef,
    volumeContainerRef,
    pairLabel: pairSelector.activePairLabel,
    pairPrice: pairSelector.activePair.price,
    externalTradingMode: portfolio.tradingMode,
    setConnected,
    botIsActive: effectiveBotActive,
    blueBoxOverlay: portfolio.blueBoxOverlay,
    entryCandles,
    patternNeon,
  });
  const tfMoveStats = useTfMoveStats(pairSelector.activePairLabel, timeframe);

  // Persist docked launcher chips across reloads.
  useEffect(() => {
    persistLauncherSlots(launcherSlots);
  }, [launcherSlots]);

  // On load: restore chips. Prefer backend watchlist when engine already ON
  // (browser reopen must not wipe VPS scan list with stale localStorage).
  useEffect(() => {
    let cancelled = false;

    async function hydrateLauncher() {
      let engineOn = false;
      try {
        const st = await authFetch('/bot/status');
        if (st.ok) {
          const data = await st.json();
          engineOn = Boolean(data.is_active);
          if (!cancelled && engineOn && Array.isArray(data.watchlist) && data.watchlist.length) {
            const slots = data.watchlist.slice(0, MAX_LAUNCHER_SLOTS).map((pair, i) => {
              const symbol = String(pair).split('/')[0].toUpperCase();
              return { id: `${symbol}-restored-${i}`, symbol, timeframe: '1M' };
            });
            setLauncherSlots(slots);
            persistLauncherSlots(slots);
            return;
          }
        }
      } catch {
        /* fall through to local hydrate */
      }

      const saved = readSavedLauncherSlots();
      if (saved.length) {
        if (!cancelled) setLauncherSlots(saved);
        // Only push local → backend when engine is OFF (avoid clobbering live VPS list).
        if (!engineOn) await syncWatchlist(saved);
        return;
      }
      try {
        const res = await authFetch('/watchlist');
        if (!res.ok || cancelled) return;
        const data = await res.json();
        const pairs = Array.isArray(data.watchlist) ? data.watchlist : [];
        if (!pairs.length || cancelled) return;
        const slots = pairs.slice(0, MAX_LAUNCHER_SLOTS).map((pair, i) => {
          const symbol = String(pair).split('/')[0].toUpperCase();
          return { id: `${symbol}-restored-${i}`, symbol, timeframe: '1M' };
        });
        setLauncherSlots(slots);
        persistLauncherSlots(slots);
      } catch (err) {
        console.warn('Launcher hydrate from watchlist failed:', err);
      }
    }

    hydrateLauncher();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // While engine active: launcher chips = trade-allowance list only
  // (momentum fire / watchlist capped by max_concurrent_trades). No original-20 merge.
  // Manual swap protected for 4s so user's chart swap isn't overwritten.
  const manualSwapAtRef = useRef(0);
  const chartResetPendingRef = useRef(false);
  useEffect(() => {
    if (!effectiveBotActive) return;
    if (Date.now() - manualSwapAtRef.current < 4000) return;
    const fire = Array.isArray(portfolio.momentumFirePairs) ? portfolio.momentumFirePairs : [];
    const watch = Array.isArray(portfolio.watchlist) ? portfolio.watchlist : [];
    const pairs = fire.length ? fire : watch;
    if (!pairs.length) return;

    setLauncherSlots((prev) => {
      const next = [];
      const seen = new Set();
      for (const pair of pairs) {
        const sym = String(pair).split('/')[0].toUpperCase();
        if (!sym || seen.has(sym)) continue;
        seen.add(sym);
        const prevSlot = prev.find((s) => String(s.symbol || '').toUpperCase() === sym);
        next.push({
          id: prevSlot?.id || `${sym}-fire-${next.length}`,
          symbol: sym,
          timeframe: prevSlot?.timeframe || '1M',
        });
      }
      const same =
        next.length === prev.length &&
        next.every((s, i) => s.symbol === prev[i]?.symbol);
      if (same) return prev;
      persistLauncherSlots(next);
      return next;
    });

    // After START scan: snap main chart to #1 fire coin once.
    if (chartResetPendingRef.current && fire.length) {
      chartResetPendingRef.current = false;
      const firstSym = String(fire[0]).split('/')[0].toUpperCase();
      if (firstSym && firstSym !== pairSelector.activeSymbol) {
        pairSelector.selectPair(firstSym, { silent: true });
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    effectiveBotActive,
    portfolio.watchlist,
    portfolio.momentumFirePairs,
    portfolio.momentumLastRefreshMs,
  ]);

  // Engine OFF → restore launcher to original 20 (drop momentum-added chips).
  useEffect(() => {
    if (effectiveBotActive) return;
    setLauncherSlots((prev) => {
      const hasMomentum = prev.some(
        (s) => !ORIGINAL_SYMBOLS.has(String(s.symbol || '').toUpperCase())
      );
      if (!hasMomentum) return prev;
      const restored = TRADING_PAIRS.map((p, i) => ({
        id: `${p.symbol}-orig-${i}`,
        symbol: p.symbol,
        timeframe: '1M',
      }));
      persistLauncherSlots(restored);
      return restored;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effectiveBotActive]);

  function pushActionLog(message) {
    setActionLogs((prev) => [
      { timestamp: new Date().toISOString(), message },
      ...prev,
    ].slice(0, 20));
  }

  // START → Instructions popup → Safety check → /agent/config + /bot/start
  // STOP  → Hold vs Emergency popup (or direct stop if no open trades)
  // Session Momentum ON → main button stops session (with confirm popup)
  async function handleControlClick() {
    if (sessionEngineEnabled) {
      if (activeCount > 0) {
        pushActionLog('Session Momentum Engine STOP — choose Hold or Emergency…');
        setSessionStopConfirmOpen(true);
        return;
      }
      pushActionLog('Session Momentum Engine STOP…');
      const result = await stopSessionEngine();
      pushActionLog(result?.ok ? 'Session Momentum Engine OFF.' : 'Session engine stop failed.');
      return;
    }
    if (effectiveBotActive) {
      // Always confirm — even with 0 open trades. Accidental STOP was halting
      // the VPS scanner; browser close must never be confused with stop.
      pushActionLog('AI Engine STOP — confirm Hold or Emergency…');
      setStopConfirmOpen(true);
      return;
    }
    pushActionLog('AI Engine START requested. Opening instructions popup.');
    debugLog('Opening AI Engine Instructions modal…');
    setAgentModalOpen(true);
  }

  async function handleSessionStopHold() {
    setSessionStopConfirmOpen(false);
    pushActionLog(`Session Momentum STOP (Hold) — ${activeCount} trade(s) stay managed…`);
    const result = await stopSessionEngine();
    pushActionLog(
      result?.ok
        ? 'Session Momentum Engine OFF (Hold). Open trades keep TP/SL; portfolio still updates.'
        : 'Session engine hold-stop failed.',
    );
  }

  async function handleSessionStopEmergency() {
    setSessionStopConfirmOpen(false);
    pushActionLog('Session Momentum STOP (Emergency) — closing all…');
    const sess = await stopSessionEngine();
    const ok = await stopBotEngine('emergency');
    pushActionLog(
      sess?.ok && ok
        ? 'Session Momentum Engine OFF. All positions closed.'
        : 'Session emergency stop failed.',
    );
  }

  async function handleStopHold() {
    setStopConfirmOpen(false);
    pushActionLog(`AI Engine STOP (Hold) — ${activeCount} trade(s) stay managed…`);
    const ok = await stopBotEngine('hold');
    pushActionLog(
      ok
        ? 'AI Engine OFF (Hold). Open trades keep TP/SL; portfolio still updates.'
        : 'AI Engine hold-stop failed.',
    );
  }

  async function handleStopEmergency() {
    setStopConfirmOpen(false);
    pushActionLog('AI Engine STOP (Emergency) — closing all…');
    const ok = await stopBotEngine('emergency');
    pushActionLog(ok ? 'AI Engine OFF. All positions closed.' : 'AI Engine emergency stop failed.');
  }

  function handleAgentStartRequest(config) {
    debugLog('AI Instructions confirmed. Opening Final Safety Check…', config);
    pushActionLog(
      `AI config confirmed. risk=${config.stopLossPct}%, daily_profit=${config.dailyProfitPct}%, max_trades=${config.trades}`,
    );
    setAgentModalOpen(false);
    setPendingConfig(config);
    setStartConfirmOpen(true);
  }

  async function handleBootCancel() {
    // Do not instant-stop — same confirm as main STOP (prevents fat-finger / tab close confusion).
    pushActionLog('Boot Cancel — confirm to stop AI Engine (VPS keeps running until you confirm)…');
    setStopConfirmOpen(true);
  }

  function handleConfirmContinue() {
    if (!pendingConfig) return;
    setStartConfirmOpen(false);
    pushActionLog('Safety check OK. Opening Terms & Conditions…');
    setTermsOpen(true);
  }

  function handleTermsCancel() {
    pushActionLog('Terms declined. AI Engine start aborted.');
    setTermsOpen(false);
    setPendingConfig(null);
  }

  async function handleTermsContinue() {
    if (!pendingConfig) return;
    const { stopLossPct, dailyProfitPct, trades } = pendingConfig;
    setTermsOpen(false);
    pushActionLog(
      `Terms accepted. Starting AI Engine (risk=${stopLossPct}%, daily=${dailyProfitPct}%, max_trades=${trades})…`,
    );
    try {
      const configRes = await authFetch('/agent/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          stop_loss_pct: stopLossPct,
          daily_profit_pct: dailyProfitPct,
          max_concurrent_trades: trades,
        }),
      });
      const configData = await configRes.json().catch(() => ({}));
      if (!configRes.ok || configData.status === 'error') {
        pushActionLog(`Agent config rejected: ${configData.message || 'error'}`);
        window.alert(configData.message || 'Could not apply AI agent settings. Bot not started.');
        setPendingConfig(null);
        return;
      }
      pushActionLog(`Agent config applied. max_concurrent_trades=${configData.max_concurrent_trades}`);
      // Engine START: clear old launcher; fresh momentum scan rebuilds trade-allowance chips.
      chartResetPendingRef.current = true;
      setLauncherSlots([]);
      persistLauncherSlots([]);
      const ok = await startBotEngine({ watchlistPairs: [] });
      pushActionLog(ok ? 'AI Engine ON — watchlist/chart reset for fresh scan.' : 'AI Engine start failed.');
      if (ok) refreshSessionEngine();
      else chartResetPendingRef.current = false;
    } catch (err) {
      console.error('Failed to start AI Engine after Terms:', err);
      pushActionLog('AI Engine start failed (network).');
    } finally {
      setPendingConfig(null);
    }
  }

  function handleConfirmExit() {
    pushActionLog('Safety check cancelled. AI Engine start aborted.');
    debugLog('Safety check: Emergency Exit. Start cancelled.');
    setStartConfirmOpen(false);
    setTermsOpen(false);
    setPendingConfig(null);
  }

  async function handleSessionStart(setup = {}) {
    const {
      timeframe: tf,
      stopLossPct = 5,
      dailyProfitPct = 0,
      trades = Math.max(1, Math.floor(stopLossPct * 2 + 0.5)),
    } = setup || {};
    pushActionLog(
      `Session Momentum setup: chart=${tf || timeframe}, risk=${stopLossPct}%, max_trades=${trades}…`,
    );
    try {
      const configRes = await authFetch('/agent/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          stop_loss_pct: stopLossPct,
          daily_profit_pct: dailyProfitPct,
          max_concurrent_trades: trades,
        }),
      });
      const configData = await configRes.json().catch(() => ({}));
      if (!configRes.ok || configData.status === 'error') {
        pushActionLog(`Session config rejected: ${configData.message || 'error'}`);
        window.alert(configData.message || 'Could not apply risk settings. Session not started.');
        return { ok: false, message: configData.message };
      }
      if (tf) {
        switchTimeframe(String(tf).toUpperCase());
      }
      pushActionLog('Session Momentum Engine START…');
      const result = await startSessionEngine();
      pushActionLog(
        result?.ok
          ? `Session Momentum Engine ON · ${tf || timeframe} · risk ${stopLossPct}% (Main AI OFF).`
          : 'Session engine start failed.',
      );
      return result;
    } catch (err) {
      console.error('Session Momentum start failed:', err);
      pushActionLog('Session engine start failed (network).');
      return { ok: false, message: err?.message };
    }
  }

  async function handleSessionStop() {
    pushActionLog('Session Momentum Engine STOP…');
    const result = await stopSessionEngine();
    pushActionLog(result?.ok ? 'Session Momentum Engine OFF.' : 'Session engine stop failed.');
    return result;
  }

  function requestForceClose(tradeId) {
    setExitConfirm({ open: true, type: 'force-close', tradeId });
  }

  async function handleForceCloseConfirm() {
    const tradeId = exitConfirm.tradeId;
    setExitConfirm({ open: false, type: null, tradeId: null });
    if (!tradeId) return;
    try {
      const data = await closeTrade(tradeId, true);
      if (data?.status === 'error') {
        const msg = data.message || 'Force close failed.';
        pushActionLog(`Force close failed #${tradeId}: ${msg}`);
        window.alert(msg);
        return;
      }
      pushActionLog(data?.message || `Position #${tradeId} force-closed.`);
    } catch (err) {
      console.error('Force close failed:', err);
      pushActionLog(`Force close failed #${tradeId} (network).`);
      window.alert(err?.message || 'Force close failed (network).');
    }
  }

  async function syncWatchlist(slots) {
    const pairs = (slots || []).map((s) => `${s.symbol}/USDT`);
    try {
      const res = await authFetch('/set-watchlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pairs }),
      });
      const data = await res.json().catch(() => ({}));
      if (data.status === 'success') {
        pushActionLog(
          `AI watchlist synced: ${(data.scan_pairs || pairs).join(', ') || 'chart pair only'}`,
        );
        debugLog('Watchlist synced →', data.scan_pairs || pairs);
      } else {
        console.warn('Watchlist sync failed:', data.message || data);
      }
    } catch (err) {
      console.warn('Watchlist sync error:', err);
    }
  }

  function handleLauncherMinimizeToSlot({ id, symbol, timeframe: tf }) {
    if (effectiveBotActive) return;
    let nextSlots = null;
    setLauncherSlots((prev) => {
      const sameSymbol = prev.find((s) => s.symbol === symbol);
      if (id && prev.some((s) => s.id === id)) {
        nextSlots = prev.map((s) => (s.id === id ? { ...s, symbol, timeframe: tf } : s));
      } else if (sameSymbol) {
        nextSlots = prev.map((s) => (s.symbol === symbol ? { ...s, timeframe: tf } : s));
      } else if (prev.length >= MAX_LAUNCHER_SLOTS) {
        window.alert(`Maximum ${MAX_LAUNCHER_SLOTS} coins in the minimize list.`);
        nextSlots = prev;
      } else {
        nextSlots = [...prev, { id: `${symbol}-${Date.now()}`, symbol, timeframe: tf }];
      }
      return nextSlots;
    });
    setLauncherEditorOpen(false);
    setLauncherEditingId(null);
    if (nextSlots) syncWatchlist(nextSlots);
  }

  /** Chart-only: docked coin ↔ current main chart (not hard-coded BTC). */
  async function handleLauncherSwapWithMain(slotId) {
    const slot = launcherSlots.find((s) => s.id === slotId);
    if (!slot) return;

    // Always read the live main-chart symbol (persisted selector), never assume BTC.
    const mainSym = String(pairSelector.activeSymbol || '')
      .trim()
      .toUpperCase()
      .split('/')[0];
    const incoming = String(slot.symbol || '')
      .trim()
      .toUpperCase()
      .split('/')[0];
    if (!mainSym || !incoming || incoming === mainSym) return;

    // 1) Switch main chart first so UI/chart leave the current pair (not stuck on BTC).
    await pairSelector.selectPair(incoming, { silent: true });

    // 2) Park the previous main coin on this chip; drop duplicate chips of that symbol.
    const nextSlots = launcherSlots
      .map((s) =>
        s.id === slotId
          ? { ...s, symbol: mainSym, timeframe: timeframe || s.timeframe }
          : s,
      )
      .filter((s, _i, arr) => {
        if (s.symbol !== mainSym) return true;
        const same = arr.filter((x) => x.symbol === mainSym);
        if (same.length <= 1) return true;
        // Keep the chip we just wrote (clicked slot), drop older duplicates.
        return s.id === slotId;
      });

    setLauncherSlots(nextSlots);
    setLauncherEditorOpen(false);
    setLauncherEditingId(null);
    manualSwapAtRef.current = Date.now();
    syncWatchlist(nextSlots);
    pushActionLog(`Chart swap: ${incoming}/USDT → main, ${mainSym}/USDT → dock`);
  }

  /** Live/exited trade row → main chart replace + scroll to that trade’s neon fire candle. */
  async function handleTradeSelectOnChart(trade) {
    if (!trade?.pair) return;
    const incoming = String(trade.pair)
      .trim()
      .toUpperCase()
      .split('/')[0];
    const mainSym = String(pairSelector.activeSymbol || '')
      .trim()
      .toUpperCase()
      .split('/')[0];
    if (!incoming) return;

    // Queue neon focus before pair reload so history load scrolls to the fire bar.
    focusTradeCandle(trade);

    if (incoming !== mainSym) {
      const dockSlot = launcherSlots.find(
        (s) => String(s.symbol || '').trim().toUpperCase() === incoming,
      );
      if (dockSlot) {
        await handleLauncherSwapWithMain(dockSlot.id);
      } else {
        await pairSelector.selectPair(incoming, { silent: true });
      }
      // Re-arm focus after swap (pair clear/reload may have raced).
      focusTradeCandle(trade);
      pushActionLog(
        `Chart replace: ${incoming}/USDT → main · trade #${trade.id} neon candle`,
      );
    } else {
      pushActionLog(`Chart focus: ${trade.pair} trade #${trade.id} neon candle`);
    }
  }

  async function handleManualBuy() {
    const pair = pairSelector.activePairLabel;
    pushActionLog(`Manual BUY (LONG) on ${pair}…`);
    debugLog(`Manual BUY (LONG) → POST /open-trade pair=${pair}`);
    try {
      const res = await authFetch('/open-trade', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ side: 'LONG', pair }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.status === 'error') {
        const msg = data.message || 'Manual LONG failed.';
        console.error('Manual LONG failed:', msg);
        pushActionLog(`Manual LONG failed: ${msg}`);
        window.alert(msg);
      } else {
        pushActionLog(data.message || `Manual LONG filled on ${pair}.`);
        debugLog(data.message || 'Manual LONG executed.');
      }
    } catch (err) {
      console.error('Manual LONG failed:', err);
      pushActionLog('Manual LONG failed (network).');
      window.alert(err?.message || 'Manual LONG failed (network).');
    }
  }

  async function handleManualSell() {
    const pair = pairSelector.activePairLabel;
    pushActionLog(`Manual SELL (SHORT) on ${pair}…`);
    debugLog(`Manual SELL (SHORT) → POST /open-trade pair=${pair}`);
    try {
      const res = await authFetch('/open-trade', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ side: 'SHORT', pair }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.status === 'error') {
        const msg = data.message || 'Manual SHORT failed.';
        console.error('Manual SHORT failed:', msg);
        pushActionLog(`Manual SHORT failed: ${msg}`);
        window.alert(msg);
      } else {
        pushActionLog(data.message || `Manual SHORT filled on ${pair}.`);
        debugLog(data.message || 'Manual SHORT executed.');
      }
    } catch (err) {
      console.error('Manual SHORT failed:', err);
      pushActionLog('Manual SHORT failed (network).');
      window.alert(err?.message || 'Manual SHORT failed (network).');
    }
  }

  function handleExitConfirm() {
    if (exitConfirm.type === 'force-close') {
      pushActionLog(`Force close confirmed for trade #${exitConfirm.tradeId}.`);
      return handleForceCloseConfirm();
    }
  }

  const exitConfirmCopy = (() => {
    if (exitConfirm.type === 'force-close') {
      const trade = trades.find((t) => t.id === exitConfirm.tradeId);
      const lockedNote =
        trade?.status === 'locked'
          ? ' Trailing lock will be cleared — this exits immediately at market.'
          : '';
      return {
        title: 'Force Close Position?',
        message: `Close position #${exitConfirm.tradeId} now at market price?${lockedNote}`,
        detail: trade
          ? `${trade.pair} ${trade.side} @ $${trade.entry} | Current PnL: ${trade.pnl >= 0 ? '+' : ''}${trade.pnl?.toFixed(2)}%`
          : 'This action cannot be undone.',
        confirmLabel: 'Force Close',
      };
    }
    return { title: '', message: '', confirmLabel: 'Confirm' };
  })();

  // Prefer live portfolio WS session counters (AI session only; frozen after STOP).
  const openTrades = trades.filter((t) => t.status !== 'sold');
  const wsDaily = Number(portfolio.dailyProfit) || 0;
  const wsFee = Number(portfolio.dailyBrokerFee) || 0;
  const wsSeason = Number(portfolio.seasonProfit) || 0;
  const wsSeasonNet = Number(portfolio.seasonProfitNet);
  const dailyProfit = wsDaily;
  const dailyBrokerFee = wsFee;
  const seasonProfit = wsSeason;
  const seasonProfitNet = Number.isFinite(wsSeasonNet) ? wsSeasonNet : wsSeason - wsFee;
  const seasonActive = Boolean(portfolio.seasonActive);
  const dailyProfitPct = Number(portfolio.dailyProfitPct) || 0;
  const seasonProfitPct = Number(portfolio.seasonProfitPct) || 0;
  const seasonProfitNetPct = Number(portfolio.seasonProfitNetPct) || 0;

  // Total Capital from portfolio ledger (WS). Paper hook is only a seed.
  const totalEquity =
    Number(portfolio.cashLedger) > 0 || Number(portfolio.totalCapital) > 0
      ? (portfolio.cashLedger ?? portfolio.totalCapital)
      : (paperCapital != null && Number.isFinite(Number(paperCapital))
          ? Number(paperCapital)
          : (portfolio.cashLedger ?? portfolio.totalCapital ?? 0));
  const tradeValue = Number(portfolio.tradeNotional) || 0;
  const sessionOpenPositions = Number(portfolio.sessionOpenPositions) || 0;

  return (
    <div className="h-screen max-h-screen overflow-hidden flex flex-col bg-lightBg dark:bg-darkBg">
      <Header
        totalCapital={totalEquity}
        tradeValue={tradeValue}
        dailyProfit={dailyProfit}
        dailyProfitPct={dailyProfitPct}
        dailyBrokerFee={dailyBrokerFee}
        seasonProfit={seasonProfit}
        seasonProfitPct={seasonProfitPct}
        seasonProfitNet={seasonProfitNet}
        seasonProfitNetPct={seasonProfitNetPct}
        seasonActive={seasonActive}
        tradesCount={sessionOpenPositions}
        exitedPnlUsd={Number(portfolio.exitedBookedUsd) || 0}
        apiStatus={apiStatus}
        tradingMode={portfolio.tradingMode}
        dayHigh={dayStats.high}
        dayLow={dayStats.low}
        tfMovePct={tfMoveStats.displayPct ?? tfMoveStats.totalPct ?? tfMoveStats.avgPct}
        tfMoveLabel={tfMoveStats.windowLabel}
        chartTimeframe={timeframe}
        notifications={notifications}
        unreadCount={unreadCount}
        markAllRead={markAllRead}
        onOpenPaperModal={() => {
          if (portfolio.tradingMode === 'LIVE_TRADING') return;
          setPaperModalOpen(true);
        }}
        onOpenSettings={() => setSettingsOpen(true)}
        onOpenLog={() => setLogModalOpen(true)}
        onOpenStatement={() => setStatementOpen(true)}
        onLogout={logout}
        username={username}
      />

      <main className="flex-1 min-h-0 overflow-y-auto overflow-x-hidden flex flex-col p-2 lg:p-4 gap-3 pb-4">
        <ChartPanel
          pairSelector={pairSelector}
          chartContainerRef={chartContainerRef}
          volumeContainerRef={volumeContainerRef}
          timeframe={timeframe}
          switchTimeframe={switchTimeframe}
          readouts={readouts}
          botIsActive={effectiveBotActive}
          tfMovePct={tfMoveStats.displayPct ?? tfMoveStats.totalPct ?? tfMoveStats.avgPct}
          tfMoveLabel={tfMoveStats.windowLabel}
          launcher={{
            slots: launcherSlots,
            editorOpen: launcherEditorOpen,
            editingId: launcherEditingId,
            onOpenNew: () => {
              if (effectiveBotActive) return;
              if (launcherSlots.length >= MAX_LAUNCHER_SLOTS) {
                window.alert(`Maximum ${MAX_LAUNCHER_SLOTS} coins in the minimize list.`);
                return;
              }
              setLauncherEditingId(null);
              setLauncherEditorOpen(true);
            },
            onCloseEditor: () => {
              setLauncherEditorOpen(false);
              setLauncherEditingId(null);
            },
            onMinimizeToSlot: handleLauncherMinimizeToSlot,
            onRestoreSlot: (id) => {
              if (effectiveBotActive) return;
              setLauncherEditingId(id);
              setLauncherEditorOpen(true);
            },
            onSwapWithMain: handleLauncherSwapWithMain,
            onRemoveSlot: (id) => {
              if (effectiveBotActive) return;
              setLauncherSlots((prev) => {
                const next = prev.filter((s) => s.id !== id);
                syncWatchlist(next);
                return next;
              });
              if (launcherEditingId === id) {
                setLauncherEditorOpen(false);
                setLauncherEditingId(null);
              }
            },
          }}
        />
        <AgentChatStrip isActive={effectiveBotActive} lines={portfolio.agentChat} />

        <LiveTradesPanel
          trades={trades}
          activeCount={activeCount}
          activePair={activeTradesPair}
          onRequestClose={requestForceClose}
          onSelectTrade={handleTradeSelectOnChart}
        />
      </main>

      <ControlBar
        botIsActive={effectiveBotActive}
        botLoading={botLoading}
        sessionEngineEnabled={sessionEngineEnabled}
        sessionLoading={sessionLoading}
        connectivityFrozen={Boolean(portfolio.connectivityFrozen)}
        freezeReason={portfolio.freezeReason}
        oneMFeeHold={Boolean(portfolio.oneMFeeHold)}
        uptime={uptime}
        lastUpdated={readouts.lastUpdated}
        onClick={handleControlClick}
        onManualBuy={handleManualBuy}
        onManualSell={handleManualSell}
        onOpenSessionModal={() => setSessionModalOpen(true)}
      />

      <EngineBootOverlay
        active={effectiveBotActive}
        warmupRemainingSec={portfolio.warmupRemainingSec}
        warmupTotalSec={portfolio.warmupTotalSec}
        introSec={portfolio.bootIntroSec}
        analysisSec={portfolio.bootAnalysisSec}
        momentumThresholdPct={portfolio.momentumThresholdPct}
        momentumFirePairs={portfolio.momentumFirePairs}
        momentumScores={portfolio.momentumScores}
        momentumGateReady={portfolio.momentumGateReady}
        momentumScanDone={portfolio.momentumScanDone}
        momentumScanTotal={portfolio.momentumScanTotal}
        momentumScanStage={portfolio.momentumScanStage}
        onCancel={handleBootCancel}
        cancelLoading={botLoading}
      />

      <PaperTradingModal
        open={paperModalOpen}
        onClose={() => setPaperModalOpen(false)}
        isLive={portfolio.tradingMode === 'LIVE_TRADING'}
        paperCapital={paperCapital}
        paperLoading={paperLoading}
        onRefreshStatus={refreshPaperStatus}
        onSetCapital={setPaperCapital}
      />

      <SessionMomentumModal
        open={sessionModalOpen}
        onClose={() => setSessionModalOpen(false)}
        enabled={sessionEngineEnabled}
        loading={sessionLoading}
        status={sessionStatus}
        mainEngineActive={effectiveBotActive}
        chartTimeframe={timeframe}
        onRefresh={refreshSessionEngine}
        onStart={handleSessionStart}
        onStop={handleSessionStop}
      />

      <AlertModal open={alertOpen} onClose={() => setAlertOpen(false)} />

      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} onLiveTradingConnected={() => {}} />

      <TradingStatementModal open={statementOpen} onClose={() => setStatementOpen(false)} />

      <AgentInstructionsModal
        open={agentModalOpen}
        onClose={() => setAgentModalOpen(false)}
        onStart={handleAgentStartRequest}
      />

      <StartConfirmModal
        open={startConfirmOpen}
        config={pendingConfig}
        activeCount={activeCount}
        onContinue={handleConfirmContinue}
        onExit={handleConfirmExit}
      />

      <TermsConditionsModal
        open={termsOpen}
        onContinue={handleTermsContinue}
        onCancel={handleTermsCancel}
      />

      <StopEngineModal
        open={stopConfirmOpen}
        openCount={activeCount}
        loading={botLoading}
        onHold={handleStopHold}
        onEmergency={handleStopEmergency}
        onCancel={() => setStopConfirmOpen(false)}
      />

      <SessionStopConfirmModal
        open={sessionStopConfirmOpen}
        openCount={activeCount}
        loading={sessionLoading || botLoading}
        onHold={handleSessionStopHold}
        onEmergency={handleSessionStopEmergency}
        onCancel={() => setSessionStopConfirmOpen(false)}
      />

      <TradeExitConfirmModal
        open={exitConfirm.open}
        title={exitConfirmCopy.title}
        message={exitConfirmCopy.message}
        detail={exitConfirmCopy.detail}
        confirmLabel={exitConfirmCopy.confirmLabel}
        onConfirm={handleExitConfirm}
        onCancel={() => setExitConfirm({ open: false, type: null, tradeId: null })}
      />

      <SystemLogModal
        open={logModalOpen}
        onClose={() => setLogModalOpen(false)}
        apiStatus={apiStatus}
        tradingMode={portfolio.tradingMode}
        chartSourceMode={chartSourceMode}
        chartHistorySource={chartHistorySource}
        chartLiveSource={chartLiveSource}
        timeframe={timeframe}
        activePair={pairSelector.activePairLabel}
        lastUpdated={readouts.lastUpdated}
        settingsStatus={settingsStatus}
        systemLogs={systemLogs}
        actionLogs={actionLogs}
        onRefresh={fetchSystemLogs}
      />
    </div>
  );
}
