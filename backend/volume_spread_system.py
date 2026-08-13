"""Candlestick helpers retained for kline parse / sizing compatibility.

Strategy wiped: evaluate_uvss always returns NO_TRADE; blue-box overlay is inactive.
"""
from __future__ import annotations

from dataclasses import dataclass

# Strategy wiped — no auto pattern entries.
UVSS_POLICIES_ENABLED = False
# Cost-aware — OFF unless COST_AWARE_ENABLED=true
UVSS_COST_AWARE_ENTRY = __import__("os").environ.get("COST_AWARE_ENABLED", "false").strip().lower() in (
    "1", "true", "yes", "on",
)
UVSS_SL_EXIT_ENABLED = False

EMA_FAST = 50
EMA_SLOW = 200
BODY_AVG_PERIOD = 20
VOLUME_MA_PERIOD = 20
# Volume gate — set VOLUME_CONFIRM_MULT=0 or SKIP_VOLUME_GATE=true to disable.
VOLUME_CONFIRM_MULT = float(__import__("os").environ.get("VOLUME_CONFIRM_MULT", "0"))
VOLUME_VS_PREV_MULT = float(__import__("os").environ.get("VOLUME_VS_PREV_MULT", "0"))
SKIP_VOLUME_GATE = __import__("os").environ.get("SKIP_VOLUME_GATE", "true").strip().lower() in (
    "1", "true", "yes", "on",
)
VOLUME_SETUP_MULT: dict[str, float] = {
    "pin_bar": 1.8,
    "doji": 1.9,
    "harami": 1.9,
    "tweezer": 1.8,
    "inside_bar": 1.7,
    "engulfing": 1.8,
    "star": 1.7,
    "pierce": 1.7,
    "belt": 1.6,
    "marubozu": 1.6,
    "soldiers": 1.7,
    "crows": 1.7,
}
# Soft relative check OFF by default (PDF has no 3× candle-size hard rule).
CANDLE_TO_VOLUME_MULT = float(__import__("os").environ.get("CANDLE_TO_VOLUME_MULT", "0"))
TREND_LOOKBACK = 5
MIN_CANDLES = max(EMA_SLOW + BODY_AVG_PERIOD + 5, 60)
RISK_PCT_PER_TRADE = 0.01
RR_RATIO = 2.0
SL_BUFFER_PCT = 0.001
LOCAL_SLOPE_PCT = 0.0015

# code → human label
PATTERN_LABELS: dict[str, str] = {
    "BULL_ENGULF": "Bullish Engulfing",
    "BEAR_ENGULF": "Bearish Engulfing",
    "HAMMER": "Hammer (bullish pin)",
    "SHOOTING_STAR": "Shooting Star (bearish pin)",
    "PIN_BULL": "Bullish Pin Bar",
    "PIN_BEAR": "Bearish Pin Bar",
    "MORNING_STAR": "Morning Star",
    "EVENING_STAR": "Evening Star",
    "PIERCING": "Piercing Line",
    "DARK_CLOUD": "Dark Cloud Cover",
    "BULL_HARAMI": "Bullish Harami",
    "BEAR_HARAMI": "Bearish Harami",
    "INSIDE_UP": "Inside Bar Break Up",
    "INSIDE_DOWN": "Inside Bar Break Down",
    "TWEEZER_BOT": "Tweezer Bottom",
    "TWEEZER_TOP": "Tweezer Top",
    "DRAGONFLY": "Dragonfly Doji",
    "GRAVESTONE": "Gravestone Doji",
    "THREE_WHITE": "Three White Soldiers",
    "THREE_BLACK": "Three Black Crows",
    "BULL_BELT": "Bullish Belt Hold",
    "BEAR_BELT": "Bearish Belt Hold",
    "MBZ_L": "Bullish Marubozu continuation",
    "MBZ_S": "Bearish Marubozu continuation",
}

# Higher = preferred when multiple fire (pin/engulf/inside preferred)
PATTERN_PRIORITY: dict[str, int] = {
    "BULL_ENGULF": 95,
    "BEAR_ENGULF": 95,
    "PIN_BULL": 92,
    "PIN_BEAR": 92,
    "HAMMER": 90,
    "SHOOTING_STAR": 90,
    "INSIDE_UP": 88,
    "INSIDE_DOWN": 88,
    "MORNING_STAR": 86,
    "EVENING_STAR": 86,
    "PIERCING": 80,
    "DARK_CLOUD": 80,
    "TWEEZER_BOT": 78,
    "TWEEZER_TOP": 78,
    "BULL_HARAMI": 72,
    "BEAR_HARAMI": 72,
    "DRAGONFLY": 70,
    "GRAVESTONE": 70,
    "THREE_WHITE": 75,
    "THREE_BLACK": 75,
    "BULL_BELT": 68,
    "BEAR_BELT": 68,
    "MBZ_L": 74,
    "MBZ_S": 74,
}

