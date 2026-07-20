# Trade Policy (live)

**Authoritative strategy:** [`DATA/AGENT_STRATEGY.md`](DATA/AGENT_STRATEGY.md)  
**Short ops spec:** [`DATA/TRADING POLICIES.txt`](DATA/TRADING%20POLICIES.txt)

## Live pipeline (normal pairs)
1. Detect candle pattern (`backend/volume_spread_system.py`)
2. Bible RAM fetch (`candlestick_bible_memory`)
3. Cost-aware gate **ON** (mid: λ=0.55, abs range ≥0.01%) + strength≥0.5 / no bars-gap cooldown / one-per-candle
4. Fire BUY→LONG / SELL→SHORT (`main.py` auto_buy_loop)

## Whale flow (merged into BTC/USDT)
- Source: [WhaleBotAlerts](https://t.me/s/WhaleBotAlerts)
- SHORT: Unknown → Exchange (≥100 BTC)
- LONG: Exchange → Unknown (≥100 BTC)
- Poll every **60 seconds (1m)** — rules not loosened with candle gate
- Runs alongside candle patterns when active pair is BTC/USDT (`whale_alert_loop`)

## Exits
Strict Exit: +0.20% min lock, +1.2% hard target, trail peak − 1.5×0.10%. No SL auto-exit.

## Session schedule (optional UI switch)
Mon–Fri IST auto on/off (no browser needed): Morning 05:30–08:30 · Peak Overlap 18:30–23:30 · US Core 19:30–01:30.

## Trading Statement (MySQL)
Closed/open trades persist to Hostinger MySQL (`backend/sql/schema.sql`). Profile → Trading Statement. Setup: `upload/HOSTINGER_MYSQL.md`.

## Size
Auto fire size = chart timeframe capital % of available capital:
1m **3%** · 5m **7%** · 15m **10%** · 1h **15%** · 1D **20%** (`timeframe_profiles.py`).
UI shows expected win/lose rates per TF (display guide) on chart hover/select.
