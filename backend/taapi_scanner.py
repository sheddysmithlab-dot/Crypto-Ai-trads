"""Core candlestick-pattern scanning + trade-math logic, per SYSTEM_RULES.md.

Two responsibilities only:
  1. fetch_taapi_signals()  - pulls every pattern in TAAPI_PATTERNS from TAAPI.io.
  2. evaluate_trade()       - turns those signals + the signal candle's OHLC into a
                              concrete BUY/SELL/REJECT/NO_TRADE decision with
                              entry/SL/TP prices, per the strict boundary rules.

All constants below are copied from SYSTEM_RULES.md - do not hardcode
fee/profit/buffer numbers anywhere else; change them here only.

TAAPI is queried through its BULK endpoint (POST /bulk, max 20 calculations per
request on every plan -> 30 patterns = 2 batched requests) instead of 30
individual GETs: the free plan allows only 1 request / 15 seconds, so 30
per-pattern calls could never finish inside a candle period, while 2 bulk
requests spaced one rate-limit window apart always can.
"""
import os
import time

import requests

# ==========================================
# 1. BYBIT FEE & PROFIT MATH CONSTANTS
# ==========================================
BYBIT_TAKER_FEE = 0.0011  # 0.11% round-trip (0.055% open + 0.055% close, USDT perpetual taker)
# Formula for API Target: Gross_TP = User_Net_Profit + BYBIT_TAKER_FEE

# ==========================================
# 2. TIMEFRAME PROFIT & MAX SL MATRIX (STRICT LOOKUP)
# Shifted one step after 30s chart removal: each TF inherits the prior
# (faster) row so 1m now starts at the old 30s targets (0.40% net / 0.60% gross).
# ==========================================
_TIMEFRAME_RULES_PRE_SHIFT = {
    "30s": {"net_profit": 0.004, "gross_tp": 0.006, "max_allowed_sl": 0.006, "buffer": 0.0005},
    "1m":  {"net_profit": 0.006, "gross_tp": 0.008, "max_allowed_sl": 0.008, "buffer": 0.0005},
    "3m":  {"net_profit": 0.006, "gross_tp": 0.008, "max_allowed_sl": 0.008, "buffer": 0.0010},
    "5m":  {"net_profit": 0.006, "gross_tp": 0.008, "max_allowed_sl": 0.008, "buffer": 0.0010},
    "10m": {"net_profit": 0.008, "gross_tp": 0.010, "max_allowed_sl": 0.010, "buffer": 0.0010},
    "15m": {"net_profit": 0.008, "gross_tp": 0.010, "max_allowed_sl": 0.010, "buffer": 0.0015},
    "30m": {"net_profit": 0.010, "gross_tp": 0.012, "max_allowed_sl": 0.012, "buffer": 0.0015},
    "1h":  {"net_profit": 0.012, "gross_tp": 0.014, "max_allowed_sl": 0.014, "buffer": 0.0015},
    "1D":  {"net_profit": 0.015, "gross_tp": 0.017, "max_allowed_sl": 0.017, "buffer": 0.0015},
}
_TIMEFRAME_RULE_SHIFT_FROM = {
    "1m": "30s", "3m": "1m", "5m": "3m", "10m": "5m", "15m": "10m",
    "30m": "15m", "1h": "30m", "1D": "1h",
}
TIMEFRAME_RULES = {
    tf: dict(_TIMEFRAME_RULES_PRE_SHIFT[src])
    for tf, src in _TIMEFRAME_RULE_SHIFT_FROM.items()
}
TIMEFRAME_RULES["30s"] = dict(_TIMEFRAME_RULES_PRE_SHIFT["30s"])

# ==========================================
# 3. TAAPI.IO PATTERN DICTIONARY
# Structure: "endpoint": "Action_Type" - 'BUY', 'SELL', or 'BOTH'.
#
# Endpoint names are TAAPI's REAL indicator slugs (verified against
# https://taapi.io/indicators/) - SYSTEM_RULES.md's draft listed them with a
# TA-Lib-style "cdl_" prefix (cdl_hammer, cdl_belt_hold, ...) that does not
# exist on TAAPI's API; querying those names 404s on every single call, which
# silently zeroed every signal. Same 30 patterns, same action types - only the
# slugs are corrected (incl. the cdl_dragondflydoji typo -> dragonflydoji).
#
# Action_Type drives signal direction - NOT the raw value's sign alone:
# TA-Lib reports doji-family shapes as +100 whenever found (the shape itself
# is direction-neutral), so e.g. gravestonedoji comes back POSITIVE even
# though the rules dictionary classifies it as a SELL setup. One-sided BUY/
# SELL patterns therefore take their direction from this dictionary, and only
# 'BOTH' patterns (engulfing, harami, ...) use the value's sign (+1 bullish
# variant / -1 bearish variant).
# ==========================================
# TAAPI pattern engine — paused; SMC+VSA uses Bybit klines. TIMEFRAME_RULES still used for TP/SL %.
PATTERN_TRADE_POLICIES_ENABLED = False
TAAPI_PAUSED = True

# Pattern → action map (BUY / SELL / BOTH). Empty until owner defines rules.
# Example when ready:
#   "hammer": "BUY",
#   "engulfing": "BOTH",
#   "shootingstar": "SELL",
TAAPI_PATTERNS = {}

# TAAPI only supports these query intervals: 1m/5m/15m/30m/1h/2h/4h/12h/1d/1w.
# TIMEFRAME_RULES' "30s"/"3m"/"10m"/"1D" keys have no TAAPI equivalent and map
# to the nearest supported granularity (callers keep using TIMEFRAME_RULES
# keys everywhere else - only the TAAPI query itself is translated).
TAAPI_INTERVAL_MAP = {
    "30s": "1m", "1m": "1m", "3m": "1m", "5m": "5m", "10m": "5m",
    "15m": "15m", "30m": "30m", "1h": "1h", "1d": "1d", "1D": "1d",
}

