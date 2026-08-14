"""AI TRADING AGENT — MASTER BRAIN (1M entry / 5M direction).

Execute LONG or SHORT every market situation unless exactly one of:
  (1) Buyer Pressure and Seller Pressure are balanced
  (2) Buy Volume and Sell Volume are balanced
  (3) overall signal confidence is low
No other NO TRADE reasons. Exits: ±0.5% in main.py.
"""
from __future__ import annotations

from typing import Any

ENGINE_NAME = "scalp_1m5m"
ENTRY_PATTERN_NAME = "SCALP_1M5M_V1"
SCALP_TFS = frozenset({"1m", "5m", "30s", "1M", "5M", "30S"})

LOOKBACK = 5
MIN_1M = 16
MIN_5M = 12
MIN_CONFIDENCE = 45  # below this = NO TRADE reason (3) only
EPS = 0.015
EXIT_LOSS_PCT = 0.5
EXIT_PROFIT_PCT = 0.5
BALANCE_EPS = 0.08  # |buy-sell| / total < 8% → balanced

BRAIN_MEMORY = (
    "Always LONG or SHORT unless exactly: (1) buyer/seller pressure balanced, "
    "(2) buy/sell volume balanced, or (3) confidence low. "
    "5M = dominant direction, 1M = entry timing. "
    "Conflict → pick stronger Price+Volume+Pressure side. Never skip for sideways, "
    "weak candle, conflict, low volume, or uncertainty outside the three reasons."
)


def is_scalp_timeframe(timeframe_key: str | None) -> bool:
    k = (timeframe_key or "").strip()
    return k in SCALP_TFS or k.lower() in {"1m", "5m", "30s"}


def htf_key_for(timeframe_key: str) -> str:
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
    buyer_act = buy_vol * (1.0 + strength if bull else 0.5)
    seller_act = sell_vol * (1.0 + strength if bear else 0.5)
    return {
        "o": o, "h": h, "l": l, "c": cl, "vol": vol, "rng": rng,
        "body": body, "strength": strength,
        "buy_vol": buy_vol, "sell_vol": sell_vol,
        "buyer_act": buyer_act, "seller_act": seller_act,
        "bull": bull, "bear": bear,
    }


def _up(now: float, prev: float, thr: float = EPS) -> bool:
    if prev <= 0:
        return now > 0
    return (now - prev) / prev >= thr


def _lvl(ratio: float) -> str:
    if ratio >= 1.25:
        return "HIGH"
    if ratio >= 1.05:
        return "MEDIUM"
    return "LOW"


def _balanced(a: float, b: float) -> bool:
    tot = a + b
    if tot <= 1e-12:
        return True
    return abs(a - b) / tot < BALANCE_EPS


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
        "buy_dom": buy_vol > sell_vol * (1.0 + BALANCE_EPS),
        "sell_dom": sell_vol > buy_vol * (1.0 + BALANCE_EPS),
        "strength": last["strength"],
        "last": last,
        "bull_count": sum(1 for b in bars if b["bull"]),
        "bear_count": sum(1 for b in bars if b["bear"]),
    }


def _delta(cur: dict, prev: dict) -> dict:
    return {
        "buyer_up": _up(cur["buyer_act"], prev["buyer_act"]),
        "seller_up": _up(cur["seller_act"], prev["seller_act"]),
        "buyer_strong": _up(cur["buyer_act"], prev["buyer_act"], thr=0.20),
        "seller_strong": _up(cur["seller_act"], prev["seller_act"], thr=0.20),
        "buy_vol_up": _up(cur["buy_vol"], prev["buy_vol"]),
        "sell_vol_up": _up(cur["sell_vol"], prev["sell_vol"]),
        "total_up": _up(cur["total"], prev["total"]),
    }


def _m5_trend(c5: list[dict]) -> str:
    cur = _window(c5[-LOOKBACK:])
    if not cur:
        return "NEUTRAL"
    long_v = short_v = 0
    if cur["price_up"]:
        long_v += 2
    if cur["price_dn"]:
        short_v += 2
    if cur["buy_dom"]:
        long_v += 1
    if cur["sell_dom"]:
        short_v += 1
    if cur["buyer_act"] > cur["seller_act"]:
        long_v += 1
    if cur["seller_act"] > cur["buyer_act"]:
        short_v += 1
    if cur["bull_count"] > cur["bear_count"]:
        long_v += 1
    if cur["bear_count"] > cur["bull_count"]:
        short_v += 1
    if long_v > short_v:
        return "BULLISH"
    if short_v > long_v:
        return "BEARISH"
    return "NEUTRAL"


