export const TRADING_PAIRS = [
  { symbol: 'BTC', icon: '₿', color: '#f7931a', starred: true, price: 68415.7 },
  { symbol: 'SOL', icon: 'S', color: '#14f195', starred: true, price: 145.32 },
  { symbol: 'ETH', icon: 'Ξ', color: '#627eea', starred: true, price: 3480.55 },
  { symbol: 'MNT', icon: 'M', color: '#a855f7', starred: true, price: 0.982 },
  { symbol: 'SLX', icon: 'SL', color: '#6b7280', starred: false, price: 1.245 },
  { symbol: 'HYPE', icon: 'H', color: '#ec4899', starred: false, price: 28.15 },
  { symbol: 'GRAM', icon: 'G', color: '#14b8a6', starred: false, price: 0.0452 },
  { symbol: 'CSPR', icon: 'C', color: '#ef4444', starred: false, price: 0.0186 },
  { symbol: 'BNB', icon: 'B', color: '#f3ba2f', starred: false, price: 612.3 },
];

export const BINANCE_SYMBOL_MAP = { BTC: 'btcusdt', ETH: 'ethusdt', SOL: 'solusdt', BNB: 'bnbusdt' };

export function getBinanceSymbol(pairLabel) {
  const symbol = (pairLabel || '').split('/')[0];
  return BINANCE_SYMBOL_MAP[symbol] || null;
}

export function getPairMeta(pair) {
  const symbol = (pair || '').split('/')[0];
  return TRADING_PAIRS.find((p) => p.symbol === symbol) || { symbol, icon: symbol.charAt(0), color: '#6b7280' };
}

export function fmtNum(num) {
  const decimals = Math.abs(num) < 1 ? 4 : 2;
  return Number(num).toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}
