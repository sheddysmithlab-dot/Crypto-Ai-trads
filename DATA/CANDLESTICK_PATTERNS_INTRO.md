# Candlestick Patterns — Introduction (38 Patterns)

Source: "38 Candlestick Patterns for Pro Traders — Bullish And Bearish Chart Patterns" (Groww).

This file is the **introduction / vocabulary** layer only. It lists every pattern
name, its type, candle structure, and what it indicates. Detection rules and
risk:reward are wired separately in `volume_spread_system.py` (`PATTERN_LABELS`
and `evaluate_uvss`). Until those rules are added, the engine still returns
`NO_TRADE` — this document is the reference the agent reads first.

Conventions used below:
- **Bullish candle** = close > open (green).
- **Bearish candle** = close < open (red).
- **Body** = |close − open|.
- **Upper shadow** = high − max(open, close).
- **Lower shadow** = min(open, close) − low.
- **Engulf** = second body fully covers first body (open2 ≤ close1 and close2 ≥ open1 for bullish engulf; reverse for bearish).
- "Gap up/down" = empty space between candles' ranges (rare on 24/7 crypto — treat as body-only gap on intraday timeframes).

---

## A. Bullish Patterns (21)

### 1. Bullish Engulfing Pattern
- **Type:** Bullish reversal (downtrend).
- **Structure:** 2 candles — small bearish candle, then a larger bullish candle whose body completely engulfs the prior bearish body.
- **Indicates:** Strong buying strength; sellers losing control.

### 2. Hammer Pattern
- **Type:** Bullish reversal (downtrend).
- **Structure:** 1 candle — small body near the top, long lower shadow (≈2–3× body), little/no upper shadow.
- **Indicates:** Buyers became active at lower prices; potential upside.

### 3. Morning Star Pattern
- **Type:** Bullish reversal (downtrend).
- **Structure:** 3 candles — long bearish, small-bodied candle (either color), long bullish closing well into the first candle's body.
- **Indicates:** Sellers losing control, buyers taking over.

### 4. Piercing Line Pattern
- **Type:** Bullish reversal (downtrend).
- **Structure:** 2 candles — strong bearish, then bullish that opens below prior close but closes **above the midpoint (50%)** of the prior bearish body.
- **Indicates:** Buyers stepping in, reversing the downtrend.

### 5. Bullish Harami Pattern
- **Type:** Bullish reversal (downtrend).
- **Structure:** 2 candles — large bearish, then small bullish body fully contained inside the prior bearish body.
- **Indicates:** Selling pressure decreasing; possible upside reversal.

### 6. Three White Soldiers Pattern
- **Type:** Bullish reversal (downtrend).
- **Structure:** 3 consecutive long bullish candles with small wicks; each opens inside prior body and closes higher.
- **Indicates:** Transition from downtrend to uptrend.

### 7. Inverted Hammer Pattern
- **Type:** Bullish reversal (downtrend).
- **Structure:** 1 candle — small body at the bottom, long upper shadow, little/no lower shadow.
- **Indicates:** Buyers attempted to push price higher during the session.

### 8. Dragonfly Doji (Bullish)
- **Type:** Bullish reversal (downtrend).
- **Structure:** 1 candle — almost no body (doji), long lower shadow, open/high/close near same level at the top.
- **Indicates:** Indecision resolved toward buyers; similar to Hammer but with no body.

### 9. Bullish Abandoned Baby
- **Type:** Bullish reversal (downtrend).
- **Structure:** 3 candles — long bearish, doji that gaps down, long bullish that gaps up.
- **Indicates:** Sharp sentiment shift from bearish to bullish.

### 10. Three Inside Up Pattern
- **Type:** Bullish reversal (downtrend).
- **Structure:** 3 candles — large bearish, small bullish closing above 50% of first body, third bullish closing above first candle's open.
- **Indicates:** Potential reversal confirmed.