RULE_RR: dict[str, float] = {code: 2.0 for code in PATTERN_LABELS}
RULE_RR.update(
    {
        "BULL_HARAMI": 1.5,
        "BEAR_HARAMI": 1.5,
        "DRAGONFLY": 1.5,
        "GRAVESTONE": 1.5,
        "BULL_BELT": 1.5,
        "BEAR_BELT": 1.5,
    }
)


@dataclass
class BlueBoxState:
    """Compat stub — sweep traps retired; kept so reset_blue_box_state still works."""

    bullish_active: bool = False
    bearish_active: bool = False
    bullish_sweep_index: int | None = None
    bearish_sweep_index: int | None = None
    bullish_sweep_low: float | None = None
    bearish_sweep_high: float | None = None
    bullish_sweep_time: int | None = None
    bearish_sweep_time: int | None = None


_state_store: dict[str, BlueBoxState] = {}


def _state_key(pair: str, timeframe_key: str) -> str:
    return f"{pair}|{timeframe_key}"


def get_blue_box_state(pair: str, timeframe_key: str) -> BlueBoxState:
    key = _state_key(pair, timeframe_key)
    if key not in _state_store:
        _state_store[key] = BlueBoxState()
    return _state_store[key]


def reset_blue_box_state(pair: str | None = None, timeframe_key: str | None = None) -> None:
    if pair is None and timeframe_key is None:
        _state_store.clear()
        return
    prefix = f"{pair}|" if pair else ""
    suffix = f"|{timeframe_key}" if timeframe_key else ""
    for k in [k for k in list(_state_store) if (not pair or k.startswith(prefix)) and (not timeframe_key or k.endswith(suffix))]:
        del _state_store[k]


def parse_bybit_kline(row: list) -> dict:
    return {
        "open": float(row[1]),
        "high": float(row[2]),
        "low": float(row[3]),
        "close": float(row[4]),
        "volume": float(row[5]),
        "close_time": int(row[0]),
    }


def _body(c: dict) -> float:
    return abs(c["close"] - c["open"])


def _range(c: dict) -> float:
    return max(c["high"] - c["low"], 1e-12)


def _upper_wick(c: dict) -> float:
    return c["high"] - max(c["close"], c["open"])


def _lower_wick(c: dict) -> float:
    return min(c["close"], c["open"]) - c["low"]


def _is_green(c: dict) -> bool:
    return c["close"] > c["open"]


def _is_red(c: dict) -> bool:
    return c["close"] < c["open"]


def _is_doji(c: dict, body_frac: float = 0.1) -> bool:
    return _body(c) <= _range(c) * body_frac


def _midpoint(c: dict) -> float:
    return (c["open"] + c["close"]) / 2.0


def _is_pin_bull(c: dict) -> bool:
    # Strict pin: long rejection wick, tiny opposite wick.
    body, upper, lower, rng = _body(c), _upper_wick(c), _lower_wick(c), _range(c)
    return lower >= body * 2.0 and lower >= rng * 0.6 and upper <= body * 0.8


def _is_pin_bear(c: dict) -> bool:
    body, upper, lower, rng = _body(c), _upper_wick(c), _lower_wick(c), _range(c)
    return upper >= body * 2.0 and upper >= rng * 0.6 and lower <= body * 0.8


def _is_marubozu(c: dict, candles: list[dict]) -> bool:
    avg = _avg_body(candles)
    body = _body(c)
    if avg <= 0 or body < avg * 1.0:
        return False
    return _upper_wick(c) <= body * 0.15 and _lower_wick(c) <= body * 0.15


def _engulfs(outer: dict, inner: dict) -> bool:
    """Nison: second real body must ENTIRELY cover the first real body.

    Previous loose 95% cover let tiny red bars 'engulf' larger greens mid-trend
    and fire false BEAR_ENGULF shorts (classic impulse trap).
    """
    o_hi = max(outer["open"], outer["close"])
    o_lo = min(outer["open"], outer["close"])
    i_hi = max(inner["open"], inner["close"])
    i_lo = min(inner["open"], inner["close"])
    if _body(outer) <= _body(inner):
        return False
    # Full body enclosure (no near-miss).
    return o_hi >= i_hi and o_lo <= i_lo


