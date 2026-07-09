import { useEffect, useRef, useState } from 'react';
import { backendWsUrl } from '../config/api';

// Portfolio value, daily PnL, bot active state, and trading mode (paper/live).
export function usePortfolio(setConnected) {
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
  const reconnectTimer = useRef(null);

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
