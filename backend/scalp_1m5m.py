"""AI TRADING AGENT — MASTER BRAIN (1M entry / 5M direction).

Strict doctrine only. No extra patterns, EMA stacks, or bible compare.
Read market → confirm → LONG / SHORT / NO TRADE.
Exits: fixed ±0.5% in main.py.
"""
from __future__ import annotations

from typing import Any

ENGINE_NAME = "scalp_1m5m"
ENTRY_PATTERN_NAME = "SCALP_1M5M_V1"
SCALP_TFS = frozenset({"1m", "5m", "30s", "1M", "5M", "30S"})

LOOKBACK = 5
MIN_1M = 16
MIN_5M = 12
MIN_CONFIDENCE = 60
MIN_CONFIRMS = 3
EPS = 0.015
EXIT_LOSS_PCT = 0.5
EXIT_PROFIT_PCT = 0.5

BRAIN_MEMORY = (
    "5M determines dominant direction. 1M determines entry timing only. "
    "Never trade every candle. Never decide from one condition. "
    "Sequence: 5M TREND → 1M SETUP → BUY/SELL ACTIVITY → VOLUME → PRICE → SIGNAL → ENTRY. "
    "Strong confirmation = Trade. Weak = NO TRADE. Conflict = WAIT. Protect capital first."
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


def _dn(now: float, prev: float, thr: float = EPS) -> bool:
    if prev <= 0:
        return False
    return (prev - now) / prev >= thr


def _lvl(ratio: float) -> str:
    if ratio >= 1.25:
        return "HIGH"
    if ratio >= 1.05:
        return "MEDIUM"
    return "LOW"


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
    avg_rng = sum(b["rng"] for b in bars) / len(bars)
    avg_str = sum(b["strength"] for b in bars) / len(bars)
    return {
        "price_chg": price_chg,
        "price_up": price_chg > EPS,
        "price_dn": price_chg < -EPS,
        "price_flat": abs(price_chg) <= EPS,
        "buy_vol": buy_vol,
        "sell_vol": sell_vol,
        "total": total,
        "buyer_act": buyer_act,
        "seller_act": seller_act,
        "buy_dom": buy_vol > sell_vol * 1.08,
        "sell_dom": sell_vol > buy_vol * 1.08,
        "balanced_vol": abs(buy_vol - sell_vol) / max(buy_vol + sell_vol, 1e-12) < 0.08,
        "strength": last["strength"],
        "avg_strength": avg_str,
        "avg_rng": avg_rng,
        "last": last,
        "bull_count": sum(1 for b in bars if b["bull"]),
        "bear_count": sum(1 for b in bars if b["bear"]),
        "bars": bars,
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
        "total_low": cur["total"] < prev["total"] * 0.55 if prev["total"] > 0 else False,
    }


def _m5_trend(c5: list[dict]) -> str:
    """STEP 1 — 5M dominant direction. Never overridden by weak 1M."""
    cur = _window(c5[-LOOKBACK:])
    if not cur:
        return "NEUTRAL"
    votes_long = 0
    votes_short = 0
    if cur["price_up"]:
        votes_long += 2
    if cur["price_dn"]:
        votes_short += 2
    if cur["buy_dom"]:
        votes_long += 1
    if cur["sell_dom"]:
        votes_short += 1
    if cur["buyer_act"] > cur["seller_act"] * 1.08:
        votes_long += 1
    if cur["seller_act"] > cur["buyer_act"] * 1.08:
        votes_short += 1
    if cur["bull_count"] > cur["bear_count"]:
        votes_long += 1
    if cur["bear_count"] > cur["bull_count"]:
        votes_short += 1
    if cur["avg_strength"] >= 0.45 and cur["price_up"]:
        votes_long += 1
    if cur["avg_strength"] >= 0.45 and cur["price_dn"]:
        votes_short += 1
    if votes_long >= 3 and votes_long > votes_short:
        return "BULLISH"
    if votes_short >= 3 and votes_short > votes_long:
        return "BEARISH"
    return "NEUTRAL"


def _m1_signal(cur: dict, d: dict) -> str:
    """STEP 2 — 1M entry timing from current vs previous 3–5 candles."""
    long_pts = 0
    short_pts = 0
    if cur["price_up"]:
        long_pts += 1
    if cur["price_dn"]:
        short_pts += 1
    if d["buyer_up"]:
        long_pts += 1
    if d["seller_up"]:
        short_pts += 1
    if d["buy_vol_up"]:
        long_pts += 1
    if d["sell_vol_up"]:
        short_pts += 1
    if cur["last"]["bull"]:
        long_pts += 1
    if cur["last"]["bear"]:
        short_pts += 1
    if cur["buy_dom"]:
        long_pts += 1
    if cur["sell_dom"]:
        short_pts += 1
    if long_pts >= 3 and long_pts > short_pts:
        return "BULLISH"
    if short_pts >= 3 and short_pts > long_pts:
        return "BEARISH"
    return "NEUTRAL"


def _pressure_lvl(cur: dict, prev: dict, side: str) -> str:
    if side == "BUY":
        ratio = cur["buyer_act"] / max(prev["buyer_act"], 1e-12)
    else:
        ratio = cur["seller_act"] / max(prev["seller_act"], 1e-12)
    return _lvl(ratio)


def _vol_lvl(cur: dict, prev: dict, side: str) -> str:
    if side == "BUY":
        ratio = cur["buy_vol"] / max(prev["buy_vol"], 1e-12)
    else:
        ratio = cur["sell_vol"] / max(prev["sell_vol"], 1e-12)
    return _lvl(ratio)


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
        return _no_trade("scalp engine only for 1m/5m", timeframe_key, pair)

    tf = (timeframe_key or "1m").strip().lower()
    c1 = candles_1m or (candles if tf in ("1m", "30s") else None)
    c5 = candles_5m or htf_candles or (candles if tf == "5m" else None)

    if not c1 or len(c1) < MIN_1M:
        return _no_trade(f"Need {MIN_1M}+ 1m candles", timeframe_key, pair)
    if not c5 or len(c5) < MIN_5M:
        return _no_trade(f"Need {MIN_5M}+ 5m candles", timeframe_key, pair)
    if len(c1) < LOOKBACK * 2:
        return _no_trade("Need previous 3–5 candles to compare", timeframe_key, pair)

    # STEP 1 — 5M
    m5 = _m5_trend(c5)

    # STEP 2 — 1M current vs previous window
    cur = _window(c1[-LOOKBACK:])
    prev = _window(c1[-LOOKBACK * 2 : -LOOKBACK])
    if not cur or not prev:
        return _no_trade("Unable to compare 1m windows", timeframe_key, pair)

    d = _delta(cur, prev)
    m1 = _m1_signal(cur, d)
    buy_p = _pressure_lvl(cur, prev, "BUY")
    sell_p = _pressure_lvl(cur, prev, "SELL")
    buy_v = _vol_lvl(cur, prev, "BUY")
    sell_v = _vol_lvl(cur, prev, "SELL")
    last = cur["last"]

    long_c: list[str] = []
    short_c: list[str] = []

    # STEP 3 — Buying pressure
    if d["buyer_up"] and d["buy_vol_up"] and d["total_up"] and cur["price_up"]:
        long_c.append("buying_pressure")
        if d["buyer_strong"] and buy_v == "HIGH":
            long_c.append("strong_long")

    # STEP 4 — Selling pressure
    if d["seller_up"] and d["sell_vol_up"] and d["total_up"] and cur["price_dn"]:
        short_c.append("selling_pressure")
        if d["seller_strong"] and sell_v == "HIGH":
            short_c.append("strong_short")

    # STEP 5 — Buyer exhaustion / trap
    if d["buyer_strong"] and not cur["price_up"] and not d["buy_vol_up"]:
        short_c.append("buyer_trap")

    # STEP 6 — Seller exhaustion / trap
    if d["seller_strong"] and not cur["price_dn"] and not d["sell_vol_up"]:
        long_c.append("seller_trap")

    # STEP 7 — Volume–price conflict (absorption) — need extra confirm later
    absorb_short = d["buy_vol_up"] and cur["price_dn"]
    absorb_long = d["sell_vol_up"] and cur["price_up"]
    if absorb_short:
        short_c.append("buy_vol_up_price_dn")
    if absorb_long:
        long_c.append("sell_vol_up_price_up")

    # STEP 8 — Who creates volume?
    if cur["buy_dom"]:
        long_c.append("buy_volume_dominates")
    elif cur["sell_dom"]:
        short_c.append("sell_volume_dominates")

    if m5 == "BULLISH":
        long_c.append("5m_bullish")
    elif m5 == "BEARISH":
        short_c.append("5m_bearish")
    if m1 == "BULLISH":
        long_c.append("1m_bullish")
    elif m1 == "BEARISH":
        short_c.append("1m_bearish")

    long_c = list(dict.fromkeys(long_c))
    short_c = list(dict.fromkeys(short_c))

    # STEP 9 — 5M + 1M confirmation / WAIT
    if m5 == "BULLISH" and m1 == "BEARISH":
        return _no_trade(
            "WAIT: 5M bullish vs 1M bearish",
            timeframe_key, pair,
            m5=m5, m1=m1, buy_p=buy_p, sell_p=sell_p, buy_v=buy_v, sell_v=sell_v,
        )
    if m5 == "BEARISH" and m1 == "BULLISH":
        return _no_trade(
            "WAIT: 5M bearish vs 1M bullish",
            timeframe_key, pair,
            m5=m5, m1=m1, buy_p=buy_p, sell_p=sell_p, buy_v=buy_v, sell_v=sell_v,
        )

    # STEP 11 — NO TRADE filters
    if cur["balanced_vol"] and abs(cur["buyer_act"] - cur["seller_act"]) / max(
        cur["buyer_act"] + cur["seller_act"], 1e-12
    ) < 0.08:
        return _no_trade(
            "NO TRADE: buyer/seller and volume balanced",
            timeframe_key, pair,
            m5=m5, m1=m1, buy_p=buy_p, sell_p=sell_p, buy_v=buy_v, sell_v=sell_v,
        )
    if d["total_low"]:
        return _no_trade(
            "NO TRADE: unusually low volume",
            timeframe_key, pair,
            m5=m5, m1=m1, buy_p=buy_p, sell_p=sell_p, buy_v=buy_v, sell_v=sell_v,
        )
    if cur["price_flat"] and last["strength"] < 0.35:
        return _no_trade(
            "NO TRADE: sideways / candle too small",
            timeframe_key, pair,
            m5=m5, m1=m1, buy_p=buy_p, sell_p=sell_p, buy_v=buy_v, sell_v=sell_v,
        )
    if m5 == "NEUTRAL" and m1 == "NEUTRAL":
        return _no_trade(
            "NO TRADE: 5M and 1M neutral",
            timeframe_key, pair,
            m5=m5, m1=m1, buy_p=buy_p, sell_p=sell_p, buy_v=buy_v, sell_v=sell_v,
        )

    # STEP 12 — Confidence 0–100
    def _score(side: str) -> int:
        confirms = long_c if side == "LONG" else short_c
        pts = 0
        if (side == "LONG" and m5 == "BULLISH") or (side == "SHORT" and m5 == "BEARISH"):
            pts += 22
        if (side == "LONG" and m1 == "BULLISH") or (side == "SHORT" and m1 == "BEARISH"):
            pts += 18
        if (side == "LONG" and cur["price_up"]) or (side == "SHORT" and cur["price_dn"]):
            pts += 14
        if (side == "LONG" and cur["buy_dom"]) or (side == "SHORT" and cur["sell_dom"]):
            pts += 14
        if d["total_up"] and (
            (side == "LONG" and cur["buy_dom"]) or (side == "SHORT" and cur["sell_dom"])
        ):
            pts += 10
        if (side == "LONG" and d["buyer_up"]) or (side == "SHORT" and d["seller_up"]):
            pts += 12
        if last["strength"] >= 0.45 and (
            (side == "LONG" and last["bull"]) or (side == "SHORT" and last["bear"])
        ):
            pts += 10
        if side == "LONG" and ("seller_trap" in confirms or absorb_long):
            pts += 6
        if side == "SHORT" and ("buyer_trap" in confirms or absorb_short):
            pts += 6
        return min(100, pts)

    long_score = _score("LONG")
    short_score = _score("SHORT")

    # STEP 10 — Entry logic (5M + 1M + activity + volume + price)
    pick = None
    if (
        m5 == "BULLISH"
        and m1 == "BULLISH"
        and d["buy_vol_up"]
        and d["buyer_up"]
        and (cur["price_up"] or last["bull"])
        and long_score >= MIN_CONFIDENCE
        and len(long_c) >= MIN_CONFIRMS
    ):
        pick = "LONG"
    elif (
        m5 == "BEARISH"
        and m1 == "BEARISH"
        and d["sell_vol_up"]
        and d["seller_up"]
        and (cur["price_dn"] or last["bear"])
        and short_score >= MIN_CONFIDENCE
        and len(short_c) >= MIN_CONFIRMS
    ):
        pick = "SHORT"
    # Strong trap/absorption may fire with aligned 5M even if 1M was WAIT-blocked above
    elif (
        m5 == "BULLISH"
        and "seller_trap" in long_c
        and long_score >= MIN_CONFIDENCE
        and len(long_c) >= MIN_CONFIRMS
        and m1 != "BEARISH"
    ):
        pick = "LONG"
    elif (
        m5 == "BEARISH"
        and "buyer_trap" in short_c
        and short_score >= MIN_CONFIDENCE
        and len(short_c) >= MIN_CONFIRMS
        and m1 != "BULLISH"
    ):
        pick = "SHORT"

    if pick is None:
        return _no_trade(
            (
                f"NO TRADE: weak confirmation L{long_score}/S{short_score} "
                f"(need {MIN_CONFIDENCE}+ and {MIN_CONFIRMS}+ confirms)"
            ),
            timeframe_key, pair,
            m5=m5, m1=m1, buy_p=buy_p, sell_p=sell_p, buy_v=buy_v, sell_v=sell_v,
            confidence=max(long_score, short_score),
        )

    score = long_score if pick == "LONG" else short_score
    confirms = long_c if pick == "LONG" else short_c
    action = "BUY" if pick == "LONG" else "SELL"
    entry = last["c"]
    loss = EXIT_LOSS_PCT / 100.0
    profit = EXIT_PROFIT_PCT / 100.0
    if pick == "LONG":
        sl, tp = entry * (1.0 - loss), entry * (1.0 + profit)
    else:
        sl, tp = entry * (1.0 + loss), entry * (1.0 - profit)

    reason = f"{pick} via 5M {m5} + 1M {m1}; {' / '.join(confirms[:4])}"
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