def _engulf_close_conviction(action: str, candle: dict) -> bool:
    """Sellers/buyers must finish in control of the bar (bar psychology)."""
    r = _range(candle)
    if r <= 0:
        return False
    if action == "SELL":
        # Close in lower 35% — decisive control of the bar.
        return candle["close"] <= candle["low"] + r * 0.35
    # BUY: close in upper 35%
    return candle["close"] >= candle["high"] - r * 0.35


def _engulf_trend_gate(
    action: str,
    trend: str | None,
    local: str | None,
    candles: list[dict],
    signal: dict,
) -> tuple[bool, str]:
    """Trend: 'the trend should be your best friend' + MA pullback strategy.

    With-trend engulfing (continuation / pullback) is preferred.
    Counter-trend reversal only as exhaustion — never mid-impulse above
    stacked MAs (the trap in the BEAR_ENGULF screenshot).
    """
    avg = max(_avg_body(candles), 1e-12)
    body = _body(signal)
    closes = [c["close"] for c in candles]
    ema8 = compute_ema(closes, 8)
    ema21 = compute_ema(closes, 21)

    if action == "SELL":
        # With-trend short — PDF: sell in downtrend / on MA resistance.
        if trend == "downtrend":
            if _is_impulse_chase("SELL", candles):
                return False, "downtrend_chase_blocked"
            return True, "with_trend_down"
        if trend == "range" and local in ("down", "flat", None):
            if body >= avg * 1.2 and _engulf_close_conviction("SELL", signal):
                return True, "range_bear"
            return False, "weak_range_bear_engulf"
        # Counter-trend short into uptrend — ONLY exhaustion.
        if trend == "uptrend" or local == "up":
            # Must be a decisive engulf (large body) that breaks the fast MA.
            body_ok = body >= avg * 1.7
            broke_ema8 = ema8 is not None and signal["close"] < ema8
            # Prefer rejection near extended stretch above ema21, then reclaim.
            was_extended = (
                ema21 is not None
                and len(candles) >= 2
                and candles[-2]["high"] >= ema21 * 1.01
            )
            close_ok = _engulf_close_conviction("SELL", signal)
            if body_ok and broke_ema8 and close_ok and was_extended:
                return True, "exhaustion_bear_reversal"
            return False, "counter_trend_bear_trap_blocked"
        return False, "bear_engulf_no_context"

    # BUY / bull engulf
    if trend == "uptrend":
        if _is_impulse_chase("BUY", candles):
            return False, "uptrend_chase_blocked"
        return True, "with_trend_up"
    if trend == "range" and local in ("up", "flat", None):
        if body >= avg * 1.2 and _engulf_close_conviction("BUY", signal):
            return True, "range_bull"
        return False, "weak_range_bull_engulf"
    if trend == "downtrend" or local == "down":
        body_ok = body >= avg * 1.7
        broke_ema8 = ema8 is not None and signal["close"] > ema8
        was_extended = (
            ema21 is not None
            and len(candles) >= 2
            and candles[-2]["low"] <= ema21 * 0.99
        )
        close_ok = _engulf_close_conviction("BUY", signal)
        if body_ok and broke_ema8 and close_ok and was_extended:
            return True, "exhaustion_bull_reversal"
        return False, "counter_trend_bull_trap_blocked"
    return False, "bull_engulf_no_context"


def _is_impulse_chase(action: str, candles: list[dict]) -> bool:
    """Trending (trending_markets + engulfing_ma): price far from MA = overbought/oversold.

    Buying THREE_WHITE / marubozu after a vertical spike = buying the END of the
    impulsive move (professionals take profit there — classic bull trap).
    """
    closes = [c["close"] for c in candles]
    ema8 = compute_ema(closes, 8)
    ema21 = compute_ema(closes, 21)
    if ema8 is None or ema21 is None:
        return False
    close = float(candles[-1]["close"])
    if action == "BUY":
        # Any stretch above fast+mid MA → chase / end-of-impulse.
        return close >= ema21 * 1.0015 and close >= ema8 * 1.0008
    if action == "SELL":
        return close <= ema21 * 0.9985 and close <= ema8 * 0.9992
    return False


