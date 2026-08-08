"""1-minute fade engine (file: 1min.py).

Activated only when user selects the 1M chart timeframe.
Completely separate from Fire Engine v3.

Rules
-----
- Patterns: Doji, Bullish Engulfing, Bearish Engulfing
- Direction: OPPOSITE of the pattern bias (fade)
- Cadence: at most one new auto trade per closed 1m candle (global)
- Hold up to 10 open trades
- No individual SL/TP — batch exit when combined net P&L
  (after broker fees) >= +2% of batch capital baseline
- Then open the next batch of up to 10, and repeat
"""
from __future__ import annotations

import os
from typing import Any

ENTRY_PATTERN_NAME = "MIN1_FADE_V1"
ENGINE_NAME = "1min"

MAX_OPEN = int(os.environ.get("MIN1_MAX_OPEN", "10"))
BATCH_PROFIT_PCT = float(os.environ.get("MIN1_BATCH_PROFIT_PCT", "2.0"))
# Fraction of *available* capital used as notional per new 1m trade.
SIZE_FRAC = float(os.environ.get("MIN1_SIZE_FRAC", "0.09"))
LOOKBACK = int(os.environ.get("MIN1_LOOKBACK", "30"))
DOJI_BODY_RATIO = float(os.environ.get("MIN1_DOJI_BODY_RATIO", "0.10"))


def entry_pattern_profile() -> dict[str, Any]:
    return {
        "name": ENTRY_PATTERN_NAME,
        "engine": ENGINE_NAME,
        "description": (
            "1M fade: Doji/Engulfing → opposite side; max "
            f"{MAX_OPEN} open; batch +{BATCH_PROFIT_PCT}% net after fees → close all"
        ),
        "max_open": MAX_OPEN,
        "batch_profit_pct": BATCH_PROFIT_PCT,
        "size_frac": SIZE_FRAC,
        "lookback": LOOKBACK,
    }


def is_min1_timeframe(timeframe_key: str | None) -> bool:
    return (timeframe_key or "").strip() in ("1m", "30s")


def is_min1_trade(trade: dict | None) -> bool:
    if not trade:
        return False
    if trade.get("entry_pattern") == ENTRY_PATTERN_NAME:
        return True
    if trade.get("exit_mode") == "min1_batch_2pct":
        return True
    tf = (trade.get("timeframe_key") or "").strip()
    return tf in ("1m", "30s") and trade.get("source") == "auto"


def _body(c: dict) -> float:
    return abs(float(c["close"]) - float(c["open"]))


def _range(c: dict) -> float:
    return max(float(c["high"]) - float(c["low"]), 1e-12)


def _is_bullish(c: dict) -> bool:
    return float(c["close"]) > float(c["open"])


def _is_bearish(c: dict) -> bool:
    return float(c["close"]) < float(c["open"])


def _is_doji(c: dict) -> bool:
    return _body(c) <= _range(c) * DOJI_BODY_RATIO


def _bullish_engulfing(prev: dict, cur: dict) -> bool:
    if not (_is_bearish(prev) and _is_bullish(cur)):
        return False
    # Current body engulfs previous body
    return float(cur["open"]) <= float(prev["close"]) and float(cur["close"]) >= float(prev["open"])


def _bearish_engulfing(prev: dict, cur: dict) -> bool:
    if not (_is_bullish(prev) and _is_bearish(cur)):
        return False
    return float(cur["open"]) >= float(prev["close"]) and float(cur["close"]) <= float(prev["open"])


def detect_fade_signal(candles: list[dict]) -> dict[str, Any] | None:
    """Return fade signal dict or None. Action is already the OPPOSITE side."""
    if len(candles) < 2:
        return None
    prev, cur = candles[-2], candles[-1]
    entry = float(cur["close"])

    pattern = None
    natural = None  # pattern's native bias: BUY or SELL

    if _is_doji(cur):
        pattern = "Doji"
        # Slight lean: fade the candle color; flat doji fades prior candle
        if _is_bullish(cur):
            natural = "BUY"
        elif _is_bearish(cur):
            natural = "SELL"
        else:
            natural = "BUY" if _is_bullish(prev) else "SELL"
    elif _bullish_engulfing(prev, cur):
        pattern = "Bullish Engulfing"
        natural = "BUY"
    elif _bearish_engulfing(prev, cur):
        pattern = "Bearish Engulfing"
        natural = "SELL"
    else:
        return None

    # OPPOSITE trade
    action = "SELL" if natural == "BUY" else "BUY"
    side = "SHORT" if action == "SELL" else "LONG"
    return {
        "action": action,
        "side": side,
        "pattern": pattern,
        "natural_bias": natural,
        "entry": entry,
        "confidence": 0.75,
        "strength": 0.75,
        "reason": (
            f"1M fade: {pattern} native={natural} → OPPOSITE {action}. "
            f"Batch hold up to {MAX_OPEN}; exit all at +{BATCH_PROFIT_PCT}% net after fees."
        ),
    }


