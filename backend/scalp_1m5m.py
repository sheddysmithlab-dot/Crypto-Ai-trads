"""1m / 5m scalp engine — fast traps + pin bars + EMA momentum.

Main Bible engine stays on 15m / 1h / 1D. This module is only for short TFs.
Confirmation bar matches live policy: 50%. Exits are applied by main (±0.5%).
"""
from __future__ import annotations

from typing import Any

CONFIRM = 0.50
ENGINE_NAME = "scalp_1m5m"
ENTRY_PATTERN_NAME = "SCALP_1M5M_V1"
SCALP_TFS = frozenset({"1m", "5m", "30s", "1M", "5M", "30S"})
HTF_FOR = {"1m": "5m", "5m": "15m", "30s": "5m"}
MIN_BARS = 30


def is_scalp_timeframe(timeframe_key: str | None) -> bool:
    return (timeframe_key or "").strip() in SCALP_TFS or (timeframe_key or "").strip().lower() in {
        "1m",
        "5m",
        "30s",
    }


def htf_key_for(timeframe_key: str) -> str:
    k = (timeframe_key or "1m").strip().lower()
    return HTF_FOR.get(k, "15m")


def _c(candle: dict) -> dict:
    o = float(candle["open"])
    h = float(candle["high"])
    l = float(candle["low"])
    cl = float(candle["close"])
    body = abs(cl - o)
    rng = max(h - l, 1e-12)
    return {
        "o": o,
        "h": h,
        "l": l,
        "c": cl,
        "body": body,
        "rng": rng,
        "up": h - max(o, cl),
        "dn": min(o, cl) - l,
        "bull": cl > o,
        "bear": cl < o,
        "mid": (max(o, cl) + min(o, cl)) / 2.0,
    }