def _soldiers_after_pullback(candles: list[dict], avg: float) -> bool:
    """Three White/Black only valid near start of impulse (after pullback / at MA).

    PDF: buy beginning of impulsive move — not after 3 greens already mid-air.
    """
    if len(candles) < 5:
        return False
    first = candles[-3]  # first of the three soldiers
    pre = candles[-4]
    closes = [c["close"] for c in candles]
    ema21 = compute_ema(closes, 21)
    pullback_bar = _is_red(pre) or _body(pre) <= avg * 0.45
    near_ma = (
        ema21 is not None
        and abs(float(first["open"]) - ema21) / max(ema21, 1e-12) <= 0.0025
    )
    return bool(pullback_bar or near_ma)
def compute_ema(closes: list[float], length: int) -> float | None:
    if len(closes) < length:
        return None
    k = 2.0 / (length + 1)
    ema = sum(closes[:length]) / length
    for px in closes[length:]:
        ema = px * k + ema * (1 - k)
    return ema


def _avg_body(candles: list[dict], period: int = BODY_AVG_PERIOD) -> float:
    window = candles[-period:]
    if not window:
        return 0.0
    return sum(_body(c) for c in window) / len(window)


def _volume_ma(candles: list[dict], period: int = VOLUME_MA_PERIOD) -> float | None:
    """SMA of volume on the bars *before* the signal candle (matches chart Vol MA feel)."""
    if len(candles) < period + 1:
        return None
    window = candles[-(period + 1) : -1]
    if not window:
        return None
    return sum(float(c.get("volume") or 0.0) for c in window) / len(window)


def volume_confirm(
    candles: list[dict],
    *,
    setup: str | None = None,
) -> tuple[bool, dict]:
    """Volume gate — EVERY pattern (PDF: participation required, not climax-only).

    Rules (otherwise NO TRADE):
      1) vol ≥ VOLUME_CONFIRM_MULT × Vol MA  (default 1.6×)
      2) vol ≥ VOLUME_VS_PREV_MULT × previous bar
      3) optional: if CANDLE_TO_VOLUME_MULT > 0, rel_vol ≥ mult × rel_candle
    """
    if SKIP_VOLUME_GATE or VOLUME_CONFIRM_MULT <= 0:
        signal = candles[-1]
        vol = float(signal.get("volume") or 0.0)
        return True, {
            "volume": round(vol, 4),
            "skipped": True,
            "reason": "volume_gate_disabled",
            "ok": True,
        }

    signal = candles[-1]
    prev = candles[-2] if len(candles) >= 2 else None
    vol = float(signal.get("volume") or 0.0)
    prev_vol = float(prev.get("volume") or 0.0) if prev else 0.0
    vol_ma = _volume_ma(candles)
    setup_key = (setup or "").strip() or "default"
    setup_mult = float(VOLUME_SETUP_MULT.get(setup_key, VOLUME_CONFIRM_MULT))
    mult = max(VOLUME_CONFIRM_MULT, setup_mult)
    ratio = (vol / vol_ma) if vol_ma and vol_ma > 0 else None
    vs_prev = (vol / prev_vol) if prev_vol > 0 else None

    avg_range = 0.0
    window = candles[-BODY_AVG_PERIOD:] if len(candles) >= 2 else candles
    ranges = [_range(c) for c in window[:-1]] if len(window) >= 2 else [_range(c) for c in window]
    if ranges:
        avg_range = sum(ranges) / len(ranges)
    candle_range = _range(signal)
    rel_candle = (candle_range / avg_range) if avg_range > 0 else None
    candle_vol_need = (
        CANDLE_TO_VOLUME_MULT * max(rel_candle, 0.25)
        if CANDLE_TO_VOLUME_MULT > 0 and rel_candle is not None
        else None
    )

    info = {
        "volume": round(vol, 4),
        "volume_ma": round(vol_ma, 4) if vol_ma is not None else None,
        "volume_prev": round(prev_vol, 4),
        "volume_mult_required": mult,
        "volume_vs_prev_required": VOLUME_VS_PREV_MULT,
        "volume_ratio": round(ratio, 4) if ratio is not None else None,
        "volume_vs_prev": round(vs_prev, 4) if vs_prev is not None else None,
        "candle_range": round(candle_range, 8),
        "avg_range": round(avg_range, 8) if avg_range else None,
        "rel_candle": round(rel_candle, 4) if rel_candle is not None else None,
        "candle_to_volume_mult": CANDLE_TO_VOLUME_MULT,
        "candle_vol_need": round(candle_vol_need, 4) if candle_vol_need is not None else None,
        "setup": setup_key,
    }
    if vol_ma is None or vol_ma <= 0:
        return False, {**info, "ok": False, "reason": "Volume MA unavailable — no trade without volume context"}
    if vol <= 0:
        return False, {**info, "ok": False, "reason": "Zero volume bar — pattern ignored"}
    if vol < vol_ma * mult:
        return False, {
            **info,
            "ok": False,
            "reason": (
                f"Volume {vol:.2f} < MA{VOLUME_MA_PERIOD}×{mult:.2f} "
                f"({vol_ma * mult:.2f}) — needs participation"
            ),
        }
    if candle_vol_need is not None and ratio is not None and ratio < candle_vol_need:
        return False, {
            **info,
            "ok": False,
            "reason": (
                f"Candle/volume mismatch: rel_vol {ratio:.2f}× < "
                f"need {candle_vol_need:.2f}× — no fire"
            ),
        }
    if prev_vol > 0 and vol < prev_vol * VOLUME_VS_PREV_MULT:
        return False, {
            **info,
            "ok": False,
            "reason": (
                f"Volume {vol:.2f} < prev×{VOLUME_VS_PREV_MULT:.2f} "
                f"({prev_vol * VOLUME_VS_PREV_MULT:.2f}) — shrinking volume, skip"
            ),
        }
    return True, {**info, "ok": True, "reason": "Volume confirmed (MA + expanding)"}


