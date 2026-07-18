"""Candlestick pattern entry engine — merged 38-pattern + Bible + ML fire path.

Pipeline (closed bar only):
  1) Detect pattern on last candle(s)
  2) Attach Bible section id for microsecond fetch
  3) Score signal strength for ML cost-aware gate (main.py)
  4) Return BUY/SELL with entry/SL/TP

Legacy Blue Box / VSA rule codes are retired. Helpers for klines, sizing,
and chart overlay stay for main.py compatibility.
"""
from __future__ import annotations

from dataclasses import dataclass

UVSS_POLICIES_ENABLED = True
# ML paper: naive sign trades die under fees — cost-aware gate ON by default.
UVSS_COST_AWARE_ENTRY = True
UVSS_SL_EXIT_ENABLED = False

EMA_FAST = 50
EMA_SLOW = 200
BODY_AVG_PERIOD = 20
TREND_LOOKBACK = 8
MIN_CANDLES = max(EMA_SLOW + BODY_AVG_PERIOD + 5, 60)
RISK_PCT_PER_TRADE = 0.01
RR_RATIO = 2.0
SL_BUFFER_PCT = 0.001

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

# code → Bible memory alias (candlestick_bible_memory)
PATTERN_BIBLE_KEY: dict[str, str] = {
    "BULL_ENGULF": "engulfing_bar",
    "BEAR_ENGULF": "engulfing_bar",
    "HAMMER": "hammer",
    "SHOOTING_STAR": "shooting_star",
    "PIN_BULL": "pin_bar_strategy",
    "PIN_BEAR": "pin_bar_strategy",
    "MORNING_STAR": "morning_star",
    "EVENING_STAR": "evening_star",
    "PIERCING": "engulfing_bar",
    "DARK_CLOUD": "engulfing_bar",
    "BULL_HARAMI": "harami",
    "BEAR_HARAMI": "harami",
    "INSIDE_UP": "inside_bar",
    "INSIDE_DOWN": "inside_bar",
    "TWEEZER_BOT": "tweezers",
    "TWEEZER_TOP": "tweezers",
    "DRAGONFLY": "dragonfly_doji",
    "GRAVESTONE": "gravestone_doji",
    "THREE_WHITE": "patterns_intro",
    "THREE_BLACK": "patterns_intro",
    "BULL_BELT": "patterns_intro",
    "BEAR_BELT": "patterns_intro",
    "MBZ_L": "engulfing_how_to_trade",
    "MBZ_S": "engulfing_how_to_trade",
}

# Higher = preferred when multiple fire (Bible priority: pin/engulf/inside)
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


def _engulfs(outer: dict, inner: dict) -> bool:
    """Outer body fully covers inner body."""
    o_hi = max(outer["open"], outer["close"])
    o_lo = min(outer["open"], outer["close"])
    i_hi = max(inner["open"], inner["close"])
    i_lo = min(inner["open"], inner["close"])
    return o_hi >= i_hi and o_lo <= i_lo and _body(outer) > _body(inner)


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
    if b > a * 1.001:
        return "up"
    if b < a * 0.999:
        return "down"
    return "flat"


def _is_pin_bull(c: dict) -> bool:
    body, upper, lower, rng = _body(c), _upper_wick(c), _lower_wick(c), _range(c)
    return lower >= body * 2.0 and lower >= rng * 0.55 and upper <= body * 1.0


def _is_pin_bear(c: dict) -> bool:
    body, upper, lower, rng = _body(c), _upper_wick(c), _lower_wick(c), _range(c)
    return upper >= body * 2.0 and upper >= rng * 0.55 and lower <= body * 1.0


