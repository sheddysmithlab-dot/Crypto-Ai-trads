"""1-minute fade engine (file: 1min.py).

Activated only when user selects the 1M chart timeframe.
Completely separate from Fire Engine v3.

Rules
-----
- Patterns: Doji, Bullish Engulfing, Bearish Engulfing
- Direction: OPPOSITE of the pattern bias (fade)
- Timing: pattern on candle #1 → wait #2 + #3 → FIRE on closed candle #4
- Per coin (chart pair): at least 1 minute gap between fires
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
# Pattern on bar 1; fire when bar FIRE_CANDLE (4) has just closed.
FIRE_CANDLE = int(os.environ.get("MIN1_FIRE_CANDLE", "4"))
# Per-pair minimum gap between fires (ms). 1m chart = 60_000.
PAIR_GAP_MS = int(os.environ.get("MIN1_PAIR_GAP_MS", "60000"))


def entry_pattern_profile() -> dict[str, Any]:
    return {
        "name": ENTRY_PATTERN_NAME,
        "engine": ENGINE_NAME,
        "description": (
            f"1M fade: Doji/Engulfing → opposite; pattern bar1 → fire bar{FIRE_CANDLE}; "
            f"per-coin {PAIR_GAP_MS // 1000}s gap; max {MAX_OPEN}; "
            f"batch +{BATCH_PROFIT_PCT}% net after fees → close all"
        ),
        "max_open": MAX_OPEN,
        "batch_profit_pct": BATCH_PROFIT_PCT,
        "size_frac": SIZE_FRAC,
        "lookback": LOOKBACK,
        "fire_candle": FIRE_CANDLE,
        "pair_gap_ms": PAIR_GAP_MS,
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
    return float(cur["open"]) <= float(prev["close"]) and float(cur["close"]) >= float(prev["open"])


def _bearish_engulfing(prev: dict, cur: dict) -> bool:
    if not (_is_bullish(prev) and _is_bearish(cur)):
        return False
    return float(cur["open"]) >= float(prev["close"]) and float(cur["close"]) <= float(prev["open"])


def _pattern_on_bar(prev: dict | None, cur: dict) -> tuple[str | None, str | None]:
    """Return (pattern_name, natural_bias BUY|SELL) for a completed bar."""
    if _is_doji(cur):
        if _is_bullish(cur):
            natural = "BUY"
        elif _is_bearish(cur):
            natural = "SELL"
        elif prev is not None:
            natural = "BUY" if _is_bullish(prev) else "SELL"
        else:
            natural = "BUY"
        return "Doji", natural
    if prev is not None and _bullish_engulfing(prev, cur):
        return "Bullish Engulfing", "BUY"
    if prev is not None and _bearish_engulfing(prev, cur):
        return "Bearish Engulfing", "SELL"
    return None, None


def detect_fade_signal(candles: list[dict]) -> dict[str, Any] | None:
    """Pattern on candle #1; fire only when candle #FIRE_CANDLE just closed.

    Indexing (oldest → newest among the last FIRE_CANDLE bars):
      bar1 = candles[-FIRE_CANDLE]   ← Doji/Engulf must complete here
      bar2..bar(N-1)                 ← wait
      barN = candles[-1]             ← FIRE at this close (opposite side)
    """
    need = max(FIRE_CANDLE, 2)
    # Engulfing needs one bar before pattern bar.
    if len(candles) < need + 1:
        return None

    pattern_bar = candles[-FIRE_CANDLE]
    prev_of_pattern = candles[-FIRE_CANDLE - 1]
    fire_bar = candles[-1]

    pattern, natural = _pattern_on_bar(prev_of_pattern, pattern_bar)
    if not pattern or not natural:
        return None

    # OPPOSITE trade
    action = "SELL" if natural == "BUY" else "BUY"
    side = "SHORT" if action == "SELL" else "LONG"
    entry = float(fire_bar["close"])
    pattern_time = pattern_bar.get("close_time")
    fire_time = fire_bar.get("close_time")

    return {
        "action": action,
        "side": side,
        "pattern": pattern,
        "natural_bias": natural,
        "entry": entry,
        "confidence": 0.75,
        "strength": 0.75,
        "pattern_candle_time": pattern_time,
        "fire_candle_time": fire_time,
        "fire_candle": FIRE_CANDLE,
        "reason": (
            f"1M fade: {pattern} on bar1 (native={natural}) → OPPOSITE {action} "
            f"fired on bar{FIRE_CANDLE}. Per-coin {PAIR_GAP_MS // 1000}s gap; "
            f"batch hold up to {MAX_OPEN}; exit all at +{BATCH_PROFIT_PCT}% net after fees."
        ),
    }


def pair_gap_ok(last_fire_close_time: int | None, current_close_time: int) -> bool:
    """True if this pair may fire again (chart-wise ≥ 1m since last fire)."""
    if last_fire_close_time is None:
        return True
    try:
        last_i = int(last_fire_close_time)
        cur_i = int(current_close_time)
    except (TypeError, ValueError):
        return True
    # Support seconds or ms timestamps from Bybit.
    gap = PAIR_GAP_MS
    if cur_i < 1_000_000_000_000:  # seconds
        gap = max(1, PAIR_GAP_MS // 1000)
    return cur_i >= last_i + gap


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

    need = max(FIRE_CANDLE + 1, 3)
    if len(candles) < need:
        return {
            "action": "NO_TRADE",
            "reason": f"Need {need}+ closed candles for bar1→bar{FIRE_CANDLE} fire",
            "engine": ENGINE_NAME,
            "entry_pattern": ENTRY_PATTERN_NAME,
        }

    sig = detect_fade_signal(candles)
    if sig is None:
        return {
            "action": "NO_TRADE",
            "reason": (
                f"No Doji/Engulfing on bar1 (looking back {FIRE_CANDLE} closed candles) "
                f"— no fire on bar{FIRE_CANDLE}"
            ),
            "engine": ENGINE_NAME,
            "entry_pattern": ENTRY_PATTERN_NAME,
            "long_rules": [],
            "short_rules": [],
            "rules_fired": [],
        }

    pattern = sig["pattern"]
    return {
        "action": sig["action"],
        "pattern": pattern,
        "pattern_names": [pattern, "FADE_OPPOSITE", f"FIRE_BAR{FIRE_CANDLE}"],
        "reason": sig["reason"],
        "entry": float(sig["entry"]),
        "sl": None,
        "tp": None,
        "strength": float(sig["strength"]),
        "confidence": float(sig["confidence"]),
        "risk_reward": None,
        "engine": ENGINE_NAME,
        "entry_pattern": ENTRY_PATTERN_NAME,
        "signal_candle_time": sig.get("fire_candle_time") or candles[-1].get("close_time"),
        "pattern_candle_time": sig.get("pattern_candle_time"),
        "side": sig["side"],
        "exit_mode": "min1_batch_2pct",
        "natural_bias": sig["natural_bias"],
        "fire_candle": FIRE_CANDLE,
        "timeframe_key": timeframe_key,
        "long_rules": [pattern] if sig["action"] == "BUY" else [],
        "short_rules": [pattern] if sig["action"] == "SELL" else [],
        "rules_fired": [pattern, "FADE_OPPOSITE", f"FIRE_BAR{FIRE_CANDLE}"],
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
        "fire_candle": FIRE_CANDLE,
        "pair_gap_ms": PAIR_GAP_MS,
    }
