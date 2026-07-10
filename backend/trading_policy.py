"""Cost-aware entry/exit policy (paper-style execution filter for TAAPI signals).

Maps the academic cost-aware rule to this bot:
  trade only when expected edge exceeds λ × round-trip transaction cost.
"""
from __future__ import annotations

import os

from taapi_scanner import TIMEFRAME_RULES

_DEFAULT_LAMBDA = 2.0
_DEFAULT_MIN_CANDLE_RANGE = 0.5


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def cost_aware_enabled() -> bool:
    return _env_bool("COST_AWARE_ENABLED", True)


def cost_aware_dry_run() -> bool:
    return _env_bool("COST_AWARE_DRY_RUN", False)


def cost_aware_lambda() -> float:
    try:
        return float(os.environ.get("COST_AWARE_LAMBDA", str(_DEFAULT_LAMBDA)))
    except ValueError:
        return _DEFAULT_LAMBDA


def cost_aware_min_candle_range_mult() -> float:
    try:
        return float(os.environ.get("COST_AWARE_MIN_CANDLE_RANGE", str(_DEFAULT_MIN_CANDLE_RANGE)))
    except ValueError:
        return _DEFAULT_MIN_CANDLE_RANGE


def round_trip_cost_pct(taker_fee_pct: float) -> float:
    """Open + close taker fees as % of notional."""
    return 2.0 * float(taker_fee_pct)


def entry_hurdle_pct(taker_fee_pct: float, lambda_mult: float | None = None) -> float:
    lam = cost_aware_lambda() if lambda_mult is None else lambda_mult
    return lam * round_trip_cost_pct(taker_fee_pct)


def compute_remaining_edge_pct(action: str, current_price: float, entry: float, tp: float) -> float | None:
    if not current_price or current_price <= 0 or entry is None or tp is None:
        return None
    if action == "BUY":
        return (float(tp) - float(current_price)) / float(current_price) * 100.0
    if action == "SELL":
        return (float(current_price) - float(tp)) / float(current_price) * 100.0
    return None


def compute_candle_range_pct(candle_high: float, candle_low: float) -> float | None:
    if candle_low is None or candle_high is None or float(candle_low) <= 0:
        return None
    return (float(candle_high) - float(candle_low)) / float(candle_low) * 100.0


def planned_gross_tp_pct(timeframe_key: str) -> float | None:
    rules = TIMEFRAME_RULES.get(timeframe_key)
    if not rules:
        return None
    return float(rules["gross_tp"]) * 100.0


def get_effective_exit_floor_pct(chart_floor_pct: float, taker_fee_pct: float) -> float:
    """Exit when gross >= max(chart TF floor, λ × round-trip cost)."""
    return max(float(chart_floor_pct), entry_hurdle_pct(taker_fee_pct))


def evaluate_cost_aware_entry(
    result: dict,
    candle: dict,
    current_price: float,
    timeframe_key: str,
    taker_fee_pct: float,
) -> dict:
    """Return diagnostics + whether the entry gate passes (paper cost-aware filter)."""
    lam = cost_aware_lambda()
    rt_cost = round_trip_cost_pct(taker_fee_pct)
    hurdle = entry_hurdle_pct(taker_fee_pct, lam)
    min_range_mult = cost_aware_min_candle_range_mult()
    min_range_hurdle = min_range_mult * rt_cost

    action = result.get("action", "")
    entry = result.get("entry")
    tp = result.get("tp")
    remaining = compute_remaining_edge_pct(action, current_price, entry, tp)
    candle_range = compute_candle_range_pct(candle.get("high"), candle.get("low"))
    planned_tp = planned_gross_tp_pct(timeframe_key)

    remaining_ok = remaining is not None and remaining >= hurdle
    range_ok = candle_range is not None and candle_range >= min_range_hurdle
    gate_ok = remaining_ok and range_ok

    enabled = cost_aware_enabled()
    dry_run = cost_aware_dry_run()
    would_block = enabled and not gate_ok

    block_reasons = []
    if enabled and remaining is not None and not remaining_ok:
        block_reasons.append(
            f"remaining edge {remaining:.3f}% < hurdle {hurdle:.3f}% (λ={lam})"
        )
    if enabled and candle_range is not None and not range_ok:
        block_reasons.append(
            f"candle range {candle_range:.3f}% < min {min_range_hurdle:.3f}%"
        )
    if enabled and remaining is None:
        block_reasons.append("could not compute remaining edge")
    if enabled and candle_range is None:
        block_reasons.append("could not compute candle range")

    return {
        "enabled": enabled,
        "dry_run": dry_run,
        "lambda": lam,
        "round_trip_cost_pct": round(rt_cost, 4),
        "entry_hurdle_pct": round(hurdle, 4),
        "min_candle_range_pct": round(min_range_hurdle, 4),
        "remaining_edge_pct": round(remaining, 4) if remaining is not None else None,
        "candle_range_pct": round(candle_range, 4) if candle_range is not None else None,
        "planned_gross_tp_pct": round(planned_tp, 4) if planned_tp is not None else None,
        "passed": (not enabled) or gate_ok,
        "would_block": would_block,
        "block_reason": "; ".join(block_reasons) if block_reasons else None,
    }


def policy_summary_suffix(taker_fee_pct: float, chart_floor_pct: float) -> str:
    eff_exit = get_effective_exit_floor_pct(chart_floor_pct, taker_fee_pct)
    lam = cost_aware_lambda()
    hurdle = entry_hurdle_pct(taker_fee_pct)
    mode = "ON" if cost_aware_enabled() else "OFF"
    if cost_aware_dry_run():
        mode += " (dry-run)"
    return (
        f"cost-aware entry {mode} λ={lam} hurdle≥{hurdle:.2f}% gross | "
        f"exit floor≥{eff_exit:.2f}% gross"
    )
