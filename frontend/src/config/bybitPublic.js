/** Public Bybit linear market data — no API key required. */
export const BYBIT_PUBLIC_REST = 'https://api.bybit.com';
export const BYBIT_PUBLIC_WS_LINEAR = 'wss://stream.bybit.com/v5/public/linear';
export const BYBIT_MARKET_CATEGORY = 'linear';

export function bybitKlineUrl(symbol, interval, limit = 200) {
  return `${BYBIT_PUBLIC_REST}/v5/market/kline?category=${BYBIT_MARKET_CATEGORY}&symbol=${symbol}&interval=${interval}&limit=${limit}`;
}

export function bybitRecentTradeUrl(symbol, limit = 1000) {
  return `${BYBIT_PUBLIC_REST}/v5/market/recent-trade?category=${BYBIT_MARKET_CATEGORY}&symbol=${symbol}&limit=${limit}`;
}

export function bybitPublicTradeTopic(symbol) {
  return `publicTrade.${symbol}`;
}
