"""Agent brain — Fire Engine v3.1 (patterns + structure + indicators)."""
from __future__ import annotations

from typing import Any

try:
    from fire_engine_bridge import ENTRY_PATTERN_NAME, entry_pattern_profile
except Exception:
    ENTRY_PATTERN_NAME = "FIRE_ENGINE_V3"

    def entry_pattern_profile() -> dict[str, Any]:
        return {"name": ENTRY_PATTERN_NAME, "engine": "fire_trade_engine"}


PIPELINE_STEPS = (
    "1_market_structure",
    "2_candlestick_patterns",
    "3_shadow_psychology",
    "4_tech_confluence",
    "5_atr_sl_tp_fire",
)


def enrich_signal(result: dict[str, Any], *, max_ml_chars: int = 900) -> dict[str, Any]:
    out = dict(result)
    out["brain"] = {
        "pipeline": list(PIPELINE_STEPS),
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
    if action in ("BUY", "SELL"):
        return (
            f"FireEngine: {pattern} → {action} "
            f"(conf={enriched.get('confidence') or enriched.get('strength')})"
        )
    return f"FireEngine: no setup — {enriched.get('reason', 'skip')}"


def strategy_system_blurb() -> str:
    return (
        f"AI AGENT — {ENTRY_PATTERN_NAME} (Live Fire Engine v3.1):\n"
        "1) Market structure filter (skip sideways; soft-block weak retracement entries).\n"
        "2) DETECT 15+ candlestick patterns + shadow psychology on closed bars.\n"
        "3) EMA/MACD/ADX/RSI tech bias → weighted confluence gate.\n"
        "4) FIRE LONG/SHORT with pattern-extreme SL + ATR pad, TP at 1:2 R:R.\n"
        "5) Auto-exit when mark hits SL or TP (manual/emergency still available)."
    )
