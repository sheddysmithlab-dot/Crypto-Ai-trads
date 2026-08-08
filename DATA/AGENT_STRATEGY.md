# Agent Strategy — active engines

| TF | Profile | Engine |
|----|---------|--------|
| **1M** | `MIN1_FADE_V1` | [`backend/1min.py`](../backend/1min.py) — Doji/Engulf fade opposite |
| **5M+** | `FIRE_ENGINE_V3` | [`backend/fire_trade_engine.py`](../backend/fire_trade_engine.py) |

## 1M fade
```
closed 1m → Doji|Engulfing → OPPOSITE side → stack to 10
→ batch net after fees ≥ +2% of batch capital → close all → repeat
```

## Fire Engine
```
closed candle → patterns + structure + tech confluence → LONG/SHORT
→ SL pattern±ATR · TP 1:2 → mark exit
```
