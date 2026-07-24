export const TRADING_PAIRS = [
  { symbol: 'BTC', icon: '₿', color: '#f7931a', starred: true, price: 95000 },
  { symbol: 'SOL', icon: '◎', color: '#9945ff', starred: true, price: 150 },
  { symbol: 'DOGE', icon: 'Ð', color: '#c2a633', starred: true, price: 0.15 },
  { symbol: 'BNB', icon: 'B', color: '#f3ba2f', starred: false, price: 600 },
  { symbol: 'ADA', icon: 'A', color: '#0033ad', starred: false, price: 0.6 },
  { symbol: 'AVAX', icon: 'A', color: '#e84142', starred: false, price: 30 },
  { symbol: 'LINK', icon: '⬡', color: '#2a5ada', starred: false, price: 15 },
  { symbol: 'DOT', icon: '●', color: '#e6007a', starred: false, price: 6 },
  { symbol: 'POL', icon: 'P', color: '#8247e5', starred: false, price: 0.4 },
  { symbol: 'NEAR', icon: 'N', color: '#00c08b', starred: false, price: 4 },
  { symbol: 'ATOM', icon: '⚛', color: '#2e3148', starred: false, price: 8 },
  { symbol: 'UNI', icon: 'U', color: '#ff007a', starred: false, price: 8 },
  { symbol: 'APT', icon: 'A', color: '#000000', starred: false, price: 8 },
  { symbol: 'ARB', icon: 'A', color: '#28a0f0', starred: false, price: 0.5 },
  { symbol: 'OP', icon: 'O', color: '#ff0420', starred: false, price: 1.5 },
  { symbol: 'SUI', icon: 'S', color: '#4da2ff', starred: false, price: 2 },
  { symbol: 'PEPE', icon: '🐸', color: '#3d9a3d', starred: false, price: 0.01 },
  { symbol: 'WIF', icon: 'W', color: '#d4a017', starred: false, price: 1.5 },
  { symbol: 'BONK', icon: 'B', color: '#f7931a', starred: false, price: 0.02 },
];

// Bybit USDT perpetual (linear) — matches backend signal engine.
// POL (not MATIC). PEPE/BONK trade as 1000× contracts on Bybit.
export const BYBIT_SYMBOL_MAP = {
  BTC: 'BTCUSDT',
  SOL: 'SOLUSDT',
  DOGE: 'DOGEUSDT',
  BNB: 'BNBUSDT',
  ADA: 'ADAUSDT',
  AVAX: 'AVAXUSDT',
  LINK: 'LINKUSDT',
  DOT: 'DOTUSDT',
  POL: 'POLUSDT',
  NEAR: 'NEARUSDT',
  ATOM: 'ATOMUSDT',
  UNI: 'UNIUSDT',
  APT: 'APTUSDT',
  ARB: 'ARBUSDT',
  OP: 'OPUSDT',
  SUI: 'SUIUSDT',
  PEPE: '1000PEPEUSDT',
  WIF: 'WIFUSDT',
  BONK: '1000BONKUSDT',
};

export function pairLabelForSymbol(symbol) {
  return `${symbol}/USDT`;
}

export function getBybitSymbol(pairLabel) {
  const symbol = (pairLabel || '').split('/')[0];
  return BYBIT_SYMBOL_MAP[symbol] || null;
}

export function getPairMeta(pair) {
  const symbol = (pair || '').split('/')[0];
  return TRADING_PAIRS.find((p) => p.symbol === symbol) || { symbol, icon: symbol.charAt(0), color: '#6b7280' };
}

export function fmtNum(num) {
  const n = Number(num);
  if (!Number.isFinite(n)) return '—';
  const abs = Math.abs(n);
  let decimals = 2;
  if (abs > 0 && abs < 0.0001) decimals = 8;
  else if (abs > 0 && abs < 0.01) decimals = 6;
  else if (abs > 0 && abs < 1) decimals = 4;
  return n.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}
