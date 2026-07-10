"""Blue Box + VSA + Marubozu entry system (closed-bar only).

Modules: Blue Box sweep/displacement, classic VSA+SMC (9 rules), Marubozu pullback,
momentum. SL/TP: pattern-specific with 1:1 or 1:2 R:R. Size: 1% risk to SL.
"""
from __future__ import annotations

from dataclasses import dataclass

UVSS_POLICIES_ENABLED = True
UVSS_COST_AWARE_ENTRY = False
RATIO_PERIOD = 30
SWEEP_LOOKBACK = 20
EMA_FAST = 50
EMA_SLOW = 200
BODY_AVG_PERIOD = 20
MARUBOZU_BODY_MULT = 1.5
MARUBOZU_WICK_RATIO = 0.05
SL_BUFFER_PCT = 0.001  # 0.1% beyond marubozu extreme
MIN_CANDLES = max(SWEEP_LOOKBACK + RATIO_PERIOD + 2, EMA_SLOW + BODY_AVG_PERIOD + 5)
RISK_PCT_PER_TRADE = 0.01
RR_RATIO = 2.0

PATTERN_LABELS = {
    "BB-L": "Blue Box LONG — bullish sweep + green displacement",
    "BB-S": "Blue Box SHORT — bearish sweep + red displacement",
    "MOM-L": "Momentum LONG — green body ≥ 2× expected",
    "MOM-S": "Momentum SHORT — red body ≥ 2× expected",
    "MBZ-L": "Marubozu LONG — uptrend pullback + green marubozu",
    "MBZ-S": "Marubozu SHORT — downtrend pullback + red marubozu",
    "L1": "VSA L1: Red exhaustion (uptrend)",
    "L2": "VSA L2: Hammer (uptrend)",
    "L3": "VSA L3: Bullish spring sweep (no EMA)",
    "L4": "VSA L4: Buy absorption (uptrend)",
    "L5": "VSA L5: Momentum long (no EMA)",
    "S1": "VSA S1: Green exhaustion (downtrend)",
    "S2": "VSA S2: Bearish up-thrust (no EMA)",
    "S3": "VSA S3: Sell absorption (downtrend)",
    "S4": "VSA S4: Momentum short (no EMA)",
}

# R:R multiplier per rule (1 = 1:1, 2 = 1:2)
RULE_RR: dict[str, float] = {
    "BB-L": 2.0,
    "BB-S": 2.0,
    "MOM-L": 2.0,
    "MOM-S": 2.0,
    "MBZ-L": 2.0,
    "MBZ-S": 2.0,
    "L1": 1.0,
    "L2": 2.0,
    "L3": 2.0,
    "L4": 2.0,
    "L5": 1.0,
    "S1": 1.0,
    "S2": 2.0,
    "S3": 2.0,
    "S4": 1.0,
}


@dataclass
class BlueBoxState:
    """Per-market state for the sweep → displacement trap sequence."""

    bullish_active: bool = False
    bearish_active: bool = False
    bullish_sweep_index: int | None = None
    bearish_sweep_index: int | None = None
    bullish_sweep_low: float | None = None
    bearish_sweep_high: float | None = None
    bullish_sweep_time: int | None = None  # candle open time (ms or sec)
    bearish_sweep_time: int | None = None


# Keyed by "{pair}|{timeframe}" — survives across closed-bar evaluations.
_state_store: dict[str, BlueBoxState] = {}


def _state_key(pair: str, timeframe_key: str) -> str:
    return f"{pair}|{timeframe_key}"


def get_blue_box_state(pair: str, timeframe_key: str) -> BlueBoxState:
    key = _state_key(pair, timeframe_key)
    if key not in _state_store:
        _state_store[key] = BlueBoxState()
    return _state_store[key]


def reset_blue_box_state(pair: str | None = None, timeframe_key: str | None = None) -> None:
    """Clear trap state when pair/timeframe changes or on demand."""
    if pair is None and timeframe_key is None:
        _state_store.clear()
        return
    prefix = f"{pair}|" if pair else ""
    suffix = f"|{timeframe_key}" if timeframe_key else ""
    keys = [
        k
        for k in list(_state_store)
        if (not pair or k.startswith(prefix)) and (not timeframe_key or k.endswith(suffix))
    ]
    for k in keys:
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