### 11. Three Outside Up Pattern
- **Type:** Bullish reversal (downtrend).
- **Structure:** 3 candles — small bearish, bullish that engulfs the first, second bullish closing higher than the engulfing candle's close.
- **Indicates:** Confirms strength of bullish reversal.

### 12. Bullish Kicker Pattern
- **Type:** Bullish reversal.
- **Structure:** 2 candles — long bearish, then even longer bullish that opens above prior close (gap up) and keeps rising.
- **Indicates:** Sudden buyer takeover; strong sentiment reversal.

### 13. Tweezer Bottom Pattern
- **Type:** Bullish reversal (downtrend).
- **Structure:** 2 candles — roughly equal-sized bearish then bullish, both touching the same low (matching lows).
- **Indicates:** Market found a support level.

### 14. Rising Three Methods Pattern
- **Type:** Bullish continuation (uptrend).
- **Structure:** 5 candles — long bullish, three small bearish staying within the first candle's range, final long bullish closing above the first candle's high.
- **Indicates:** Brief pause before uptrend resumes; buyers still in control.

### 15. Concealing Baby Swallow Pattern
- **Type:** Bullish reversal (downtrend).
- **Structure:** 4 candles — two long bearish, a small candle that gaps down, then a long bearish candle that engulfs the small third candle.
- **Indicates:** Selling pressure exhausting despite downward move.

### 16. Mat Hold Pattern (Bullish)
- **Type:** Bullish continuation (uptrend).
- **Structure:** 5 candles — long bullish, three small bearish within first candle's range, final long bullish closing above first candle's high.
- **Indicates:** Brief consolidation before uptrend continues.

### 17. Bullish Separating Lines Pattern
- **Type:** Bullish continuation.
- **Structure:** 2 candles — bearish, then bullish that opens at the same level as the prior bearish candle's open.
- **Indicates:** Uptrend continues after a brief pause.

### 18. Bullish Belt Hold Pattern
- **Type:** Bullish reversal (downtrend).
- **Structure:** 1 candle — opens at the low (no lower shadow), closes near the high.
- **Indicates:** Strong buying pressure; potential shift to uptrend.

### 19. Three-Line Strike Pattern (Bullish)
- **Type:** Bullish continuation.
- **Structure:** 4 candles — three consecutive bullish (each closing higher), then a long bearish that opens above the third close and closes below the first candle's open.
- **Indicates:** Brief profit-taking pause before uptrend resumes.

### 20. Ladder Bottom Pattern
- **Type:** Bullish reversal (downtrend).
- **Structure:** 5 candles — three long bearish, a small candle with long upper wick, then a long bullish candle (often gapping up) closing higher.
- **Indicates:** Bearish trend ending; buyers taking control.

### 21. Meeting Lines Pattern (Bullish)
- **Type:** Bullish reversal (downtrend).
- **Structure:** 2 candles — long bearish, then long bullish that opens lower but closes at the same level as the bearish candle's close.
- **Indicates:** Shift from selling pressure to buying pressure.

---

## B. Bearish Patterns (17)

### 22. Bearish Engulfing Pattern
- **Type:** Bearish reversal (uptrend).
- **Structure:** 2 candles — small bullish, then large bearish body that engulfs the prior bullish body.
- **Indicates:** Sellers have taken control; price likely to fall.

### 23. Bearish Belt Hold Pattern
- **Type:** Bearish reversal (uptrend).
- **Structure:** 1 candle — opens at the high (no upper shadow), closes near the low with little/no lower shadow.
- **Indicates:** Strong selling pressure; reversal to downtrend.

### 24. Three Black Crows Pattern
- **Type:** Bearish continuation / reversal.
- **Structure:** 3 consecutive long bearish candles with small wicks; each opens inside prior body and closes lower.
- **Indicates:** Strong, steady selling pressure; downtrend continues.

### 25. Bearish Three-Line Strike Pattern
- **Type:** Bearish continuation.
- **Structure:** 4 candles — three consecutive bearish (each closing lower), then a long bullish that opens below the third close and closes above the first candle's open.
- **Indicates:** Short pullback, then downtrend continues.

