# Liquidity Trap Brain — Curriculum (1M / 5M)

Code: `backend/scalp_1m5m.py` (`liquidity_trap_v1`) · brain: `backend/agent_brain.py`

## SHORT — sell the trap (not the breakout)

```
STEP 1  Recent_High = Highest(High, 20 prior candles)
        → where ~90% retail plans breakout buys / stops

STEP 2  Breakout_Happen = Current_High > Recent_High   // bait

STEP 3  Reclaim_Happen  = Current_Close < Recent_High  // trap

STEP 4  Strong_Rejection = UpperShadow > Body * 1.5    // wick psych

STEP 5  IF all TRUE → SHORT
        SL = Current_High + (ATR * 0.5)
        TP = 1:2 R:R
        Live manage: 50% @ 1R → SL to BE → trail rest
```

## LONG — mirror
`Recent_Low` · `Low < Low` · `Close > Low` · lower wick > 1.5× body · `SL = Low − ATR×0.5`

## Still required / soft (so bot does not freeze)
| Filter | Default |
|--------|---------|
| News ±15m / panic vol | **Hard** block |
| Clean trap (bait+reclaim+1.5×wick) | **Required** to fire · auto-passes scorecard |
| ADX | **Soft** (`SCALP_HARD_ADX=0`) — chop does not freeze |
| HTF 15m | **Soft** (`SCALP_REQUIRE_HTF=0`) — missing HTF fail-open |
| Kill-zone | Soft unless `SCALP_REQUIRE_KILLZONE=1` |

Trap direction **overrides** Fire Engine if they disagree (trap is source of truth).

## Knobs
`SCALP_SWEEP_LOOKBACK=20` · `SCALP_REJECTION_BODY_MULT=1.5` · `SCALP_ATR_SL_MULT=0.5` · `SCALP_TRAIL_RR=2`
