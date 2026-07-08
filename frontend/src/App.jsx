import { useRef, useState } from 'react';
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

import Header from './components/Header';
import MobilePortfolioCard from './components/MobilePortfolioCard';
import ChartPanel from './components/ChartPanel';
import LiveTradesPanel from './components/LiveTradesPanel';
import ControlBar from './components/ControlBar';
import PaperTradingModal from './components/PaperTradingModal';
import RiskAlertModal from './components/RiskAlertModal';
import AlertModal from './components/AlertModal';
import SettingsModal from './components/SettingsModal';
import AgentInstructionsModal from './components/AgentInstructionsModal';
import StartConfirmModal from './components/StartConfirmModal';
import TradeExitConfirmModal from './components/TradeExitConfirmModal';

export default function App() {
  const { logout } = useAuth();
  const { status: apiStatus, setConnected } = useApiStatus();
  const pairSelector = usePairSelector();
  const { trades, activeCount, activePair: activeTradesPair, closeTrade } = useTrades(setConnected);
  const { notifications, unreadCount, markAllRead } = useNotifications();

  const [riskModal, setRiskModal] = useState({ open: false, lossPct: 0, threshold: 2.5 });
  const [alertOpen, setAlertOpen] = useState(false);
  const [paperModalOpen, setPaperModalOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [agentModalOpen, setAgentModalOpen] = useState(false);
  const [startConfirmOpen, setStartConfirmOpen] = useState(false);
  const [pendingConfig, setPendingConfig] = useState(null);
  const [manualCapital, setManualCapital] = useState(null);
  const [exitConfirm, setExitConfirm] = useState({ open: false, type: null, tradeId: null });

  const portfolio = usePortfolio(setConnected, {
    onEmergencyTriggered: (lossPct, threshold) => setRiskModal({ open: true, lossPct, threshold }),
  });

  const uptime = useUptime(portfolio.isActive);
  const dayStats = useDayStats(pairSelector.activePairLabel);

  const chartContainerRef = useRef(null);
  const volumeContainerRef = useRef(null);
  const { timeframe, switchTimeframe, readouts } = useTradingChart({
    chartContainerRef,
    volumeContainerRef,
    pairLabel: pairSelector.activePairLabel,
    pairPrice: pairSelector.activePair.price,
    externalTradingMode: portfolio.tradingMode,
    setConnected,
  });

  async function handleControlClick() {
    if (!portfolio.isActive) {
      // START AI AUTOMATION opens the AI Agent Instructions pre-start popup first.
      // The actual /start-bot call happens inside handleAgentStart once the user
      // confirms stop-loss / daily-profit and clicks Start.
      debugLog('User clicked START AI AUTOMATION. Opening AI Agent Instructions modal...');
      setAgentModalOpen(true);
    } else {
      debugLog('User clicked STOP AI AUTOMATION. Sending POST /emergency-exit to Backend...');
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
    setAgentModalOpen(false);
    setPendingConfig(config);
    setStartConfirmOpen(true);
  }

  // Safety check -> Continue: actually apply the config and start the bot.
  async function handleConfirmContinue() {
    if (!pendingConfig) return;
    const { stopLossPct, dailyProfitPct } = pendingConfig;
    setStartConfirmOpen(false);
    debugLog(`Safety check: Continue. Applying config (stopLoss=${stopLossPct}%, dailyProfit=${dailyProfitPct}%) and starting bot...`);
    try {
      await authFetch('/agent/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stop_loss_pct: stopLossPct, daily_profit_pct: dailyProfitPct }),
      });
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
    debugLog('Safety check: Emergency Exit. Bot start cancelled.');
    setStartConfirmOpen(false);
    setPendingConfig(null);
  }

  async function handleRiskExit() {
    setRiskModal((r) => ({ ...r, open: false }));
    try {
      debugLog('Emergency Exit button clicked from modal');
      await authFetch('/emergency-exit', { method: 'POST' });
    } catch (err) {
      console.error('Emergency exit request failed:', err);
    }
    setAlertOpen(true);
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

  function requestManualSell() {
    setExitConfirm({ open: true, type: 'manual-sell', tradeId: null });
  }

  async function handleManualSellConfirm() {
    setExitConfirm({ open: false, type: null, tradeId: null });
    debugLog('Manual SELL confirmed. Sending POST /manual-sell to Backend...');
    try {
      const res = await authFetch('/manual-sell', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirmed: true }),
      });
      const data = await res.json();
      if (data.status === 'error') {
        console.error('Manual SELL failed:', data.message);
      } else {
        debugLog(data.message || 'Manual SELL executed.');
      }
    } catch (err) {
      console.error('Manual sell failed:', err);
    }
  }

  function handleExitConfirm() {
    if (exitConfirm.type === 'force-close') return handleForceCloseConfirm();
    if (exitConfirm.type === 'manual-sell') return handleManualSellConfirm();
  }

  const exitConfirmCopy = (() => {
    if (exitConfirm.type === 'manual-sell') {
      return {
        title: 'Confirm Manual SELL?',
        message: 'Yeh action aapki best manual position ko market par close karega. Profit ya loss lock ho jayega.',
        detail: 'Sirf manually opened trades close hongi — AI trades touch nahi hongi.',
        confirmLabel: 'Sell Position',
      };
    }
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

  async function handleManualBuy() {
    debugLog('Manual BUY button clicked. Sending POST /open-trade to Backend...');
    try {
      const res = await authFetch('/open-trade', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ side: 'LONG' }),
      });
      const data = await res.json();
      if (data.status === 'error') {
        console.error('Manual BUY failed:', data.message);
        if (data.message?.toLowerCase().includes('insufficient')) {
          window.alert(data.message);
        }
      } else {
        debugLog(data.message || 'Manual BUY executed.');
      }
    } catch (err) {
      console.error('Manual buy failed:', err);
    }
  }

  async function handleManualSell() {
    requestManualSell();
  }

  async function handleRiskContinue() {
    setRiskModal((r) => ({ ...r, open: false }));
    try {
      debugLog('Continue button clicked - raising stop-loss threshold');
      const res = await authFetch('/continue-trading', { method: 'POST' });
      const data = await res.json();
      debugLog('[RULE 8] ' + data.message);
    } catch (err) {
      console.error('Continue-trading request failed:', err);
    }
  }

  const displayCapital = manualCapital ?? portfolio.totalCapital;

  return (
    <div className="min-h-screen flex flex-col">
      <Header
        totalCapital={displayCapital}
        dailyProfit={portfolio.dailyProfit}
        dailyProfitPct={portfolio.dailyProfitPct}
        seasonProfit={portfolio.seasonProfit}
        seasonProfitPct={portfolio.seasonProfitPct}
        seasonActive={portfolio.seasonActive}
        tradesCount={activeCount}
        apiStatus={apiStatus}
        tradingMode={portfolio.tradingMode}
        dayHigh={dayStats.high}
        dayLow={dayStats.low}
        notifications={notifications}
        unreadCount={unreadCount}
        markAllRead={markAllRead}
        onOpenPaperModal={() => {
          if (portfolio.tradingMode === 'LIVE_TRADING') return;
          setPaperModalOpen(true);
        }}
        onOpenSettings={() => setSettingsOpen(true)}
        onLogout={logout}
      />

      <MobilePortfolioCard
        totalCapital={displayCapital}
        dailyProfit={portfolio.dailyProfit}
        seasonProfit={portfolio.seasonProfit}
        seasonActive={portfolio.seasonActive}
        tradesCount={activeCount}
      />

      <main className="flex-grow p-2 lg:p-4 space-y-3">
        <ChartPanel
          pairSelector={pairSelector}
          chartContainerRef={chartContainerRef}
          volumeContainerRef={volumeContainerRef}
          timeframe={timeframe}
          switchTimeframe={switchTimeframe}
          readouts={readouts}
        />

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
      />

      <PaperTradingModal
        open={paperModalOpen}
        onClose={() => setPaperModalOpen(false)}
        currentCapital={displayCapital}
        onCapitalSet={(capital) => setManualCapital(capital)}
        isLive={portfolio.tradingMode === 'LIVE_TRADING'}
      />

      <RiskAlertModal
        open={riskModal.open}
        lossPct={riskModal.lossPct}
        currentThreshold={riskModal.threshold}
        onExit={handleRiskExit}
        onContinue={handleRiskContinue}
      />

      <AlertModal open={alertOpen} onClose={() => setAlertOpen(false)} />

      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} onLiveTradingConnected={() => {}} />

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
    </div>
  );
}
