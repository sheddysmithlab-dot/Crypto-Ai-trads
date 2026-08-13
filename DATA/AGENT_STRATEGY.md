# Candlestick Trading Bible — live engine

Active module: `backend/engine.py` (`CandlestickTradingBibleEngine`).

Priority order:
1. Smart-money traps (deviate & reclaim)
2. Strict 10-pattern bible recognition
3. Market structure / impulsive vs retracement / choppy filter
4. Risk: ~1–2% guidance, SL beyond wick/ATR, min 1:2 R:R TP

Auto exits hit hard SL/TP. Manual BUY/SELL and emergency sell-all remain available.
