export const TRADING_PAIRS = [
  { symbol: 'BTC', icon: '₿', color: '#f7931a', starred: true, price: 62281.7 },
  { symbol: 'ETH', icon: 'Ξ', color: '#627eea', starred: true, price: 1742.08 },
  { symbol: 'XRP', icon: 'X', color: '#25a768', starred: true, price: 1.085 },
  { symbol: 'LTC', icon: 'Ł', color: '#345d9d', starred: false, price: 43.79 },
  // XMR is DELISTED from both Bybit and Binance spot (no live market data on
  // either source) - kept in the dropdown per product decision, but it has no
  // symbol mapping below, so the chart falls back to simulated data and the
  // backend runs its synthetic price feed for it. No TAAPI scans / auto trades
  // can fire on it.
  { symbol: 'XMR', icon: 'ɱ', color: '#f26822', starred: false, price: 165.0 },
];

// Bybit USDT perpetual (linear) — matches backend signal engine.
export const BYBIT_SYMBOL_MAP = {
  BTC: 'BTCUSDT',
  ETH: 'ETHUSDT',
  XRP: 'XRPUSDT',
  LTC: 'LTCUSDT',
};

export function getBybitSymbol(pairLabel) {
  const symbol = (pairLabel || '').split('/')[0];
  return BYBIT_SYMBOL_MAP[symbol] || null;
}

export function getPairMeta(pair) {
  const symbol = (pair || '').split('/')[0];
  return TRADING_PAIRS.find((p) => p.symbol === symbol) || { symbol, icon: symbol.charAt(0), color: '#6b7280' };
}

export function fmtNum(num) {
  const decimals = Math.abs(num) < 1 ? 4 : 2;
  return Number(num).toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}
