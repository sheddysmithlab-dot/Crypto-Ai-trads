"""Adapter between backend/brain.py and the rest of the project.

brain.py is kept completely untouched.  This module owns every schema/unit
conversion and exposes the same function signatures that main.py calls so that
switching to the new engine required zero changes inside main.py's call sites.

Decision path (canonical):
  1. Convert dict candles -> brain.Candle objects.
  2. Normalise timeframe key to brain.TIMEFRAMES keys.
  3. Call Brain.think(tf) which applies the 10th-man (trap) policy: fresh traps
     override standard signals; HOLD when nothing qualifies.
  4. Flatten AnalysisResult -> plain dict with action/reason/entry/sl/tp/...
     that the rest of the backend already understands.

No trade-logic lives here — brain.py is the single source of truth.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Sequence

import brain as _b

ENGINE_NAME = "candlestick_brain_v2"
ENTRY_PATTERN_NAME = "BRAIN_V2"

# ─── timeframe normalisation ──────────────────────────────────────────────────
_TF_NORM: Dict[str, str] = {
    # uppercase variants
    "1M": "1m", "5M": "5m", "15M": "15m", "1H": "1h", "1D": "1d",
    # aliases not in brain.TIMEFRAMES
    "30s": "1m", "30S": "1m",
    # already correct passthrough
    "1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "1d": "1d",
}

def _norm_tf(key: str) -> str:
    k = (key or "1h").strip()
    return _TF_NORM.get(k, "1h")   # default to 1h config if unknown


# ─── candle conversion ────────────────────────────────────────────────────────
def _to_candles(dicts: Sequence[dict]) -> List[_b.Candle]:
    out: List[_b.Candle] = []
    for c in dicts:
        try:
            out.append(_b.Candle(
                open=float(c["open"]),
                high=float(c["high"]),
                low=float(c["low"]),
                close=float(c["close"]),
                volume=float(c.get("volume") or 0.0),
                timestamp=float(c.get("close_time") or 0) / 1000.0,
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return out


# ─── result flattening ────────────────────────────────────────────────────────
def _flatten(res: dict, *, pair: str, timeframe_key: str,
             risk_pct_pct: float, equity: float) -> Dict[str, Any]:
    """Turn brain.Brain.think() result dict -> backend-compatible dict."""
    verdict = res.get("verdict", "HOLD")
    sig: Optional[_b.Signal] = res.get("signal")
    trap: Optional[_b.TrapSignal] = res.get("trap")
    stance: Optional[_b.SmartStance] = res.get("stance")
    ms: Optional[_b.MarketStructure] = res.get("structure")

    # action
    if verdict == "BUY":
        action = "BUY"
    elif verdict == "SELL":
        action = "SELL"
    else:
        action = "NO_TRADE"

    # entry / sl / tp come from trap if it drove the verdict, else signal
    entry_src = None
    if stance and stance.source == "trap" and trap is not None:
        entry_src = trap
    elif sig is not None:
        entry_src = sig

    entry_price = float(entry_src.entry) if entry_src else None
    sl = float(entry_src.stop) if entry_src else None
    tp = float(entry_src.target) if entry_src else None
    rr = float(getattr(entry_src, "rr", 0) or 0) if entry_src else None

    # risk plan
    plan: Optional[_b.TradePlan] = res.get("plan")
    if plan is None and entry_src is not None and action != "NO_TRADE":
        try:
            plan = _b.plan_trade(
                equity,
                risk_pct_pct,
                action,
                entry_src.entry,
                entry_src.stop,
                entry_src.target,
            )
        except Exception:
            plan = None

    # pattern name
    if stance and stance.source == "trap" and trap is not None:
        pattern_name = trap.trap_type.replace("_", " ")
        strategy_name = "trap_reverse"
        confluences = list(trap.reasons) if trap.reasons else []
    elif sig is not None:
        pattern_name = sig.patterns[0] if sig.patterns else sig.strategy
        strategy_name = sig.strategy.replace("_", " ")
        confluences = list(sig.confluence or []) + list(sig.reasons or [])
    else:
        pattern_name = None
        strategy_name = None
        confluences = []

    # reason string
    detail = res.get("verdict_detail", "")
    if stance and stance.narrative:
        detail = stance.narrative
    reason_parts = [detail]
    if confluences:
        reason_parts.append("confluences: " + "; ".join(confluences[:4]))
    reason = " | ".join(p for p in reason_parts if p)

    # market structure text
    trend = ms.trend if ms else None
    trend_strength = ms.trend_strength if ms else None

    # ML annotation
    ml = res.get("ml") or {}
    ml_label = ml.get("prediction", {}).get("label") if isinstance(ml, dict) else None

    return {
        "action": action,
        "reason": reason or "brain: no qualifying setup",
        "engine": ENGINE_NAME,
        "entry_pattern": ENTRY_PATTERN_NAME,
        "pattern": pattern_name,
        "strategy": strategy_name,
        "entry": entry_price,
        "sl": sl,
        "tp": tp,
        "stop": sl,
        "target": tp,
        "risk_reward": rr,
        "confidence": float(sig.confidence) if sig else (0.5 if action != "NO_TRADE" else 0.0),
        "score": float(sig.score) if sig else (float(trap.score) if trap else 0.0),
        "confluences": confluences,
        "psychology": pattern_name,
        "market_structure": trend,
        "market_phase": trend_strength,
        "timeframe_key": timeframe_key,
        "pair": pair,
        "direction": "LONG" if action == "BUY" else ("SHORT" if action == "SELL" else None),
        "source": (stance.source if stance else None),
        "ml_bias": ml_label,
        "trap_type": trap.trap_type if trap else None,
        "n_candles": res.get("n", 0),
        "last_close": res.get("last_close"),
        "risk_plan": {
            "units": plan.units,
            "risk_amount": plan.risk_amount,
            "rr": plan.rr,
            "entry": plan.entry,
            "stop": plan.stop,
            "target": plan.target,
        } if plan else None,
    }


# ─── public API ───────────────────────────────────────────────────────────────
MIN_CANDLES = 30  # minimum bars required before we run brain


def evaluate_live_entry(
    candles: List[dict],
    timeframe_key: str,
    *,
    pair: str = "default",
    htf_candles: Optional[List[dict]] = None,
    candles_1m: Optional[List[dict]] = None,
    candles_5m: Optional[List[dict]] = None,
    account_balance: float = 10000.0,
    risk_pct: float = 0.01,      # fractional (0.01 = 1%) — converted internally
) -> Dict[str, Any]:
    """Drop-in replacement for agent_brain.evaluate_live_entry().

    Uses brain.Brain.think() for the given timeframe.  Higher-timeframe
    context is derived from htf_candles (or candles_5m for 1m).
    """
    tf = _norm_tf(timeframe_key)
    risk_pct_pct = float(risk_pct) * 100.0   # brain.py expects 1.0 for 1%

    if len(candles) < MIN_CANDLES:
        return {
            "action": "NO_TRADE",
            "reason": f"Need {MIN_CANDLES}+ closed candles (have {len(candles)})",
            "engine": ENGINE_NAME,
            "entry_pattern": ENTRY_PATTERN_NAME,
            "timeframe_key": timeframe_key,
            "pair": pair,
        }

    brain_candles = _to_candles(candles)
    if len(brain_candles) < MIN_CANDLES:
        return {
            "action": "NO_TRADE",
            "reason": "Candle conversion produced too few valid bars",
            "engine": ENGINE_NAME,
            "entry_pattern": ENTRY_PATTERN_NAME,
            "timeframe_key": timeframe_key,
            "pair": pair,
        }

    # Build the data dict: main tf + whatever higher-tf we have
    data: Dict[str, List[_b.Candle]] = {tf: brain_candles}

    htf_raw = htf_candles or candles_5m
    if htf_raw and len(htf_raw) >= 10:
        htf_brain = _to_candles(htf_raw)
        if htf_brain:
            htf_tf = "5m" if tf == "1m" else ("1h" if tf == "5m" else ("1d" if tf == "1h" else tf))
            if htf_tf != tf:
                data[htf_tf] = htf_brain

    try:
        b = _b.Brain(data, equity=float(account_balance), risk_pct=risk_pct_pct)
        res = b.think(tf)
    except Exception as exc:
        return {
            "action": "NO_TRADE",
            "reason": f"Brain engine error: {exc}",
            "engine": ENGINE_NAME,
            "entry_pattern": ENTRY_PATTERN_NAME,
            "timeframe_key": timeframe_key,
            "pair": pair,
        }

    return _flatten(res, pair=pair, timeframe_key=timeframe_key,
                    risk_pct_pct=risk_pct_pct, equity=float(account_balance))


def enrich_signal(result: Dict[str, Any]) -> Dict[str, Any]:
    """Attach a 'brain' metadata block (consumed by system log / frontend)."""
    out = dict(result)
    out["brain"] = {
        "engine": ENGINE_NAME,
        "entry_pattern": ENTRY_PATTERN_NAME,
        "pipeline": ["market_structure", "patterns", "signals", "trap_policy", "ml_bias", "risk_plan"],
        "pattern_label": result.get("pattern"),
        "strategy": result.get("strategy"),
        "confidence": result.get("confidence"),
        "score": result.get("score"),
        "risk_reward": result.get("risk_reward"),
        "reasoning": result.get("reason"),
        "psychology": result.get("psychology"),
        "market_structure": result.get("market_structure"),
        "market_phase": result.get("market_phase"),
        "source": result.get("source"),
        "ml_bias": result.get("ml_bias"),
        "trap_type": result.get("trap_type"),
        "risk_plan": result.get("risk_plan"),
        "scalp": False,
    }
    return out


def entry_pattern_profile(timeframe_key: str | None = None) -> Dict[str, Any]:
    tf = _norm_tf(timeframe_key or "1h")
    tf_cfg = _b.TIMEFRAMES.get(tf, _b.TIMEFRAMES["1h"])
    return {
        "name": ENTRY_PATTERN_NAME,
        "engine": ENGINE_NAME,
        "description": (
            f"Candlestick Brain — pattern scan, market structure, trap & reverse (10th-man), "
            f"ML bias, and risk planning. Timeframe: {tf_cfg.label}. "
            f"Min confluence score: {tf_cfg.min_score}, min R:R: {tf_cfg.min_rr}. "
            f"{tf_cfg.note}"
        ),
        "timeframes": list(_b.TIMEFRAMES.keys()),
        "min_score": tf_cfg.min_score,
        "min_rr": tf_cfg.min_rr,
    }


def brain_chat_summary(result: Dict[str, Any]) -> str:
    action = result.get("action", "NO_TRADE")
    pattern = result.get("pattern") or result.get("strategy") or "—"
    reason = result.get("reason", "")
    source = result.get("source") or "signal"
    return f"Brain [{source}] {action}: {pattern} — {reason}"


def strategy_system_blurb() -> str:
    return (
        "CANDLESTICK BRAIN v2:\n"
        "1) Market structure → pattern scan → trap & reverse policy (10th-man) → ML bias → risk plan.\n"
        "2) Trap (smart money) overrides plain signal when fresh on the last bar.\n"
        "3) HOLD when no setup meets confluence score / R:R thresholds.\n"
        "4) All timeframes (1m–1d) unified through one engine. Exit: brain-driven SL/TP."
    )


def is_scalp_timeframe(timeframe_key: str | None) -> bool:
    """For routing compatibility — no longer splits to a different engine."""
    return False   # brain.py handles all TFs the same way


async def run_in_thread(candles, timeframe_key, **kw):
    """Async wrapper so brain (CPU-bound) does not block the event loop."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: evaluate_live_entry(candles, timeframe_key, **kw),
    )
