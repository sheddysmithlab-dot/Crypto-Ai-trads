import { useEffect, useRef, useState } from 'react';
import { backendWsUrl } from '../config/api';

// Single source of truth for portfolio value, daily PnL, bot active state,
// trading mode (paper/live) and the automatic emergency-exit trigger.
export function usePortfolio(setConnected, { onEmergencyTriggered } = {}) {
  const [portfolio, setPortfolio] = useState({
    totalCapital: 0,
    dailyProfit: 0,
    dailyProfitPct: 0,
    seasonProfit: 0,
    seasonProfitPct: 0,
    seasonActive: false,
    isActive: false,
    tradingMode: null,
  });
  const lastEmergencyState = useRef(false);
  const reconnectTimer = useRef(null);
  const onEmergencyRef = useRef(onEmergencyTriggered);
  onEmergencyRef.current = onEmergencyTriggered;

  useEffect(() => {
    function connect() {
      const ws = new WebSocket(backendWsUrl('/ws/portfolio'));

      ws.onopen = () => setConnected('portfolio', true);

      ws.onmessage = (event) => {
        const data = JSON.parse(event.data);

        setPortfolio({
          totalCapital: data.total_portfolio_value,
          dailyProfit: data.daily_profit,
          dailyProfitPct: data.daily_profit_pct,
          seasonProfit: data.ai_season_profit ?? 0,
          seasonProfitPct: data.ai_season_profit_pct ?? 0,
          seasonActive: Boolean(data.ai_season_active),
          isActive: data.is_active,
          tradingMode: data.trading_mode,
        });

        // Only show the risk modal on transition from false -> true, not every tick.
        if (data.emergency && !lastEmergencyState.current) {
          console.warn(
            `[EMERGENCY] Modal triggered - Portfolio drop: ${data.portfolio_drop_pct}%, Threshold: ${data.max_loss_pct}%`
          );
          onEmergencyRef.current?.(data.portfolio_drop_pct, data.max_loss_pct || 2.5);
        }
        lastEmergencyState.current = data.emergency;
      };

      ws.onclose = () => {
        setConnected('portfolio', false);
        console.warn('Portfolio WebSocket closed, reconnecting...');
        reconnectTimer.current = setTimeout(connect, 2000);
      };

      return ws;
    }

    const ws = connect();
    return () => {
      clearTimeout(reconnectTimer.current);
      ws.onclose = null;
      ws.close();
    };
  }, [setConnected]);

  return portfolio;
}
