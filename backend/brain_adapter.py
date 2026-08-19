"""Brain adapter — AI API is the driver, brain.py is the analyst.

Flow per candle scan:
  1. brain.py analyses the candle series fully (patterns, structure, traps, ML).
  2. brain.py's chain-of-thought reasoning is sent to the configured AI API as a
     system prompt + user question.
  3. The AI model returns BUY / SELL / HOLD — that is the final trade decision.
  4. If AI is unavailable / misconfigured, brain.py's own verdict is used as
     a safe fallback so the bot never stops working.

brain.py is never modified.  All glue lives here.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Dict, List, Optional, Sequence

import httpx

import brain as _b

ENGINE_NAME = "ai_driven_brain_v2"
ENTRY_PATTERN_NAME = "AI_BRAIN_V2"

# ─── timeframe normalisation ──────────────────────────────────────────────────
_TF_NORM: Dict[str, str] = {
    "1M": "1m", "5M": "5m", "15M": "15m", "1H": "1h", "1D": "1d",
    "30s": "1m", "30S": "1m",
    "1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "1d": "1d",
}

def _norm_tf(key: str) -> str:
    return _TF_NORM.get((key or "1h").strip(), "1h")


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


# ─── brain.py analysis → structured dict ─────────────────────────────────────
def _run_brain(candles: List[dict], tf: str,
               htf_candles: Optional[List[dict]],
               equity: float, risk_pct_pct: float) -> dict:
    """Run brain.py and return a structured analysis dict."""
    brain_candles = _to_candles(candles)
    data: Dict[str, List[_b.Candle]] = {tf: brain_candles}
    if htf_candles and len(htf_candles) >= 10:
        htf_brain = _to_candles(htf_candles)
        if htf_brain:
            htf_tf = {"1m": "5m", "5m": "1h", "15m": "1h", "1h": "1d"}.get(tf, tf)
            if htf_tf != tf:
                data[htf_tf] = htf_brain

    b = _b.Brain(data, equity=equity, risk_pct=risk_pct_pct)
    res = b.think(tf)       # dict with verdict, signal, trap, stance, ml, plan, …
    reasoning = b.reason(tf)  # natural-language chain-of-thought
    return {"think": res, "reasoning": reasoning}


# ─── AI system prompt builder ─────────────────────────────────────────────────
_SYSTEM_PROMPT = """\
You are an expert algorithmic cryptocurrency trader.
You will receive a full technical analysis of a crypto chart from the Candlestick Brain engine.
The analysis includes market structure, candlestick patterns, trap & reverse signals, an ML
price-direction bias, and a risk plan.

Your job: read the analysis carefully, then output EXACTLY ONE of these three words on its own line:
  BUY
  SELL
  HOLD