def _ema(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    k = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    for px in closes[period:]:
        ema = px * k + ema * (1.0 - k)
    return ema


def _atr(candles: list[dict], period: int = 14) -> float:
    if len(candles) < period + 1:
        return 0.0
    trs: list[float] = []
    for i in range(1, len(candles)):
        h = float(candles[i]["high"])
        l = float(candles[i]["low"])
        pc = float(candles[i - 1]["close"])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    window = trs[-period:]
    return sum(window) / len(window) if window else 0.0


def _htf_bias(htf_candles: list[dict] | None) -> str:
    """Return LONG / SHORT / NONE from HTF EMA20 vs last close."""
    if not htf_candles or len(htf_candles) < 21:
        return "NONE"
    closes = [float(c["close"]) for c in htf_candles]
    ema20 = _ema(closes, 20)
    if ema20 is None:
        return "NONE"
    last = closes[-1]
    if last > ema20:
        return "LONG"
    if last < ema20:
        return "SHORT"
    return "NONE"


def _out(
    action: str,
    pattern: str,
    reason: str,
    candle: dict,
    *,
    timeframe_key: str,
    pair: str,
    confidence: float,
    extra: dict | None = None,
) -> dict[str, Any]:
    a = _c(candle)
    atr = extra.get("atr", 0.0) if extra else 0.0
    pad = max(a["rng"] * 0.1, atr * 0.25)
    if action == "BUY":
        sl = a["l"] - pad
        tp = a["c"] + abs(a["c"] - sl)
    else:
        sl = a["h"] + pad
        tp = a["c"] - abs(sl - a["c"])
    payload: dict[str, Any] = {
        "action": action,
        "reason": reason,
        "engine": ENGINE_NAME,
        "entry_pattern": ENTRY_PATTERN_NAME,
        "pattern": pattern,
        "entry": a["c"],
        "sl": sl,
        "tp": tp,
        "risk_reward": 1.0,
        "confidence": confidence,
        "timeframe_key": timeframe_key,
        "pair": pair,
        "scalp": True,
        "direction": "LONG" if action == "BUY" else "SHORT",
    }
    if extra:
        payload.update({k: v for k, v in extra.items() if k != "atr"})
    return payload


def _no_trade(reason: str, timeframe_key: str, pair: str) -> dict[str, Any]:
    return {
        "action": "NO_TRADE",
        "reason": reason,
        "engine": ENGINE_NAME,
        "entry_pattern": ENTRY_PATTERN_NAME,
        "timeframe_key": timeframe_key,
        "pair": pair,
        "scalp": True,
    }


def evaluate_scalp_entry(
    candles: list[dict],
    timeframe_key: str,
    *,
    pair: str = "default",
    htf_candles: list[dict] | None = None,
) -> dict[str, Any]:
    if not is_scalp_timeframe(timeframe_key):
        return _no_trade("scalp engine only runs on 1m/5m", timeframe_key, pair)
    if not candles or len(candles) < MIN_BARS:
        n = len(candles) if candles else 0
        return _no_trade(f"Need {MIN_BARS}+ closed candles (have {n})", timeframe_key, pair)

    c0 = candles[-1]
    c1 = candles[-2]
    a0 = _c(c0)
    a1 = _c(c1)
    atr = _atr(candles, 14)
    bias = _htf_bias(htf_candles)
    look = candles[-21:-1]
    swing_high = max(float(c["high"]) for c in look)
    swing_low = min(float(c["low"]) for c in look)

    # --- 1) Liquidity sweep + reclaim (trap) ---
    if a0["h"] > swing_high and a0["c"] < swing_high and a0["up"] >= max(a0["body"] * 1.0, a0["rng"] * CONFIRM):
        if bias != "LONG":
            return _out(
                "SELL",
                "1m5m Bull Trap",
                f"Sweep above {swing_high:.6g} then reclaim | HTF={bias}",
                c0,
                timeframe_key=timeframe_key,
                pair=pair,
                confidence=0.62,
                extra={"atr": atr, "htf_bias": bias},
            )
    if a0["l"] < swing_low and a0["c"] > swing_low and a0["dn"] >= max(a0["body"] * 1.0, a0["rng"] * CONFIRM):
        if bias != "SHORT":
            return _out(
                "BUY",
                "1m5m Bear Trap",
                f"Sweep below {swing_low:.6g} then reclaim | HTF={bias}",
                c0,
                timeframe_key=timeframe_key,
                pair=pair,
                confidence=0.62,
                extra={"atr": atr, "htf_bias": bias},
            )

    # --- 2) Fast engulfing (50% confirm: current body larger + covers prior body) ---
    covers = a0["mid"] and (max(a0["o"], a0["c"]) >= max(a1["o"], a1["c"])) and (
        min(a0["o"], a0["c"]) <= min(a1["o"], a1["c"])
    )
    if covers and a0["body"] > a1["body"] and a0["body"] >= a0["rng"] * CONFIRM:
        if a0["bull"] and a1["bear"] and bias != "SHORT":
            return _out(
                "BUY",
                "1m5m Bull Engulf",
                f"Bullish engulf of prior bar | HTF={bias}",
                c0,
                timeframe_key=timeframe_key,
                pair=pair,
                confidence=0.55,
                extra={"atr": atr, "htf_bias": bias},
            )
        if a0["bear"] and a1["bull"] and bias != "LONG":
            return _out(
                "SELL",
                "1m5m Bear Engulf",
                f"Bearish engulf of prior bar | HTF={bias}",
                c0,
                timeframe_key=timeframe_key,
                pair=pair,
                confidence=0.55,
                extra={"atr": atr, "htf_bias": bias},
            )

    # --- 3) Pin bar / rejection (wick ≥ 50% of range) ---
    if a0["dn"] >= a0["rng"] * CONFIRM and a0["up"] <= a0["rng"] * 0.25 and bias != "SHORT":
        return _out(
            "BUY",
            "1m5m Hammer",
            f"Lower wick {a0['dn'] / a0['rng']:.0%} of range | HTF={bias}",
            c0,
            timeframe_key=timeframe_key,
            pair=pair,
            confidence=0.52,
            extra={"atr": atr, "htf_bias": bias},
        )
    if a0["up"] >= a0["rng"] * CONFIRM and a0["dn"] <= a0["rng"] * 0.25 and bias != "LONG":
        return _out(
            "SELL",
            "1m5m Shooting Star",
            f"Upper wick {a0['up'] / a0['rng']:.0%} of range | HTF={bias}",
            c0,
            timeframe_key=timeframe_key,
            pair=pair,
            confidence=0.52,
            extra={"atr": atr, "htf_bias": bias},
        )

    # --- 4) EMA9/21 momentum (body ≥ 50%) ---
    closes = [float(c["close"]) for c in candles]
    ema9 = _ema(closes, 9)
    ema21 = _ema(closes, 21)
    if ema9 is not None and ema21 is not None and a0["body"] >= a0["rng"] * CONFIRM:
        if a0["bull"] and a0["c"] > ema9 >= ema21 and bias != "SHORT":
            return _out(
                "BUY",
                "1m5m EMA Momentum",
                f"Bull body above EMA9/21 stack | HTF={bias}",
                c0,
                timeframe_key=timeframe_key,
                pair=pair,
                confidence=0.50,
                extra={"atr": atr, "htf_bias": bias},
            )
        if a0["bear"] and a0["c"] < ema9 <= ema21 and bias != "LONG":
            return _out(
                "SELL",
                "1m5m EMA Momentum",
                f"Bear body below EMA9/21 stack | HTF={bias}",
                c0,
                timeframe_key=timeframe_key,
                pair=pair,
                confidence=0.50,
                extra={"atr": atr, "htf_bias": bias},
            )

    return _no_trade("No 1m/5m scalp setup on last closed candle", timeframe_key, pair)