def _upper_wick(c: dict) -> float:
    return c["high"] - max(c["close"], c["open"])


def _lower_wick(c: dict) -> float:
    return min(c["close"], c["open"]) - c["low"]


def _is_green(c: dict) -> bool:
    return c["close"] > c["open"]


def _is_red(c: dict) -> bool:
    return c["close"] < c["open"]


def compute_ema(closes: list[float], length: int) -> float | None:
    if len(closes) < length:
        return None
    k = 2.0 / (length + 1)
    ema = sum(closes[:length]) / length
    for price in closes[length:]:
        ema = price * k + ema * (1.0 - k)
    return ema


def _avg_body(candles: list[dict], period: int = BODY_AVG_PERIOD) -> float:
    bodies = [_body(b) for b in candles[-period:]]
    return sum(bodies) / len(bodies) if bodies else 0.0


def _is_marubozu(candle: dict, candles: list[dict]) -> bool:
    body = _body(candle)
    if body <= 0:
        return False
    avg_body = _avg_body(candles[:-1] if len(candles) > 1 else candles)
    if avg_body <= 0:
        return False
    upper = _upper_wick(candle)
    lower = _lower_wick(candle)
    return (
        body > avg_body * MARUBOZU_BODY_MULT
        and upper <= body * MARUBOZU_WICK_RATIO
        and lower <= body * MARUBOZU_WICK_RATIO
    )


def _pullback_mostly(candles: list[dict], color: str) -> bool:
    """True if any of the prior 2–4 closed bars are mostly green/red (retracement)."""
    if len(candles) < 5:
        return False
    prior = candles[-5:-1]
    check = _is_green if color == "green" else _is_red
    for size in (2, 3, 4):
        if len(prior) < size:
            continue
        window = prior[-size:]
        if sum(1 for b in window if check(b)) > size / 2:
            return True
    return False


def _trend_state(candles: list[dict], close: float) -> tuple[str | None, float | None, float | None]:
    closes = [x["close"] for x in candles]
    ema50 = compute_ema(closes, EMA_FAST)
    ema200 = compute_ema(closes, EMA_SLOW)
    if ema50 is None or ema200 is None:
        return None, ema50, ema200
    if ema50 > ema200 and close > ema50:
        return "uptrend", ema50, ema200
    if ema50 < ema200 and close < ema50:
        return "downtrend", ema50, ema200
    return "neutral", ema50, ema200


def _sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _is_hammer(upper: float, body: float, lower: float) -> bool:
    if body <= 0:
        return False
    return upper <= body * 0.1 and body <= lower * 0.5


def _vsa_ema200_trend(candles: list[dict], close: float) -> str | None:
    ema200 = compute_ema([x["close"] for x in candles], EMA_SLOW)
    if ema200 is None:
        return None
    if close > ema200:
        return "uptrend"
    if close < ema200:
        return "downtrend"
    return "neutral"


def _signal_sl(action: str, candle: dict) -> float:
    h, l = candle["high"], candle["low"]
    if action == "BUY":
        return l * (1.0 - SL_BUFFER_PCT)
    return h * (1.0 + SL_BUFFER_PCT)


def _rr_for_pattern(pattern: str) -> float:
    codes = pattern.split("+")
    return max(RULE_RR.get(c, RR_RATIO) for c in codes)


