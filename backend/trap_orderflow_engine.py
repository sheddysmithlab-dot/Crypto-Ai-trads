"""Order-flow TRAP DETECTION ENGINE (1M execution + 5M bias).

Integrates alongside brain.py structure traps (bull/bear trap, spring, upthrust).
Bybit linear klines do not expose separate Buy/Sell volume, so we derive proxies
from OHLC + total volume (standard volume-split heuristic) unless fields are
already present on the candle dict.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

EPS = 1e-12

# Calibratable thresholds (policy defaults)
THR_PRESSURE = 0.60
THR_RV_VOL = 1.20
THR_PRICE_EFFORT = 0.25
THR_UPPER_WICK = 0.35
THR_LOWER_WICK = 0.35
THR_BODY_RATIO = 0.35
THR_Z_ACT = 1.0
THR_Z_EXHAUST = 1.5
THR_Z_TVOL = 1.0
THR_FAKE_WICK = 0.30
THR_BREAK_ATR = 0.10
THR_BALANCED = 0.05
THR_SCORE = 65.0  # default floor (5m / 15m / 1h / 1d)
THR_SCORE_1M = 75.0  # tighter pattern confidence floor — 1m only
THR_RV_PRICE_WEAK = 0.70
LOOKBACK = 20


def thr_score_for_tf(exec_tf: str | None) -> float:
    """Pattern confidence floor (0–100). Only 1m uses the raised 75% gate."""
    tf = (exec_tf or "").strip().lower()
    if tf == "1m":
        return THR_SCORE_1M
    return THR_SCORE


@dataclass
class FlowBar:
    open: float
    high: float
    low: float
    close: float
    volume: float
    buy_volume: float
    sell_volume: float
    buyer_qty: float
    seller_qty: float
    close_time: int = 0


@dataclass
class TrapOFResult:
    timeframe: str
    pattern: str
    bias_5m: str
    buy_pressure: float
    sell_pressure: float
    buy_volume_ratio: float
    sell_volume_ratio: float
    long_score: float
    short_score: float
    final_signal: str  # LONG | SHORT | NO_TRADE
    confidence: float
    primary_reason: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timeframe": self.timeframe,
            "pattern": self.pattern,
            "bias_5m": self.bias_5m,
            "buy_pressure": round(self.buy_pressure, 4),
            "sell_pressure": round(self.sell_pressure, 4),
            "buy_volume_ratio": round(self.buy_volume_ratio, 4),
            "sell_volume_ratio": round(self.sell_volume_ratio, 4),
            "long_score": round(self.long_score, 2),
            "short_score": round(self.short_score, 2),
            "final_signal": self.final_signal,
            "confidence": round(self.confidence, 4),
            "primary_reason": self.primary_reason,
            "details": self.details,
            "line": (
                f"{self.timeframe} | {self.pattern} | {self.bias_5m} | "
                f"{self.buy_pressure:.2f} | {self.sell_pressure:.2f} | "
                f"{self.buy_volume_ratio:.2f} | {self.sell_volume_ratio:.2f} | "
                f"{self.long_score:.0f} | {self.short_score:.0f} | "
                f"{self.final_signal} | {self.confidence:.2f} | {self.primary_reason}"
            ),
        }


def _f(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs: Sequence[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return math.sqrt(var) if var > 0 else 0.0


def _ma20(series: Sequence[float], i: int) -> float:
    start = max(0, i - LOOKBACK)
    window = list(series[start:i])
    return _mean(window) if window else (series[i] if i < len(series) else 0.0)


def _std20(series: Sequence[float], i: int) -> float:
    start = max(0, i - LOOKBACK)
    window = list(series[start:i])
    return _std(window)


def _rv(x: float, ma: float) -> float:
    return x / ma if abs(ma) > EPS else 1.0


def _z(x: float, ma: float, sd: float) -> float:
    return (x - ma) / sd if sd > EPS else 0.0


def candle_to_flow(c: dict) -> FlowBar:
    o = _f(c.get("open"))
    h = _f(c.get("high"))
    lo = _f(c.get("low"))
    cl = _f(c.get("close"))
    vol = max(_f(c.get("volume")), 0.0)
    rng = max(h - lo, EPS)

    # Prefer explicit fields when present; else OHLC volume-split proxy.
    buy_v = c.get("buy_volume")
    sell_v = c.get("sell_volume")
    if buy_v is None or sell_v is None:
        buy_v = vol * max(cl - lo, 0.0) / rng
        sell_v = vol * max(h - cl, 0.0) / rng
    else:
        buy_v = max(_f(buy_v), 0.0)
        sell_v = max(_f(sell_v), 0.0)

    buyer_q = c.get("buyer_qty", c.get("buyer_activity"))
    seller_q = c.get("seller_qty", c.get("seller_activity"))
    if buyer_q is None or seller_q is None:
        # Activity proxy: same split (effort share of volume)
        buyer_q = buy_v
        seller_q = sell_v
    else:
        buyer_q = max(_f(buyer_q), 0.0)
        seller_q = max(_f(seller_q), 0.0)

    ct = int(c.get("close_time") or c.get("time") or 0)
    return FlowBar(o, h, lo, cl, vol, float(buy_v), float(sell_v), float(buyer_q), float(seller_q), ct)


def _metrics(bar: FlowBar) -> dict:
    rng = max(bar.high - bar.low, EPS)
    body = abs(bar.close - bar.open)
    upper = (bar.high - max(bar.open, bar.close)) / rng
    lower = (min(bar.open, bar.close) - bar.low) / rng
    body_ratio = body / rng
    price_effort = (bar.close - bar.open) / rng
    total_v = max(bar.buy_volume + bar.sell_volume, EPS)
    buy_ratio = bar.buy_volume / total_v
    sell_ratio = bar.sell_volume / total_v
    act = max(bar.buyer_qty + bar.seller_qty, EPS)
    buyer_ratio = bar.buyer_qty / act
    seller_ratio = bar.seller_qty / act
    vol_pressure = (bar.buy_volume - bar.sell_volume) / total_v
    return {
        "range": rng,
        "body": body,
        "body_ratio": body_ratio,
        "upper_wick": upper,
        "lower_wick": lower,
        "price_effort": price_effort,
        "buy_ratio": buy_ratio,
        "sell_ratio": sell_ratio,
        "buyer_ratio": buyer_ratio,
        "seller_ratio": seller_ratio,
        "volume_pressure": vol_pressure,
    }


def _atr20(bars: Sequence[FlowBar], i: int) -> float:
    start = max(1, i - LOOKBACK)
    trs = []
    for j in range(start, i + 1):
        prev = bars[j - 1].close
        tr = max(
            bars[j].high - bars[j].low,
            abs(bars[j].high - prev),
            abs(bars[j].low - prev),
        )
        trs.append(tr)
    return _mean(trs) if trs else max(bars[i].high - bars[i].low, EPS)


def _classify_tf(m: dict, z_buyer: float, z_seller: float) -> str:
    bull = 0
    bear = 0
    if m["price_effort"] > 0.15:
        bull += 1
    elif m["price_effort"] < -0.15:
        bear += 1
    if m["volume_pressure"] > 0.08:
        bull += 1
    elif m["volume_pressure"] < -0.08:
        bear += 1
    if m["buyer_ratio"] >= 0.55:
        bull += 1
    elif m["seller_ratio"] >= 0.55:
        bear += 1
    if m["lower_wick"] >= 0.35 and m["price_effort"] >= 0:
        bull += 1
    if m["upper_wick"] >= 0.35 and m["price_effort"] <= 0:
        bear += 1
    if z_buyer > z_seller + 0.5:
        bull += 1
    elif z_seller > z_buyer + 0.5:
        bear += 1
    if bull > bear:
        return "bullish"
    if bear > bull:
        return "bearish"
    return "neutral"


def _detect_patterns(
    bars: Sequence[FlowBar],
    i: int,
    m: dict,
    stats: dict,
) -> List[dict]:
    """Return list of {name, side LONG|SHORT, priority, reason}."""
    out: List[dict] = []
    if i < 5 or i >= len(bars):
        return out
    bar = bars[i]
    prev = bars[i - 1] if i >= 1 else None

    buyer_r = m["buyer_ratio"]
    seller_r = m["seller_ratio"]
    buy_ratio = m["buy_ratio"]
    sell_ratio = m["sell_ratio"]
    pe = m["price_effort"]
    atr = stats["atr"]
    z_buyer = stats["z_buyer"]
    z_seller = stats["z_seller"]
    z_tvol = stats["z_tvol"]
    rv_buy = stats["rv_buy"]
    rv_sell = stats["rv_sell"]

    # Confirmation: current bar acts as trap candle if we have next — for live we use
    # previous bar as trap and current as confirmation when available.
    # On live closed candle i, treat i as trap candle (confirmation may be weak).
    mid = (bar.high + bar.low) / 2.0

    # ---- BUY TRAP → SHORT ----
    buy_effort = (buyer_r >= THR_PRESSURE) or (z_buyer >= THR_Z_ACT)
    buy_vol_strong = rv_buy >= THR_RV_VOL
    weak_up = (pe > 0 and pe <= THR_PRICE_EFFORT) or (m["upper_wick"] >= THR_UPPER_WICK)
    conf_below = False
    if prev is not None:
        # if previous was trap-like and current confirms
        pm = _metrics(prev)
        if ((pm["buyer_ratio"] >= THR_PRESSURE) or True) and bar.close < (prev.high + prev.low) / 2.0:
            # mild confirmation helper when current closes under prior mid after buy effort
            pass
    if buy_effort and buy_vol_strong and weak_up:
        out.append({
            "name": "BUY_TRAP",
            "side": "SHORT",
            "priority": 4,
            "reason": "Strong buyer effort / buy volume with weak upside (effort vs result)",
        })
    # confirmation close below midpoint of previous high-effort bar
    if prev is not None:
        pm = _metrics(prev)
        p_stats_buy = (pm["buyer_ratio"] >= THR_PRESSURE) and (_rv(prev.buy_volume, stats["ma_buy"]) >= THR_RV_VOL)
        if p_stats_buy and bar.close < (prev.high + prev.low) / 2.0 and pe <= 0.1:
            out.append({
                "name": "BUY_TRAP",
                "side": "SHORT",
                "priority": 4,
                "reason": "Confirmation close below buy-trap candle midpoint",
            })

    # ---- SELL TRAP → LONG ----
    sell_effort = (seller_r >= THR_PRESSURE) or (z_seller >= THR_Z_ACT)
    sell_vol_strong = rv_sell >= THR_RV_VOL
    weak_dn = (pe < 0 and abs(pe) <= THR_PRICE_EFFORT) or (m["lower_wick"] >= THR_LOWER_WICK)
    if sell_effort and sell_vol_strong and weak_dn:
        out.append({
            "name": "SELL_TRAP",
            "side": "LONG",
            "priority": 4,
            "reason": "Strong seller effort / sell volume with weak downside (effort vs result)",
        })
    if prev is not None:
        pm = _metrics(prev)
        p_stats_sell = (pm["seller_ratio"] >= THR_PRESSURE) and (_rv(prev.sell_volume, stats["ma_sell"]) >= THR_RV_VOL)
        if p_stats_sell and bar.close > (prev.high + prev.low) / 2.0 and pe >= -0.1:
            out.append({
                "name": "SELL_TRAP",
                "side": "LONG",
                "priority": 4,
                "reason": "Confirmation close above sell-trap candle midpoint",
            })

    # ---- ABSORPTION ----
    if z_tvol >= THR_Z_TVOL and m["body_ratio"] <= THR_BODY_RATIO and max(buy_ratio, sell_ratio) >= THR_PRESSURE:
        if sell_ratio >= THR_PRESSURE and pe >= -0.15:
            out.append({
                "name": "ABSORPTION",
                "side": "LONG",
                "priority": 1,
                "reason": "High sell volume absorbed - price failed to break lower",
            })
        if buy_ratio >= THR_PRESSURE and pe <= 0.15:
            out.append({
                "name": "ABSORPTION",
                "side": "SHORT",
                "priority": 1,
                "reason": "High buy volume absorbed - price failed to break higher",
            })

    # ---- EXHAUSTION ----
    # price move vs prior 3-5 bars
    look = bars[max(0, i - 5):i]
    if look:
        prev_moves = [abs(b.close - b.open) for b in look]
        ma_move = _mean(prev_moves) or EPS
        rv_price = abs(bar.close - bar.open) / ma_move
    else:
        rv_price = 1.0
    if z_buyer >= THR_Z_EXHAUST and rv_price < THR_RV_PRICE_WEAK:
        out.append({
            "name": "EXHAUSTION",
            "side": "SHORT",
            "priority": 5,
            "reason": "Buyer activity extreme with weakening upside extension",
        })
    if z_seller >= THR_Z_EXHAUST and rv_price < THR_RV_PRICE_WEAK:
        out.append({
            "name": "EXHAUSTION",
            "side": "LONG",
            "priority": 5,
            "reason": "Seller activity extreme with weakening downside extension",
        })

    # ---- FAKE BREAKOUT ----
    window5 = bars[max(0, i - 5):i]
    if window5:
        hh = max(b.high for b in window5)
        ll = min(b.low for b in window5)
        broke_up = bar.high >= hh + THR_BREAK_ATR * atr
        broke_dn = bar.low <= ll - THR_BREAK_ATR * atr
        if broke_up and bar.close < hh:
            if m["upper_wick"] >= THR_FAKE_WICK and sell_ratio > buy_ratio:
                out.append({
                    "name": "FAKE_BREAKOUT",
                    "side": "SHORT",
                    "priority": 2,
                    "reason": "Upside fake breakout - close back inside + sell pressure",
                })
            elif prev and bar.close < (prev.high + prev.low) / 2.0 and broke_up:
                out.append({
                    "name": "FAKE_BREAKOUT",
                    "side": "SHORT",
                    "priority": 2,
                    "reason": "Upside break then confirmation under midpoint",
                })
        if broke_dn and bar.close > ll:
            if m["lower_wick"] >= THR_FAKE_WICK and buy_ratio > sell_ratio:
                out.append({
                    "name": "FAKE_BREAKOUT",
                    "side": "LONG",
                    "priority": 2,
                    "reason": "Downside fake breakout - close back inside + buy pressure",
                })
            elif prev and bar.close > (prev.high + prev.low) / 2.0 and broke_dn:
                out.append({
                    "name": "FAKE_BREAKOUT",
                    "side": "LONG",
                    "priority": 2,
                    "reason": "Downside break then confirmation above midpoint",
                })

    # ---- REVERSAL TRAP ----
    if window5:
        hh = max(b.high for b in window5)
        ll = min(b.low for b in window5)
        buy_vols = [b.buy_volume for b in window5]
        sell_vols = [b.sell_volume for b in window5]
        buyer_qs = [b.buyer_qty for b in window5]
        seller_qs = [b.seller_qty for b in window5]
        if bar.high > hh:
            flow_fail = (bar.buy_volume <= max(buy_vols)) or (bar.buyer_qty <= max(buyer_qs)) or (sell_ratio > buy_ratio)
            close_fail = bar.close < bar.high - 0.4 * (bar.high - bar.low)
            if flow_fail or close_fail:
                out.append({
                    "name": "REVERSAL_TRAP",
                    "side": "SHORT",
                    "priority": 3,
                    "reason": "New high without confirming buy flow / closed off highs",
                })
        if bar.low < ll:
            flow_fail = (bar.sell_volume <= max(sell_vols)) or (bar.seller_qty <= max(seller_qs)) or (buy_ratio > sell_ratio)
            close_fail = bar.close > bar.low + 0.4 * (bar.high - bar.low)
            if flow_fail or close_fail:
                out.append({
                    "name": "REVERSAL_TRAP",
                    "side": "LONG",
                    "priority": 3,
                    "reason": "New low without confirming sell flow / closed off lows",
                })

    # Deduplicate by name+side keeping best priority (lower number = higher priority)
    best: Dict[str, dict] = {}
    for p in out:
        key = f"{p['name']}:{p['side']}"
        if key not in best or p["priority"] < best[key]["priority"]:
            best[key] = p
    return list(best.values())


def _score_side(
    side: str,
    bias_5m: str,
    setup_1m: Optional[dict],
    m: dict,
    tf_dir_1m: str,
    contradiction: bool,
) -> float:
    score = 0.0
    # 5M direction 25
    if bias_5m == "bullish" and side == "LONG":
        score += 25
    elif bias_5m == "bearish" and side == "SHORT":
        score += 25
    elif bias_5m == "neutral":
        score += 12
    else:
        score += 5  # opposing

    # 1M setup 25
    if setup_1m and setup_1m.get("side") == side:
        score += 25
    elif tf_dir_1m == ("bullish" if side == "LONG" else "bearish"):
        score += 12
    else:
        score += 5

    # Volume pressure 20
    vp = m["volume_pressure"]
    if side == "LONG":
        score += max(0.0, min(20.0, (vp + 0.5) * 20))
    else:
        score += max(0.0, min(20.0, (0.5 - vp) * 20))

    # Buyer/seller imbalance 15
    if side == "LONG":
        # for LONG we often want seller exhaustion / absorption of sells — use sell trap style
        # Prefer seller_ratio high for sell-trap longs, else buyer reclaim
        imb = max(m["seller_ratio"] - 0.5, m["buyer_ratio"] - 0.5, 0)
        score += min(15.0, imb * 30)
    else:
        imb = max(m["buyer_ratio"] - 0.5, m["seller_ratio"] - 0.5, 0)
        score += min(15.0, imb * 30)

    # Candle confirmation 15
    if side == "LONG" and m["price_effort"] >= 0:
        score += min(15.0, 8 + m["lower_wick"] * 10)
    elif side == "SHORT" and m["price_effort"] <= 0:
        score += min(15.0, 8 + m["upper_wick"] * 10)
    else:
        score += 4

    if setup_1m and setup_1m.get("side") == side:
        score += 10  # confirmed pattern bonus
    if contradiction:
        score -= 10
    return score


def evaluate_trap_orderflow(
    candles_1m: Optional[Sequence[dict]],
    candles_5m: Optional[Sequence[dict]],
    *,
    exec_tf: str = "1m",
) -> TrapOFResult:
    """Run 5M bias + 1M execution trap policy. Falls back if only one TF present."""
    bars_1m = [candle_to_flow(c) for c in (candles_1m or [])]
    bars_5m = [candle_to_flow(c) for c in (candles_5m or [])]

    # If only one series provided, use it for both context and execution
    if len(bars_1m) < LOOKBACK + 5 and len(bars_5m) >= LOOKBACK + 5:
        bars_1m = bars_5m
        exec_tf = "5m"
    if len(bars_5m) < LOOKBACK + 5 and len(bars_1m) >= LOOKBACK + 5:
        bars_5m = bars_1m

    if len(bars_1m) < LOOKBACK + 5:
        return TrapOFResult(
            timeframe=exec_tf.upper(),
            pattern="NONE",
            bias_5m="neutral",
            buy_pressure=0.5,
            sell_pressure=0.5,
            buy_volume_ratio=0.5,
            sell_volume_ratio=0.5,
            long_score=0,
            short_score=0,
            final_signal="NO_TRADE",
            confidence=0.0,
            primary_reason="Insufficient candles for order-flow trap (need 25+)",
        )

    def _stats_at(bars: Sequence[FlowBar], i: int) -> dict:
        buys = [b.buy_volume for b in bars]
        sells = [b.sell_volume for b in bars]
        buyers = [b.buyer_qty for b in bars]
        sellers = [b.seller_qty for b in bars]
        vols = [b.volume for b in bars]
        ma_buy = _ma20(buys, i)
        ma_sell = _ma20(sells, i)
        return {
            "ma_buy": ma_buy,
            "ma_sell": ma_sell,
            "rv_buy": _rv(bars[i].buy_volume, ma_buy),
            "rv_sell": _rv(bars[i].sell_volume, ma_sell),
            "z_buyer": _z(bars[i].buyer_qty, _ma20(buyers, i), _std20(buyers, i)),
            "z_seller": _z(bars[i].seller_qty, _ma20(sellers, i), _std20(sellers, i)),
            "z_tvol": _z(bars[i].volume, _ma20(vols, i), _std20(vols, i)),
            "atr": _atr20(bars, i),
        }

    i5 = len(bars_5m) - 1
    i1 = len(bars_1m) - 1
    m5 = _metrics(bars_5m[i5])
    m1 = _metrics(bars_1m[i1])
    st5 = _stats_at(bars_5m, i5)
    st1 = _stats_at(bars_1m, i1)

    bias_5m = _classify_tf(m5, st5["z_buyer"], st5["z_seller"])
    dir_1m = _classify_tf(m1, st1["z_buyer"], st1["z_seller"])

    pats_5 = _detect_patterns(bars_5m, i5, m5, st5)
    pats_1 = _detect_patterns(bars_1m, i1, m1, st1)

    # Pattern priority: Confirmed Absorption > Fake Breakout > Reversal Trap > Buy/Sell Trap > Exhaustion
    def pick(patterns: List[dict]) -> Optional[dict]:
        if not patterns:
            return None
        return sorted(patterns, key=lambda p: (p["priority"], p["name"]))[0]

    setup_5 = pick(pats_5)
    setup_1 = pick(pats_1)

    # 5M confirmed trap/absorption sets directional bias stronger
    if setup_5 and setup_5["name"] in ("ABSORPTION", "FAKE_BREAKOUT", "REVERSAL_TRAP", "BUY_TRAP", "SELL_TRAP"):
        if setup_5["side"] == "LONG":
            bias_5m = "bullish"
        else:
            bias_5m = "bearish"

    contradiction = (
        (bias_5m == "bullish" and dir_1m == "bearish")
        or (bias_5m == "bearish" and dir_1m == "bullish")
    )

    long_score = _score_side("LONG", bias_5m, setup_1, m1, dir_1m, contradiction)
    short_score = _score_side("SHORT", bias_5m, setup_1, m1, dir_1m, contradiction)

    # 5M structural override boost
    if setup_5:
        if setup_5["side"] == "LONG":
            long_score += 8
        else:
            short_score += 8

    # NO TRADE gates
    thr = thr_score_for_tf(exec_tf)
    strict_1m = (exec_tf or "").strip().lower() == "1m"
    bal_pressure = abs(m1["buyer_ratio"] - 0.50) < THR_BALANCED
    bal_volume = abs(m1["buy_ratio"] - 0.50) < THR_BALANCED
    max_score = max(long_score, short_score)
    low_conf = max_score < thr

    # Named-pattern ok flags (5m+ may bypass low conf; 1m never does)
    long_ok_pattern = setup_1 and setup_1["side"] == "LONG" and setup_1["name"] in (
        "SELL_TRAP", "ABSORPTION", "EXHAUSTION", "FAKE_BREAKOUT", "REVERSAL_TRAP",
    )
    short_ok_pattern = setup_1 and setup_1["side"] == "SHORT" and setup_1["name"] in (
        "BUY_TRAP", "ABSORPTION", "EXHAUSTION", "FAKE_BREAKOUT", "REVERSAL_TRAP",
    )
    if setup_5 and setup_5["priority"] <= 2:
        if setup_5["side"] == "LONG":
            long_ok_pattern = True
        else:
            short_ok_pattern = True

    if bal_pressure and bal_volume:
        signal = "NO_TRADE"
        reason = "Buyer/Seller pressure and Buy/Sell volume both balanced"
        pattern = "BALANCED"
        conf = 0.0
    elif strict_1m and low_conf:
        # 1m only: score ≥ thr required — pattern / 5m / RAW bypass off
        signal = "NO_TRADE"
        reason = f"1m strict: score {max_score:.0f} < {thr:.0f} (pattern bypass off)"
        pattern = setup_1["name"] if setup_1 else (setup_5["name"] if setup_5 else "NONE")
        conf = max_score / 100.0
    elif low_conf and not (long_ok_pattern or short_ok_pattern):
        signal = "NO_TRADE"
        reason = f"Low confidence (max score {max_score:.0f} < {thr:.0f})"
        pattern = setup_1["name"] if setup_1 else (setup_5["name"] if setup_5 else "NONE")
        conf = max_score / 100.0
    elif strict_1m:
        # 1m: only emit side when that side's score clears thr (no RAW force)
        if long_score >= thr and long_score >= short_score:
            signal = "LONG"
            pattern = (setup_1 or setup_5 or {}).get("name", "IMBALANCE")
            reason = (setup_1 or setup_5 or {}).get(
                "reason", f"1m LONG score {long_score:.0f} ≥ {thr:.0f}"
            )
            conf = min(1.0, long_score / 100.0)
        elif short_score >= thr and short_score > long_score:
            signal = "SHORT"
            pattern = (setup_1 or setup_5 or {}).get("name", "IMBALANCE")
            reason = (setup_1 or setup_5 or {}).get(
                "reason", f"1m SHORT score {short_score:.0f} ≥ {thr:.0f}"
            )
            conf = min(1.0, short_score / 100.0)
        else:
            signal = "NO_TRADE"
            reason = f"1m strict: neither side ≥ {thr:.0f} (L={long_score:.0f} S={short_score:.0f})"
            pattern = setup_1["name"] if setup_1 else (setup_5["name"] if setup_5 else "NONE")
            conf = max_score / 100.0
    else:
        # Higher TFs: keep prior pattern / force behavior
        prefer_long = long_score >= short_score
        if prefer_long and (long_ok_pattern or long_score >= thr or setup_5):
            signal = "LONG"
            pattern = (setup_1 or setup_5 or {}).get("name", "IMBALANCE")
            reason = (setup_1 or setup_5 or {}).get("reason", "Higher LONG score / directional evidence")
            conf = min(1.0, long_score / 100.0)
        elif (not prefer_long) and (short_ok_pattern or short_score >= thr or setup_5):
            signal = "SHORT"
            pattern = (setup_1 or setup_5 or {}).get("name", "IMBALANCE")
            reason = (setup_1 or setup_5 or {}).get("reason", "Higher SHORT score / directional evidence")
            conf = min(1.0, short_score / 100.0)
        elif prefer_long:
            signal = "LONG"
            pattern = (setup_1 or setup_5 or {}).get("name", "RAW_IMBALANCE")
            reason = "Forced directional choice - higher LONG score (NO TRADE gates not met)"
            conf = min(1.0, long_score / 100.0)
        else:
            signal = "SHORT"
            pattern = (setup_1 or setup_5 or {}).get("name", "RAW_IMBALANCE")
            reason = "Forced directional choice - higher SHORT score (NO TRADE gates not met)"
            conf = min(1.0, short_score / 100.0)

    return TrapOFResult(
        timeframe=exec_tf.upper(),
        pattern=pattern,
        bias_5m=bias_5m,
        buy_pressure=m1["buyer_ratio"],
        sell_pressure=m1["seller_ratio"],
        buy_volume_ratio=m1["buy_ratio"],
        sell_volume_ratio=m1["sell_ratio"],
        long_score=long_score,
        short_score=short_score,
        final_signal=signal,
        confidence=conf,
        primary_reason=reason,
        details={
            "dir_1m": dir_1m,
            "patterns_1m": pats_1,
            "patterns_5m": pats_5,
            "setup_1m": setup_1,
            "setup_5m": setup_5,
            "volume_pressure": m1["volume_pressure"],
            "proxy": "ohlc_volume_split",
            "thr_score": thr,
        },
    )


def merge_with_structure_trap(
    of_result: TrapOFResult,
    structure_trap_side: Optional[str],
    structure_trap_type: Optional[str],
) -> TrapOFResult:
    """Blend classic brain.py structure trap with order-flow trap (both kept)."""
    if not structure_trap_side:
        return of_result
    # structure_trap_side is BUY/SELL
    struct_signal = "LONG" if structure_trap_side == "BUY" else "SHORT"
    bonus = 8.0
    long_s = of_result.long_score + (bonus if struct_signal == "LONG" else 0)
    short_s = of_result.short_score + (bonus if struct_signal == "SHORT" else 0)

    # If OF is NO_TRADE only due to low conf but structure agrees with a side, lift it
    thr = thr_score_for_tf(of_result.timeframe)
    strict_1m = (of_result.timeframe or "").strip().lower() == "1m"
    lift_floor = thr if strict_1m else (thr - 5)
    signal = of_result.final_signal
    reason = of_result.primary_reason
    pattern = of_result.pattern
    if of_result.final_signal == "NO_TRADE" and max(long_s, short_s) >= lift_floor:
        signal = "LONG" if long_s >= short_s else "SHORT"
        pattern = f"STRUCTURE_{structure_trap_type or 'TRAP'}+OF"
        reason = f"Structure trap {structure_trap_type} aligned with order-flow scores"
    elif signal == struct_signal:
        pattern = f"{of_result.pattern}+STRUCTURE_{structure_trap_type or 'TRAP'}"
        reason = f"{of_result.primary_reason} | structure {structure_trap_type} agrees"
        long_s = long_s + (5 if signal == "LONG" else 0)
        short_s = short_s + (5 if signal == "SHORT" else 0)
    elif signal in ("LONG", "SHORT") and signal != struct_signal:
        reason = f"{of_result.primary_reason} | note: structure trap wanted {struct_signal}"

    return TrapOFResult(
        timeframe=of_result.timeframe,
        pattern=pattern,
        bias_5m=of_result.bias_5m,
        buy_pressure=of_result.buy_pressure,
        sell_pressure=of_result.sell_pressure,
        buy_volume_ratio=of_result.buy_volume_ratio,
        sell_volume_ratio=of_result.sell_volume_ratio,
        long_score=long_s,
        short_score=short_s,
        final_signal=signal,
        confidence=min(1.0, max(long_s, short_s) / 100.0),
        primary_reason=reason,
        details={**of_result.details, "structure_trap": structure_trap_type, "structure_side": structure_trap_side},
    )
