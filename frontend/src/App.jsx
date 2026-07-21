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

import Header from './components/Header';
import MobilePortfolioCard from './components/MobilePortfolioCard';
import ChartPanel from './components/ChartPanel';
import LiveTradesPanel from './components/LiveTradesPanel';
import ControlBar from './components/ControlBar';
import PaperTradingModal from './components/PaperTradingModal';
import AlertModal from './components/AlertModal';
import SettingsModal from './components/SettingsModal';
import AgentInstructionsModal from './components/AgentInstructionsModal';
import StartConfirmModal from './components/StartConfirmModal';
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
  const { trades, activeCount, activePair: activeTradesPair, closeTrade, entryCandles } = useTrades(setConnected);
  const { notifications, unreadCount, markAllRead } = useNotifications();

  const [alertOpen, setAlertOpen] = useState(false);
  const [paperModalOpen, setPaperModalOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [agentModalOpen, setAgentModalOpen] = useState(false);
  const [startConfirmOpen, setStartConfirmOpen] = useState(false);
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

  const uptime = useUptime(portfolio.isActive);
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
    botIsActive: portfolio.isActive,
    blueBoxOverlay: portfolio.blueBoxOverlay,
    entryCandles,
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

  async function handleToggleSchedule() {
    const currentlyOn = Boolean(portfolio.sessionSchedule?.enabled);
    const next = !currentlyOn;
    try {
      const res = await authFetch('/settings/session-schedule', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: next }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        window.alert(data.message || 'Could not update session schedule.');
        return;
      }
      pushActionLog(data.message || `Session schedule ${next ? 'ON' : 'OFF'}`);
    } catch (err) {
      console.warn('Session schedule toggle failed:', err);
      window.alert('Could not reach backend to update session schedule.');
    }
  }

  async function handleControlClick() {
    if (!portfolio.isActive) {
      // START AI AUTOMATION opens the AI Agent Instructions pre-start popup first.
      // The actual /start-bot call happens inside handleAgentStart once the user
      // confirms risk level / daily-profit and clicks Start.
      debugLog('User clicked START AI AUTOMATION. Opening AI Agent Instructions modal...');
      pushActionLog('START AI AUTOMATION requested. Showing AI instructions popup.');
      setAgentModalOpen(true);
    } else {
      debugLog('User clicked STOP AI AUTOMATION. Sending POST /emergency-exit to Backend...');
      pushActionLog('STOP AI AUTOMATION requested. Sending emergency exit request to backend.');
      try {
        await authFetch('/emergency-exit', { method: 'POST' });
      } catch (err) {
        console.error('Emergency exit failed:', err);
      }
    }
  }

  // Step 3 -> Step 4 wiring: the AI Agent Instructions "Start AI Automation"
  // button hands the chosen config here. We close that popup and immediately
  // open the "Emergency Exit & Continue" final safety check with the config.
  function handleAgentStartRequest(config) {
    debugLog('AI Instructions confirmed. Opening Emergency Exit & Continue safety check...', config);
    pushActionLog(`AI config confirmed. risk_level=${config.stopLossPct}%, daily_profit=${config.dailyProfitPct}%`);
    setAgentModalOpen(false);
    setPendingConfig(config);
    setStartConfirmOpen(true);
  }

  // Safety check -> Continue: actually apply the config and start the bot.
  async function handleConfirmContinue() {
    if (!pendingConfig) return;
    const { stopLossPct, dailyProfitPct, trades } = pendingConfig;
    setStartConfirmOpen(false);
    pushActionLog(`Safety check continued. Starting bot with risk_level=${stopLossPct}%, daily_profit=${dailyProfitPct}%, max_trades=${trades}`);
    debugLog(`Safety check: Continue. Applying config (riskLevel=${stopLossPct}%, dailyProfit=${dailyProfitPct}%, maxTrades=${trades}) and starting bot...`);
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
      const configData = await configRes.json();
      if (configData.status === 'error') {
        pushActionLog(`Agent config rejected: ${configData.message}`);
        console.error('Agent config rejected:', configData.message);
        window.alert(configData.message || 'Could not apply AI agent settings. Bot not started.');
        setPendingConfig(null);
        return;
      }
      pushActionLog(`Agent config applied. max_concurrent_trades=${configData.max_concurrent_trades}`);
      await syncWatchlist(launcherSlots);
      const res = await authFetch('/start-bot', { method: 'POST' });
      const data = await res.json();
      debugLog('Bot started successfully:', data.message);
    } catch (err) {
      console.error('Failed to start bot from safety check:', err);
    } finally {
      setPendingConfig(null);
    }
  }

  // Safety check -> Emergency Exit (or 30s auto-default): cancel, do NOT start.
  function handleConfirmExit() {
    pushActionLog('Safety check cancelled. Bot start aborted.');
    debugLog('Safety check: Emergency Exit. Bot start cancelled.');
    setStartConfirmOpen(false);
    setPendingConfig(null);
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
    if (portfolio.isActive) return;
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

  // Total Capital = available cash (drops 10% per open trade). Trade Value = open notional.
  const totalEquity = portfolio.cashLedger ?? portfolio.totalCapital;
  const tradeValue = portfolio.tradeNotional > 0
    ? portfolio.tradeNotional
    : trades.reduce((sum, t) => {
        if (t.status === 'sold') return sum;
        return sum + (Number(t.position_size) || 0);
      }, 0);

  return (
    <div className="min-h-screen flex flex-col">
      <Header
        totalCapital={totalEquity}
        tradeValue={tradeValue}
        dailyProfit={portfolio.dailyProfit}
        dailyProfitPct={portfolio.dailyProfitPct}
        dailyBrokerFee={portfolio.dailyBrokerFee}
        seasonProfit={portfolio.seasonProfit}
        seasonProfitPct={portfolio.seasonProfitPct}
        seasonActive={portfolio.seasonActive}
        tradesCount={activeCount}
        apiStatus={apiStatus}
        tradingMode={portfolio.tradingMode}
        dayHigh={dayStats.high}
        dayLow={dayStats.low}
        tfMovePct={tfMoveStats.avgPct}
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

      <MobilePortfolioCard
        totalCapital={totalEquity}
        tradeValue={tradeValue}
        dailyProfit={portfolio.dailyProfit}
        dailyBrokerFee={portfolio.dailyBrokerFee}
        seasonProfit={portfolio.seasonProfit}
        seasonActive={portfolio.seasonActive}
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
          botIsActive={portfolio.isActive}
          tfMovePct={tfMoveStats.avgPct}
          tfMoveLabel={tfMoveStats.windowLabel}
          launcher={{
            slots: launcherSlots,
            editorOpen: launcherEditorOpen,
            editingId: launcherEditingId,
            onOpenNew: () => {
              if (portfolio.isActive) return;
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
              if (portfolio.isActive) return;
              setLauncherEditingId(id);
              setLauncherEditorOpen(true);
            },
            onRemoveSlot: (id) => {
              if (portfolio.isActive) return;
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
        <AgentChatStrip isActive={portfolio.isActive} lines={portfolio.agentChat} />

        <LiveTradesPanel
          trades={trades}
          activeCount={activeCount}
          activePair={activeTradesPair}
          onRequestClose={requestForceClose}
        />
      </main>

      <ControlBar
        botIsActive={portfolio.isActive}
        uptime={uptime}
        lastUpdated={readouts.lastUpdated}
        onClick={handleControlClick}
        onManualBuy={handleManualBuy}
        onManualSell={handleManualSell}
        sessionSchedule={portfolio.sessionSchedule}
        onToggleSchedule={handleToggleSchedule}
      />

      <PaperTradingModal
        open={paperModalOpen}
        onClose={() => setPaperModalOpen(false)}
        currentCapital={totalEquity}
        onCapitalSet={() => {
          /* Backend /ws/portfolio pushes the reset equity after save. */
        }}
        isLive={portfolio.tradingMode === 'LIVE_TRADING'}
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