def _check_vsa_rules(
    candles: list[dict],
    signal: dict,
    expected_body: float,
    lowest_20: float,
    highest_20: float,
) -> dict | None:
    """Classic VSA + SMC nine-rule set on the closed bar (L5/S4 overlap momentum)."""
    c = signal["close"]
    h, l, v = signal["high"], signal["low"], signal["volume"]
    body = _body(signal)
    upper = _upper_wick(signal)
    lower = _lower_wick(signal)
    mid = (h + l) / 2.0
    vsa_trend = _vsa_ema200_trend(candles, c)
    is_uptrend = vsa_trend == "uptrend"
    is_downtrend = vsa_trend == "downtrend"
    volumes = [x["volume"] for x in candles]
    vol_sma_20 = _sma(volumes, 20)
    vol_sma_30 = _sma(volumes, 30)
    high_effort = vol_sma_30 is not None and v > vol_sma_30 * 2
    no_result = body <= expected_body * 0.5

    long_hits: list[str] = []
    if _is_red(signal) and body >= 2 * expected_body and is_uptrend:
        long_hits.append("L1")
    if (
        (_is_red(signal) or _is_green(signal))
        and _is_hammer(upper, body, lower)
        and body <= 0.5 * expected_body
        and is_uptrend
    ):
        long_hits.append("L2")
    if (
        l < lowest_20
        and c > mid
        and vol_sma_20 is not None
        and v > vol_sma_20 * 1.5
    ):
        long_hits.append("L3")
    if _is_red(signal) and high_effort and no_result and lower >= body and is_uptrend:
        long_hits.append("L4")
    if _is_green(signal) and body >= 2 * expected_body:
        long_hits.append("L5")

    short_hits: list[str] = []
    if _is_green(signal) and body >= 2 * expected_body and is_downtrend:
        short_hits.append("S1")
    if (
        h > highest_20
        and c < mid
        and vol_sma_20 is not None
        and v > vol_sma_20 * 1.5
    ):
        short_hits.append("S2")
    if _is_green(signal) and high_effort and no_result and upper >= body and is_downtrend:
        short_hits.append("S3")
    if _is_red(signal) and body >= 2 * expected_body:
        short_hits.append("S4")

    if long_hits and short_hits:
        return {"conflict": True, "long_rules": long_hits, "short_rules": short_hits}

    if not long_hits and not short_hits:
        return None

    hits = long_hits if long_hits else short_hits
    action = "BUY" if long_hits else "SELL"
    pattern = "+".join(hits)
    return {
        "pattern": pattern,
        "action": action,
        "sl": _signal_sl(action, signal),
        "setup": "vsa",
        "rr": _rr_for_pattern(pattern),
        "rules_fired": hits,
        "long_rules": long_hits,
        "short_rules": short_hits,
    }


def _marubozu_sl(action: str, candle: dict) -> float:
    h, l = candle["high"], candle["low"]
    if action == "BUY":
        return l * (1.0 - SL_BUFFER_PCT)
    return h * (1.0 + SL_BUFFER_PCT)


def _check_marubozu_continuation(
    candles: list[dict],
    signal: dict,
    trend: str | None,
) -> dict | None:
    if not _is_marubozu(signal, candles):
        return None

    if trend == "uptrend" and _is_green(signal) and _pullback_mostly(candles, "red"):
        return {
            "pattern": "MBZ-L",
            "action": "BUY",
            "sl": _marubozu_sl("BUY", signal),
            "setup": "marubozu_pullback",
            "rr": RULE_RR["MBZ-L"],
        }

    if trend == "downtrend" and _is_red(signal) and _pullback_mostly(candles, "green"):
        return {
            "pattern": "MBZ-S",
            "action": "SELL",
            "sl": _marubozu_sl("SELL", signal),
            "setup": "marubozu_pullback",
            "rr": RULE_RR["MBZ-S"],
        }
    return None


def _avg_ratio(candles: list[dict], period: int = RATIO_PERIOD) -> float:
    ratios = []
    for bar in candles[-period:]:
        vol = bar["volume"]
        ratios.append((_body(bar) / vol) if vol > 0 else 0.0)
    return sum(ratios) / len(ratios) if ratios else 0.0


def _expected_body(candle: dict, candles: list[dict]) -> float:
    vol = candle["volume"]
    if vol <= 0:
        return 0.0
    return vol * _avg_ratio(candles)


def _lowest_20(candles: list[dict]) -> float:
    prior = candles[-21:-1]
    return min(x["low"] for x in prior) if prior else candles[-1]["low"]


def _highest_20(candles: list[dict]) -> float:
    prior = candles[-21:-1]
    return max(x["high"] for x in prior) if prior else candles[-1]["high"]


