"""Agent brain — Candlestick Trading Bible engine (`engine.py`)."""
from __future__ import annotations

from typing import Any

import pandas as pd

from engine import CandlestickTradingBibleEngine, TradeSignal

ENTRY_PATTERN_NAME = "CANDLESTICK_BIBLE_V1"
ENGINE_NAME = "candlestick_bible"

PIPELINE_STEPS: tuple[str, ...] = (
    "smart_money_traps",
    "bible_10_patterns",
    "market_structure",
    "risk_1to2",
)


def entry_pattern_profile() -> dict[str, Any]:
    return {
        "name": ENTRY_PATTERN_NAME,
        "engine": ENGINE_NAME,
        "description": (
            "Candlestick Trading Bible: traps → 10 patterns → structure/phase → 1:2 R:R SL/TP."
        ),
    }


def _candles_to_df(candles: list[dict]) -> pd.DataFrame:
    rows = []
    for c in candles:
        rows.append(
            {
                "Open": float(c["open"]),
                "High": float(c["high"]),
                "Low": float(c["low"]),
                "Close": float(c["close"]),
            }
        )
    idx = [int(c.get("close_time") or i) for i, c in enumerate(candles)]
    return pd.DataFrame(rows, index=idx)


def _signal_to_dict(sig: TradeSignal, *, timeframe_key: str, pair: str) -> dict[str, Any]:
    direction = (sig.direction or "").upper()
    action = "BUY" if direction == "LONG" else "SELL" if direction == "SHORT" else "NO_TRADE"
    psych = getattr(sig.bible_psychology, "name", str(sig.bible_psychology))
    trend = getattr(sig.market_structure, "name", str(sig.market_structure))
    phase = getattr(sig.market_phase, "name", str(sig.market_phase))
    return {
        "action": action,
        "reason": (
            f"{sig.pattern_name} | {psych} | {trend}/{phase} | "
            f"R:R 1:{sig.risk_reward_ratio} | confs={', '.join(sig.confluences)}"
        ),
        "engine": ENGINE_NAME,
        "entry_pattern": ENTRY_PATTERN_NAME,
        "pattern": sig.pattern_name,
        "entry": float(sig.entry_price),
        "sl": float(sig.stop_loss),
        "tp": float(sig.take_profit),
        "risk_reward": float(sig.risk_reward_ratio),
        "confidence": 0.8 if "Trap" in (sig.pattern_name or "") else 0.72,
        "confluences": list(sig.confluences or []),
        "psychology": psych,
        "market_structure": trend,
        "market_phase": phase,
        "timeframe_key": timeframe_key,
        "pair": pair,
        "direction": direction,
    }


def evaluate_bible_entry(
    candles: list[dict],
    timeframe_key: str,
    *,
    pair: str = "default",
    account_balance: float = 10000.0,
    risk_pct: float = 0.01,
) -> dict[str, Any]:
    """Run Bible engine on the latest closed candle."""
    if not candles or len(candles) < 25:
        return {
            "action": "NO_TRADE",
            "reason": f"Need 25+ closed candles (have {len(candles) if candles else 0})",
            "engine": ENGINE_NAME,
            "entry_pattern": ENTRY_PATTERN_NAME,
            "timeframe_key": timeframe_key,
            "pair": pair,
        }

    df = _candles_to_df(candles)
    engine = CandlestickTradingBibleEngine(
        account_balance=max(float(account_balance or 10000.0), 1.0),
        risk_pct=float(risk_pct or 0.01),
    )
    try:
        sig = engine.evaluate_candle(pair, df, len(df) - 1)
    except Exception as exc:
        return {
            "action": "NO_TRADE",
            "reason": f"Bible engine error: {exc}",
            "engine": ENGINE_NAME,
            "entry_pattern": ENTRY_PATTERN_NAME,
            "timeframe_key": timeframe_key,
            "pair": pair,
        }

    if sig is None:
        return {
            "action": "NO_TRADE",
            "reason": "No high-probability Bible setup on last closed candle",
            "engine": ENGINE_NAME,
            "entry_pattern": ENTRY_PATTERN_NAME,
            "timeframe_key": timeframe_key,
            "pair": pair,
        }

    return _signal_to_dict(sig, timeframe_key=timeframe_key, pair=pair)


def enrich_signal(result: dict[str, Any], *, max_ml_chars: int = 900) -> dict[str, Any]:
    out = dict(result)
    out["brain"] = {
        "pipeline": list(PIPELINE_STEPS),
        "entry_pattern": ENTRY_PATTERN_NAME,
        "pattern_label": result.get("pattern"),
        "confidence": result.get("confidence"),
        "risk_reward": result.get("risk_reward"),
        "reasoning": result.get("reason"),
        "psychology": result.get("psychology"),
        "market_structure": result.get("market_structure"),
        "market_phase": result.get("market_phase"),
        "scalp": False,
    }
    return out


def brain_chat_summary(enriched: dict[str, Any]) -> str:
    action = enriched.get("action", "NO_TRADE")
    pattern = enriched.get("pattern") or "—"
    return f"Bible {action}: {pattern} — {enriched.get('reason', '')}"


def strategy_system_blurb() -> str:
    return (
        "AI AGENT — CANDLESTICK TRADING BIBLE:\n"
        "1) Priority: smart-money traps (deviate & reclaim).\n"
        "2) Then strict 10-pattern bible recognition (shadow math, no color bias).\n"
        "3) Market structure filter (HH/HL, impulsive vs retracement, choppy = no trade).\n"
        "4) Risk: ~1% account risk sizing guidance; SL beyond wick; min 1:2 R:R TP.\n"
        "5) Auto exits on SL/TP; manual BUY/SELL + emergency sell-all still work."
    )
