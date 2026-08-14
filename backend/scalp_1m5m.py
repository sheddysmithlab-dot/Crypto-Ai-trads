"""1m/5m decision brain.

5-minute = dominant direction. 1-minute = entry timing only.
Never trade every candle. Never decide from a single indicator.
Exits stay in main.py (±0.5%).
"""
from __future__ import annotations

from typing import Any

ENGINE_NAME = "scalp_1m5m"
ENTRY_PATTERN_NAME = "SCALP_1M5M_V1"
SCALP_TFS = frozenset({"1m", "5m", "30s", "1M", "5M", "30S"})
LOOKBACK = 5  # current + previous 3–5 candles
MIN_1M_BARS = 20
MIN_5M_BARS = 16
MIN_CONFIRMS = 3
MIN_CONFIDENCE = 60  # 0–100; capital protection > frequency
EPS = 0.02  # 2% relative increase counts as "up"

BRAIN_MEMORY = (
    "Disciplined short-term engine: 5M sets dominant direction, 1M is entry timing only. "
    "Never trade every candle or a single indicator. Compare current vs previous 3–5 candles "
    "for price, buyer/seller activity, buy/sell/total volume and candle strength. "
    "Price+Volume first, buy/sell volume second, activity third. Multiple confirms required. "
    "5M+1M aligned to trade; conflict = WAIT unless strong reversal. "
    "Confidence 0–100; fire only when high. Final output LONG, SHORT or NO TRADE."
)


def is_scalp_timeframe(timeframe_key: str | None) -> bool:
    k = (timeframe_key or "").strip()
    return k in SCALP_TFS or k.lower() in {"1m", "5m", "30s"}


def htf_key_for(timeframe_key: str) -> str:
    """Scalp HTF is always 5m (direction), regardless of chart TF."""
    return "5m"