def compute_sl_tp(action: str, entry: float, sl: float, rr: float = RR_RATIO) -> tuple[float, float, float] | None:
    """Entry at close; SL from sweep or breakout candle; TP at fixed R:R."""
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
    """Quantity = (balance × 1%) / risk_distance."""
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
    """Detailed pre-order log (mock ccxt.create_order)."""
    risk_dist = abs(entry - sl)
    risk_usd = balance * RISK_PCT_PER_TRADE
    print("=" * 60)
    print(f"[EXECUTE_TRADE] {direction} | pattern={pattern}")
    print(f"  Entry Price : {entry:.6f}")
    print(f"  Stop Loss   : {sl:.6f}")
    print(f"  Take Profit : {tp:.6f}  (1:{RR_RATIO} R:R)")
    print(f"  Risk Dist   : {risk_dist:.6f}")
    print(f"  Balance     : ${balance:,.2f}")
    print(f"  Risk (1%)   : ${risk_usd:,.2f}")
    print(f"  Quantity    : {qty}")
    print("  >> ccxt.create_order() [mock — Bybit executor fires real/testnet order]")
    print("=" * 60)


def _expire_sweeps(state: BlueBoxState, current_index: int) -> None:
    """Reset trap state if more than 2 candles passed without displacement."""
    if state.bullish_active and state.bullish_sweep_index is not None:
        if current_index - state.bullish_sweep_index > 2:
            state.bullish_active = False
            state.bullish_sweep_index = None
            state.bullish_sweep_low = None
            state.bullish_sweep_time = None
    if state.bearish_active and state.bearish_sweep_index is not None:
        if current_index - state.bearish_sweep_index > 2:
            state.bearish_active = False
            state.bearish_sweep_index = None
            state.bearish_sweep_high = None
            state.bearish_sweep_time = None


def _clear_bullish(state: BlueBoxState) -> None:
    state.bullish_active = False
    state.bullish_sweep_index = None
    state.bullish_sweep_low = None
    state.bullish_sweep_time = None


def _clear_bearish(state: BlueBoxState) -> None:
    state.bearish_active = False
    state.bearish_sweep_index = None
    state.bearish_sweep_high = None
    state.bearish_sweep_time = None


def _check_bullish_displacement(
    state: BlueBoxState,
    current_index: int,
    candle: dict,
    expected_body: float,
) -> dict | None:
    if not state.bullish_active or state.bullish_sweep_index is None:
        return None
    bars_since = current_index - state.bullish_sweep_index
    if bars_since not in (1, 2):
        return None
    body = _body(candle)
    if _is_green(candle) and body >= 2 * expected_body and state.bullish_sweep_low is not None:
        return {
            "pattern": "BB-L",
            "action": "BUY",
            "sl": state.bullish_sweep_low,
            "setup": "sweep",
            "rr": RULE_RR["BB-L"],
        }
    return None


def _check_bearish_displacement(
    state: BlueBoxState,
    current_index: int,
    candle: dict,
    expected_body: float,
) -> dict | None:
    if not state.bearish_active or state.bearish_sweep_index is None:
        return None
    bars_since = current_index - state.bearish_sweep_index
    if bars_since not in (1, 2):
        return None
    body = _body(candle)
    if _is_red(candle) and body >= 2 * expected_body and state.bearish_sweep_high is not None:
        return {
            "pattern": "BB-S",
            "action": "SELL",
            "sl": state.bearish_sweep_high,
            "setup": "sweep",
            "rr": RULE_RR["BB-S"],
        }
    return None


def _detect_bullish_sweep(
    state: BlueBoxState,
    current_index: int,
    low: float,
    close: float,
    lowest_20: float,
    candle: dict,
) -> bool:
    if low < lowest_20 and close > lowest_20:
        state.bullish_active = True
        state.bullish_sweep_index = current_index
        state.bullish_sweep_low = low
        state.bullish_sweep_time = candle.get("close_time")
        _clear_bearish(state)
        return True
    return False


def _detect_bearish_sweep(
    state: BlueBoxState,
    current_index: int,
    high: float,
    close: float,
    highest_20: float,
    candle: dict,
) -> bool:
    if high > highest_20 and close < highest_20:
        state.bearish_active = True
        state.bearish_sweep_index = current_index
        state.bearish_sweep_high = high
        state.bearish_sweep_time = candle.get("close_time")
        _clear_bullish(state)
        return True
    return False


def _check_momentum(candle: dict, expected_body: float) -> dict | None:
    body = _body(candle)
    if _is_green(candle) and body >= 2 * expected_body:
        return {
            "pattern": "MOM-L",
            "action": "BUY",
            "sl": _signal_sl("BUY", candle),
            "setup": "momentum",
            "rr": RULE_RR["MOM-L"],
        }
    if _is_red(candle) and body >= 2 * expected_body:
        return {
            "pattern": "MOM-S",
            "action": "SELL",
            "sl": _signal_sl("SELL", candle),
            "setup": "momentum",
            "rr": RULE_RR["MOM-S"],
        }
    return None


