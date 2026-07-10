"""Ultimate SMC + VSA System — 11 rule groups (R1–R12) + 200 EMA trend filter.

Port of TradingView Pine indicator (bar-close signals only).
VSA rules R1–R7 + SMC pro-filters R8–R12 (liquidity sweep, absorption, no supply).
"""
from __future__ import annotations

from taapi_scanner import TIMEFRAME_RULES

UVSS_POLICIES_ENABLED = True  # master switch for auto entry policy
UVSS_COST_AWARE_ENTRY = False
EMA_LENGTH = 200
RATIO_PERIOD = 30
VOLUME_CLIMAX_PERIOD = 30
SWEEP_LOOKBACK = 20
MIN_CANDLES = EMA_LENGTH + SWEEP_LOOKBACK + 2

RULE_SIZE_MULT = {
    "R1": 1,
    "R2": 2,
    "R3": 2,
    "R4": 1,
    "R5": 2,
    "R6": 2,
    "R7": 2,
    "R8": 2,
    "R9": 2,
    "R10": 2,
    "R11": 2,
    "R12": 1,
}

RULE_LABELS = {
    "R1": "R1: Red Big Candle — VSA Long 1x",
    "R2": "R2: Red Hammer — VSA Long 2x",
    "R3": "R3: Volume Divergence Seq — VSA Long 2x",
    "R4": "R4: Green Big Candle — VSA Short 1x",
    "R5": "R5: Green Hammer — VSA Long 2x",
    "R6": "R6: Hidden Selling Seq — VSA Short 2x",
    "R7": "R7: Volume Climax Reversal — VSA Short 2x",
    "R8": "R8: Wyckoff Spring — Liq Sweep Long 2x",
    "R9": "R9: Up-Thrust — Liq Sweep Short 2x",
    "R10": "R10: Buy Absorption — SMC Long 2x",
    "R11": "R11: Sell Absorption — SMC Short 2x",
    "R12": "R12: No Supply Dry-Up — SMC Long 1x",
}


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


def _upper_tail(c: dict) -> float:
    return c["high"] - max(c["open"], c["close"])


def _lower_tail(c: dict) -> float:
    return min(c["open"], c["close"]) - c["low"]


def _is_green(c: dict) -> bool:
    return c["close"] > c["open"]


def _is_red(c: dict) -> bool:
    return c["close"] < c["open"]


def _pine_bar(candles: list[dict], offset: int) -> dict | None:
    if offset < 0:
        return None
    idx = -(offset + 1)
    if abs(idx) > len(candles):
        return None
    return candles[idx]


def compute_ema(closes: list[float], length: int) -> float | None:
    if len(closes) < length:
        return None
    k = 2.0 / (length + 1)
    ema = sum(closes[:length]) / length
    for price in closes[length:]:
        ema = price * k + ema * (1.0 - k)
    return ema


def _sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _trade_prices(action: str, entry: float, candle: dict, timeframe_key: str) -> tuple[float, float, float] | None:
    rules = TIMEFRAME_RULES.get(timeframe_key)
    if not rules or candle.get("low", 0) <= 0:
        return None
    buffer = candle["low"] * rules["buffer"]
    gross_tp = rules["gross_tp"]
    if action == "BUY":
        sl = candle["low"] - buffer
        tp = entry * (1.0 + gross_tp)
    else:
        sl = candle["high"] + buffer
        tp = entry * (1.0 - gross_tp)
    return entry, sl, tp


def _sequence_volumes(candles: list[dict]) -> tuple[float, float]:
    buy_vol = 0.0
    sell_vol = 0.0
    for i in range(1, 13):
        bar = _pine_bar(candles, i)
        if bar is None:
            continue
        if _is_green(bar):
            buy_vol += bar["volume"]
        else:
            sell_vol += bar["volume"]
    return buy_vol, sell_vol