def _f(c: dict, key: str, default: float = 0.0) -> float:
    try:
        return float(c.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def _bar(c: dict) -> dict:
    o, h, l, cl = _f(c, "open"), _f(c, "high"), _f(c, "low"), _f(c, "close")
    vol = max(_f(c, "volume"), 0.0)
    rng = max(h - l, 1e-12)
    body = abs(cl - o)
    strength = body / rng
    buy_vol = vol * max(cl - l, 0.0) / rng
    sell_vol = vol * max(h - cl, 0.0) / rng
    bull = cl > o
    bear = cl < o
    # Activity = volume-weighted participation of that side, boosted by candle strength.
    buyer_act = buy_vol * (1.0 + strength if bull else 0.5)
    seller_act = sell_vol * (1.0 + strength if bear else 0.5)
    return {
        "o": o,
        "h": h,
        "l": l,
        "c": cl,
        "vol": vol,
        "rng": rng,
        "body": body,
        "strength": strength,
        "buy_vol": buy_vol,
        "sell_vol": sell_vol,
        "buyer_act": buyer_act,
        "seller_act": seller_act,
        "bull": bull,
        "bear": bear,
    }


def _up(now: float, prev: float) -> bool:
    if prev <= 0 and now > 0:
        return True
    if prev <= 0:
        return False
    return (now - prev) / prev >= EPS


def _dn(now: float, prev: float) -> bool:
    if prev <= 0:
        return now < 0
    return (prev - now) / prev >= EPS


def _window(candles: list[dict]) -> dict | None:
    if not candles:
        return None
    bars = [_bar(c) for c in candles]
    first, last = bars[0], bars[-1]
    buy_vol = sum(b["buy_vol"] for b in bars)
    sell_vol = sum(b["sell_vol"] for b in bars)
    total = sum(b["vol"] for b in bars)
    buyer_act = sum(b["buyer_act"] for b in bars)
    seller_act = sum(b["seller_act"] for b in bars)
    px0 = first["c"] or first["o"] or 1e-12
    price_chg = (last["c"] - px0) / px0
    return {
        "price_chg": price_chg,
        "price_up": price_chg > EPS,
        "price_dn": price_chg < -EPS,
        "buy_vol": buy_vol,
        "sell_vol": sell_vol,
        "total": total,
        "buyer_act": buyer_act,
        "seller_act": seller_act,
        "buy_dom": buy_vol > sell_vol * 1.05,
        "sell_dom": sell_vol > buy_vol * 1.05,
        "strength": last["strength"],
        "last": last,
        "bull_count": sum(1 for b in bars if b["bull"]),
        "bear_count": sum(1 for b in bars if b["bear"]),
    }


def _ema(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    k = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    for px in closes[period:]:
        ema = px * k + ema * (1.0 - k)
    return ema


def _m5_direction(c5: list[dict]) -> str:
    """Dominant 5M direction from price, EMA, and buy/sell volume — not total volume alone."""
    cur = _window(c5[-LOOKBACK:])
    if not cur:
        return "NONE"
    closes = [_f(c, "close") for c in c5]
    ema9 = _ema(closes, 9)
    last = closes[-1]
    ema_ok_long = ema9 is not None and last > ema9
    ema_ok_short = ema9 is not None and last < ema9
    long_votes = 0
    short_votes = 0
    if cur["price_up"]:
        long_votes += 1
    if cur["price_dn"]:
        short_votes += 1
    if ema_ok_long:
        long_votes += 1
    if ema_ok_short:
        short_votes += 1
    if cur["buy_dom"]:
        long_votes += 1
    if cur["sell_dom"]:
        short_votes += 1
    if cur["bull_count"] > cur["bear_count"]:
        long_votes += 1
    if cur["bear_count"] > cur["bull_count"]:
        short_votes += 1
    if long_votes >= 3 and long_votes > short_votes:
        return "LONG"
    if short_votes >= 3 and short_votes > long_votes:
        return "SHORT"
    return "NONE"


def _m1_momentum(cur: dict) -> str:
    last = cur["last"]
    if cur["price_up"] and cur["buy_dom"] and last["bull"]:
        return "LONG"
    if cur["price_dn"] and cur["sell_dom"] and last["bear"]:
        return "SHORT"
    if last["bull"] and last["strength"] >= 0.5 and cur["buy_dom"]:
        return "LONG"
    if last["bear"] and last["strength"] >= 0.5 and cur["sell_dom"]:
        return "SHORT"
    return "NONE"


def _delta(cur: dict, prev: dict) -> dict:
    return {
        "buyer_up": _up(cur["buyer_act"], prev["buyer_act"]),
        "seller_up": _up(cur["seller_act"], prev["seller_act"]),
        "buy_vol_up": _up(cur["buy_vol"], prev["buy_vol"]),
        "sell_vol_up": _up(cur["sell_vol"], prev["sell_vol"]),
        "total_up": _up(cur["total"], prev["total"]),
        "buyer_strong": _up(cur["buyer_act"], prev["buyer_act"] * 1.15) if prev["buyer_act"] > 0 else False,
        "seller_strong": _up(cur["seller_act"], prev["seller_act"] * 1.15) if prev["seller_act"] > 0 else False,
    }


def _no_trade(reason: str, timeframe_key: str, pair: str, extra: dict | None = None) -> dict[str, Any]:
    out = {
        "action": "NO_TRADE",
        "reason": reason,
        "engine": ENGINE_NAME,
        "entry_pattern": ENTRY_PATTERN_NAME,
        "timeframe_key": timeframe_key,
        "pair": pair,
        "scalp": True,
        "confidence": 0,
        "brain_memory": BRAIN_MEMORY,
    }
    if extra:
        out.update(extra)
    return out


def evaluate_scalp_entry(
    candles: list[dict],
    timeframe_key: str,
    *,
    pair: str = "default",
    htf_candles: list[dict] | None = None,
    candles_1m: list[dict] | None = None,
    candles_5m: list[dict] | None = None,
) -> dict[str, Any]:
    if not is_scalp_timeframe(timeframe_key):
        return _no_trade("scalp engine only runs on 1m/5m", timeframe_key, pair)

    tf = (timeframe_key or "1m").strip().lower()
    c1 = candles_1m or (candles if tf in ("1m", "30s") else None)
    c5 = candles_5m or htf_candles or (candles if tf == "5m" else None)
    if not c1 or len(c1) < MIN_1M_BARS:
        return _no_trade(
            f"Need {MIN_1M_BARS}+ 1m candles for entry timing (have {len(c1) if c1 else 0})",
            timeframe_key,
            pair,
        )
    if not c5 or len(c5) < MIN_5M_BARS:
        return _no_trade(
            f"Need {MIN_5M_BARS}+ 5m candles for direction (have {len(c5) if c5 else 0})",
            timeframe_key,
            pair,
        )

    n = LOOKBACK
    if len(c1) < n * 2:
        return _no_trade("Need 3–5 prior 1m candles to compare", timeframe_key, pair)

    cur = _window(c1[-n:])
    prev = _window(c1[-n * 2 : -n])
    if not cur or not prev:
        return _no_trade("Unable to compare current vs previous 1m window", timeframe_key, pair)

    d = _delta(cur, prev)
    m5 = _m5_direction(c5)
    m1 = _m1_momentum(cur)
    last = cur["last"]

    long_confirms: list[str] = []
    short_confirms: list[str] = []

    # Priority 1: Price + Volume
    if d["buyer_up"] and d["buy_vol_up"] and d["total_up"] and cur["price_up"]:
        long_confirms.append("buyer+buy_vol+total+price")
    if d["seller_up"] and d["sell_vol_up"] and d["total_up"] and cur["price_dn"]:
        short_confirms.append("seller+sell_vol+total+price")

    # Priority 2: Buy/Sell volume dominance — total volume alone is never LONG
    if d["buy_vol_up"] and cur["price_up"] and cur["buy_dom"]:
        long_confirms.append("buy_vol_dom+price")
    if d["sell_vol_up"] and cur["price_dn"] and cur["sell_dom"]:
        short_confirms.append("sell_vol_dom+price")

    # Absorption
    if d["buy_vol_up"] and cur["price_dn"]:
        short_confirms.append("buy_vol_up+price_dn=selling_pressure")
    if d["sell_vol_up"] and cur["price_up"]:
        long_confirms.append("sell_vol_up+price_up=buying_absorption")

    # Priority 3: Activity traps / exhaustion
    if d["buyer_strong"] and not cur["price_up"] and not d["buy_vol_up"]:
        short_confirms.append("buyer_exhaustion_trap")
    if d["seller_strong"] and not cur["price_dn"] and not d["sell_vol_up"]:
        long_confirms.append("seller_exhaustion_trap")

    if d["buyer_up"] and cur["buy_dom"]:
        long_confirms.append("buyer_activity")
    if d["seller_up"] and cur["sell_dom"]:
        short_confirms.append("seller_activity")

    if last["strength"] >= 0.50 and last["bull"] and cur["buy_dom"]:
        long_confirms.append("strong_bull_candle")
    if last["strength"] >= 0.50 and last["bear"] and cur["sell_dom"]:
        short_confirms.append("strong_bear_candle")

    if m5 == "LONG":
        long_confirms.append("5m_bullish")
    elif m5 == "SHORT":
        short_confirms.append("5m_bearish")
    if m1 == "LONG":
        long_confirms.append("1m_bullish_momentum")
    elif m1 == "SHORT":
        short_confirms.append("1m_bearish_momentum")

    long_confirms = list(dict.fromkeys(long_confirms))
    short_confirms = list(dict.fromkeys(short_confirms))

    strong_rev_short = "buyer_exhaustion_trap" in short_confirms or "buy_vol_up+price_dn=selling_pressure" in short_confirms
    strong_rev_long = "seller_exhaustion_trap" in long_confirms or "sell_vol_up+price_up=buying_absorption" in long_confirms

    # 5M + 1M alignment
    if m5 == "LONG" and m1 == "SHORT" and not strong_rev_short:
        return _no_trade(
            "WAIT: 5M bullish vs 1M bearish — no strong reversal",
            timeframe_key,
            pair,
            extra={"m5": m5, "m1": m1, "confidence": 0},
        )
    if m5 == "SHORT" and m1 == "LONG" and not strong_rev_long:
        return _no_trade(
            "WAIT: 5M bearish vs 1M bullish — no strong reversal",
            timeframe_key,
            pair,
            extra={"m5": m5, "m1": m1, "confidence": 0},
        )
    if m5 == "NONE" and m1 == "NONE":
        return _no_trade(
            "NO TRADE: 5M and 1M unclear / balanced",
            timeframe_key,
            pair,
        )

    # Confidence 0–100
    def _score(side: str) -> int:
        confirms = long_confirms if side == "LONG" else short_confirms
        pts = 0
        if m5 == side:
            pts += 20
        if m1 == side:
            pts += 18
        if (side == "LONG" and cur["price_up"]) or (side == "SHORT" and cur["price_dn"]):
            pts += 15
        if (side == "LONG" and cur["buy_dom"]) or (side == "SHORT" and cur["sell_dom"]):
            pts += 15
        if d["total_up"] and (
            (side == "LONG" and cur["buy_dom"]) or (side == "SHORT" and cur["sell_dom"])
        ):
            pts += 10
        if (side == "LONG" and d["buyer_up"]) or (side == "SHORT" and d["seller_up"]):
            pts += 12
        if last["strength"] >= 0.50 and (
            (side == "LONG" and last["bull"]) or (side == "SHORT" and last["bear"])
        ):
            pts += 10
        # extra trap/absorption bonus (capped)
        bonus = 0
        if side == "SHORT" and strong_rev_short:
            bonus += 8
        if side == "LONG" and strong_rev_long:
            bonus += 8
        return min(100, pts + bonus)

    long_score = _score("LONG")
    short_score = _score("SHORT")

    # Balanced / unclear activity+price+volume
    if not long_confirms and not short_confirms:
        return _no_trade("NO TRADE: buyer/seller, volume and price balanced", timeframe_key, pair)

    pick = None
    if long_score >= short_score and long_score >= MIN_CONFIDENCE and len(long_confirms) >= MIN_CONFIRMS:
        if m5 == "SHORT" and not strong_rev_long:
            pick = None
        else:
            pick = "LONG"
    if short_score > long_score and short_score >= MIN_CONFIDENCE and len(short_confirms) >= MIN_CONFIRMS:
        if m5 == "LONG" and not strong_rev_short:
            pick = None
        else:
            pick = "SHORT"
    # If scores equal and both pass, take the 5M side only
    if pick is None and long_score == short_score and long_score >= MIN_CONFIDENCE:
        if m5 == "LONG" and len(long_confirms) >= MIN_CONFIRMS:
            pick = "LONG"
        elif m5 == "SHORT" and len(short_confirms) >= MIN_CONFIRMS:
            pick = "SHORT"

    if pick is None:
        why = (
            f"NO TRADE conf L{long_score}/S{short_score} need {MIN_CONFIDENCE}+ "
            f"and {MIN_CONFIRMS}+ confirms (L={len(long_confirms)} S={len(short_confirms)}) "
            f"5M={m5} 1M={m1}"
        )
        return _no_trade(why, timeframe_key, pair, extra={"confidence": max(long_score, short_score)})

    confirms = long_confirms if pick == "LONG" else short_confirms
    score = long_score if pick == "LONG" else short_score
    action = "BUY" if pick == "LONG" else "SELL"
    pad = max(last["rng"] * 0.1, 1e-12)
    if pick == "LONG":
        sl, tp = last["l"] - pad, last["c"] + abs(last["c"] - (last["l"] - pad))
        pattern = "1m5m LONG confluence"
    else:
        sl, tp = last["h"] + pad, last["c"] - abs((last["h"] + pad) - last["c"])
        pattern = "1m5m SHORT confluence"

    return {
        "action": action,
        "reason": (
            f"{pick} conf {score}/100 | 5M={m5} 1M={m1} | "
            f"confirms={', '.join(confirms[:6])}"
        ),
        "engine": ENGINE_NAME,
        "entry_pattern": ENTRY_PATTERN_NAME,
        "pattern": pattern,
        "entry": last["c"],
        "sl": sl,
        "tp": tp,
        "risk_reward": 1.0,
        "confidence": score,
        "timeframe_key": timeframe_key,
        "pair": pair,
        "scalp": True,
        "direction": pick,
        "m5": m5,
        "m1": m1,
        "confirms": confirms,
        "brain_memory": BRAIN_MEMORY,
    }