def _state_diagnostics(state: BlueBoxState, current_index: int) -> dict:
    bull_bars = (
        current_index - state.bullish_sweep_index
        if state.bullish_active and state.bullish_sweep_index is not None
        else None
    )
    bear_bars = (
        current_index - state.bearish_sweep_index
        if state.bearish_active and state.bearish_sweep_index is not None
        else None
    )
    return {
        "bullish_sweep_active": state.bullish_active,
        "bearish_sweep_active": state.bearish_active,
        "bars_since_bull_sweep": bull_bars,
        "bars_since_bear_sweep": bear_bars,
        "bullish_sweep_low": state.bullish_sweep_low,
        "bearish_sweep_high": state.bearish_sweep_high,
    }


def evaluate_uvss(
    candles: list[dict],
    timeframe_key: str,
    *,
    pair: str = "default",
) -> dict:
    """Blue Box + VSA + Marubozu on the last closed candle only."""
    if not UVSS_POLICIES_ENABLED:
        return {"action": "NO_TRADE", "reason": "Entry policies disabled"}

    if len(candles) < MIN_CANDLES:
        return {
            "action": "NO_TRADE",
            "reason": f"Need {MIN_CANDLES}+ closed candles (have {len(candles)})",
        }

    state = get_blue_box_state(pair, timeframe_key)
    current_index = len(candles) - 1
    signal = candles[-1]
    h, l, c, v = signal["high"], signal["low"], signal["close"], signal["volume"]
    body = _body(signal)
    expected_body = _expected_body(signal, candles)
    lowest_20 = _lowest_20(candles)
    highest_20 = _highest_20(candles)
    trend, ema50, ema200 = _trend_state(candles, c)
    marubozu = _is_marubozu(signal, candles)

    _expire_sweeps(state, current_index)

    diagnostics = {
        "expected_body": round(expected_body, 8),
        "body": round(body, 8),
        "volume": round(v, 4),
        "lowest_20": round(lowest_20, 4),
        "highest_20": round(highest_20, 4),
        "ema50": round(ema50, 4) if ema50 is not None else None,
        "ema200": round(ema200, 4) if ema200 is not None else None,
        "trend": trend,
        "is_marubozu": marubozu,
        "avg_body_20": round(_avg_body(candles), 8),
        **_state_diagnostics(state, current_index),
    }

    sweep_events: list[str] = []
    signal_hit: dict | None = None

    bull_disp = _check_bullish_displacement(state, current_index, signal, expected_body)
    bear_disp = _check_bearish_displacement(state, current_index, signal, expected_body)

    if bull_disp and bear_disp:
        return {
            "action": "NO_TRADE",
            "reason": "Conflicting Blue Box displacement signals",
            **diagnostics,
        }

    if bull_disp:
        signal_hit = bull_disp
        _clear_bullish(state)
    elif bear_disp:
        signal_hit = bear_disp
        _clear_bearish(state)

    if signal_hit is None:
        vsa = _check_vsa_rules(candles, signal, expected_body, lowest_20, highest_20)
        if vsa:
            if vsa.get("conflict"):
                return {
                    "action": "NO_TRADE",
                    "reason": "Conflicting VSA long and short rules on same bar",
                    "long_rules": vsa.get("long_rules", []),
                    "short_rules": vsa.get("short_rules", []),
                    **diagnostics,
                }
            signal_hit = vsa
            diagnostics["long_rules"] = vsa.get("long_rules", [])
            diagnostics["short_rules"] = vsa.get("short_rules", [])

    if signal_hit is None:
        mbz = _check_marubozu_continuation(candles, signal, trend)
        if mbz:
            signal_hit = mbz

    if signal_hit is None:
        mom = _check_momentum(signal, expected_body)
        if mom:
            signal_hit = mom

    if _detect_bullish_sweep(state, current_index, l, c, lowest_20, signal):
        sweep_events.append("bullish_sweep")
    if _detect_bearish_sweep(state, current_index, h, c, highest_20, signal):
        sweep_events.append("bearish_sweep")

    diagnostics.update(_state_diagnostics(state, current_index))
    diagnostics["sweep_events"] = sweep_events

    if signal_hit is None:
        if sweep_events:
            traps = ", ".join(sweep_events)
            return {
                "action": "NO_TRADE",
                "reason": f"Liquidity sweep detected ({traps}) — waiting for displacement",
                **diagnostics,
            }
        if marubozu and trend in ("uptrend", "downtrend"):
            return {
                "action": "NO_TRADE",
                "reason": "Marubozu candle but pullback/trend continuation not aligned",
                **diagnostics,
            }
        return {
            "action": "NO_TRADE",
            "reason": "No Blue Box, VSA, Marubozu, or momentum signal",
            **diagnostics,
        }

    action = signal_hit["action"]
    pattern = signal_hit["pattern"]
    sl = signal_hit["sl"]
    entry_px = c
    rr = float(signal_hit.get("rr", RR_RATIO))
    prices = compute_sl_tp(action, entry_px, sl, rr=rr)
    if not prices:
        return {"action": "NO_TRADE", "reason": "Could not compute SL/TP", **diagnostics}

    entry_px, sl, tp = prices
    risk_dist = abs(entry_px - sl)
    codes = signal_hit.get("rules_fired") or pattern.split("+")
    long_rules = [c for c in codes if c.startswith(("L", "BB-L", "MOM-L", "MBZ-L"))]
    short_rules = [c for c in codes if c.startswith(("S", "BB-S", "MOM-S", "MBZ-S"))]
    if not long_rules and action == "BUY":
        long_rules = codes
    if not short_rules and action == "SELL":
        short_rules = codes

    return {
        "action": action,
        "pattern": pattern,
        "reason": " | ".join(PATTERN_LABELS.get(r, r) for r in codes),
        "setup": signal_hit.get("setup"),
        "size_mult": rr,
        "target_mult": rr,
        "rules_fired": codes,
        "long_rules": long_rules,
        "short_rules": short_rules,
        "entry": entry_px,
        "sl": sl,
        "tp": tp,
        "risk_distance": round(risk_dist, 6),
        **diagnostics,
    }