def volume_strength_boost(vol_info: dict) -> float:
    """Map volume quality into extra pattern strength (all setups)."""
    ratio = float(vol_info.get("volume_ratio") or 0.0)
    vs_prev = float(vol_info.get("volume_vs_prev") or 0.0)
    if ratio <= 0:
        return 0.0
    # 1.6×MA → ~0.39, 2.5×MA → ~0.98, cap 1.0
    boost_ma = max(0.0, min((ratio - 1.0) * 0.65, 1.0))
    boost_prev = max(0.0, min((vs_prev - 1.0) * 0.35, 0.4)) if vs_prev else 0.0
    return round(boost_ma + boost_prev, 4)


def _trend_state(candles: list[dict], close: float) -> tuple[str | None, float | None, float | None]:
    closes = [c["close"] for c in candles]
    ema50 = compute_ema(closes, EMA_FAST)
    ema200 = compute_ema(closes, EMA_SLOW)
    if ema50 is None or ema200 is None:
        return None, ema50, ema200
    if close > ema50 > ema200:
        return "uptrend", ema50, ema200
    if close < ema50 < ema200:
        return "downtrend", ema50, ema200
    return "range", ema50, ema200


def _recent_direction(candles: list[dict], lookback: int = TREND_LOOKBACK) -> str | None:
    """Local slope from lookback bars ago → prior bar (excludes signal candle)."""
    if len(candles) < lookback + 2:
        return None
    a = candles[-(lookback + 1)]["close"]
    b = candles[-2]["close"]
    if b > a * (1.0 + LOCAL_SLOPE_PCT):
        return "up"
    if b < a * (1.0 - LOCAL_SLOPE_PCT):
        return "down"
    return "flat"


def _signal_sl(action: str, candle: dict) -> float:
    if action == "BUY":
        return candle["low"] * (1.0 - SL_BUFFER_PCT)
    return candle["high"] * (1.0 + SL_BUFFER_PCT)


def compute_sl_tp(
    action: str, entry: float, sl: float, rr: float = RR_RATIO
) -> tuple[float, float, float] | None:
    if entry <= 0 or sl <= 0:
        return None
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    if action == "BUY":
        if sl >= entry:
            return None
        tp = entry + risk * rr
    elif action == "SELL":
        if sl <= entry:
            return None
        tp = entry - risk * rr
    else:
        return None
    return entry, sl, tp


