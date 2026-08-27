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
from trap_orderflow_engine import (
    evaluate_trap_orderflow,
    merge_with_structure_trap,
    thr_score_for_setup,
    thr_score_for_tf,
)

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
            htf_tf = {"1m": "5m", "5m": "15m", "15m": "1h", "1h": "1d"}.get(tf, tf)
            if htf_tf != tf:
                data[htf_tf] = htf_brain

    b = _b.Brain(data, equity=equity, risk_pct=risk_pct_pct)
    res = b.think(tf)       # dict with verdict, signal, trap, stance, ml, plan, …
    reasoning = b.reason(tf)  # natural-language chain-of-thought
    return {"think": res, "reasoning": reasoning}


# ─── AI confirm-only (YES/NO) — does not invent BUY/SELL ─────────────────────
_CONFIRM_SYSTEM = (
    "You are the confirmation layer for a crypto trading agent. "
    "A pattern/trap was already detected; LONG/SHORT direction is fixed — never invent BUY/SELL. "
    "Analyze under agent candle-read policy: market structure, LONG vs SHORT, "
    "classic traps, inverse/fake-breakout/absorption/exhaustion, order-flow scores, confluence, R:R. "
    "Only reply YES if judged confidence meets the TF floor in the brief "
    "(overall ≥75%; named traps ≥90%). Otherwise reply NO. "
    "Reply with exactly one word: YES or NO. No other text."
)


def _matching_side_score(of_trap: Optional[dict], action: str) -> float:
    of_trap = of_trap or {}
    if action == "BUY":
        return float(of_trap.get("long_score") or 0)
    if action == "SELL":
        return float(of_trap.get("short_score") or 0)
    return max(float(of_trap.get("long_score") or 0), float(of_trap.get("short_score") or 0))


def _ai_yes_thr_for_tf(timeframe_key: str, pattern: str | None = None) -> float:
    """AI YES floor: named traps ≥90, otherwise overall ≥75."""
    return float(thr_score_for_setup(timeframe_key, pattern))


def _setup_meets_ai_confirm_threshold(
    action: str,
    of_trap: Optional[dict],
    timeframe_key: str,
    think: dict,
) -> bool:
    """AI is called only when OF confidence clears the setup YES floor."""
    if action not in ("BUY", "SELL"):
        return False
    tf = _norm_tf(timeframe_key)
    pattern = (of_trap or {}).get("pattern")
    thr = _ai_yes_thr_for_tf(tf, pattern)
    of_score = _matching_side_score(of_trap, action)
    if of_score >= thr:
        return True
    # Strict score path on 1m/5m — never call AI below floor
    if tf in ("1m", "30s", "5m"):
        return False
    sig = think.get("signal")
    tf_cfg = _b.TIMEFRAMES.get(tf, _b.TIMEFRAMES["1h"])
    if sig is not None and (not of_trap or of_score <= 0):
        try:
            if float(getattr(sig, "score", 0) or 0) >= float(tf_cfg.min_score):
                return True
        except (TypeError, ValueError):
            pass
    return False


def _setup_label_and_score(think: dict, of_trap: Optional[dict], action: str) -> tuple:
    """Pattern name + trap/OF score for logs and prompts."""
    of_trap = of_trap or {}
    sig = think.get("signal")
    trap = think.get("trap")
    of_signal = of_trap.get("final_signal")
    pattern = None
    score = None
    if of_trap and of_signal in ("LONG", "SHORT"):
        pattern = of_trap.get("pattern") or "orderflow_trap"
        score = _matching_side_score(of_trap, action)
    elif trap is not None:
        pattern = getattr(trap, "trap_type", None) or "structure_trap"
        score = getattr(trap, "score", None)
    elif sig is not None:
        pattern = (sig.patterns[0] if sig.patterns else None) or sig.strategy
        score = getattr(sig, "score", None) or getattr(sig, "confidence", None)
    return (str(pattern or "setup"), score)


