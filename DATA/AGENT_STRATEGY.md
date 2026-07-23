# Agent Strategy — Deep Merge (3 PDFs → Fire)

## Pipeline

```
closed candle → DETECT pattern → READ Bible section → ML cost-aware gate → FIRE
```

| Step | Source | Module |
|------|--------|--------|
| Detect | 38 patterns + Bible structures | `volume_spread_system.py` |
| Read | Candlestick Trading Bible | `candlestick_bible_memory.py` |
| Gate | ML paper cost-aware filter | `trading_policy.py` + `ml_trading_memory.py` |
| Fire | Bybit / paper | `main.py` `auto_buy_loop` |

## Detection rules (active codes)

**Reversal / pin family:** `HAMMER`, `PIN_BULL`, `SHOOTING_STAR`, `PIN_BEAR`, `DRAGONFLY`, `GRAVESTONE`  
**Engulf family:** `BULL_ENGULF`, `BEAR_ENGULF`, `PIERCING`, `DARK_CLOUD`  
**Multi-candle:** `MORNING_STAR`, `EVENING_STAR`, `THREE_WHITE`, `THREE_BLACK`  
**Bible tactics:** `INSIDE_UP`, `INSIDE_DOWN`, `BULL_HARAMI`, `BEAR_HARAMI`, `TWEEZER_BOT`, `TWEEZER_TOP`  
**Continuation:** `MBZ_L`, `MBZ_S`, `BULL_BELT`, `BEAR_BELT`

Trend filters: EMA50/EMA200 + local slope. Same-bar bull+bear conflict → `NO_TRADE`.

## Bible read (auto)
Each pattern maps to a Bible section id (`PATTERN_BIBLE_KEY`). On signal, agent
fetches that section in microseconds and logs it in System Log / AI confirm.

## ML fire discipline (cost-aware ON + entry rules)
- Gate **ON** — λ=3.0, abs candle range ≥ 0.06%
- Min pattern strength ≥ 1.5
- **One auto fire per candle**; candle gap OFF (0 bars)
- Volume hard rule: ≥3× Vol MA and rel_vol ≥ 3× rel_candle — else NO TRADE
- Block opposite side while an auto position is open
- Whale: ≥100 BTC, poll **60s**

## Exits (unchanged code)
Profit lock: activate +0.15% gross, step +0.02% from peak, sell on retreat.
No SL auto-exit (SL used for sizing / reference).

## Whale flow (merged into BTC/USDT)
- No separate UI pair — runs with BTC candle automation when active pair is BTC/USDT
- Source: [WhaleBotAlerts](https://t.me/s/WhaleBotAlerts)
- SHORT: Unknown → Exchange, amount ≥ 100 BTC
- LONG: Exchange → Unknown, amount ≥ 100 BTC
- First poll seeds existing alerts (no historical fire); only NEW alerts fire
- Loop: `main.py` `whale_alert_loop` (parallel to `auto_buy_loop`)