def compute_risk_trade_plan(
    balance_usd: float,
    entry: float,
    sl: float,
    *,
    qty_decimals: int = 5,
    leverage: float = 100.0,
) -> dict | None:
    if balance_usd <= 0 or entry <= 0:
        return None
    risk_distance = abs(entry - sl)
    if risk_distance <= 0:
        return None
    risk_usd = balance_usd * RISK_PCT_PER_TRADE
    qty = round(risk_usd / risk_distance, qty_decimals)
    if qty <= 0:
        return None
    position_usd = round(qty * entry, 2)
    margin = round(position_usd / leverage, 4)
    side = "BUY" if entry > sl else "SELL"
    prices = compute_sl_tp(side, entry, sl)
    tp = prices[2] if prices else None
    return {
        "total_capital": round(balance_usd, 2),
        "position_usd": position_usd,
        "capital_pct": round(RISK_PCT_PER_TRADE * 100, 2),
        "risk_usd": round(risk_usd, 2),
        "risk_distance": round(risk_distance, 6),
        "qty": qty,
        "qty_decimals": qty_decimals,
        "margin": margin,
        "price": entry,
        "tp": tp,
    }


def log_trade_execution(
    direction: str,
    entry: float,
    sl: float,
    tp: float,
    qty: float,
    balance: float,
    pattern: str,
) -> None:
    print(
        f"[EXECUTE_TRADE] {direction} | pattern={pattern} | "
        f"entry={entry} sl={sl} tp={tp} qty={qty} balance={balance}"
    )


def _hit(code: str, action: str, candle: dict, *, strength: float, setup: str) -> dict:
    return {
        "pattern": code,
        "action": action,
        "sl": _signal_sl(action, candle),
        "setup": setup,
        "rr": RULE_RR.get(code, RR_RATIO),
        "strength": round(strength, 4),
        "priority": PATTERN_PRIORITY.get(code, 50),
        "label": PATTERN_LABELS.get(code, code),
    }


