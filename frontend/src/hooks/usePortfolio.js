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
    seasonProfit: 0,
    seasonProfitPct: 0,
    seasonActive: false,
    sessionStatsFrozen: false,
    sessionOpenPositions: 0,
    isActive: false,
    tradingMode: null,
    agentChat: [],
    blueBoxOverlay: null,
    sessionSchedule: null,
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
          seasonProfit: data.ai_season_profit ?? 0,
          seasonProfitPct: data.ai_season_profit_pct ?? 0,
          seasonActive: Boolean(data.ai_season_active),
          sessionStatsFrozen: Boolean(data.session_stats_frozen),
          sessionOpenPositions: Number(data.trades) || 0,
          isActive: data.is_active,
          tradingMode: data.trading_mode,
          agentChat: data.agent_chat || [],
          blueBoxOverlay: data.blue_box_overlay || null,
          sessionSchedule: data.session_schedule || null,
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
