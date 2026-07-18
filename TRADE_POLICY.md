# Trade Policy (live)

**Authoritative strategy:** [`DATA/AGENT_STRATEGY.md`](DATA/AGENT_STRATEGY.md)  
**Short ops spec:** [`DATA/TRADING POLICIES.txt`](DATA/TRADING%20POLICIES.txt)

## Live pipeline (normal pairs)
1. Detect candle pattern (`backend/volume_spread_system.py`) — loosened pins / local / conflict pick
2. Bible RAM fetch (`candlestick_bible_memory`)
3. ML cost-aware gate (`trading_policy` · default λ=1.0, range≥0.25×RT)
4. Fire BUY→LONG / SELL→SHORT (`main.py` auto_buy_loop)

## Whale flow (merged into BTC/USDT)
- Source: [WhaleBotAlerts](https://t.me/s/WhaleBotAlerts)
- SHORT: Unknown → Exchange (≥100 BTC)
- LONG: Exchange → Unknown (≥100 BTC)
- Runs alongside candle patterns when active pair is BTC/USDT (`whale_alert_loop`)
- No separate WHALE/BTC UI pair

## Exits
Profit lock: +0.15% activate, +0.02% steps from peak. No SL auto-exit.

## Size
Auto fire uses ~10% of available capital (see `AUTO_TRADE_CAPITAL_PCT` in `main.py`).
