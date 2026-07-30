# Trade Policy (live) — FIRE ENGINE V3.1

**Engine:** [`backend/fire_trade_engine.py`](backend/fire_trade_engine.py) via [`backend/fire_engine_bridge.py`](backend/fire_engine_bridge.py)  
**Spec:** `pettern -4.txt` Chapters 1–7 (ML/DQN chapters not in live bot)

## Live pipeline
1. Closed-candle Bybit OHLCV (watchlist pairs; lookback ≥ 120 for EMA 95)
2. Patterns (15+) + shadow psychology + market structure (skip sideways; soft-block mid-retracement unless strong reversal)
3. Tech bias: EMA 6/95, MACD+ADX, RSI soft filter
4. Weighted confluence (patterns ~0.45, shadow ~0.20, structure ~0.15, tech ~0.20) → fire if ≥ `FIRE_ENGINE_MIN_CONFLUENCE` (default 0.72)
5. SL = pattern extreme ± ATR pad · TP = 1:2 R:R
6. Auto-exit when mark hits SL or TP (auto trades only)

## Knobs (`.env`)
`FIRE_ENGINE_LOOKBACK`, `FIRE_ENGINE_MIN_CONFLUENCE`, `FIRE_ENGINE_MIN_EDGE`, `FIRE_ENGINE_MIN_CONFIDENCE`, `FIRE_ENGINE_ATR_SL_PAD`, `FIRE_ENGINE_RR`, `FIRE_ENGINE_SKIP_SIDEWAYS`

## Manual
Manual open/close and emergency sell-all still work.

Old PATTERN_1 / PATTERN_2 / UVSS / trailing profit-book paths stay wiped.
