"""Bridge: Bybit OHLCV candles → LiveTradeFireEngine → bot signal dict."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from fire_trade_engine import LiveTradeFireEngine, TradeSignal

ENTRY_PATTERN_NAME = "FIRE_ENGINE_V3"

LOOKBACK = int(os.environ.get("FIRE_ENGINE_LOOKBACK", "120"))
MIN_CONFIDENCE = float(os.environ.get("FIRE_ENGINE_MIN_CONFIDENCE", "0.0"))
MIN_CONFLUENCE = float(os.environ.get("FIRE_ENGINE_MIN_CONFLUENCE", "0.72"))
MIN_EDGE = float(os.environ.get("FIRE_ENGINE_MIN_EDGE", "0.04"))
ATR_SL_PAD = float(os.environ.get("FIRE_ENGINE_ATR_SL_PAD", "0.1"))
RR = float(os.environ.get("FIRE_ENGINE_RR", "2.0"))
SKIP_SIDEWAYS = os.environ.get("FIRE_ENGINE_SKIP_SIDEWAYS", "1").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

_ENGINE = LiveTradeFireEngine(
    min_confluence=MIN_CONFLUENCE,
    min_edge=MIN_EDGE,
    atr_sl_pad=ATR_SL_PAD,
    rr=RR,
    skip_sideways=SKIP_SIDEWAYS,
)


def entry_pattern_profile() -> dict[str, Any]:
    return {
        "name": ENTRY_PATTERN_NAME,
        "engine": "fire_trade_engine",
        "description": (
            "Fire Engine v3.1 (pettern-4 Ch1–7) — patterns + shadow psych + "
            "market structure + EMA/MACD/ADX/RSI confluence; ATR SL + 1:2 TP"
        ),
        "lookback": LOOKBACK,
        "min_confidence": MIN_CONFIDENCE,
        "min_confluence": MIN_CONFLUENCE,
        "min_edge": MIN_EDGE,
        "rr": RR,
        "skip_sideways": SKIP_SIDEWAYS,
    }


def candles_to_dataframe(candles: list[dict]) -> pd.DataFrame:
    """Convert backend candle dicts (open/high/low/close/volume/close_time) to engine DF."""
    rows = []
    index = []
    for c in candles:
        ts = c.get("close_time") or c.get("start_time") or c.get("time")
        if ts is None:
            continue
        raw = int(ts)
        # Bybit close_time is usually ms
        if raw > 1_000_000_000_000:
            dt = datetime.fromtimestamp(raw / 1000.0, tz=timezone.utc)
        elif raw > 1_000_000_000:
            dt = datetime.fromtimestamp(raw, tz=timezone.utc)
        else:
            dt = datetime.fromtimestamp(raw, tz=timezone.utc)
        index.append(dt)
        rows.append(
            {
                "Open": float(c["open"]),
                "High": float(c["high"]),
                "Low": float(c["low"]),
                "Close": float(c["close"]),
                "Volume": float(c.get("volume") or 0.0),
            }
        )
    if not rows:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    return pd.DataFrame(rows, index=pd.DatetimeIndex(index))


def trade_signal_to_result(signal: TradeSignal, *, pair: str, candle_close_time: int | None) -> dict[str, Any]:
    action = "BUY" if signal.action == "LONG" else "SELL"
    patterns = signal.pattern_names or []
    pattern = patterns[0] if patterns else "fire_engine"
    out: dict[str, Any] = {
        "action": action,
        "pattern": pattern,
        "pattern_names": patterns,
        "reason": signal.reasoning,
        "entry": float(signal.entry_price),
        "sl": float(signal.stop_loss),
        "tp": float(signal.take_profit),
        "strength": float(signal.confidence),
        "confidence": float(signal.confidence),
        "risk_reward": float(signal.risk_reward),
        "engine": "fire_trade_engine",
        "entry_pattern": ENTRY_PATTERN_NAME,
        "signal_candle_time": candle_close_time,
        "side": signal.action,
    }
    if getattr(signal, "confluence", None):
        out["confluence"] = signal.confluence
    return out


def evaluate_fire_engine(
    candles: list[dict],
    timeframe_key: str,
    *,
    pair: str = "default",
) -> dict[str, Any]:
    """Run LiveTradeFireEngine on closed-candle history. Returns BUY/SELL/NO_TRADE."""
    need = max(LOOKBACK, 100)
    if len(candles) < need:
        return {
            "action": "NO_TRADE",
            "reason": f"Need {need}+ candles (have {len(candles)})",
            "engine": "fire_trade_engine",
            "entry_pattern": ENTRY_PATTERN_NAME,
        }

    df = candles_to_dataframe(candles)
    if df.empty or len(df) < need:
        return {
            "action": "NO_TRADE",
            "reason": "Could not build OHLCV frame for fire engine",
            "engine": "fire_trade_engine",
            "entry_pattern": ENTRY_PATTERN_NAME,
        }

    signal = _ENGINE.scan_and_fire(pair, df, lookback=LOOKBACK)
    if signal is None:
        return {
            "action": "NO_TRADE",
            "reason": "No high-probability fire-engine setup",
            "engine": "fire_trade_engine",
            "entry_pattern": ENTRY_PATTERN_NAME,
            "long_rules": [],
            "short_rules": [],
            "rules_fired": [],
        }

    if float(signal.confidence) < MIN_CONFIDENCE:
        return {
            "action": "NO_TRADE",
            "reason": f"Confidence {signal.confidence} < min {MIN_CONFIDENCE}",
            "engine": "fire_trade_engine",
            "entry_pattern": ENTRY_PATTERN_NAME,
            "pattern": (signal.pattern_names or ["fire_engine"])[0],
        }

    close_time = candles[-1].get("close_time")
    result = trade_signal_to_result(signal, pair=pair, candle_close_time=close_time)
    result["timeframe_key"] = timeframe_key
    result["long_rules"] = [result["pattern"]] if result["action"] == "BUY" else []
    result["short_rules"] = [result["pattern"]] if result["action"] == "SELL" else []
    result["rules_fired"] = [result["pattern"]]
    return result