def _detect_patterns(candles: list[dict], trend: str | None) -> list[dict]:
    """Return all pattern hits on the closed signal bar (last candle)."""
    if len(candles) < 5:
        return []
    c0 = candles[-1]
    c1 = candles[-2]
    c2 = candles[-3]
    local = _recent_direction(candles)
    avg = max(_avg_body(candles), 1e-12)
    strength_base = min(_body(c0) / avg, 3.0)
    hits: list[dict] = []
    # With-trend setups are highest quality ("trend is your friend").
    bull_with_trend = trend == "uptrend" or local == "up"
    bear_with_trend = trend == "downtrend" or local == "down"

    # --- Engulfing \(core\) — strict body + trend gate (anti mid-impulse trap) ---
    if _is_red(c1) and _is_green(c0) and _engulfs(c0, c1) and _engulf_close_conviction("BUY", c0):
        ok, why = _engulf_trend_gate("BUY", trend, local, candles, c0)
        if ok:
            s = strength_base + (0.55 if bull_with_trend else 0.2)
            if why.startswith("exhaustion"):
                s += 0.15
            hits.append(_hit("BULL_ENGULF", "BUY", c0, strength=s, setup="engulfing"))
    if _is_green(c1) and _is_red(c0) and _engulfs(c0, c1) and _engulf_close_conviction("SELL", c0):
        ok, why = _engulf_trend_gate("SELL", trend, local, candles, c0)
        if ok:
            s = strength_base + (0.55 if bear_with_trend else 0.2)
            if why.startswith("exhaustion"):
                s += 0.15
            hits.append(_hit("BEAR_ENGULF", "SELL", c0, strength=s, setup="engulfing"))

    # --- Pin / Hammer / Shooting Star ---
    if _is_pin_bull(c0):
        code = "HAMMER" if _is_green(c0) or _body(c0) <= _range(c0) * 0.4 else "PIN_BULL"
        s = strength_base + (0.4 if (trend == "downtrend" or local == "down") else 0.15)
        # Pin reversals against a strong with-trend impulse get reduced priority later via strength.
        if not (trend == "uptrend" and local == "up"):
            hits.append(_hit(code, "BUY", c0, strength=s, setup="pin_bar"))
        elif _body(c0) >= avg * 0.5:
            hits.append(_hit(code, "BUY", c0, strength=s * 0.7, setup="pin_bar"))
    if _is_pin_bear(c0):
        code = "SHOOTING_STAR" if _is_red(c0) or _body(c0) <= _range(c0) * 0.4 else "PIN_BEAR"
        s = strength_base + (0.4 if (trend == "uptrend" or local == "up") else 0.15)
        # Block weak shooting stars mid strong uptrend (same trap family as false bear engulf).
        if trend == "uptrend" and local == "up" and _body(c0) < avg * 1.1:
            pass  # skip — fade into impulse
        else:
            hits.append(_hit(code, "SELL", c0, strength=s, setup="pin_bar"))

    # --- Morning / Evening Star ---
    if (
        _is_red(c2)
        and _body(c1) <= _avg_body(candles) * 0.75
        and _is_green(c0)
        and c0["close"] > _midpoint(c2)
    ):
        s = strength_base + (0.6 if (trend != "uptrend") else 0.25)
        hits.append(_hit("MORNING_STAR", "BUY", c0, strength=s, setup="star"))
    if (
        _is_green(c2)
        and _body(c1) <= _avg_body(candles) * 0.75
        and _is_red(c0)
        and c0["close"] < _midpoint(c2)
    ):
        # Evening star into strong uptrend without size = trap — require body.
        if trend == "uptrend" and local == "up" and _body(c0) < avg * 1.2:
            pass
        else:
            s = strength_base + (0.6 if bear_with_trend else 0.25)
            hits.append(_hit("EVENING_STAR", "SELL", c0, strength=s, setup="star"))

    # --- Piercing / Dark Cloud ---
    if (
        _is_red(c1)
        and _is_green(c0)
        and c0["open"] < c1["close"]
        and c0["close"] > _midpoint(c1)
        and c0["close"] < c1["open"]
    ):
        hits.append(_hit("PIERCING", "BUY", c0, strength=strength_base + 0.3, setup="pierce"))
    if (
        _is_green(c1)
        and _is_red(c0)
        and c0["open"] > c1["close"]
        and c0["close"] < _midpoint(c1)
        and c0["close"] > c1["open"]
    ):
        if not (trend == "uptrend" and local == "up" and _body(c0) < avg * 1.15):
            hits.append(_hit("DARK_CLOUD", "SELL", c0, strength=strength_base + 0.3, setup="pierce"))

    # --- Harami ---
    if _is_red(c1) and _is_green(c0) and _engulfs(c1, c0) and _body(c0) < _body(c1) * 0.75:
        if trend != "downtrend" or local != "up":
            hits.append(_hit("BULL_HARAMI", "BUY", c0, strength=strength_base, setup="harami"))
    if _is_green(c1) and _is_red(c0) and _engulfs(c1, c0) and _body(c0) < _body(c1) * 0.75:
        # Harami is weak — never fade a clear uptrend with it (trap family).
        if trend != "uptrend":
            hits.append(_hit("BEAR_HARAMI", "SELL", c0, strength=strength_base, setup="harami"))

    # --- Inside bar break ---
    # Mother = c2, inside = c1, break = c0
    if (
        c1["high"] <= c2["high"]
        and c1["low"] >= c2["low"]
        and _body(c1) < _body(c2)
    ):
        if c0["close"] > c2["high"]:
            hits.append(_hit("INSIDE_UP", "BUY", c0, strength=strength_base + 0.45, setup="inside_bar"))
        if c0["close"] < c2["low"]:
            hits.append(_hit("INSIDE_DOWN", "SELL", c0, strength=strength_base + 0.45, setup="inside_bar"))

    # --- Tweezers ---
    low_tol = max(c0["low"], c1["low"]) * 0.0006
    high_tol = max(c0["high"], c1["high"]) * 0.0006
    if abs(c0["low"] - c1["low"]) <= low_tol and _is_red(c1) and _is_green(c0):
        hits.append(_hit("TWEEZER_BOT", "BUY", c0, strength=strength_base + 0.2, setup="tweezer"))
    if abs(c0["high"] - c1["high"]) <= high_tol and _is_green(c1) and _is_red(c0):
        if trend != "uptrend":
            hits.append(_hit("TWEEZER_TOP", "SELL", c0, strength=strength_base + 0.2, setup="tweezer"))

    # --- Doji extremes ---
    if _is_doji(c0) and _lower_wick(c0) >= _range(c0) * 0.55 and _upper_wick(c0) <= _range(c0) * 0.15:
        hits.append(_hit("DRAGONFLY", "BUY", c0, strength=max(strength_base, 0.8), setup="doji"))
    if _is_doji(c0) and _upper_wick(c0) >= _range(c0) * 0.55 and _lower_wick(c0) <= _range(c0) * 0.15:
        if not (trend == "uptrend" and local == "up"):
            hits.append(_hit("GRAVESTONE", "SELL", c0, strength=max(strength_base, 0.8), setup="doji"))

    # --- Three soldiers / crows ---
    # PDF: enter at START of impulse. Mid-spike THREE_WHITE = bull trap (buy the top).
    if len(candles) >= 5:
        a, b, d = candles[-3], candles[-2], candles[-1]
        if (
            _is_green(a) and _is_green(b) and _is_green(d)
            and d["close"] > b["close"] > a["close"]
            and _body(a) > avg * 0.55 and _body(b) > avg * 0.55 and _body(d) > avg * 0.55
            and _soldiers_after_pullback(candles, avg)
            and not _is_impulse_chase("BUY", candles)
        ):
            hits.append(_hit("THREE_WHITE", "BUY", d, strength=strength_base + 0.35, setup="soldiers"))
        if (
            _is_red(a) and _is_red(b) and _is_red(d)
            and d["close"] < b["close"] < a["close"]
            and _body(a) > avg * 0.55 and _body(b) > avg * 0.55 and _body(d) > avg * 0.55
            and _soldiers_after_pullback(candles, avg)
            and not _is_impulse_chase("SELL", candles)
        ):
            hits.append(_hit("THREE_BLACK", "SELL", d, strength=strength_base + 0.35, setup="crows"))

    # --- Belt hold (mid: body >= 1.0× avg — between loose 0.85 and tight 1.1) ---
    if (
        _is_green(c0)
        and _lower_wick(c0) <= _body(c0) * 0.08
        and _body(c0) >= avg * 1.0
        and local == "down"
        and not _is_impulse_chase("BUY", candles)
    ):
        hits.append(_hit("BULL_BELT", "BUY", c0, strength=strength_base + 0.2, setup="belt"))
    if (
        _is_red(c0)
        and _upper_wick(c0) <= _body(c0) * 0.08
        and _body(c0) >= avg * 1.0
        and local == "up"
        and not _is_impulse_chase("SELL", candles)
    ):
        hits.append(_hit("BEAR_BELT", "SELL", c0, strength=strength_base + 0.2, setup="belt"))

    # --- Marubozu continuation (with clear trend only) ---
    # Block when already stretched from MA — chasing the end of the impulse.
    if _is_marubozu(c0, candles):
        if trend == "uptrend" and _is_green(c0) and not _is_impulse_chase("BUY", candles):
            hits.append(_hit("MBZ_L", "BUY", c0, strength=strength_base + 0.3, setup="marubozu"))
        if trend == "downtrend" and _is_red(c0) and not _is_impulse_chase("SELL", candles):
            hits.append(_hit("MBZ_S", "SELL", c0, strength=strength_base + 0.3, setup="marubozu"))

    return hits