evaluate_smc_vsa = evaluate_uvss


def _to_chart_time(raw: int | None) -> int | None:
    """Normalize Bybit kline open time to unix seconds for the frontend chart."""
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
    """Snapshot for chart liquidity lines + trap boxes (portfolio WS / UI)."""
    if not is_active or not UVSS_POLICIES_ENABLED:
        return {"engine": "blue_box", "active": False}

    state = get_blue_box_state(pair, timeframe_key)
    decision = (last_scan or {}).get("decision") or {}
    lowest_20 = decision.get("lowest_20")
    highest_20 = decision.get("highest_20")
    ema50 = decision.get("ema50")
    ema200 = decision.get("ema200")
    trend = decision.get("trend")

    bullish_trap = None
    if state.bullish_active and state.bullish_sweep_low is not None:
        bullish_trap = {
            "active": True,
            "sweep_low": state.bullish_sweep_low,
            "zone_top": lowest_20,
            "sweep_time": _to_chart_time(state.bullish_sweep_time),
            "bars_window": 3,
        }

    bearish_trap = None
    if state.bearish_active and state.bearish_sweep_high is not None:
        bearish_trap = {
            "active": True,
            "sweep_high": state.bearish_sweep_high,
            "zone_bottom": highest_20,
            "sweep_time": _to_chart_time(state.bearish_sweep_time),
            "bars_window": 3,
        }

    status = "watching"
    if bullish_trap:
        status = "bull_trap"
    elif bearish_trap:
        status = "bear_trap"
    elif decision.get("action") in ("BUY", "SELL"):
        status = "signal"
    elif decision.get("is_marubozu"):
        status = "marubozu_watch"

    return {
        "engine": "blue_box",
        "active": True,
        "status": status,
        "pair": pair,
        "timeframe": timeframe_key,
        "lowest_20": lowest_20,
        "highest_20": highest_20,
        "ema50": ema50,
        "ema200": ema200,
        "trend": trend,
        "is_marubozu": decision.get("is_marubozu"),
        "bullish_trap": bullish_trap,
        "bearish_trap": bearish_trap,
        "last_pattern": decision.get("pattern"),
        "last_action": decision.get("action"),
    }