def _build_confirm_user_prompt(
    *,
    pair: str,
    timeframe: str,
    action: str,
    think: dict,
    of_trap: Optional[dict],
) -> str:
    """After pattern detect: policy analysis brief; answer YES/NO only at TF confidence floor."""
    side = "LONG" if action == "BUY" else "SHORT"
    tf = _norm_tf(timeframe)
    thr = _ai_yes_thr_for_tf(tf)
    tf_cfg = _b.TIMEFRAMES.get(tf, _b.TIMEFRAMES["1h"])
    pattern, trap_score = _setup_label_and_score(think, of_trap, action)
    thr = _ai_yes_thr_for_tf(tf, pattern)
    ms = think.get("structure")
    sig = think.get("signal")
    trap = think.get("trap")
    stance = think.get("stance")
    of_trap = of_trap or {}
    side_score = _matching_side_score(of_trap, action)

    lines = [
        f"PATTERN DETECTED → confirm {side} {pattern}.",
        f"Pair={pair} TF={timeframe} ({tf_cfg.label}).",
        f"HARD RULE: reply YES only if confidence ≥ {thr:.0f}% on this TF "
        f"(overall ≥75; named traps ≥90). Otherwise NO.",
        "",
        "ANALYZE (policy):",
        "- LONG vs SHORT quality vs market structure",
        "- Trap / inverse / fake-breakout / absorption / exhaustion validity",
        "- Order-flow side score vs TF floor",
        "- Confluence + R:R vs rulebook",
        "",
        "POLICY FLOORS:",
        f"- AI YES confidence floor this TF: {thr:.0f}%",
        f"- Brain confluence min: {tf_cfg.min_score} · min R:R: {tf_cfg.min_rr}",
        f"- Direction already set as {side}; you only YES/NO — do not invent BUY/SELL",
        f"- Note: {tf_cfg.note}",
        "",
        "SETUP FACTS:",
        f"- Detected side: {side} · pattern={pattern} · side_score={side_score:.1f} (need ≥{thr:.0f})",
    ]
    if trap_score is not None and trap_score != side_score:
        lines.append(f"- Trap/pattern score field: {trap_score}")
    if ms is not None:
        lines.append(
            f"- Structure: {getattr(ms, 'trend', '?')} strength={getattr(ms, 'trend_strength', '?')}"
        )
    if sig is not None:
        pats = ", ".join(sig.patterns[:4]) if getattr(sig, "patterns", None) else sig.strategy
        lines.append(
            f"- Pattern signal: {pats} score={getattr(sig, 'score', '?')} "
            f"conf={getattr(sig, 'confidence', '?')} R:R={getattr(sig, 'rr', '?')}"
        )
        if getattr(sig, "entry", None) is not None:
            lines.append(
                f"- Levels: entry={sig.entry} stop={getattr(sig, 'stop', '?')} "
                f"target={getattr(sig, 'target', '?')}"
            )
    if trap is not None:
        lines.append(
            f"- Structure trap: {getattr(trap, 'trap_type', '?')} "
            f"smart={getattr(trap, 'smart_action', '?')} side={getattr(trap, 'side', '?')} "
            f"(inverse/trap read if applicable)"
        )
    if of_trap:
        lines.append(
            f"- Order-flow: {of_trap.get('final_signal') or of_trap.get('line')} "
            f"pattern={of_trap.get('pattern')} bias_5m={of_trap.get('bias_5m')} "
            f"LONG={of_trap.get('long_score')} SHORT={of_trap.get('short_score')}"
        )
        if of_trap.get("primary_reason"):
            lines.append(f"- OF reason: {str(of_trap.get('primary_reason'))[:200]}")
    if stance is not None:
        lines.append(
            f"- Smart stance: {getattr(stance, 'action', '?')} "
            f"source={getattr(stance, 'source', '?')}"
        )
    brain_v = think.get("verdict")
    if brain_v:
        lines.append(f"- Brain verdict: {brain_v}")
    lines.append("")
    lines.append(
        f"If side_score {side_score:.1f} < {thr:.0f} → you MUST answer NO. "
        f"Output exactly: YES or NO"
    )
    return "\n".join(lines)[:4000]