### 26. Hanging Man Pattern (Bearish)
- **Type:** Bearish reversal (uptrend).
- **Structure:** 1 candle — small body at the top, long lower shadow, appears at the top of an uptrend.
- **Indicates:** Selling pressure rising; uptrend may be ending.

### 27. Upside Gap Two Crows Pattern
- **Type:** Bearish reversal (uptrend).
- **Structure:** 3 candles — long bullish, small bearish that gaps up, second bearish that opens above the first bearish open and closes below its close (but stays above the first bullish close).
- **Indicates:** Reversal or consolidation before downtrend.

### 28. Bearish Evening Star Pattern
- **Type:** Bearish reversal (uptrend).
- **Structure:** 3 candles — long bullish, small-bodied star that gaps up, long bearish closing well into the first candle's body.
- **Indicates:** Uptrend losing momentum; downtrend may start.

### 29. Bearish Shooting Star Pattern
- **Type:** Bearish reversal (uptrend).
- **Structure:** 1 candle — small body at the bottom, long upper shadow (≈2–3× body), little/no lower shadow, at top of uptrend.
- **Indicates:** Buyers failed to hold highs; sellers pushed back near open.

### 30. Bearish Harami Pattern
- **Type:** Bearish reversal (uptrend).
- **Structure:** 2 candles — large bullish, then small bearish body fully contained inside the prior bullish body.
- **Indicates:** Buying pressure weakening; momentum stalling.

### 31. Bearish Doji Star Pattern
- **Type:** Bearish reversal (uptrend).
- **Structure:** 2 candles — long bullish, then a doji (very small body) appearing above the prior close.
- **Indicates:** Indecision; bearish candle next confirms reversal.

### 32. Bearish Abandoned Baby Pattern
- **Type:** Bearish reversal (uptrend).
- **Structure:** 3 candles — long bullish, doji that gaps up, long bearish that gaps down from the doji.
- **Indicates:** Sharp reversal; start of a downtrend.

### 33. Bearish Tweezer Top Pattern
- **Type:** Bearish reversal (uptrend).
- **Structure:** 2 (or more) candles with matching highs at the top of an uptrend (typically bullish then bearish).
- **Indicates:** Upward momentum weakening; reversal to bearish.

### 34. Bearish Kicker Pattern
- **Type:** Bearish reversal.
- **Structure:** 2 candles — long bullish, then long bearish that opens below the prior bullish open and closes lower.
- **Indicates:** Dramatic sentiment shift; sudden downside reversal.

### 35. Three Inside Down Pattern (Bearish)
- **Type:** Bearish reversal (uptrend).
- **Structure:** 3 candles — large bullish, small bearish inside the first body, then another bearish closing lower than the second.
- **Indicates:** Sellers gaining dominance; downtrend likely.

### 36. Bearish Three Outside Down Pattern
- **Type:** Bearish reversal (uptrend).
- **Structure:** 3 candles — small bullish, large bearish that engulfs the first, then another bearish closing lower.
- **Indicates:** Confirms strength of bearish reversal.

### 37. Bearish Mat Hold Pattern
- **Type:** Bearish continuation (downtrend).
- **Structure:** 5 candles — long bearish, three small bullish within first candle's range, final long bearish closing below the first candle's close.
- **Indicates:** Brief pause before downtrend continues.

### 38. Dark Cloud Cover Pattern
- **Type:** Bearish reversal (uptrend).
- **Structure:** 2 candles — long bullish, then bearish that opens above prior high (gap up) but closes below the midpoint of the prior bullish body.
- **Indicates:** Uptrend ending; downtrend may begin.

---

## Next step
This is the **introduction** layer. The next training step is to choose which
patterns to actually detect, assign each a short code (e.g. `BULL_ENGULF`,
`HAMMER`, `EVENING_STAR`), define exact numeric thresholds, and add them to
`PATTERN_LABELS` + `evaluate_uvss()` in `volume_spread_system.py`.