def _is_marubozu(c: dict, candles: list[dict]) -> bool:
    avg = _avg_body(candles)
    body = _body(c)
    if avg <= 0 or body < avg * 1.4:
        return False
    return _upper_wick(c) <= body * 0.08 and _lower_wick(c) <= body * 0.08


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
        "bible_key": PATTERN_BIBLE_KEY.get(code, "patterns_intro"),
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

    # --- Engulfing (Bible core) ---
    if _is_red(c1) and _is_green(c0) and _engulfs(c0, c1) and local in ("down", "flat", None):
        hits.append(_hit("BULL_ENGULF", "BUY", c0, strength=strength_base + 0.5, setup="engulfing"))
    if _is_green(c1) and _is_red(c0) and _engulfs(c0, c1) and local in ("up", "flat", None):
        hits.append(_hit("BEAR_ENGULF", "SELL", c0, strength=strength_base + 0.5, setup="engulfing"))

    # --- Pin / Hammer / Shooting Star ---
    if _is_pin_bull(c0) and local in ("down", "flat", None):
        code = "HAMMER" if _is_green(c0) or _body(c0) <= _range(c0) * 0.35 else "PIN_BULL"
        hits.append(_hit(code, "BUY", c0, strength=strength_base + 0.4, setup="pin_bar"))
    if _is_pin_bear(c0) and local in ("up", "flat", None):
        code = "SHOOTING_STAR" if _is_red(c0) or _body(c0) <= _range(c0) * 0.35 else "PIN_BEAR"
        hits.append(_hit(code, "SELL", c0, strength=strength_base + 0.4, setup="pin_bar"))

    # --- Morning / Evening Star ---
    if (
        _is_red(c2)
        and _body(c1) <= _avg_body(candles) * 0.6
        and _is_green(c0)
        and c0["close"] > _midpoint(c2)
        and local in ("down", "flat", None)
    ):
        hits.append(_hit("MORNING_STAR", "BUY", c0, strength=strength_base + 0.6, setup="star"))
    if (
        _is_green(c2)
        and _body(c1) <= _avg_body(candles) * 0.6
        and _is_red(c0)
        and c0["close"] < _midpoint(c2)
        and local in ("up", "flat", None)
    ):
        hits.append(_hit("EVENING_STAR", "SELL", c0, strength=strength_base + 0.6, setup="star"))

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
        hits.append(_hit("DARK_CLOUD", "SELL", c0, strength=strength_base + 0.3, setup="pierce"))

    # --- Harami ---
    if _is_red(c1) and _is_green(c0) and _engulfs(c1, c0) and _body(c0) < _body(c1) * 0.6:
        hits.append(_hit("BULL_HARAMI", "BUY", c0, strength=strength_base, setup="harami"))
    if _is_green(c1) and _is_red(c0) and _engulfs(c1, c0) and _body(c0) < _body(c1) * 0.6:
        hits.append(_hit("BEAR_HARAMI", "SELL", c0, strength=strength_base, setup="harami"))

    # --- Inside bar break (Bible strategy) ---
    # Mother = c2, inside = c1, break = c0
    if (
        c1["high"] <= c2["high"]
        and c1["low"] >= c2["low"]
        and _body(c1) < _body(c2)
    ):
        if c0["close"] > c2["high"] and (trend == "uptrend" or local == "up"):
            hits.append(_hit("INSIDE_UP", "BUY", c0, strength=strength_base + 0.45, setup="inside_bar"))
        if c0["close"] < c2["low"] and (trend == "downtrend" or local == "down"):
            hits.append(_hit("INSIDE_DOWN", "SELL", c0, strength=strength_base + 0.45, setup="inside_bar"))

    # --- Tweezers ---
    low_tol = max(c0["low"], c1["low"]) * 0.0004
    high_tol = max(c0["high"], c1["high"]) * 0.0004
    if abs(c0["low"] - c1["low"]) <= low_tol and _is_red(c1) and _is_green(c0):
        hits.append(_hit("TWEEZER_BOT", "BUY", c0, strength=strength_base + 0.2, setup="tweezer"))
    if abs(c0["high"] - c1["high"]) <= high_tol and _is_green(c1) and _is_red(c0):
        hits.append(_hit("TWEEZER_TOP", "SELL", c0, strength=strength_base + 0.2, setup="tweezer"))

    # --- Doji extremes ---
    if _is_doji(c0) and _lower_wick(c0) >= _range(c0) * 0.6 and _upper_wick(c0) <= _range(c0) * 0.1:
        if local in ("down", "flat", None):
            hits.append(_hit("DRAGONFLY", "BUY", c0, strength=max(strength_base, 0.8), setup="doji"))
    if _is_doji(c0) and _upper_wick(c0) >= _range(c0) * 0.6 and _lower_wick(c0) <= _range(c0) * 0.1:
        if local in ("up", "flat", None):
            hits.append(_hit("GRAVESTONE", "SELL", c0, strength=max(strength_base, 0.8), setup="doji"))

    # --- Three soldiers / crows ---
    if len(candles) >= 4:
        a, b, d = candles[-3], candles[-2], candles[-1]
        if (
            _is_green(a) and _is_green(b) and _is_green(d)
            and d["close"] > b["close"] > a["close"]
            and _body(a) > avg * 0.7 and _body(b) > avg * 0.7 and _body(d) > avg * 0.7
        ):
            hits.append(_hit("THREE_WHITE", "BUY", d, strength=strength_base + 0.35, setup="soldiers"))
        if (
            _is_red(a) and _is_red(b) and _is_red(d)
            and d["close"] < b["close"] < a["close"]
            and _body(a) > avg * 0.7 and _body(b) > avg * 0.7 and _body(d) > avg * 0.7
        ):
            hits.append(_hit("THREE_BLACK", "SELL", d, strength=strength_base + 0.35, setup="crows"))

    # --- Belt hold ---
    if _is_green(c0) and _lower_wick(c0) <= _body(c0) * 0.05 and _body(c0) >= avg and local == "down":
        hits.append(_hit("BULL_BELT", "BUY", c0, strength=strength_base, setup="belt"))
    if _is_red(c0) and _upper_wick(c0) <= _body(c0) * 0.05 and _body(c0) >= avg and local == "up":
        hits.append(_hit("BEAR_BELT", "SELL", c0, strength=strength_base, setup="belt"))

    # --- Marubozu continuation (with trend) ---
    if _is_marubozu(c0, candles):
        if trend == "uptrend" and _is_green(c0):
            hits.append(_hit("MBZ_L", "BUY", c0, strength=strength_base + 0.3, setup="marubozu"))
        if trend == "downtrend" and _is_red(c0):
            hits.append(_hit("MBZ_S", "SELL", c0, strength=strength_base + 0.3, setup="marubozu"))

    return hits


