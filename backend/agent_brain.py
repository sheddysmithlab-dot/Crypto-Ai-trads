"""Agent brain — strategy wiped; awaiting fresh entry/exit doctrine."""
from __future__ import annotations

from typing import Any

ENTRY_PATTERN_NAME = "STRATEGY_WIPED"


def entry_pattern_profile() -> dict[str, Any]:
    return {
        "name": ENTRY_PATTERN_NAME,
        "engine": "none",
        "description": "All entry engines / SL / exits / schedule removed — paste new strategy next.",
    }


PIPELINE_STEPS: tuple[str, ...] = ()


def enrich_signal(result: dict[str, Any], *, max_ml_chars: int = 900) -> dict[str, Any]:
    out = dict(result)
    out["brain"] = {
        "pipeline": [],
        "entry_pattern": ENTRY_PATTERN_NAME,
        "pattern_label": None,
        "confidence": None,
        "risk_reward": None,
        "reasoning": result.get("reason") or "strategy wiped",
        "scalp": False,
    }
    return out


def brain_chat_summary(enriched: dict[str, Any]) -> str:
    return f"STRATEGY_WIPED: {enriched.get('reason', 'no entry engine')}"


def strategy_system_blurb() -> str:
    return (
        "AI AGENT — STRATEGY WIPED:\n"
        "1) Fire Trade Engine / bible / scalp entry logic removed.\n"
        "2) No auto stop-loss / take-profit / trailing exits.\n"
        "3) No session schedule auto on/off.\n"
        "4) Manual BUY/SELL + emergency sell-all still work.\n"
        "5) Waiting for your new fresh strategy to wire into evaluate_entry."
    )
