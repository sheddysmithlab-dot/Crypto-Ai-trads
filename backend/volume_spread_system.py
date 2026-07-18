"""Entry engine shell — pattern detection / pattern-name memory CLEARED.

Live wiring in main.py still calls evaluate_uvss(); until new rules are added
every closed candle returns NO_TRADE. Helpers for klines, sizing, and chart
overlay stay so the rest of the app does not break.
"""
from __future__ import annotations

from dataclasses import dataclass

UVSS_POLICIES_ENABLED = True
UVSS_COST_AWARE_ENTRY = False
UVSS_SL_EXIT_ENABLED = False

# Kept for candle history length / EMA-ready window when rules return.
EMA_SLOW = 200
BODY_AVG_PERIOD = 20
SWEEP_LOOKBACK = 20
RATIO_PERIOD = 30
MIN_CANDLES = max(SWEEP_LOOKBACK + RATIO_PERIOD + 2, EMA_SLOW + BODY_AVG_PERIOD + 5)
RISK_PCT_PER_TRADE = 0.01
RR_RATIO = 2.0
SL_BUFFER_PCT = 0.001

# Pattern names + labels — CLEARED (add new codes here when ready).
PATTERN_LABELS: dict[str, str] = {}
RULE_RR: dict[str, float] = {}


@dataclass
class BlueBoxState:
    """Trap state placeholder — no sweep/displacement logic until rules return."""

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
    print(
        f"[EXECUTE_TRADE] {direction} | pattern={pattern} | "
        f"entry={entry} sl={sl} tp={tp} qty={qty} balance={balance}"
    )


def evaluate_uvss(
    candles: list[dict],
    timeframe_key: str,
    *,
    pair: str = "default",
) -> dict:
    """Pattern engine cleared — no BUY/SELL until new rules are defined."""
    if not UVSS_POLICIES_ENABLED:
        return {"action": "NO_TRADE", "reason": "Entry policies disabled"}

    if len(candles) < MIN_CANDLES:
        return {
            "action": "NO_TRADE",
            "reason": f"Need {MIN_CANDLES}+ closed candles (have {len(candles)})",
        }

    # Clear any leftover trap memory on scan.
    get_blue_box_state(pair, timeframe_key)

    return {
        "action": "NO_TRADE",
        "reason": "Pattern detection memory cleared — no rules loaded",
        "pattern": None,
        "long_rules": [],
        "short_rules": [],
        "rules_fired": [],
        "sweep_events": [],
    }


evaluate_smc_vsa = evaluate_uvss


def build_blue_box_chart_overlay(
    pair: str,
    timeframe_key: str,
    *,
    is_active: bool,
    last_scan: dict | None = None,
) -> dict:
    """Chart overlay inactive while pattern memory is cleared."""
    return {
        "engine": "blue_box",
        "active": False,
        "status": "cleared",
        "pair": pair,
        "timeframe": timeframe_key,
        "last_pattern": None,
        "last_action": (last_scan or {}).get("decision", {}).get("action"),
    }