def _pick_best(hits: list[dict]) -> dict | None:
    if not hits:
        return None
    buys = [h for h in hits if h["action"] == "BUY"]
    sells = [h for h in hits if h["action"] == "SELL"]
    if buys and sells:
        # Conflict — skip (ML paper: unstable weak signals → turnover)
        return None

    side = buys or sells
    side.sort(key=lambda h: (h["priority"], h["strength"]), reverse=True)
    return side[0]


def evaluate_uvss(
    candles: list[dict],
    timeframe_key: str,
    *,
    pair: str = "default",
) -> dict:
    """Detect candle pattern → attach Bible key + strength for ML cost gate."""
    if not UVSS_POLICIES_ENABLED:
        return {"action": "NO_TRADE", "reason": "Entry policies disabled"}

    if len(candles) < MIN_CANDLES:
        return {
            "action": "NO_TRADE",
            "reason": f"Need {MIN_CANDLES}+ closed candles (have {len(candles)})",
        }

    get_blue_box_state(pair, timeframe_key)  # keep store keyed
    signal = candles[-1]
    close = signal["close"]
    trend, ema50, ema200 = _trend_state(candles, close)
    local = _recent_direction(candles)
    avg_body = _avg_body(candles)
    candle_range_pct = (_range(signal) / max(signal["low"], 1e-12)) * 100.0

    diagnostics = {
        "engine": "candle_pattern_v1",
        "trend": trend,
        "local_dir": local,
        "ema50": round(ema50, 4) if ema50 is not None else None,
        "ema200": round(ema200, 4) if ema200 is not None else None,
        "avg_body": round(avg_body, 8),
        "body": round(_body(signal), 8),
        "candle_range_pct": round(candle_range_pct, 4),
        "volume": round(signal.get("volume", 0.0), 4),
        "long_rules": [],
        "short_rules": [],
        "rules_fired": [],
        "sweep_events": [],
        "candidates": [],
    }

    hits = _detect_patterns(candles, trend)
    diagnostics["candidates"] = [
        {"pattern": h["pattern"], "action": h["action"], "priority": h["priority"], "strength": h["strength"]}
        for h in hits
    ]

    best = _pick_best(hits)
    if best is None:
        if hits:
            return {
                "action": "NO_TRADE",
                "reason": "Conflicting bullish and bearish candle patterns on same bar",
                "pattern": None,
                **diagnostics,
            }
        return {
            "action": "NO_TRADE",
            "reason": "No candle pattern signal (Bible patterns + trend filter)",
            "pattern": None,
            **diagnostics,
        }

    action = best["action"]
    pattern = best["pattern"]
    sl = best["sl"]
    entry_px = close
    rr = float(best.get("rr", RR_RATIO))
    prices = compute_sl_tp(action, entry_px, sl, rr=rr)
    if not prices:
        return {"action": "NO_TRADE", "reason": "Could not compute SL/TP", "pattern": pattern, **diagnostics}

    entry_px, sl, tp = prices
    risk_dist = abs(entry_px - sl)
    # ML magnitude proxy: pattern strength × candle range (paper: |forecast| vs cost)
    signal_magnitude = round(float(best["strength"]) * max(candle_range_pct, 0.01), 4)

    diagnostics["long_rules"] = [pattern] if action == "BUY" else []
    diagnostics["short_rules"] = [pattern] if action == "SELL" else []
    diagnostics["rules_fired"] = [pattern]

    return {
        "action": action,
        "pattern": pattern,
        "reason": f"{best['label']} · trend={trend} · local={local} · strength={best['strength']}",
        "setup": best.get("setup"),
        "size_mult": rr,
        "target_mult": rr,
        "entry": entry_px,
        "sl": sl,
        "tp": tp,
        "risk_distance": round(risk_dist, 6),
        "strength": best["strength"],
        "signal_magnitude": signal_magnitude,
        "bible_key": best.get("bible_key"),
        "ml_gate": "cost_aware",
        **diagnostics,
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
        return {"engine": "candle_pattern", "active": False, "status": "idle"}
    status = "signal" if decision.get("action") in ("BUY", "SELL") else "scanning"
    return {
        "engine": "candle_pattern",
        "active": True,
        "status": status,
        "pair": pair,
        "timeframe": timeframe_key,
        "ema50": decision.get("ema50"),
        "ema200": decision.get("ema200"),
        "trend": decision.get("trend"),
        "last_pattern": decision.get("pattern"),
        "last_action": decision.get("action"),
        "bible_key": decision.get("bible_key"),
        "strength": decision.get("strength"),
        "bullish_trap": None,
        "bearish_trap": None,
    }
