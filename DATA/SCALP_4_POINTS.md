# Bull / Bear Trap → Reverse Trade (1M / 5M)

Code: `backend/scalp_1m5m.py` (`bull_bear_trap_reverse_v1`)

## Law
**Confirmed trap (~80%) → NEVER skip — REVERSE fire.**  
**No trap → normal Fire Engine ≥ 0.72.**

```
Resistance = Highest(High, 20 prior)
Support    = Lowest(Low, 20 prior)

# BULL TRAP — retail buys breakout → BOT SHORTS
IF High > Resistance AND Close < Resistance AND UpperWick > Body*1.5:
    Trap_Confidence = 80%
    Execute = SHORT
    SL = High + ATR*0.5
    TP = 1:2
    # then live: 50%@1R → BE → trail

# BEAR TRAP — retail sells breakdown → BOT LONGS
ELSE IF Low < Support AND Close > Support AND LowerWick > Body*1.5:
    Trap_Confidence = 80%
    Execute = LONG
    SL = Low - ATR*0.5
    TP = 1:2

# NO TRAP
ELSE:
    Run_Normal_0.72_Score_Logic()  # Fire Engine
```

## Hard skips only
News ±15m · volatility panic  

ADX / HTF = soft (do not freeze the bot).
