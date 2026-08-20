import { useCallback, useEffect, useRef, useState } from 'react';
import { useAuth } from './hooks/useAuth.jsx';
import { authFetch } from './config/api';
import { debugLog } from './config/debug';
import { useApiStatus } from './hooks/useApiStatus';
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
import MobilePortfolioCard from './components/MobilePortfolioCard';
import ChartPanel from './components/ChartPanel';
import LiveTradesPanel from './components/LiveTradesPanel';
import ControlBar from './components/ControlBar';
import PaperTradingModal from './components/PaperTradingModal';
import SessionMomentumModal from './components/SessionMomentumModal';
import AlertModal from './components/AlertModal';
import SettingsModal from './components/SettingsModal';
import TradeExitConfirmModal from './components/TradeExitConfirmModal';
import SystemLogModal from './components/SystemLogModal';
import AgentChatStrip from './components/AgentChatStrip';
import TradingStatementModal from './components/TradingStatementModal';
import { MAX_LAUNCHER_SLOTS } from './components/TradeLauncherPopup';

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
  const pairSelector = usePairSelector();
  const { trades, activeCount, activePair: activeTradesPair, closeTrade, entryCandles, patternNeon } = useTrades(setConnected);
  const { notifications, unreadCount, markAllRead } = useNotifications();

  const [alertOpen, setAlertOpen] = useState(false);
  const [paperModalOpen, setPaperModalOpen] = useState(false);
  const [sessionModalOpen, setSessionModalOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
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

  const serverBotActive = Boolean(portfolio.isActive);
  const {
    isActive: effectiveBotActive,
    loading: botLoading,
    toggle: toggleBotEngine,
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
  const { timeframe, switchTimeframe, readouts, chartSourceMode, chartHistorySource, chartLiveSource } = useTradingChart({
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

  // On load: restore chips + re-sync backend watchlist (backend is in-memory).
  useEffect(() => {
    let cancelled = false;

    async function hydrateLauncher() {
      const saved = readSavedLauncherSlots();
      if (saved.length) {
        if (!cancelled) setLauncherSlots(saved);
        await syncWatchlist(saved);
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

  function pushActionLog(message) {
    setActionLogs((prev) => [
      { timestamp: new Date().toISOString(), message },
      ...prev,
    ].slice(0, 20));
  }

  // Fresh AI Engine toggle — only /bot/start and /bot/stop
  async function handleControlClick() {
    const pairs = launcherSlots.map((s) => `${s.symbol}/USDT`);
    const goingOn = !effectiveBotActive;
    pushActionLog(goingOn ? 'AI Engine START…' : 'AI Engine STOP…');
    const ok = await toggleBotEngine({ watchlistPairs: pairs });
    pushActionLog(ok ? (goingOn ? 'AI Engine ON.' : 'AI Engine OFF.') : 'AI Engine request failed.');
    debugLog(ok ? 'AI Engine toggle ok' : 'AI Engine toggle failed');
    if (ok && goingOn) {
      // Main engine start disables Session Momentum Engine on the backend
      refreshSessionEngine();
    }
  }

  async function handleSessionStart() {
    pushActionLog('Session Momentum Engine START…');
    const result = await startSessionEngine();
    pushActionLog(result?.ok ? 'Session Momentum Engine ON (Main AI Engine OFF).' : 'Session engine start failed.');
    return result;
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
      await closeTrade(tradeId, true);
    } catch (err) {
      console.error('Force close failed:', err);
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

  async function handleManualBuy() {
    pushActionLog('Manual BUY (LONG) clicked. Sending open-trade request.');
    debugLog('Manual BUY (LONG) clicked. Sending POST /open-trade to Backend...');
    try {
      const res = await authFetch('/open-trade', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ side: 'LONG' }),
      });
      const data = await res.json();
      if (data.status === 'error') {
        console.error('Manual LONG failed:', data.message);
        if (data.message?.toLowerCase().includes('insufficient')) {
          window.alert(data.message);
        }
      } else {
        debugLog(data.message || 'Manual LONG executed.');
      }
    } catch (err) {
      console.error('Manual LONG failed:', err);
    }
  }

  async function handleManualSell() {
    pushActionLog('Manual SELL (SHORT) clicked. Sending open-trade request.');
    debugLog('Manual SELL (SHORT) clicked. Sending POST /open-trade to Backend...');
    try {
      const res = await authFetch('/open-trade', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ side: 'SHORT' }),
      });
      const data = await res.json();
      if (data.status === 'error') {
        console.error('Manual SHORT failed:', data.message);
        if (data.message?.toLowerCase().includes('insufficient')) {
          window.alert(data.message);
        }
      } else {
        debugLog(data.message || 'Manual SHORT executed.');
      }
    } catch (err) {
      console.error('Manual SHORT failed:', err);
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
      return {
        title: 'Force Close Position?',
        message: `Position #${exitConfirm.tradeId} ko abhi market price par close karna chahte hain?`,
        detail: trade
          ? `${trade.pair} ${trade.side} @ $${trade.entry} | Current PnL: ${trade.pnl >= 0 ? '+' : ''}${trade.pnl?.toFixed(2)}%`
          : 'Yeh action undo nahi ho sakta.',
        confirmLabel: 'Force Close',
      };
    }
    return { title: '', message: '', confirmLabel: 'Confirm' };
  })();

  // Prefer live portfolio WS; fall back to open-trade rollups if WS lags at zeros.
  const openTrades = trades.filter((t) => t.status !== 'sold');
  const tradesNetUsd = openTrades.reduce((sum, t) => sum + (Number(t.net_pnl_usd) || 0), 0);
  const tradesFeeUsd = openTrades.reduce((sum, t) => sum + (Number(t.entry_fee_usd) || 0), 0);
  const wsDaily = Number(portfolio.dailyProfit) || 0;
  const wsFee = Number(portfolio.dailyBrokerFee) || 0;
  const wsSeason = Number(portfolio.seasonProfit) || 0;
  const pnlFallbackNeeded =
    openTrades.length > 0 &&
    wsDaily === 0 &&
    wsFee === 0 &&
    (Math.abs(tradesNetUsd) > 0.0001 || tradesFeeUsd > 0.0001);
  const dailyProfit = pnlFallbackNeeded ? tradesNetUsd : wsDaily;
  const dailyBrokerFee = pnlFallbackNeeded ? tradesFeeUsd : wsFee;
  const seasonProfit =
    portfolio.seasonActive || openTrades.length > 0
      ? (pnlFallbackNeeded ? tradesNetUsd : wsSeason)
      : wsSeason;
  const seasonActive = Boolean(portfolio.seasonActive || openTrades.length > 0);
  const capitalBase = Number(portfolio.cashLedger || portfolio.totalCapital || paperCapital || 0) || 1;
  const dailyProfitPct = pnlFallbackNeeded
    ? (dailyProfit / capitalBase) * 100
    : (Number(portfolio.dailyProfitPct) || 0);
  const seasonProfitPct = pnlFallbackNeeded
    ? (seasonProfit / capitalBase) * 100
    : (Number(portfolio.seasonProfitPct) || 0);

  // Total Capital from portfolio ledger (WS). Paper hook is only a seed.
  const totalEquity =
    Number(portfolio.cashLedger) > 0 || Number(portfolio.totalCapital) > 0
      ? (portfolio.cashLedger ?? portfolio.totalCapital)
      : (paperCapital != null && Number.isFinite(Number(paperCapital))
          ? Number(paperCapital)
          : (portfolio.cashLedger ?? portfolio.totalCapital ?? 0));
  const tradeValue = portfolio.tradeNotional > 0
    ? portfolio.tradeNotional
    : openTrades.reduce((sum, t) => sum + (Number(t.position_size) || 0), 0);

  return (
    <div className="min-h-screen flex flex-col">
      <Header
        totalCapital={totalEquity}
        tradeValue={tradeValue}
        dailyProfit={dailyProfit}
        dailyProfitPct={dailyProfitPct}
        dailyBrokerFee={dailyBrokerFee}
        seasonProfit={seasonProfit}
        seasonProfitPct={seasonProfitPct}
        seasonActive={seasonActive}
        tradesCount={activeCount}
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
        onOpenSessionModal={() => setSessionModalOpen(true)}
        sessionEngineEnabled={sessionEngineEnabled}
        onOpenSettings={() => setSettingsOpen(true)}
        onOpenLog={() => setLogModalOpen(true)}
        onOpenStatement={() => setStatementOpen(true)}
        onLogout={logout}
        username={username}
      />

      <MobilePortfolioCard
        totalCapital={totalEquity}
        tradeValue={tradeValue}
        dailyProfit={dailyProfit}
        dailyBrokerFee={dailyBrokerFee}
        seasonProfit={seasonProfit}
        seasonActive={seasonActive}
        tradesCount={activeCount}
      />

      <main className="flex-grow flex flex-col min-h-0 p-2 lg:p-4 gap-3">
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
        />
      </main>

      <ControlBar
        botIsActive={effectiveBotActive}
        botLoading={botLoading}
        sessionEngineEnabled={sessionEngineEnabled}
        uptime={uptime}
        lastUpdated={readouts.lastUpdated}
        onClick={handleControlClick}
        onManualBuy={handleManualBuy}
        onManualSell={handleManualSell}
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
        onRefresh={refreshSessionEngine}
        onStart={handleSessionStart}
        onStop={handleSessionStop}
      />

      <AlertModal open={alertOpen} onClose={() => setAlertOpen(false)} />

      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} onLiveTradingConnected={() => {}} />

      <TradingStatementModal open={statementOpen} onClose={() => setStatementOpen(false)} />

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