def evaluate_uvss(candles: list[dict], timeframe_key: str) -> dict:
    """Evaluate SMC+VSA on the last closed candle (chronological history)."""
    if not UVSS_POLICIES_ENABLED:
        return {"action": "NO_TRADE", "reason": "SMC+VSA policies disabled"}

    if len(candles) < MIN_CANDLES:
        return {
            "action": "NO_TRADE",
            "reason": f"Need {MIN_CANDLES}+ candles for EMA/sweep lookback (have {len(candles)})",
        }

    signal = candles[-1]
    h, l, c, v = signal["high"], signal["low"], signal["close"], signal["volume"]
    body = _body(signal)
    upper_tail = _upper_tail(signal)
    lower_tail = _lower_tail(signal)
    is_green = _is_green(signal)
    is_red = _is_red(signal)
    mid = (h + l) / 2.0

    closes = [x["close"] for x in candles]
    volumes = [x["volume"] for x in candles]
    ema200 = compute_ema(closes, EMA_LENGTH)
    if ema200 is None:
        return {"action": "NO_TRADE", "reason": "Could not compute 200 EMA"}

    is_uptrend = c > ema200
    is_downtrend = c < ema200

    ratios = []
    for bar in candles[-RATIO_PERIOD:]:
        b = _body(bar)
        vol = bar["volume"]
        ratios.append((b / vol) if vol > 0 else 0.0)
    avg_ratio = sum(ratios) / len(ratios) if ratios else 0.0
    expected_body = v * avg_ratio if v > 0 else 0.0

    vol_window = volumes[-VOLUME_CLIMAX_PERIOD:]
    highest_vol_30 = max(vol_window) if vol_window else 0.0

    buy_vol, sell_vol = _sequence_volumes(candles)

    b9 = _pine_bar(candles, 9)
    b10 = _pine_bar(candles, 10)
    b11 = _pine_bar(candles, 11)
    b1 = _pine_bar(candles, 1)
    b2 = _pine_bar(candles, 2)
    b3 = _pine_bar(candles, 3)

    r3_candles_green = b9 and b10 and b11 and all(_is_green(x) for x in (b9, b10, b11))
    r3_candles_red = any(_is_red(x) for x in (b1, b2, b3) if x)
    r6_candles_red = b9 and b10 and b11 and all(_is_red(x) for x in (b9, b10, b11))
    r6_candles_green = any(_is_green(x) for x in (b1, b2, b3) if x)

    # --- VSA rules 1–7 ---
    rule1 = is_red and body >= 2 * expected_body and is_uptrend
    rule2 = (
        is_red
        and upper_tail <= body * 0.1
        and body <= lower_tail * 0.5
        and body <= 0.5 * expected_body
        and is_uptrend
    )
    rule3 = r3_candles_green and r3_candles_red and buy_vol < sell_vol and is_uptrend
    rule4 = is_green and body >= 2 * expected_body and is_downtrend
    rule5 = (
        is_green
        and upper_tail <= body * 0.1
        and body <= lower_tail * 0.5
        and body <= 0.5 * expected_body
        and is_uptrend
    )
    rule6 = r6_candles_red and r6_candles_green and buy_vol < sell_vol and is_downtrend
    rule7 = is_green and v >= highest_vol_30 and upper_tail >= body * 0.5 and is_downtrend

    # --- SMC rules 8–12 ---
    prior_20 = candles[-21:-1]
    lowest_20 = min(x["low"] for x in prior_20) if prior_20 else l
    highest_20 = max(x["high"] for x in prior_20) if prior_20 else h

    vol_sma_20 = _sma(volumes, 20)
    vol_sma_30 = _sma(volumes, 30)
    vol_sma_15 = _sma(volumes, 15)

    rule8 = (
        l < lowest_20
        and c > mid
        and vol_sma_20 is not None
        and v > vol_sma_20 * 1.5
    )
    rule9 = (
        h > highest_20
        and c < mid
        and vol_sma_20 is not None
        and v > vol_sma_20 * 1.5
    )

    high_effort = vol_sma_30 is not None and v > vol_sma_30 * 2
    no_result = body <= expected_body * 0.5
    rule10 = is_red and high_effort and no_result and lower_tail >= body and is_uptrend
    rule11 = is_green and high_effort and no_result and upper_tail >= body and is_downtrend
    rule12 = is_red and vol_sma_15 is not None and v < vol_sma_15 * 0.5 and is_uptrend

    long_hits = []
    for flag, code in (
        (rule1, "R1"),
        (rule2, "R2"),
        (rule3, "R3"),
        (rule5, "R5"),
        (rule8, "R8"),
        (rule10, "R10"),
        (rule12, "R12"),
    ):
        if flag:
            long_hits.append(code)

    short_hits = []
    for flag, code in (
        (rule4, "R4"),
        (rule6, "R6"),
        (rule7, "R7"),
        (rule9, "R9"),
        (rule11, "R11"),
    ):
        if flag:
            short_hits.append(code)

    diagnostics = {
        "ema200": round(ema200, 4),
        "trend": "uptrend" if is_uptrend else "downtrend" if is_downtrend else "neutral",
        "expected_body": round(expected_body, 8),
        "body": round(body, 8),
        "highest_vol_30": round(highest_vol_30, 4),
        "volume": round(v, 4),
        "buy_vol_12": round(buy_vol, 4),
        "sell_vol_12": round(sell_vol, 4),
        "lowest_20": round(lowest_20, 4),
        "highest_20": round(highest_20, 4),
        "long_rules": long_hits,
        "short_rules": short_hits,
    }

    if long_hits and short_hits:
        return {
            "action": "NO_TRADE",
            "reason": "Conflicting long and short SMC+VSA rules on same bar",
            "long_rules": long_hits,
            "short_rules": short_hits,
            **diagnostics,
        }

    if not long_hits and not short_hits:
        return {"action": "NO_TRADE", "reason": "No SMC+VSA rule matched", **diagnostics}

    if long_hits:
        action = "BUY"
        hits = long_hits
    else:
        action = "SELL"
        hits = short_hits

    size_mult = max(RULE_SIZE_MULT[r] for r in hits)
    pattern = "+".join(hits)
    reason = " | ".join(RULE_LABELS[r] for r in hits)
    prices = _trade_prices(action, c, signal, timeframe_key)
    if not prices:
        return {"action": "NO_TRADE", "reason": f"Unknown timeframe '{timeframe_key}'", **diagnostics}

    entry_px, sl, tp = prices
    return {
        "action": action,
        "pattern": pattern,
        "reason": reason,
        "size_mult": size_mult,
        "rules_fired": hits,
        "entry": entry_px,
        "sl": sl,
        "tp": tp,
        **diagnostics,
    }


evaluate_smc_vsa = evaluate_uvss