async def _confirm_setup_with_ai(
    settings,
    *,
    pair: str,
    timeframe: str,
    action: str,
    think: dict,
    of_trap: Optional[dict],
) -> Optional[bool]:
    """Ask AI to confirm an existing BUY/SELL setup (policy-aware brief).

    Returns:
      True  — YES
      False — NO (skip trade)
      None  — unreachable / not configured / cool-down (fail-open → fire)
    """
    provider = getattr(settings, "ai_provider", "none")
    api_key = getattr(settings, "ai_api_key", "") or ""
    if provider == "none" or not api_key:
        return None

    try:
        from main import agent as _agent
        if not _agent.ai_consult_allowed():
            print("[AI-CONFIRM] Cool-down active — fail-open (no AI block).")
            return None
    except Exception:
        pass

    messages = [
        {"role": "system", "content": _CONFIRM_SYSTEM},
        {
            "role": "user",
            "content": _build_confirm_user_prompt(
                pair=pair,
                timeframe=timeframe,
                action=action,
                think=think,
                of_trap=of_trap,
            ),
        },
    ]

    _DEFAULTS = {
        "z-ai": {"base_url": "https://api.z.ai/api/paas/v4", "model": "glm-4.5-flash", "auth": "bearer"},
        "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini", "auth": "bearer"},
        "zhipu-glm": {"base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4.5-flash", "auth": "bearer"},
        "azure-openai": {"base_url": None, "model": "gpt-4o-mini", "auth": "api-key"},
        "custom": {"base_url": None, "model": "glm-4.5-flash", "auth": "bearer"},
    }
    cfg = _DEFAULTS.get(provider, _DEFAULTS["custom"])
    base_url = (getattr(settings, "ai_base_url", None) or cfg["base_url"] or "").rstrip("/")
    if not base_url:
        print(f"[AI-CONFIRM] No base_url for '{provider}' — fail-open.")
        return None
    model = getattr(settings, "ai_model", None) or cfg["model"]

    headers = {"Content-Type": "application/json"}
    if cfg["auth"] == "api-key":
        headers["api-key"] = api_key
    else:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": 4,
                    "temperature": 0,
                },
            )
        if resp.status_code != 200:
            print(f"[AI-CONFIRM] '{provider}' HTTP {resp.status_code} — fail-open.")
            _notify_ai_health(False)
            return None
        raw = resp.json()["choices"][0]["message"]["content"].strip().upper()
        _notify_ai_health(True)
        token = raw.replace(".", " ").replace(",", " ").split()[0] if raw else ""
        if token.startswith("NO"):
            print(f"[AI-CONFIRM] '{provider}' → NO  (raw: {raw!r}) — skip trade.")
            return False
        if token.startswith("YES"):
            thr = _ai_yes_thr_for_tf(timeframe, (of_trap or {}).get("pattern"))
            side_score = _matching_side_score(of_trap, action)
            if side_score < thr:
                print(
                    f"[AI-CONFIRM] '{provider}' YES ignored — "
                    f"side_score {side_score:.1f} < floor {thr:.0f}"
                )
                return False
            print(f"[AI-CONFIRM] '{provider}' → YES  (raw: {raw!r}, score={side_score:.1f}≥{thr:.0f})")
            return True
        print(f"[AI-CONFIRM] '{provider}' unclear {raw!r} — fail-open.")
        return None
    except Exception as exc:
        print(f"[AI-CONFIRM] API error ({exc}) — fail-open.")
        _notify_ai_health(False)
        return None


def _notify_ai_health(ok: bool) -> None:
    try:
        from main import agent as _agent
        _agent.note_ai_result(ok)
    except Exception:
        pass


