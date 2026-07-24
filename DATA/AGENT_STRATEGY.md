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

## Detection → fire allowlist (Bible priority)

**Fire:** `BULL_ENGULF`, `BEAR_ENGULF`, `HAMMER`, `PIN_BULL`, `SHOOTING_STAR`, `PIN_BEAR`, `MORNING_STAR`, `EVENING_STAR`, `INSIDE_UP`, `INSIDE_DOWN`, `PIERCING`, `DARK_CLOUD`

**Detect but do not fire:** harami, tweezers, dragonfly/gravestone, three white/black, belt, marubozu

Trend filters: EMA50/EMA200 + local slope. Same-bar bull+bear conflict → `NO_TRADE`.

## Bible read (auto)
Each pattern maps to a Bible section id (`PATTERN_BIBLE_KEY`). On signal, agent
fetches that section in microseconds and logs it in System Log / AI confirm.

## ML fire discipline (cost-aware ON + entry rules)
- Gate **ON** — λ=1.2, abs candle range ≥ 0.02%
- Min pattern strength ≥ 0.75
- **3-candle entry:** detect on bar1 close → confirm direction on bar2 → fire at bar3 open
- **One auto fire per candle**; ≥3 bars between entries
- Volume ≥ 1.6× Vol MA, vs prev ≥ 1.15×
- Block opposite side while an auto position is open
- Whale: ≥100 BTC, poll **60s** — **same 3-candle queue** (no instant fire)
- Prefer chart **5m** (1m optional watch); PDF/Bible top-down favors higher TF

## Exits
Profit lock: activate +0.40% gross, 1.5× trail from peak, hard +1.2%.
Stop: **structure SL** (pattern+confirm swing invalidation) + **TF hard stop**
(1m −0.60% / 5m −0.75% / 15m −1.0% / 1h −1.2%). Reverse-% trail removed.

## Whale flow (merged into BTC/USDT)
- No separate UI pair — runs with BTC candle automation when active pair is BTC/USDT
- Source: [WhaleBotAlerts](https://t.me/s/WhaleBotAlerts)
- SHORT: Unknown → Exchange, amount ≥ 100 BTC
- LONG: Exchange → Unknown, amount ≥ 100 BTC
- First poll seeds existing alerts (no historical fire); only NEW alerts fire
- Loop: `main.py` `whale_alert_loop` (parallel to `auto_buy_loop`)
