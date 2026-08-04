"""Agent brain — routes messaging for Fire Engine + 1M fade engine."""
from __future__ import annotations

from typing import Any

try:
    from fire_engine_bridge import ENTRY_PATTERN_NAME as FIRE_ENTRY_NAME, entry_pattern_profile as fire_profile
except Exception:
    FIRE_ENTRY_NAME = "FIRE_ENGINE_V3"

    def fire_profile() -> dict[str, Any]:
        return {"name": FIRE_ENTRY_NAME, "engine": "fire_trade_engine"}

try:
    import min1_engine
except Exception:
    min1_engine = None  # type: ignore

ENTRY_PATTERN_NAME = FIRE_ENTRY_NAME

PIPELINE_STEPS = (
    "1_market_structure",
    "2_candlestick_patterns",
    "3_shadow_psychology",
    "4_tech_confluence",
    "5_atr_sl_tp_fire",
)

PIPELINE_STEPS_1MIN = (
    "1_detect_doji_or_engulfing",
    "2_fade_opposite_side",
    "3_stack_up_to_10",
    "4_batch_exit_plus_2pct_net",
)


def entry_pattern_profile(timeframe_key: str | None = None) -> dict[str, Any]:
    if min1_engine is not None and min1_engine.is_min1_timeframe(timeframe_key or ""):
        return min1_engine.entry_pattern_profile()
    return fire_profile()


def enrich_signal(result: dict[str, Any], *, max_ml_chars: int = 900) -> dict[str, Any]:
    out = dict(result)
    engine = result.get("engine") or ""
    steps = list(PIPELINE_STEPS_1MIN) if engine == "1min" else list(PIPELINE_STEPS)
    out["brain"] = {
        "pipeline": steps,
        "entry_pattern": result.get("entry_pattern") or ENTRY_PATTERN_NAME,
        "pattern_label": result.get("pattern"),
        "confidence": result.get("confidence") or result.get("strength"),
        "risk_reward": result.get("risk_reward"),
        "reasoning": result.get("reason"),
    }
    return out


def brain_chat_summary(enriched: dict[str, Any]) -> str:
    action = enriched.get("action")
    pattern = enriched.get("pattern") or "n/a"
    engine = enriched.get("engine") or ""
    label = "1M-fade" if engine == "1min" else "FireEngine"
    if action in ("BUY", "SELL"):
        return (
            f"{label}: {pattern} → {action} "
            f"(conf={enriched.get('confidence') or enriched.get('strength')})"
        )
    return f"{label}: no setup — {enriched.get('reason', 'skip')}"


def strategy_system_blurb(timeframe_key: str | None = None) -> str:
    if min1_engine is not None and min1_engine.is_min1_timeframe(timeframe_key or "1m"):
        return (
            f"AI AGENT — {min1_engine.ENTRY_PATTERN_NAME} (1-minute fade):\n"
            "1) On each closed 1m candle detect Doji or Engulfing.\n"
            "2) Trade the OPPOSITE side (fade).\n"
            f"3) Hold up to {min1_engine.MAX_OPEN} trades (one new entry per minute).\n"
            f"4) When batch net P&L after fees ≥ +{min1_engine.BATCH_PROFIT_PCT}% of batch capital → close all.\n"
            "5) Repeat next batch. Other TFs use Fire Engine."
        )
    return (
        f"AI AGENT — {ENTRY_PATTERN_NAME} (Live Fire Engine v3.1):\n"
        "1) Market structure filter (skip sideways; soft-block weak retracement entries).\n"
        "2) DETECT 15+ candlestick patterns + shadow psychology on closed bars.\n"
        "3) EMA/MACD/ADX/RSI tech bias → weighted confluence gate.\n"
        "4) FIRE LONG/SHORT with pattern-extreme SL + ATR pad, TP at 1:2 R:R.\n"
        "5) Auto-exit when mark hits SL or TP (manual/emergency still available).\n"
        "Note: Select 1M timeframe to switch to the separate 1min fade engine."
    )