# ─── flatten brain result → backend dict ─────────────────────────────────────
def _flatten(think: dict, *, ai_action: str, pair: str, timeframe_key: str,
             risk_pct_pct: float, equity: float,
             of_trap: Optional[dict] = None) -> Dict[str, Any]:
    sig: Optional[_b.Signal] = think.get("signal")
    trap: Optional[_b.TrapSignal] = think.get("trap")
    stance: Optional[_b.SmartStance] = think.get("stance")
    ms: Optional[_b.MarketStructure] = think.get("structure")
    ml = think.get("ml") or {}

    # Entry / SL / TP — prefer trap stance, else pattern signal (brain/OF setup)
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

    # Pattern / confluence labels — order-flow trap can label when it drove decision
    of_signal = (of_trap or {}).get("final_signal")
    if of_trap and of_signal in ("LONG", "SHORT") and (
        (ai_action == "BUY" and of_signal == "LONG") or (ai_action == "SELL" and of_signal == "SHORT")
    ):
        pattern_name = of_trap.get("pattern") or "orderflow_trap"
        strategy_name = "trap_orderflow"
        confluences = [
            of_trap.get("primary_reason") or "",
            f"5M bias={of_trap.get('bias_5m')}",
            f"LONG_SCORE={of_trap.get('long_score')} SHORT_SCORE={of_trap.get('short_score')}",
        ]
        if trap is not None:
            confluences.append(f"structure_trap={trap.trap_type}")
    elif stance and stance.source == "trap" and trap is not None:
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
    if of_trap and of_trap.get("primary_reason"):
        detail = (detail + " | " if detail else "") + f"OF: {of_trap.get('primary_reason')}"
    reason_parts = [f"Brain/OF setup: {ai_action}", detail]
    if confluences:
        reason_parts.append("confluence: " + "; ".join(str(c) for c in confluences[:4] if c))
    reason = " | ".join(p for p in reason_parts if p)

    action = "BUY" if ai_action == "BUY" else "SELL" if ai_action == "SELL" else "NO_TRADE"

    ml_label = ml.get("prediction", {}).get("label") if isinstance(ml, dict) else None
    conf = float(sig.confidence) if sig else (0.6 if action != "NO_TRADE" else 0.0)
    if of_trap and of_trap.get("confidence") is not None and action != "NO_TRADE":
        conf = max(conf, float(of_trap.get("confidence") or 0))

    return {
        "action": action,
        "reason": reason or "No qualifying setup",
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
        "confidence": conf,
        "score": float(sig.score) if sig else (float(trap.score) if trap else float((of_trap or {}).get("long_score") or (of_trap or {}).get("short_score") or 0)),
        "confluences": confluences,
        "psychology": pattern_name,
        "market_structure": ms.trend if ms else None,
        "market_phase": ms.trend_strength if ms else None,
        "timeframe_key": timeframe_key,
        "pair": pair,
        "direction": "LONG" if action == "BUY" else ("SHORT" if action == "SELL" else None),
        "source": "trap_orderflow" if strategy_name == "trap_orderflow" else (stance.source if stance else None),
        "ml_bias": ml_label,
        "trap_type": trap.trap_type if trap else (of_trap.get("pattern") if of_trap else None),
        "orderflow_trap": of_trap,
        "ai_driven": False,
        "ai_confirmation": "SKIP",
        "brain_verdict": think.get("verdict"),
    }


def _resolve_1m_5m(
    candles: List[dict],
    timeframe_key: str,
    htf_candles: Optional[List[dict]],
    candles_1m: Optional[List[dict]],
    candles_5m: Optional[List[dict]],
) -> tuple:
    """Build LTF/HTF pair for order-flow the same way as the 1m system.

    1m chart  → exec=1m series, bias=5m (htf)
    5m+ chart → exec=chart series, bias=mapped HTF (or 5m feed when provided)
    Never loosen rules on higher TFs — only the candle series changes.
    """
    tf = _norm_tf(timeframe_key)
    c1 = candles_1m
    c5 = candles_5m
    if tf == "1m":
        c1 = c1 or candles
        c5 = c5 or htf_candles
    elif tf == "5m":
        c1 = c1 or candles  # 5m bars as execution series (same role as 1m on 1m chart)
        c5 = c5 or htf_candles or candles
    else:
        # 15m / 1h / 1d: chart series = execution; HTF series = bias context
        c1 = c1 or candles
        c5 = c5 or htf_candles or candles
    return c1, c5


def _run_orderflow_trap(
    candles: List[dict],
    timeframe_key: str,
    think: dict,
    *,
    htf_candles: Optional[List[dict]] = None,
    candles_1m: Optional[List[dict]] = None,
    candles_5m: Optional[List[dict]] = None,
) -> Optional[dict]:
    try:
        c1, c5 = _resolve_1m_5m(candles, timeframe_key, htf_candles, candles_1m, candles_5m)
        of = evaluate_trap_orderflow(c1, c5, exec_tf=_norm_tf(timeframe_key))
        struct = think.get("trap")
        struct_side = getattr(struct, "side", None) if struct else None
        struct_type = getattr(struct, "trap_type", None) if struct else None
        merged = merge_with_structure_trap(of, struct_side, struct_type)
        return merged.to_dict()
    except Exception as exc:
        print(f"[TRAP-OF] evaluate error: {exc}")
        return None


def _gate_1m_of_score(action: str, of_trap: Optional[dict], timeframe_key: str) -> str:
    """1m only: BUY/SELL only when matching OF side score ≥ setup floor (trap≥90 else≥75)."""
    if _norm_tf(timeframe_key) != "1m":
        return action
    if action not in ("BUY", "SELL"):
        return action
    pattern = (of_trap or {}).get("pattern")
    thr = thr_score_for_setup("1m", pattern)
    if not of_trap:
        print(f"[AI-BRAIN] 1m gate: no OF result — HOLD (need score ≥ {thr:.0f})")
        return "HOLD"
    long_s = float(of_trap.get("long_score") or 0)
    short_s = float(of_trap.get("short_score") or 0)
    if action == "BUY":
        if long_s < thr:
            print(f"[AI-BRAIN] 1m gate: BUY blocked LONG={long_s:.0f} < {thr:.0f}")
            return "HOLD"
        return "BUY"
    if short_s < thr:
        print(f"[AI-BRAIN] 1m gate: SELL blocked SHORT={short_s:.0f} < {thr:.0f}")
        return "HOLD"
    return "SELL"