TAAPI_BULK_URL = "https://api.taapi.io/bulk"
MAX_CALCULATIONS_PER_BULK = 20  # hard cap on EVERY TAAPI plan, free included


def _normalize_signal(action_type, raw_value):
    """ Applies the rules dictionary's Action_Type to TAAPI's raw pattern value:
    a one-sided pattern's direction comes from the dictionary (any non-zero hit
    counts), only 'BOTH' patterns use the sign of the value itself. """
    if not raw_value:
        return 0
    if action_type == "BUY":
        return 1
    if action_type == "SELL":
        return -1
    return 1 if raw_value > 0 else -1


def fetch_taapi_signals(symbol, interval, exchange, api_key):
    """ Queries all TAAPI_PATTERNS via the bulk endpoint (batches of <=20) and
    returns [{"pattern": <slug>, "value": 1 | -1 | 0}] - direction already
    normalized through the Action_Type dictionary. A failed batch / errored
    indicator / missing key contributes 0 for its patterns (fail safe: no
    signal, no trade), with one summary log line instead of silent zeros.

    TAAPI_BATCH_DELAY_SECONDS (env, default 16) spaces the two batches to
    respect the free plan's 1-request-per-15s limit; paid plans can set it
    to 0. """
    taapi_interval = TAAPI_INTERVAL_MAP.get(interval, interval)
    names = list(TAAPI_PATTERNS.keys())
    batches = [names[i:i + MAX_CALCULATIONS_PER_BULK] for i in range(0, len(names), MAX_CALCULATIONS_PER_BULK)]
    try:
        batch_delay = float(os.environ.get("TAAPI_BATCH_DELAY_SECONDS", "16"))
    except ValueError:
        batch_delay = 16.0

    raw_values = {name: 0 for name in names}
    failed = 0
    for batch_index, batch in enumerate(batches):
        if batch_index > 0 and batch_delay > 0:
            time.sleep(batch_delay)  # runs in a worker thread (asyncio.to_thread), never blocks the event loop
        body = {
            "secret": api_key,
            "construct": {
                "exchange": exchange,
                "symbol": symbol,
                "interval": taapi_interval,
                "indicators": [{"id": name, "indicator": name} for name in batch],
            },
        }
        try:
            resp = requests.post(TAAPI_BULK_URL, json=body, timeout=15)
            resp.raise_for_status()
            for item in resp.json().get("data", []):
                name = item.get("id")
                if name not in raw_values:
                    continue
                if item.get("errors"):
                    failed += 1
                    continue
                value = item.get("result", {}).get("value", 0)
                if isinstance(value, (int, float)):
                    raw_values[name] = value
        except Exception as exc:
            failed += len(batch)
            print(f"[TAAPI] Bulk batch {batch_index + 1}/{len(batches)} failed: {exc}")

    if failed:
        print(f"[TAAPI] {failed}/{len(names)} pattern lookups unavailable this scan "
              f"(rate limit / plan tier / symbol not on '{exchange}').")

    return [
        {"pattern": name, "value": _normalize_signal(TAAPI_PATTERNS[name], raw_values[name])}
        for name in names
    ]


def evaluate_trade(signals_list, interval, candle_high, candle_low):
    """Reduce pattern signals + candle OHLC into a trade decision (owner rules)."""
    if not PATTERN_TRADE_POLICIES_ENABLED:
        return {"action": "NO_TRADE", "reason": "Trade policies blank — waiting for candle pattern rules"}
    if not TAAPI_PATTERNS:
        return {"action": "NO_TRADE", "reason": "No candle patterns mapped yet"}

    # a) CONFLICT CHECK - a bullish AND a bearish pattern firing at once is an
    # unreliable, contradictory read - skip the candle entirely.
    has_bullish = any(s["value"] == 1 for s in signals_list)
    has_bearish = any(s["value"] == -1 for s in signals_list)
    if has_bullish and has_bearish:
        return {"action": "NO_TRADE", "reason": "Conflicting signals"}

    # b) SIGNAL EXTRACTION - first non-zero signal in the list (conflict check
    # above already guarantees every non-zero entry shares the same sign).
    first_signal = next((s for s in signals_list if s["value"] != 0), None)
    if first_signal is None:
        return {"action": "NO_TRADE", "reason": "No valid signal"}
    action = "BUY" if first_signal["value"] == 1 else "SELL"
    pattern_name = first_signal["pattern"]

    # c) MATH & REJECTION CHECK
    rules = TIMEFRAME_RULES.get(interval)
    if rules is None:
        return {"action": "NO_TRADE", "reason": f"Unknown timeframe '{interval}'"}
    buffer_pct = rules["buffer"]
    gross_tp = rules["gross_tp"]

    if candle_low <= 0:
        return {"action": "NO_TRADE", "reason": "Invalid candle price data"}

    # No trade stop-loss rejection — exits are profit-book only (per-trade trailing lock).
    buffer = candle_low * buffer_pct

    # d) FINAL PRICE CALCULATIONS
    if action == "BUY":
        entry = candle_high + buffer
        sl = candle_low - buffer
        tp = entry * (1 + gross_tp)
    else:
        entry = candle_low - buffer
        sl = candle_high + buffer
        tp = entry * (1 - gross_tp)

    # e) Final payload
    return {"action": action, "entry": entry, "sl": sl, "tp": tp, "pattern": pattern_name}
