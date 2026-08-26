import { useEffect, useRef, useState } from 'react';
import { backendWsUrl } from '../config/api';

// Portfolio value, daily PnL, bot active state, and trading mode (paper/live).
export function usePortfolio(setConnected) {
  const [portfolio, setPortfolio] = useState({
    totalCapital: 0,
    cashLedger: 0,
    unrealizedNetUsd: 0,
    marginInUse: 0,
    tradeNotional: 0,
    dailyProfit: 0,
    dailyProfitPct: 0,
    dailyBrokerFee: 0,
    exitedBookedUsd: 0,
    seasonProfit: 0,
    seasonProfitPct: 0,
    seasonProfitNet: 0,
    seasonProfitNetPct: 0,
    seasonActive: false,
    sessionStatsFrozen: false,
    sessionOpenPositions: 0,
    isActive: null,
    connectivityFrozen: false,
    freezeReason: null,
    oneMFeeHold: false,
    tradingReady: true,
    warmupRemainingSec: 0,
    warmupTotalSec: 20,
    bootIntroSec: 10,
    bootAnalysisSec: 10,
    tradingMode: null,
    agentChat: [],
    blueBoxOverlay: null,
    sessionSchedule: null,
    watchlist: [],
    scanPairs: [],
    momentumGateReady: false,
    momentumThresholdPct: 0,
    momentumFirePairs: [],
    momentumScores: [],
    momentumLastRefreshMs: 0,
  });
  const wsRef = useRef(null);
  const reconnectTimer = useRef(null);
  const stopped = useRef(false);

  useEffect(() => {
    stopped.current = false;

    function connect() {
      if (stopped.current) return;
      const ws = new WebSocket(backendWsUrl('/ws/portfolio'));
      wsRef.current = ws;

      ws.onopen = () => setConnected('portfolio', true);

      ws.onmessage = (event) => {
        setConnected('portfolio', true);
        const data = JSON.parse(event.data);

        setPortfolio({
          totalCapital: data.total_portfolio_value,
          cashLedger: data.capital,
          unrealizedNetUsd: data.unrealized_net_usd ?? 0,
          marginInUse: data.margin_in_use ?? 0,
          tradeNotional: data.trade_notional ?? 0,
          dailyProfit: data.daily_profit,
          dailyProfitPct: data.daily_profit_pct,
          dailyBrokerFee: data.daily_broker_fee ?? 0,
          exitedBookedUsd: data.exited_booked_usd ?? 0,
          seasonProfit: data.ai_season_profit ?? 0,
          seasonProfitPct: data.ai_season_profit_pct ?? 0,
          seasonProfitNet: data.ai_season_profit_net ?? (
            (Number(data.ai_season_profit) || 0) - (Number(data.daily_broker_fee) || 0)
          ),
          seasonProfitNetPct: data.ai_season_profit_net_pct ?? 0,
          seasonActive: Boolean(data.ai_season_active),
          sessionStatsFrozen: Boolean(data.session_stats_frozen),
          sessionOpenPositions: Number(data.trades) || 0,
          isActive: data.is_active,
          connectivityFrozen: Boolean(data.connectivity_frozen),
          freezeReason: data.freeze_reason || null,
          oneMFeeHold: Boolean(data.one_m_fee_hold),
          tradingReady: data.trading_ready !== false,
          warmupRemainingSec: Number(data.warmup_remaining_sec) || 0,
          warmupTotalSec: Number(data.warmup_total_sec) || 20,
          bootIntroSec: Number(data.boot_intro_sec) || 10,
          bootAnalysisSec: Number(data.boot_analysis_sec) || 10,
          tradingMode: data.trading_mode,
          agentChat: data.agent_chat || [],
          blueBoxOverlay: data.blue_box_overlay || null,
          sessionSchedule: data.session_schedule || null,
          watchlist: Array.isArray(data.watchlist) ? data.watchlist : [],
          scanPairs: Array.isArray(data.scan_pairs) ? data.scan_pairs : [],
          momentumGateReady: Boolean(data.momentum_gate_ready),
          momentumThresholdPct: Number(data.momentum_threshold_pct) || 0,
          momentumFirePairs: Array.isArray(data.momentum_fire_pairs) ? data.momentum_fire_pairs : [],
          momentumScores: Array.isArray(data.momentum_scores) ? data.momentum_scores : [],
          momentumLastRefreshMs: Number(data.momentum_last_refresh_ms) || 0,
        });
      };

      ws.onclose = () => {
        setConnected('portfolio', false);
        if (stopped.current) return;
        console.warn('Portfolio WebSocket closed, reconnecting...');
        reconnectTimer.current = setTimeout(connect, 2000);
      };
    }

    connect();
    return () => {
      stopped.current = true;
      clearTimeout(reconnectTimer.current);
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [setConnected]);

  return portfolio;
}