def _m1_signal(cur: dict, d: dict) -> str:
    long_p = short_p = 0
    if cur["price_up"]:
        long_p += 1
    if cur["price_dn"]:
        short_p += 1
    if d["buyer_up"]:
        long_p += 1
    if d["seller_up"]:
        short_p += 1
    if d["buy_vol_up"]:
        long_p += 1
    if d["sell_vol_up"]:
        short_p += 1
    if cur["last"]["bull"]:
        long_p += 1
    if cur["last"]["bear"]:
        short_p += 1
    if cur["buy_dom"]:
        long_p += 1
    if cur["sell_dom"]:
        short_p += 1
    if long_p > short_p:
        return "BULLISH"
    if short_p > long_p:
        return "BEARISH"
    return "NEUTRAL"


def _pressure_lvl(cur: dict, prev: dict, side: str) -> str:
    if side == "BUY":
        return _lvl(cur["buyer_act"] / max(prev["buyer_act"], 1e-12))
    return _lvl(cur["seller_act"] / max(prev["seller_act"], 1e-12))


def _vol_lvl(cur: dict, prev: dict, side: str) -> str:
    if side == "BUY":
        return _lvl(cur["buy_vol"] / max(prev["buy_vol"], 1e-12))
    return _lvl(cur["sell_vol"] / max(prev["sell_vol"], 1e-12))


def _no_trade(
    reason: str,
    timeframe_key: str,
    pair: str,
    *,
    m5: str = "NEUTRAL",
    m1: str = "NEUTRAL",
    buy_p: str = "LOW",
    sell_p: str = "LOW",
    buy_v: str = "LOW",
    sell_v: str = "LOW",
    confidence: int = 0,
) -> dict[str, Any]:
    return {
        "action": "NO_TRADE",
        "reason": reason,
        "engine": ENGINE_NAME,
        "entry_pattern": ENTRY_PATTERN_NAME,
        "timeframe_key": timeframe_key,
        "pair": pair,
        "scalp": True,
        "confidence": confidence,
        "m5": m5,
        "m1": m1,
        "buy_pressure": buy_p,
        "sell_pressure": sell_p,
        "buy_volume": buy_v,
        "sell_volume": sell_v,
        "signal": "NO TRADE",
        "brain_memory": BRAIN_MEMORY,
        "output": (
            f"TIMEFRAME: {timeframe_key.upper()} | 5M TREND: {m5} | 1M SIGNAL: {m1} | "
            f"BUY PRESSURE: {buy_p} | SELL PRESSURE: {sell_p} | "
            f"BUY VOLUME: {buy_v} | SELL VOLUME: {sell_v} | "
            f"SIGNAL: NO TRADE | CONFIDENCE: {confidence} | REASON: {reason}"
        ),
    }


def _fire(
    pick: str,
    *,
    timeframe_key: str,
    pair: str,
    last: dict,
    score: int,
    m5: str,
    m1: str,
    buy_p: str,
    sell_p: str,
    buy_v: str,
    sell_v: str,
    confirms: list[str],
) -> dict[str, Any]:
    entry = last["c"]
    loss = EXIT_LOSS_PCT / 100.0
    profit = EXIT_PROFIT_PCT / 100.0
    if pick == "LONG":
        sl, tp = entry * (1.0 - loss), entry * (1.0 + profit)
        action = "BUY"
    else:
        sl, tp = entry * (1.0 + loss), entry * (1.0 - profit)
        action = "SELL"
    reason = f"{pick} stronger side | 5M={m5} 1M={m1}; {' / '.join(confirms[:5]) or 'imbalance'}"
    return {
        "action": action,
        "reason": reason,
        "engine": ENGINE_NAME,
        "entry_pattern": ENTRY_PATTERN_NAME,
        "pattern": f"Master Brain {pick}",
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "risk_reward": EXIT_PROFIT_PCT / EXIT_LOSS_PCT,
        "confidence": score,
        "timeframe_key": timeframe_key,
        "pair": pair,
        "scalp": True,
        "direction": pick,
        "m5": m5,
        "m1": m1,
        "buy_pressure": buy_p,
        "sell_pressure": sell_p,
        "buy_volume": buy_v,
        "sell_volume": sell_v,
        "signal": pick,
        "confirms": confirms,
        "brain_memory": BRAIN_MEMORY,
        "output": (
            f"TIMEFRAME: {timeframe_key.upper()} | 5M TREND: {m5} | 1M SIGNAL: {m1} | "
            f"BUY PRESSURE: {buy_p} | SELL PRESSURE: {sell_p} | "
            f"BUY VOLUME: {buy_v} | SELL VOLUME: {sell_v} | "
            f"SIGNAL: {pick} | CONFIDENCE: {score} | REASON: {reason}"
        ),
    }