def _fallback_action_from_brain_and_of(
    think: dict, of_trap: Optional[dict], timeframe_key: str = "1m"
) -> str:
    """When AI unavailable: prefer strong order-flow trap, else brain verdict."""
    tf = _norm_tf(timeframe_key)
    pattern = (of_trap or {}).get("pattern") if of_trap else None
    thr = thr_score_for_setup(tf, pattern)
    if of_trap:
        sig = of_trap.get("final_signal")
        long_s = float(of_trap.get("long_score") or 0)
        short_s = float(of_trap.get("short_score") or 0)
        if sig == "LONG" and long_s >= thr:
            return "BUY"
        if sig == "SHORT" and short_s >= thr:
            return "SELL"
        if tf == "1m":
            # 1m: never fall through to brain pattern if OF score is below floor
            return "HOLD"
        if sig == "NO_TRADE":
            pass
    if tf == "1m":
        return "HOLD"
    brain_verdict = think.get("verdict", "HOLD")
    return brain_verdict if brain_verdict in ("BUY", "SELL") else "HOLD"


# ─── public API ───────────────────────────────────────────────────────────────
MIN_CANDLES = 30


async def evaluate_live_entry_async(
    candles: List[dict],
    timeframe_key: str,
    *,
    pair: str = "default",
    htf_candles: Optional[List[dict]] = None,
    candles_1m: Optional[List[dict]] = None,
    candles_5m: Optional[List[dict]] = None,
    account_balance: float = 10000.0,
    risk_pct: float = 0.01,
    settings=None,           # settings_store from main.py
) -> Dict[str, Any]:
    """Async entry: brain + order-flow set BUY/SELL; AI only confirms YES/NO.

    AI NO → NO_TRADE. AI YES or unreachable → fire (fail-open).
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
            "ai_confirmation": "SKIP",
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
            "ai_confirmation": "SKIP",
        }

    think = analysis["think"]

    of_trap = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: _run_orderflow_trap(
            candles, timeframe_key, think,
            htf_candles=htf_candles,
            candles_1m=candles_1m,
            candles_5m=candles_5m,
        ),
    )

    # Direction comes only from brain + order-flow (never invented by AI).
    setup_action = _fallback_action_from_brain_and_of(think, of_trap, timeframe_key)
    setup_action = _gate_1m_of_score(setup_action or "HOLD", of_trap, timeframe_key)

    ai_confirmation = "SKIP"
    if setup_action in ("BUY", "SELL") and settings is not None:
        # AI only on candles that already clear agent confidence / OF TF floor.
        if not _setup_meets_ai_confirm_threshold(setup_action, of_trap, timeframe_key, think):
            thr = thr_score_for_setup(tf, (of_trap or {}).get("pattern"))
            side_sc = _matching_side_score(of_trap, setup_action)
            print(
                f"[AI-CONFIRM] Skip call {pair} {timeframe_key}: "
                f"score {side_sc:.1f} < floor {thr:.0f} — no AI, HOLD"
            )
            setup_action = "HOLD"
        else:
            confirmed = await _confirm_setup_with_ai(
                settings,
                pair=pair,
                timeframe=timeframe_key,
                action=setup_action,
                think=think,
                of_trap=of_trap,
            )
            if confirmed is False:
                side = "LONG" if setup_action == "BUY" else "SHORT"
                pattern, score = _setup_label_and_score(think, of_trap, setup_action)
                rejected = _flatten(
                    think,
                    ai_action="HOLD",
                    pair=pair,
                    timeframe_key=timeframe_key,
                    risk_pct_pct=risk_pct_pct,
                    equity=float(account_balance),
                    of_trap=of_trap,
                )
                rejected["action"] = "NO_TRADE"
                rejected["ai_confirmation"] = "NO"
                rejected["ai_driven"] = False
                rejected["reason"] = (
                    f"AI rejected confirm ({side} {pattern} / trap score {score}) | "
                    f"{rejected.get('reason') or ''}"
                ).strip(" |")
                return rejected
            if confirmed is True:
                ai_confirmation = "YES"
            else:
                ai_confirmation = "SKIP"  # unreachable → fail-open

    out = _flatten(
        think,
        ai_action=setup_action,
        pair=pair,
        timeframe_key=timeframe_key,
        risk_pct_pct=risk_pct_pct,
        equity=float(account_balance),
        of_trap=of_trap,
    )
    out["ai_confirmation"] = ai_confirmation
    out["ai_driven"] = False
    if ai_confirmation != "SKIP" and out.get("action") in ("BUY", "SELL"):
        out["reason"] = f"{out.get('reason', '')} | AI={ai_confirmation}".strip(" |")
    elif ai_confirmation == "SKIP" and out.get("action") in ("BUY", "SELL"):
        out["reason"] = f"{out.get('reason', '')} | AI=SKIP (fail-open)".strip(" |")
    return out


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
    of_trap = _run_orderflow_trap(
        candles, timeframe_key, think,
        htf_candles=htf_raw,
        candles_1m=candles_1m,
        candles_5m=candles_5m or (candles if tf == "5m" else None),
    )
    ai_action = _fallback_action_from_brain_and_of(think, of_trap, timeframe_key)
    ai_action = _gate_1m_of_score(ai_action or "HOLD", of_trap, timeframe_key)
    out = _flatten(
        think,
        ai_action=ai_action,
        pair=pair,
        timeframe_key=timeframe_key,
        risk_pct_pct=risk_pct_pct,
        equity=float(account_balance),
        of_trap=of_trap,
    )
    out["ai_driven"] = False
    out["ai_confirmation"] = "SKIP"
    return out


def enrich_signal(result: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(result)
    out["brain"] = {
        "engine": ENGINE_NAME,
        "entry_pattern": ENTRY_PATTERN_NAME,
        "pipeline": ["brain_analysis", "orderflow_trap", "ai_yes_no_confirm", "risk_plan"],
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
        "orderflow_trap": result.get("orderflow_trap"),
        "brain_verdict": result.get("brain_verdict"),
        "ai_driven": False,
        "ai_confirmation": result.get("ai_confirmation", "SKIP"),
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
            "Unified 1m rulebook on every chart TF (1m→1D): brain.py patterns/structure/traps + ML; "
            "order-flow trap engine sets BUY/SELL only when OF/confidence clears TF floor; "
            "AI gets a compact policy+setup brief and answers YES/NO only "
            "(NO=skip, unreachable=fail-open); next-candle fire; path SL/TP 0.5/0.7; "
            "flip-exit on opposite signal. "
            f"Active label: {tf_cfg.label}. Min confluence: {tf_cfg.min_score}, min R:R: {tf_cfg.min_rr}. "
            f"Order-flow conf floor: overall ≥75% / named traps ≥90% "
            f"(AI YES only at/above setup floor). "
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
    of_line = (result.get("orderflow_trap") or {}).get("line")
    tag = "AI" if ai_driven else "Brain"
    base = f"{tag} [{result.get('source') or 'signal'}] {action}: {pattern} — {reason}"
    if of_line:
        return f"{base} || OF: {of_line}"
    return base


def strategy_system_blurb() -> str:
    return (
        "AI-DRIVEN CANDLESTICK BRAIN + ORDER-FLOW TRAP ENGINE (unified 1m rulebook):\n"
        "Same entry/exit training on every chart TF from 1m through 1D.\n"
        "1) brain.py: patterns, market structure, classic trap & reverse (10th-man), ML bias "
        "(min confluence score 6, min R:R 2, HTF alignment + noise guard — identical on all TFs).\n"
        "2) Order-flow TRAP DETECTION ENGINE: buy/sell trap, absorption, exhaustion,\n"
        "   fake breakout, reversal trap (effort vs result; volume & buyer/seller pressure).\n"
        "3) Combined analysis → AI API (GLM/OpenAI) → BUY / SELL / HOLD.\n"
        "4) Next-candle fire + path SL/TP 0.5%/0.7% + opposite-side flip-exit.\n"
        "5) If AI offline: strong OF setup (overall≥75 / trap≥90) or brain.py verdict as fallback.\n"
    )


def is_scalp_timeframe(timeframe_key: str | None) -> bool:
    return False


async def run_in_thread(candles, timeframe_key, **kw):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: evaluate_live_entry(candles, timeframe_key, **kw),
    )