def _pick_best(hits: list[dict]) -> dict | None:
    """Same-bar bull+bear conflict = NO_TRADE. Else highest priority."""
    if not hits:
        return None
    buys = [h for h in hits if h["action"] == "BUY"]
    sells = [h for h in hits if h["action"] == "SELL"]
    if buys and sells:
        return None  # conflict → NO_TRADE

    side = buys or sells
    side.sort(key=lambda h: (h["priority"], h["strength"]), reverse=True)
    return side[0]


def evaluate_uvss(
    candles: list[dict],
    timeframe_key: str,
    *,
    pair: str = "default",
) -> dict:
    """Strategy wiped — never emits BUY/SELL."""
    return {
        "action": "NO_TRADE",
        "reason": "Strategy wiped — manual mode only",
        "engine": "none",
        "long_rules": [],
        "short_rules": [],
        "rules_fired": [],
        "entry_pattern": "MANUAL",
        "diagnostics": {"engine": "none", "pair": pair, "timeframe": timeframe_key},
    }


def _to_chart_time(raw: int | None) -> int | None:
    if raw is None:
        return None
    if raw > 1_000_000_000_000:
        return raw // 1000
    return raw


def build_blue_box_chart_overlay(
    pair: str,
    timeframe_key: str,
    *,
    is_active: bool,
    last_scan: dict | None = None,
) -> dict:
    decision = (last_scan or {}).get("decision") or {}
    if not is_active:
        return {"engine": "none", "active": False, "status": "idle"}
    status = "signal" if decision.get("action") in ("BUY", "SELL") else "scanning"
    return {
        "engine": "none",
        "active": True,
        "status": status,
        "pair": pair,
        "timeframe": timeframe_key,
        "last_pattern": decision.get("pattern"),
        "last_action": decision.get("action"),
        "strength": decision.get("strength") or decision.get("confidence"),
        "note": "Candlestick Trading Bible engine",
    }