def evaluate_scalp_entry(
    candles: list[dict],
    timeframe_key: str,
    *,
    pair: str = "default",
    htf_candles: list[dict] | None = None,
    candles_1m: list[dict] | None = None,
    candles_5m: list[dict] | None = None,
) -> dict[str, Any]:
    # Non-scalp TF is routing, not a market NO TRADE — return neutral skip for router.
    if not is_scalp_timeframe(timeframe_key):
        return _no_trade("scalp engine only for 1m/5m", timeframe_key, pair)

    tf = (timeframe_key or "1m").strip().lower()
    c1 = candles_1m or (candles if tf in ("1m", "30s") else None)
    c5 = candles_5m or htf_candles or (candles if tf == "5m" else None)

    # Bootstrap with whatever is available (never invent a 4th NO TRADE for market).
    if not c1 or len(c1) < 4:
        c1 = candles or c1
    if not c5 or len(c5) < 4:
        c5 = htf_candles or candles_5m or c1
    if not c1 or len(c1) < 4 or not c5 or len(c5) < 4:
        # Not enough ticks yet — treat as low confidence (allowed reason 3).
        return _no_trade(
            "NO TRADE: overall signal confidence is low (insufficient bars)",
            timeframe_key, pair, confidence=0,
        )

    n = min(LOOKBACK, max(2, len(c1) // 2))
    m5 = _m5_trend(c5)
    cur = _window(c1[-n:])
    prev = _window(c1[-n * 2 : -n]) if len(c1) >= n * 2 else _window(c1[: max(1, len(c1) - n)])
    if not cur:
        return _no_trade(
            "NO TRADE: overall signal confidence is low (no readable window)",
            timeframe_key, pair, confidence=0,
        )
    if not prev:
        prev = cur

    d = _delta(cur, prev)
    m1 = _m1_signal(cur, d)
    buy_p = _pressure_lvl(cur, prev, "BUY")
    sell_p = _pressure_lvl(cur, prev, "SELL")
    buy_v = _vol_lvl(cur, prev, "BUY")
    sell_v = _vol_lvl(cur, prev, "SELL")
    last = cur["last"]

    # ——— ONLY THREE NO TRADE CHECKS ———
    # (1) Buyer Pressure and Seller Pressure balanced
    pressure_balanced = _balanced(cur["buyer_act"], cur["seller_act"])
    # (2) Buy Volume and Sell Volume balanced
    volume_balanced = _balanced(cur["buy_vol"], cur["sell_vol"])

    long_c: list[str] = []
    short_c: list[str] = []

    # Buying pressure
    if d["buyer_up"] and d["buy_vol_up"] and cur["price_up"]:
        long_c.append("buyer+buy_vol+price")
    if d["buyer_up"] and d["buy_vol_up"] and d["total_up"] and cur["price_up"]:
        long_c.append("buying_pressure")

    # Selling pressure
    if d["seller_up"] and d["sell_vol_up"] and cur["price_dn"]:
        short_c.append("seller+sell_vol+price")
    if d["seller_up"] and d["sell_vol_up"] and d["total_up"] and cur["price_dn"]:
        short_c.append("selling_pressure")

    # Buyer trap → SHORT
    if d["buyer_strong"] and not cur["price_up"] and not d["buy_vol_up"]:
        short_c.append("buyer_trap")
    # Seller trap → LONG
    if d["seller_strong"] and not cur["price_dn"] and not d["sell_vol_up"]:
        long_c.append("seller_trap")

    # Volume–price conflict
    if d["buy_vol_up"] and cur["price_dn"]:
        short_c.append("buy_vol_up_price_dn")
    if d["sell_vol_up"] and cur["price_up"]:
        long_c.append("sell_vol_up_price_up")

    # Who dominates volume / pressure
    if cur["buy_dom"]:
        long_c.append("buy_volume_dominates")
    if cur["sell_dom"]:
        short_c.append("sell_volume_dominates")
    if cur["buyer_act"] > cur["seller_act"] * (1.0 + BALANCE_EPS):
        long_c.append("buyer_pressure_dominates")
    if cur["seller_act"] > cur["buyer_act"] * (1.0 + BALANCE_EPS):
        short_c.append("seller_pressure_dominates")

    if m5 == "BULLISH":
        long_c.append("5m_bullish")
    elif m5 == "BEARISH":
        short_c.append("5m_bearish")
    if m1 == "BULLISH":
        long_c.append("1m_bullish")
    elif m1 == "BEARISH":
        short_c.append("1m_bearish")

    # Price tilt as soft vote when everything else is thin
    if cur["price_up"]:
        long_c.append("price_up")
    if cur["price_dn"]:
        short_c.append("price_dn")
    if last["bull"]:
        long_c.append("bull_candle")
    if last["bear"]:
        short_c.append("bear_candle")

    long_c = list(dict.fromkeys(long_c))
    short_c = list(dict.fromkeys(short_c))

    def _score(side: str) -> int:
        confirms = long_c if side == "LONG" else short_c
        pts = min(40, len(confirms) * 6)  # base from stacked confirms
        if (side == "LONG" and m5 == "BULLISH") or (side == "SHORT" and m5 == "BEARISH"):
            pts += 18
        if (side == "LONG" and m1 == "BULLISH") or (side == "SHORT" and m1 == "BEARISH"):
            pts += 14
        if (side == "LONG" and cur["price_up"]) or (side == "SHORT" and cur["price_dn"]):
            pts += 12
        if (side == "LONG" and cur["buy_dom"]) or (side == "SHORT" and cur["sell_dom"]):
            pts += 12
        if (side == "LONG" and d["buyer_up"]) or (side == "SHORT" and d["seller_up"]):
            pts += 10
        if d["total_up"] and (
            (side == "LONG" and cur["buy_dom"]) or (side == "SHORT" and cur["sell_dom"])
        ):
            pts += 8
        if side == "LONG" and "seller_trap" in confirms:
            pts += 6
        if side == "SHORT" and "buyer_trap" in confirms:
            pts += 6
        return min(100, pts)

    long_score = _score("LONG")
    short_score = _score("SHORT")
    best = max(long_score, short_score)

    meta = dict(m5=m5, m1=m1, buy_p=buy_p, sell_p=sell_p, buy_v=buy_v, sell_v=sell_v)

    if pressure_balanced:
        return _no_trade(
            "NO TRADE: Buyer Pressure and Seller Pressure are balanced",
            timeframe_key, pair, confidence=best, **meta,
        )
    if volume_balanced:
        return _no_trade(
            "NO TRADE: Buy Volume and Sell Volume are balanced",
            timeframe_key, pair, confidence=best, **meta,
        )
    if best < MIN_CONFIDENCE:
        return _no_trade(
            f"NO TRADE: overall signal confidence is low ({best}<{MIN_CONFIDENCE})",
            timeframe_key, pair, confidence=best, **meta,
        )

    # Always pick stronger side (conflict OK — no WAIT)
    if long_score > short_score:
        pick = "LONG"
    elif short_score > long_score:
        pick = "SHORT"
    elif m5 == "BULLISH":
        pick = "LONG"
    elif m5 == "BEARISH":
        pick = "SHORT"
    elif m1 == "BULLISH":
        pick = "LONG"
    elif m1 == "BEARISH":
        pick = "SHORT"
    elif cur["price_up"] or last["bull"]:
        pick = "LONG"
    else:
        pick = "SHORT"

    score = long_score if pick == "LONG" else short_score
    confirms = long_c if pick == "LONG" else short_c
    return _fire(
        pick,
        timeframe_key=timeframe_key,
        pair=pair,
        last=last,
        score=score,
        m5=m5,
        m1=m1,
        buy_p=buy_p,
        sell_p=sell_p,
        buy_v=buy_v,
        sell_v=sell_v,
        confirms=confirms,
    )
