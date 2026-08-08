# Trade Policy (live)

## Timeframe routing
- **1M (and 30s)** → [`backend/1min.py`](backend/1min.py) via [`backend/min1_engine.py`](backend/min1_engine.py)
- **5M+** → [`backend/fire_trade_engine.py`](backend/fire_trade_engine.py) Fire Engine v3.1

## 1M fade — ENTRY
1. Closed 1m candle: detect **Doji** or **Bullish/Bearish Engulfing**
2. Trade the **opposite** side (fade)
3. At most **one new trade per minute** (global across watchlist)
4. Hold up to **10** open trades

## 1M fade — EXIT
1. **No individual SL/TP**
2. **No** trailing profit-book / structure stop / TF hard-stop
3. When **10** are open and **combined net P&L after fees ≥ +2% of batch capital** → close all
4. Reset batch → open next 10 → repeat
5. Manual close + emergency sell-all always available

Knobs: `MIN1_MAX_OPEN`, `MIN1_BATCH_PROFIT_PCT`, `MIN1_SIZE_FRAC`, `MIN1_LOOKBACK`, `MIN1_DOJI_BODY_RATIO`

## Fire Engine (non-1M) — EXIT
Patterns + shadow + structure + EMA/MACD/ADX/RSI confluence → SL/TP 1:2 on mark.

## Manual
Manual open/close and emergency sell-all still work.