Rules:
- BUY if the combined evidence strongly favours a LONG trade.
- SELL if the combined evidence strongly favours a SHORT trade.
- HOLD if the evidence is mixed, unclear, or insufficient.
- Output only the word. No explanation, no punctuation, no extra lines.
"""

def _build_ai_messages(pair: str, timeframe: str, reasoning: str, think: dict) -> List[dict]:
    ms = think.get("structure")
    sig = think.get("signal")
    trap = think.get("trap")
    stance = think.get("stance")
    ml = think.get("ml") or {}
    plan = think.get("plan")
    verdict = think.get("verdict", "HOLD")

    # Compact fact summary (in case reasoning is very long)
    facts = [
        f"Pair: {pair}  |  Timeframe: {timeframe}",
        f"Market structure: {ms.trend if ms else 'unknown'}  (strength: {ms.trend_strength if ms else '-'})",
    ]
    if sig:
        facts.append(f"Pattern signal: {sig.side} via {sig.strategy}  |  patterns: {', '.join(sig.patterns)}")
        facts.append(f"Entry {sig.entry:.4g}  stop {sig.stop:.4g}  target {sig.target:.4g}  R:R 1:{sig.rr:.2f}  score {sig.score:.1f}/12")
        facts.append(f"Confluence: {', '.join(sig.confluence or [])}")
    if trap:
        facts.append(f"Trap: {trap.trap_type}  Smart-money action: {trap.smart_action}  ({trap.side})")
    if stance:
        facts.append(f"Smart stance: {stance.action}  source={stance.source}  {stance.narrative}")
    if ml.get("metrics"):
        facts.append(f"ML bias: {ml.get('prediction', {}).get('label')}  P(up)={ml.get('prediction', {}).get('probability_up')}")
    if plan:
        facts.append(f"Risk plan: risk {plan.risk_pct:.1f}%  units {plan.units:.4f}  reward/risk R:{plan.rr:.2f}")
    facts.append(f"Brain own verdict: {verdict}")

    user_content = (
        "=== FULL CHAIN-OF-THOUGHT ANALYSIS ===\n"
        + reasoning
        + "\n\n=== KEY FACTS SUMMARY ===\n"
        + "\n".join(facts)
        + "\n\n=== YOUR DECISION ===\n"
        "Based on the above, output exactly one word: BUY, SELL, or HOLD."
    )

    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content[:18000]},  # token guard
    ]


# ─── AI API call ──────────────────────────────────────────────────────────────
async def _call_ai_api(messages: List[dict], settings) -> Optional[str]:
    """
    Call the configured AI provider.  Returns 'BUY', 'SELL', 'HOLD', or None.
    None means AI unavailable — callers should use brain.py fallback.
    """
    provider = getattr(settings, "ai_provider", "none")
    api_key = getattr(settings, "ai_api_key", "") or ""
    if provider == "none" or not api_key:
        return None

    _DEFAULTS = {
        "z-ai":      {"base_url": "https://api.z.ai/api/paas/v4",    "model": "glm-4.5-flash", "auth": "bearer"},
        "openai":    {"base_url": "https://api.openai.com/v1",        "model": "gpt-4o-mini",   "auth": "bearer"},
        "zhipu-glm": {"base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4.5-flash", "auth": "bearer"},
        "azure-openai": {"base_url": None, "model": "gpt-4o-mini",   "auth": "api-key"},
        "custom":    {"base_url": None,   "model": "glm-4.5-flash",  "auth": "bearer"},
    }
    cfg = _DEFAULTS.get(provider, _DEFAULTS["custom"])
    base_url = (getattr(settings, "ai_base_url", None) or cfg["base_url"] or "").rstrip("/")
    if not base_url:
        print(f"[AI-BRAIN] No base_url for provider '{provider}' — using brain.py fallback.")
        return None
    model = getattr(settings, "ai_model", None) or cfg["model"]

    headers = {"Content-Type": "application/json"}
    if cfg["auth"] == "api-key":
        headers["api-key"] = api_key
    else:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": 8,
                    "temperature": 0,
                },
            )
        if resp.status_code != 200:
            print(f"[AI-BRAIN] Provider '{provider}' HTTP {resp.status_code} — brain.py fallback.")
            return None
        raw = resp.json()["choices"][0]["message"]["content"].strip().upper()
        # Extract the decision word robustly
        for word in ("BUY", "SELL", "HOLD"):
            if word in raw:
                print(f"[AI-BRAIN] '{provider}' → {word}  (raw: {raw!r})")
                return word
        print(f"[AI-BRAIN] '{provider}' unexpected reply {raw!r} — brain.py fallback.")
        return None
    except Exception as exc:
        print(f"[AI-BRAIN] API error ({exc}) — brain.py fallback.")
        return None


# ─── flatten brain result → backend dict ─────────────────────────────────────
def _flatten(think: dict, *, ai_action: str, pair: str, timeframe_key: str,
             risk_pct_pct: float, equity: float) -> Dict[str, Any]:
    sig: Optional[_b.Signal] = think.get("signal")
    trap: Optional[_b.TrapSignal] = think.get("trap")
    stance: Optional[_b.SmartStance] = think.get("stance")
    ms: Optional[_b.MarketStructure] = think.get("structure")
    ml = think.get("ml") or {}

    # Entry / SL / TP — prefer whichever source drove the AI's decision
    entry_src = None
    if stance and stance.source == "trap" and trap is not None:
        entry_src = trap
    elif sig is not None:
        entry_src = sig

    entry_price = float(entry_src.entry) if entry_src else None
    sl = float(entry_src.stop) if entry_src else None
    tp = float(entry_src.target) if entry_src else None
    rr = float(getattr(entry_src, "rr", 0) or 0) if entry_src else None

    # Risk plan
    plan: Optional[_b.TradePlan] = think.get("plan")
    if plan is None and entry_src is not None and ai_action != "HOLD":
        try:
            plan = _b.plan_trade(equity, risk_pct_pct, ai_action,
                                 entry_src.entry, entry_src.stop, entry_src.target)
        except Exception:
            plan = None

    # Pattern / confluence labels
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

    detail = think.get("verdict_detail", "")
    if stance and stance.narrative:
        detail = stance.narrative
    reason_parts = [f"AI decision: {ai_action}", detail]
    if confluences:
        reason_parts.append("confluence: " + "; ".join(confluences[:4]))
    reason = " | ".join(p for p in reason_parts if p)

    action = "BUY" if ai_action == "BUY" else "SELL" if ai_action == "SELL" else "NO_TRADE"

    ml_label = ml.get("prediction", {}).get("label") if isinstance(ml, dict) else None

    return {
        "action": action,
        "reason": reason or "AI: no qualifying setup",
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
        "confidence": float(sig.confidence) if sig else (0.6 if action != "NO_TRADE" else 0.0),
        "score": float(sig.score) if sig else (float(trap.score) if trap else 0.0),
        "confluences": confluences,
        "psychology": pattern_name,
        "market_structure": ms.trend if ms else None,
        "market_phase": ms.trend_strength if ms else None,
        "timeframe_key": timeframe_key,
        "pair": pair,
        "direction": "LONG" if action == "BUY" else ("SHORT" if action == "SELL" else None),
        "source": stance.source if stance else None,
        "ml_bias": ml_label,
        "trap_type": trap.trap_type if trap else None,
        "brain_verdict": think.get("verdict"),
        "ai_driven": True,
        "n_candles": think.get("n", 0),
        "last_close": think.get("last_close"),
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
MIN_CANDLES = 30


async def evaluate_live_entry_async(
    candles: List[dict],
    timeframe_key: str,
    *,
    pair: str = "default",
    htf_candles: Optional[List[dict]] = None,
    account_balance: float = 10000.0,
    risk_pct: float = 0.01,
    settings=None,           # settings_store from main.py
) -> Dict[str, Any]:
    """Async entry point: brain.py analysis → AI API decision.

    Used from async context (scan_and_maybe_fire_pair).
    """
    tf = _norm_tf(timeframe_key)
    risk_pct_pct = float(risk_pct) * 100.0

    if len(candles) < MIN_CANDLES:
        return {
            "action": "NO_TRADE",
            "reason": f"Need {MIN_CANDLES}+ closed candles (have {len(candles)})",
            "engine": ENGINE_NAME,
            "entry_pattern": ENTRY_PATTERN_NAME,
            "timeframe_key": timeframe_key,
            "pair": pair,
            "ai_driven": False,
        }

    # Run brain.py in a thread (CPU-bound)
    try:
        analysis = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: _run_brain(candles, tf, htf_candles, float(account_balance), risk_pct_pct)
        )
    except Exception as exc:
        return {
            "action": "NO_TRADE",
            "reason": f"Brain analysis error: {exc}",
            "engine": ENGINE_NAME,
            "entry_pattern": ENTRY_PATTERN_NAME,
            "timeframe_key": timeframe_key,
            "pair": pair,
            "ai_driven": False,
        }

    think = analysis["think"]
    reasoning = analysis["reasoning"]

    # Ask AI API for final BUY/SELL/HOLD decision
    ai_action: Optional[str] = None
    if settings is not None:
        messages = _build_ai_messages(pair, timeframe_key, reasoning, think)
        ai_action = await _call_ai_api(messages, settings)

    # Fallback to brain.py's own verdict if AI unavailable
    if ai_action is None:
        brain_verdict = think.get("verdict", "HOLD")
        ai_action = brain_verdict if brain_verdict in ("BUY", "SELL") else "HOLD"
        print(f"[AI-BRAIN] Using brain.py fallback verdict: {ai_action}")

    return _flatten(think, ai_action=ai_action, pair=pair, timeframe_key=timeframe_key,
                    risk_pct_pct=risk_pct_pct, equity=float(account_balance))


def evaluate_live_entry(
    candles: List[dict],
    timeframe_key: str,
    *,
    pair: str = "default",
    htf_candles: Optional[List[dict]] = None,
    candles_1m: Optional[List[dict]] = None,
    candles_5m: Optional[List[dict]] = None,
    account_balance: float = 10000.0,
    risk_pct: float = 0.01,
    settings=None,
) -> Dict[str, Any]:
    """Synchronous wrapper (used from thread executor in main.py evaluate_entry).

    Note: AI API call is skipped here; use evaluate_live_entry_async for full
    AI-driven path from async scan_and_maybe_fire_pair.
    """
    tf = _norm_tf(timeframe_key)
    risk_pct_pct = float(risk_pct) * 100.0

    htf_raw = htf_candles or candles_5m
    if len(candles) < MIN_CANDLES:
        return {
            "action": "NO_TRADE",
            "reason": f"Need {MIN_CANDLES}+ closed candles (have {len(candles)})",
            "engine": ENGINE_NAME,
            "entry_pattern": ENTRY_PATTERN_NAME,
            "timeframe_key": timeframe_key,
            "pair": pair,
            "ai_driven": False,
        }
    try:
        analysis = _run_brain(candles, tf, htf_raw, float(account_balance), risk_pct_pct)
    except Exception as exc:
        return {
            "action": "NO_TRADE",
            "reason": f"Brain error: {exc}",
            "engine": ENGINE_NAME,
            "entry_pattern": ENTRY_PATTERN_NAME,
            "timeframe_key": timeframe_key,
            "pair": pair,
            "ai_driven": False,
        }

    think = analysis["think"]
    brain_verdict = think.get("verdict", "HOLD")
    ai_action = brain_verdict if brain_verdict in ("BUY", "SELL") else "HOLD"
    return _flatten(think, ai_action=ai_action, pair=pair, timeframe_key=timeframe_key,
                    risk_pct_pct=risk_pct_pct, equity=float(account_balance))


def enrich_signal(result: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(result)
    out["brain"] = {
        "engine": ENGINE_NAME,
        "entry_pattern": ENTRY_PATTERN_NAME,
        "pipeline": ["brain_analysis", "ai_api_decision", "risk_plan"],
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
        "brain_verdict": result.get("brain_verdict"),
        "ai_driven": result.get("ai_driven", True),
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
            "AI-Driven Brain: brain.py analyses patterns, structure, traps, and ML; "
            "the AI API model reads the full chain-of-thought and makes the final BUY/SELL/HOLD decision. "
            f"Timeframe: {tf_cfg.label}. Min confluence score: {tf_cfg.min_score}, min R:R: {tf_cfg.min_rr}. "
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
    ai_driven = result.get("ai_driven", False)
    tag = "AI" if ai_driven else "Brain"
    return f"{tag} [{result.get('source') or 'signal'}] {action}: {pattern} — {reason}"


def strategy_system_blurb() -> str:
    return (
        "AI-DRIVEN CANDLESTICK BRAIN:\n"
        "1) brain.py runs full analysis: patterns, market structure, trap & reverse (10th-man), ML bias.\n"
        "2) The full chain-of-thought is sent to the AI API (GLM/OpenAI).\n"
        "3) AI API returns BUY / SELL / HOLD — that is the final trade decision.\n"
        "4) If AI API is offline, brain.py's own verdict is used as fallback.\n"
        "5) Brain-driven SL/TP from signal entry/stop/target. Exit: ±fixed% as safety net."
    )


def is_scalp_timeframe(timeframe_key: str | None) -> bool:
    return False


async def run_in_thread(candles, timeframe_key, **kw):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: evaluate_live_entry(candles, timeframe_key, **kw),
    )
