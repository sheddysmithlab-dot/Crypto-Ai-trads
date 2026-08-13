"""Entry bridge — strategy wiped (no Fire / scalp evaluation)."""
from __future__ import annotations

from typing import Any

ENTRY_PATTERN_NAME = "STRATEGY_WIPED"


def entry_pattern_profile() -> dict[str, Any]:
    return {
        "name": ENTRY_PATTERN_NAME,
        "engine": "none",
        "description": "Entry engines removed — awaiting new strategy.",
    }


def evaluate_fire_engine(
    candles: list[dict],
    timeframe_key: str,
    *,
    pair: str = "default",
) -> dict[str, Any]:
    """Stub: never fires until a new engine is wired."""
    return {
        "action": "NO_TRADE",
        "reason": "STRATEGY_WIPED — no entry engine (awaiting fresh strategy)",
        "engine": "none",
        "entry_pattern": ENTRY_PATTERN_NAME,
        "timeframe_key": timeframe_key,
        "pair": pair,
        "long_rules": [],
        "short_rules": [],
        "rules_fired": [],
    }
