# Trade Policy (live)

**Authoritative strategy:** [`DATA/AGENT_STRATEGY.md`](DATA/AGENT_STRATEGY.md)  
**Short ops spec:** [`DATA/TRADING POLICIES.txt`](DATA/TRADING%20POLICIES.txt)

## Live pipeline (normal pairs)
1. Detect candle pattern (`backend/volume_spread_system.py`)
2. Bible RAM fetch (`candlestick_bible_memory`)
3. Cost-aware gate **ON** (mid: λ=0.55, abs range ≥0.01%) + strength≥0.5 / 2-bar cooldown / one-per-candle
4. Fire BUY→LONG / SELL→SHORT (`main.py` auto_buy_loop)

## Whale flow (merged into BTC/USDT)
- Source: [WhaleBotAlerts](https://t.me/s/WhaleBotAlerts)
- SHORT: Unknown → Exchange (≥100 BTC)
- LONG: Exchange → Unknown (≥100 BTC)
- Poll every **60 seconds (1m)** — rules not loosened with candle gate
- Runs alongside candle patterns when active pair is BTC/USDT (`whale_alert_loop`)

## Exits
Profit lock: +0.15% activate, +0.02% steps from peak. No SL auto-exit.

## Size
Auto fire uses ~10% of available capital (see `AUTO_TRADE_CAPITAL_PCT` in `main.py`).
