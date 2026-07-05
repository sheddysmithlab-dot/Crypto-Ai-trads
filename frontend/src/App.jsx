import { useRef, useState } from 'react';
import { API_BASE } from './config/api';
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

export default function App() {
  const { status: apiStatus, setConnected } = useApiStatus();
  const pairSelector = usePairSelector();
  const { trades, activePair: activeTradesPair, closeTrade, clearTrades } = useTrades(setConnected);
  const { notifications, unreadCount, markAllRead } = useNotifications();

  const [riskModal, setRiskModal] = useState({ open: false, lossPct: 0, threshold: 2.5 });
  const [alertOpen, setAlertOpen] = useState(false);
  const [paperModalOpen, setPaperModalOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [manualCapital, setManualCapital] = useState(null);

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
      debugLog('User clicked START TRADING. Sending POST /start-bot to Backend...');
      try {
        const res = await fetch(`${API_BASE}/start-bot`, { method: 'POST' });
        const data = await res.json();
        debugLog('Bot started successfully:', data.message);
      } catch (err) {
        console.error('Failed to start bot:', err);
      }
    } else {
      // Plain voluntary stop - no loss event, so no "EMERGENCY EXIT TRIGGERED" modal here.
      // That wording is reserved for a genuine RULE 5 auto-kill resolved via handleRiskExit.
      debugLog('User clicked STOP TRADING. Sending POST /emergency-exit to Backend...');
      try {
        await fetch(`${API_BASE}/emergency-exit`, { method: 'POST' });
        clearTrades();
      } catch (err) {
        console.error('Emergency exit failed:', err);
      }
    }
  }

  async function handleRiskExit() {
    setRiskModal((r) => ({ ...r, open: false }));
    try {
      debugLog('Emergency Exit button clicked from modal');
      await fetch(`${API_BASE}/emergency-exit`, { method: 'POST' });
    } catch (err) {
      console.error('Emergency exit request failed:', err);
    }
    setAlertOpen(true);
    clearTrades();
  }

  async function handleManualBuy() {
    debugLog('Manual BUY button clicked. Sending POST /open-trade to Backend...');
    try {
      const res = await fetch(`${API_BASE}/open-trade`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ side: 'LONG' }),
      });
      const data = await res.json();
      debugLog(data.message || 'Manual BUY executed.');
    } catch (err) {
      console.error('Manual buy failed:', err);
    }
  }

  async function handleManualSell() {
    debugLog('Manual SELL button clicked. Sending POST /manual-sell to Backend...');
    try {
      const res = await fetch(`${API_BASE}/manual-sell`, { method: 'POST' });
      const data = await res.json();
      debugLog(data.message || 'Manual SELL executed.');
    } catch (err) {
      console.error('Manual sell failed:', err);
    }
  }

  async function handleRiskContinue() {
    setRiskModal((r) => ({ ...r, open: false }));
    try {
      debugLog('Continue button clicked - raising stop-loss threshold');
      const res = await fetch(`${API_BASE}/continue-trading`, { method: 'POST' });
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
        tradesCount={trades.length}
        apiStatus={apiStatus}
        tradingMode={portfolio.tradingMode}
        dayHigh={dayStats.high}
        dayLow={dayStats.low}
        notifications={notifications}
        unreadCount={unreadCount}
        markAllRead={markAllRead}
        onOpenPaperModal={() => setPaperModalOpen(true)}
        onOpenSettings={() => setSettingsOpen(true)}
      />

      <MobilePortfolioCard totalCapital={displayCapital} dailyProfit={portfolio.dailyProfit} tradesCount={trades.length} />

      <main className="flex-grow p-2 lg:p-4 space-y-3">
        <ChartPanel
          pairSelector={pairSelector}
          chartContainerRef={chartContainerRef}
          volumeContainerRef={volumeContainerRef}
          timeframe={timeframe}
          switchTimeframe={switchTimeframe}
          readouts={readouts}
        />

        <LiveTradesPanel trades={trades} activePair={activeTradesPair} closeTrade={closeTrade} />
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
    </div>
  );
}
