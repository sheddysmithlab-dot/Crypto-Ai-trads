# Agent Strategy — active entry profiles

| Profile | Env | Engine |
|---------|-----|--------|
| **PATTERN_2** (default) | `ENTRY_PATTERN_NAME=PATTERN_2` | EMA + MACD/ADX (`pattern_2_*`) |
| **PATTERN_1** | `ENTRY_PATTERN_NAME=PATTERN_1` | Candlestick Bible (`pattern_1_*`) |

## PATTERN_2
```
closed candle → EMA cross / MACD+ADX → fire
```
See [`PATTERN_2.md`](PATTERN_2.md). Source: `pettern-2.txt`.

## PATTERN_1
```
closed candle → Pin/Engulf/Inside + confluence → fire
```
See [`PATTERN_1.md`](PATTERN_1.md).
