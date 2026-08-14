"""Agent brain — routes 1m/5m scalp vs Bible (15m+)."""
from __future__ import annotations

from typing import Any

import pandas as pd

from engine import CandlestickTradingBibleEngine, TradeSignal
from scalp_1m5m import (
    ENTRY_PATTERN_NAME as SCALP_ENTRY_NAME,
    ENGINE_NAME as SCALP_ENGINE_NAME,
    evaluate_scalp_entry,
    htf_key_for,
    is_scalp_timeframe,
)

ENTRY_PATTERN_NAME = "CANDLESTICK_BIBLE_V1"
ENGINE_NAME = "candlestick_bible"

PIPELINE_STEPS: tuple[str, ...] = (
    "smart_money_traps",
    "bible_10_patterns",
    "market_structure",
    "risk_1to2",
)

SCALP_PIPELINE: tuple[str, ...] = (
    "5m_trend",
    "1m_setup",
    "buy_sell_activity",
    "volume",
    "price_confirmation",
    "signal_entry",
)


def entry_pattern_profile(timeframe_key: str | None = None) -> dict[str, Any]:
    if is_scalp_timeframe(timeframe_key):
        return {
            "name": SCALP_ENTRY_NAME,
            "engine": SCALP_ENGINE_NAME,
            "description": (
                "Master brain: 5M direction, 1M timing only. Read pressure/volume/price. "
                "Strong confirm = trade. Weak = NO TRADE. Conflict = WAIT. Exit ±0.5%."
            ),
            "timeframes": ["1m", "5m"],
        }
    return {
        "name": ENTRY_PATTERN_NAME,
        "engine": ENGINE_NAME,
        "description": (
            "Candlestick Trading Bible on 15m/1h/1D: traps → 10 patterns → structure. "
            "Exit ±0.5%."
        ),
        "timeframes": ["15m", "1h", "1D"],
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
        "confidence": 0.5 if "Momentum" in (sig.pattern_name or "") else (
            0.55 if "Trap" in (sig.pattern_name or "") else 0.50
        ),
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


def evaluate_live_entry(
    candles: list[dict],
    timeframe_key: str,
    *,
    pair: str = "default",
    htf_candles: list[dict] | None = None,
    candles_1m: list[dict] | None = None,
    candles_5m: list[dict] | None = None,
    account_balance: float = 10000.0,
    risk_pct: float = 0.01,
) -> dict[str, Any]:
    """1m/5m → scalp brain (5M direction / 1M timing); 15m+ → Bible engine."""
    if is_scalp_timeframe(timeframe_key):
        return evaluate_scalp_entry(
            candles,
            timeframe_key,
            pair=pair,
            htf_candles=htf_candles,
            candles_1m=candles_1m,
            candles_5m=candles_5m or htf_candles,
        )
    return evaluate_bible_entry(
        candles,
        timeframe_key,
        pair=pair,
        account_balance=account_balance,
        risk_pct=risk_pct,
    )


def enrich_signal(result: dict[str, Any], *, max_ml_chars: int = 900) -> dict[str, Any]:
    out = dict(result)
    scalp = bool(result.get("scalp") or result.get("engine") == SCALP_ENGINE_NAME)
    out["brain"] = {
        "pipeline": list(SCALP_PIPELINE if scalp else PIPELINE_STEPS),
        "entry_pattern": result.get("entry_pattern") or ENTRY_PATTERN_NAME,
        "pattern_label": result.get("pattern"),
        "confidence": result.get("confidence"),
        "risk_reward": result.get("risk_reward"),
        "reasoning": result.get("reason"),
        "psychology": result.get("psychology"),
        "market_structure": result.get("market_structure"),
        "market_phase": result.get("market_phase"),
        "scalp": scalp,
    }
    return out


def brain_chat_summary(enriched: dict[str, Any]) -> str:
    if enriched.get("output"):
        return str(enriched["output"])
    action = enriched.get("action", "NO_TRADE")
    pattern = enriched.get("pattern") or "—"
    tag = "Scalp" if enriched.get("engine") == SCALP_ENGINE_NAME or enriched.get("scalp") else "Bible"
    return f"{tag} {action}: {pattern} — {enriched.get('reason', '')}"


def strategy_system_blurb() -> str:
    return (
        "AI AGENT — MASTER BRAIN:\n"
        "1) 5M = dominant direction. 1M = entry timing only.\n"
        "2) Sequence: 5M TREND → 1M SETUP → ACTIVITY → VOLUME → PRICE → SIGNAL → ENTRY.\n"
        "3) Strong confirm = LONG/SHORT. Weak = NO TRADE. Conflict = WAIT.\n"
        "4) Never trade every candle. Never one-condition entries. Capital first.\n"
        "5) 15m+ Bible. Exit −0.5% / +0.5%. Manual + emergency work."
    )
