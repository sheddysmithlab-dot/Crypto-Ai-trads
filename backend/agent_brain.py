"""Agent brain — Fire Engine + liquidity-trap curriculum (1m/5m)."""
from __future__ import annotations

from typing import Any

try:
    from fire_engine_bridge import ENTRY_PATTERN_NAME, entry_pattern_profile
except Exception:
    ENTRY_PATTERN_NAME = "FIRE_ENGINE_V3"

    def entry_pattern_profile() -> dict[str, Any]:
        return {"name": ENTRY_PATTERN_NAME, "engine": "fire_trade_engine"}

try:
    import scalp_1m5m as scalp
except Exception:
    scalp = None  # type: ignore


PIPELINE_STEPS = (
    "1_market_structure",
    "2_candlestick_patterns",
    "3_shadow_psychology",
    "4_tech_confluence",
    "5_atr_sl_tp_fire",
)

# Trained trap pipeline (matches scalp_1m5m liquidity_trap_v1)
SCALP_PIPELINE_STEPS = (
    "1_recent_high_20",
    "2_breakout_bait",
    "3_sweep_reclaim",
    "4_strong_rejection_wick_1_5x",
    "5_short_sl_atr_tp_1_2",
    "6_no_trade_filters",
)

LIQUIDITY_TRAP_CURRICULUM = """
LIQUIDITY TRAP BRAIN (1m/5m) — do NOT buy the breakout; sell the trap.

STEP 1: Recent_High = Highest(High, 20 prior candles)
  → 90% retail plan breakout buys / stops above this line.

STEP 2 BAIT: Breakout_Happen = Current_High > Recent_High

STEP 3 TRAP: Reclaim_Happen = Current_Close < Recent_High
  → price could not hold above the line.

STEP 4 REJECTION: UpperShadow > Body * 1.5
  → strong wick psychology (10% club confirmation).

STEP 5 FIRE SHORT when all true:
  SL = Current_High + ATR*0.5
  TP = 1:2 R:R (then live manager: 50%@1R → BE → trail)

LONG mirror: Recent_Low, Low < Low, Close > Low, lower wick > 1.5x body,
  SL = Low - ATR*0.5.
"""


def enrich_signal(result: dict[str, Any], *, max_ml_chars: int = 900) -> dict[str, Any]:
    out = dict(result)
    scalp_mode = bool(result.get("scalp") or (result.get("entry_pattern") == getattr(scalp, "ENTRY_PATTERN_NAME", "")))
    out["brain"] = {
        "pipeline": list(SCALP_PIPELINE_STEPS if scalp_mode else PIPELINE_STEPS),
        "entry_pattern": result.get("entry_pattern") or ENTRY_PATTERN_NAME,
        "pattern_label": result.get("pattern"),
        "confidence": result.get("confidence") or result.get("strength"),
        "risk_reward": result.get("risk_reward"),
        "reasoning": result.get("reason"),
        "scalp": scalp_mode,
        "liquidity_sweep": result.get("liquidity_sweep"),
        "scorecard": result.get("scorecard"),
        "curriculum": "liquidity_trap_v1" if scalp_mode else None,
    }
    return out


def brain_chat_summary(enriched: dict[str, Any]) -> str:
    action = enriched.get("action")
    pattern = enriched.get("pattern") or "n/a"
    tag = "TrapBrain" if enriched.get("scalp") or enriched.get("entry_pattern") == getattr(
        scalp, "ENTRY_PATTERN_NAME", None
    ) else "FireEngine"
    if action in ("BUY", "SELL"):
        ls = enriched.get("liquidity_sweep") or {}
        steps = ""
        if ls.get("sweep"):
            steps = " [bait+reclaim+1.5xWick]"
        return (
            f"{tag}: {pattern}{steps} → {action} "
            f"(conf={enriched.get('confidence') or enriched.get('strength')})"
        )
    return f"{tag}: wait — {enriched.get('reason', 'no trap')}"


def strategy_system_blurb() -> str:
    return (
        f"AI AGENT — {ENTRY_PATTERN_NAME} + FIRE_SCALP_1M5M reverse-trap brain:\n"
        "15m+: Fire Engine SL/TP 1:2.\n"
        "1m/5m:\n"
        "1) BULL TRAP (High>R, Close<R, wick>1.5x) → SHORT ~80% (retail buy, bot shorts).\n"
        "2) BEAR TRAP (Low<S, Close>S, wick>1.5x) → LONG ~80% (retail sell, bot buys).\n"
        "3) No trap → normal Fire score >= 0.72 (do not freeze).\n"
        "4) SL=wick+/-ATR*0.5 · TP 1:2 · 50%@1R→BE→trail.\n"
        "Never skip a confirmed trap — reverse it."
    )


def trap_curriculum_text() -> str:
    return LIQUIDITY_TRAP_CURRICULUM.strip()