def evaluate_1min(
    candles: list[dict],
    timeframe_key: str,
    *,
    pair: str = "default",
) -> dict[str, Any]:
    """Bridge-compatible BUY/SELL/NO_TRADE result for the 1M fade engine."""
    if not is_min1_timeframe(timeframe_key):
        return {
            "action": "NO_TRADE",
            "reason": "1min engine only runs on 1m timeframe",
            "engine": ENGINE_NAME,
            "entry_pattern": ENTRY_PATTERN_NAME,
        }

    if len(candles) < 2:
        return {
            "action": "NO_TRADE",
            "reason": "Need 2+ closed candles for Doji/Engulfing",
            "engine": ENGINE_NAME,
            "entry_pattern": ENTRY_PATTERN_NAME,
        }

    sig = detect_fade_signal(candles)
    if sig is None:
        return {
            "action": "NO_TRADE",
            "reason": "No Doji / Engulfing on last closed 1m bar",
            "engine": ENGINE_NAME,
            "entry_pattern": ENTRY_PATTERN_NAME,
            "long_rules": [],
            "short_rules": [],
            "rules_fired": [],
        }

    close_time = candles[-1].get("close_time")
    pattern = sig["pattern"]
    return {
        "action": sig["action"],
        "pattern": pattern,
        "pattern_names": [pattern, "FADE_OPPOSITE"],
        "reason": sig["reason"],
        "entry": float(sig["entry"]),
        "sl": None,
        "tp": None,
        "strength": float(sig["strength"]),
        "confidence": float(sig["confidence"]),
        "risk_reward": None,
        "engine": ENGINE_NAME,
        "entry_pattern": ENTRY_PATTERN_NAME,
        "signal_candle_time": close_time,
        "side": sig["side"],
        "exit_mode": "min1_batch_2pct",
        "natural_bias": sig["natural_bias"],
        "timeframe_key": timeframe_key,
        "long_rules": [pattern] if sig["action"] == "BUY" else [],
        "short_rules": [pattern] if sig["action"] == "SELL" else [],
        "rules_fired": [pattern, "FADE_OPPOSITE"],
        "pair": pair,
    }


def batch_target_usd(batch_capital: float) -> float:
    return float(batch_capital) * (BATCH_PROFIT_PCT / 100.0)


def exit_policy_summary() -> str:
    return (
        f"{ENTRY_PATTERN_NAME} EXIT — no per-trade SL/TP | "
        f"hold up to {MAX_OPEN} | when batch full, close ALL when combined "
        f"net P&L after broker fees ≥ +{BATCH_PROFIT_PCT}% of batch capital | "
        "then open next batch (manual close + emergency sell-all still available)"
    )


def fire_exit_policy_summary(entry_name: str = "FIRE_ENGINE_V3") -> str:
    return (
        f"{entry_name} EXIT — SL=pattern extreme+ATR pad · TP=1:2 R:R | "
        "auto-exit on mark hit SL/TP (manual close + emergency sell-all still available)"
    )


def batch_status(
    *,
    open_count: int,
    batch_capital: float | None,
    net_usd: float,
) -> dict[str, Any]:
    base = float(batch_capital or 0.0)
    target = batch_target_usd(base) if base > 0 else 0.0
    return {
        "engine": ENGINE_NAME,
        "entry_pattern": ENTRY_PATTERN_NAME,
        "exit_mode": "min1_batch_2pct",
        "open_count": int(open_count),
        "max_open": MAX_OPEN,
        "batch_full": int(open_count) >= MAX_OPEN,
        "batch_capital": round(base, 2) if base else None,
        "target_usd": round(target, 4) if target else None,
        "net_usd_after_fees": round(float(net_usd), 4),
        "progress_pct": round((float(net_usd) / target) * 100, 2) if target > 0 else 0.0,
        "ready_to_exit": bool(open_count >= MAX_OPEN and target > 0 and net_usd >= target),
        "policy": exit_policy_summary(),
    }
