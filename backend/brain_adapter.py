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
import os
import re
import time
from typing import Any, Dict, List, Optional, Sequence

import httpx

import brain as _b
import family_rules
from trap_orderflow_engine import (
    evaluate_trap_orderflow,
    merge_with_structure_trap,
    score_below_floor,
    thr_score_for_setup,
    thr_score_for_tf,
)

# One confirm at a time. Free Z.ai flash 429s or times out if every pair calls at once.
_AI_CONFIRM_LOCK = asyncio.Lock()
_AI_CONFIRM_NEXT = 0.0
_AI_CONFIRM_GAP = 20.0

ENGINE_NAME = "ai_driven_brain_v2"
ENTRY_PATTERN_NAME = "AI_BRAIN_V2"
# Detect-fire only on a last-bar brain signal. Raw candle matches do not fire.
DETECT_FIRE_MIN_SCORE = 5.0

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
    "UNLIMITED mode: you may use tools, read this project, and outside research to "
    "maximize expected profit / minimize loss / avoid late entries before deciding. "
    "Only reply YES if judged confidence meets the TF floor in the brief "
    "(overall ≥75%; 5m named traps ≥80%; other named traps ≥90%). Otherwise reply NO. "
    "Final answer line must be exactly one word: YES or NO."
)


def _brain_strategy_from_think(think: dict) -> Optional[str]:
    sig = think.get("signal")
    if sig is None:
        return None
    strat = getattr(sig, "strategy", None)
    if strat:
        return str(strat)
    patterns = getattr(sig, "patterns", None) or []
    return str(patterns[0]) if patterns else None


def _matching_side_score(of_trap: Optional[dict], action: str) -> float:
    of_trap = of_trap or {}
    if action == "BUY":
        return float(of_trap.get("long_score") or 0)
    if action == "SELL":
        return float(of_trap.get("short_score") or 0)
    return max(float(of_trap.get("long_score") or 0), float(of_trap.get("short_score") or 0))


def _family_from_think(think: dict, of_trap: Optional[dict] = None) -> Optional[str]:
    """Resolve pattern family from brain signal / OF for playbook + floors."""
    sig = think.get("signal") if think else None
    pat = None
    strat = None
    if sig is not None:
        pats = getattr(sig, "patterns", None) or []
        pat = pats[0] if pats else None
        strat = getattr(sig, "strategy", None)
    if not pat and of_trap:
        pat = of_trap.get("pattern")
    if not strat:
        strat = _brain_strategy_from_think(think)
    return family_rules.resolve_family(pat, strat)


def _brain_side_ok(
    think: dict,
    action: str,
    timeframe_key: str,
) -> tuple[bool, float, str]:
    """Brain/pattern layer: matching signal or trap must clear confluence + R:R floors."""
    if action not in ("BUY", "SELL"):
        return False, 0.0, "not actionable"
    tf = _norm_tf(timeframe_key)
    tf_cfg = _b.TIMEFRAMES.get(tf, _b.TIMEFRAMES["1h"])
    min_sc = float(tf_cfg.min_score)
    min_rr = float(tf_cfg.min_rr)
    fam = _family_from_think(think)
    ov_sc, ov_rr = family_rules.effective_brain_floors(
        timeframe_key, family=fam, brain_strategy=_brain_strategy_from_think(think)
    )
    # Family DB may raise the floor (stricter). Code floor is the minimum.
    if ov_sc is not None:
        min_sc = max(min_sc, float(ov_sc))
    if ov_rr is not None:
        min_rr = max(min_rr, float(ov_rr))

    sig = think.get("signal")
    if sig is not None:
        if sig.side != action:
            sc = float(getattr(sig, "score", 0) or 0)
            return False, sc, f"brain signal {sig.side} != {action}"
        sc = float(getattr(sig, "score", 0) or 0)
        rr = float(getattr(sig, "rr", 0) or 0)
        if sc < min_sc:
            return False, sc, f"brain score {sc:.1f} < {min_sc:.0f}"
        if rr < min_rr - 1e-6:
            return False, sc, f"brain R:R {rr:.2f} < {min_rr:.0f}"
        return True, sc, "ok"

    trap = think.get("trap")
    if trap is not None and getattr(trap, "side", None) == action:
        sc = float(getattr(trap, "score", 0) or 0)
        rr = float(getattr(trap, "rr", 0) or 0)
        if sc < min_sc:
            return False, sc, f"trap score {sc:.1f} < {min_sc:.0f}"
        if rr < min_rr - 1e-6:
            return False, sc, f"trap R:R {rr:.2f} < {min_rr:.0f}"
        return True, sc, "trap"

    return False, 0.0, "no qualifying brain signal/trap"


def _of_side_ok(
    of_trap: Optional[dict],
    action: str,
    timeframe_key: str,
    think: dict,
) -> tuple[bool, float, str]:
    """Order-flow layer: final_signal must match action and side score ≥ setup floor.

    Strict: every family and trap. No candle-soft / CANDLE_* bypass.
    """
    if action not in ("BUY", "SELL"):
        return False, 0.0, "not actionable"
    if not of_trap:
        return False, 0.0, "OF missing"
    want_sig = "LONG" if action == "BUY" else "SHORT"
    of_sig = (of_trap or {}).get("final_signal")
    strategy = _brain_strategy_from_think(think)
    pattern = (of_trap or {}).get("pattern") or ""
    family = _family_from_think(think, of_trap)
    side_sc = _matching_side_score(of_trap, action)
    tf = _norm_tf(timeframe_key)
    thr = thr_score_for_setup(tf, pattern, brain_strategy=strategy, family=family)
    if thr <= 0:
        thr = float(thr_score_for_tf(tf))

    if of_sig != want_sig:
        return False, side_sc, f"OF {of_sig or 'NONE'} != {want_sig}"
    if score_below_floor(side_sc, thr):
        return False, side_sc, f"OF score {side_sc:.1f} < {thr:.0f}"
    return True, side_sc, "ok"


def _dual_score_passes(
    action: str,
    of_trap: Optional[dict],
    think: dict,
    timeframe_key: str,
) -> tuple[bool, str, float, float]:
    """Brain confluence and OF confidence both required. No candle-only bypass."""
    b_ok, b_sc, b_msg = _brain_side_ok(think, action, timeframe_key)
    if not b_ok:
        return False, f"Brain gate: {b_msg}", b_sc, 0.0
    o_ok, o_sc, o_msg = _of_side_ok(of_trap, action, timeframe_key, think)
    if not o_ok:
        return False, f"OF gate: {o_msg}", b_sc, o_sc
    return True, f"brain={b_sc:.1f} OF={o_sc:.1f}", b_sc, o_sc


def _ai_yes_thr_for_tf(
    timeframe_key: str,
    pattern: str | None = None,
    *,
    brain_strategy: str | None = None,
    family: str | None = None,
) -> float:
    return float(
        thr_score_for_setup(
            timeframe_key, pattern, brain_strategy=brain_strategy, family=family
        )
    )


def _setup_meets_ai_confirm_threshold(
    action: str,
    of_trap: Optional[dict],
    timeframe_key: str,
    think: dict,
) -> bool:
    """AI consult only when brain (+ OF or candle-soft) clears floors."""
    ok, _, _, _ = _dual_score_passes(action, of_trap, think, timeframe_key)
    return ok


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


def _fresh_closed_candle_fire(think: dict) -> Optional[dict]:
    """Last closed bar brain signal only. Score must clear DETECT_FIRE_MIN_SCORE.

    Raw pattern matches and stale lookback signals do not fire.
    """
    n = int(think.get("n") or 0)
    if n < 2:
        return None
    last_i = n - 1
    sig = think.get("signal")
    if sig is None or int(getattr(sig, "index", -99)) != last_i:
        return None
    side = getattr(sig, "side", None)
    if side not in ("BUY", "SELL"):
        return None
    score = float(getattr(sig, "score", 0) or 0)
    if score < DETECT_FIRE_MIN_SCORE:
        return None
    pats = getattr(sig, "patterns", None) or []
    return {
        "action": side,
        "pattern": (pats[0] if pats else None) or getattr(sig, "strategy", None),
        "strategy": getattr(sig, "strategy", None),
        "score": score,
    }


def _fallback_action_from_brain_and_of(
    think: dict, of_trap: Optional[dict], timeframe_key: str = "1m"
) -> str:
    """Pick BUY/SELL only when brain confluence and OF confidence both pass."""
    candidates: list[tuple[str, float]] = []
    for action in ("BUY", "SELL"):
        ok, _, b_sc, o_sc = _dual_score_passes(action, of_trap or {}, think, timeframe_key)
        if ok:
            candidates.append((action, o_sc + b_sc * 0.1))
    if not candidates:
        return "HOLD"
    candidates.sort(key=lambda x: -x[1])
    return candidates[0][0]


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
    tf_cfg = _b.TIMEFRAMES.get(tf, _b.TIMEFRAMES["1h"])
    pattern, trap_score = _setup_label_and_score(think, of_trap, action)
    strategy = _brain_strategy_from_think(think)
    family = _family_from_think(think, of_trap)
    thr = _ai_yes_thr_for_tf(tf, pattern, brain_strategy=strategy, family=family)
    ov_sc, ov_rr = family_rules.effective_brain_floors(
        timeframe, family=family, pattern=pattern, brain_strategy=strategy
    )
    brain_floor = float(ov_sc) if ov_sc is not None else float(tf_cfg.min_score)
    rr_floor = float(ov_rr) if ov_rr is not None else float(tf_cfg.min_rr)
    ms = think.get("structure")
    sig = think.get("signal")
    trap = think.get("trap")
    stance = think.get("stance")
    of_trap = of_trap or {}
    side_score = _matching_side_score(of_trap, action)

    lines = [
        f"PATTERN DETECTED → confirm {side} {pattern}.",
        f"Pair={pair} TF={timeframe} ({tf_cfg.label}).",
        f"HARD RULE: reply YES only if BOTH brain confluence ≥ {brain_floor} "
        f"AND order-flow side_score ≥ {thr:.0f} on this TF. Otherwise NO.",
        "",
        "ANALYZE (policy):",
        "- LONG vs SHORT quality vs market structure",
        "- Trap / inverse / fake-breakout / absorption / exhaustion validity",
        "- Order-flow side score vs TF floor",
        "- Confluence + R:R vs rulebook",
        "",
        "POLICY FLOORS:",
        f"- AI YES confidence floor this TF: {thr:.0f}%",
        f"- Brain confluence min: {brain_floor} · min R:R: {rr_floor}",
        f"- Direction already set as {side}; you only YES/NO — do not invent BUY/SELL",
        f"- Note: {tf_cfg.note}",
        "",
        "SETUP FACTS:",
        f"- Detected side: {side} · pattern={pattern} · family={family or '?'} · "
        f"OF={side_score:.1f} (≥{thr:.0f}) · brain≥{brain_floor} required",
    ]
    for pl in family_rules.playbook_lines(
        family=family, timeframe_key=timeframe, pattern=pattern
    ):
        lines.append(pl)
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
    """AI confirm is removed. Dual gate decides the trade."""
    return None
    global _AI_CONFIRM_NEXT
    provider = getattr(settings, "ai_provider", "none")
    api_key = getattr(settings, "ai_api_key", "") or ""
    forced_model = ""
    if provider == "none":
        print("[AI-CONFIRM] AI not configured — skip trade.")
        return None

    # Cursor bridge is disabled in this build. Confirm on Z.ai so YES/NO can return.
    if provider in ("cursor", "cursor-ai", "cursor_sdk"):
        try:
            import cursor_ai
            cursor_ready = bool(cursor_ai.is_cursor_configured())
        except Exception:
            cursor_ready = False
        if not cursor_ready:
            zkey = ""
            zai_ok = False
            try:
                from api_secrets import get_zai_api_key, is_zai_configured
                zkey = (get_zai_api_key() or "").strip()
                zai_ok = bool(is_zai_configured() and zkey)
            except Exception:
                zkey = ""
                zai_ok = False
            if zai_ok:
                print("[AI-CONFIRM] Cursor bridge off — confirming via Z.ai.")
                provider = "z-ai"
                api_key = zkey
                forced_model = "glm-4.5-flash"
            elif not api_key:
                print("[AI-CONFIRM] AI not configured — skip trade.")
                return None

    if not api_key:
        print("[AI-CONFIRM] AI not configured — skip trade.")
        return None

    _DEFAULTS = {
        "cursor": {"base_url": None, "model": "composer-2.5", "auth": "bearer"},
        "z-ai": {"base_url": "https://api.z.ai/api/paas/v4", "model": "glm-4.5-flash", "auth": "bearer"},
        "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini", "auth": "bearer"},
        "zhipu-glm": {"base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4.5-flash", "auth": "bearer"},
        "azure-openai": {"base_url": None, "model": "gpt-4o-mini", "auth": "api-key"},
        "custom": {"base_url": None, "model": "glm-4.5-flash", "auth": "bearer"},
    }
    cfg = _DEFAULTS.get(provider, _DEFAULTS["custom"])
    user_prompt = _build_confirm_user_prompt(
        pair=pair,
        timeframe=timeframe,
        action=action,
        think=think,
        of_trap=of_trap,
    )

    if provider in ("cursor", "cursor-ai", "cursor_sdk"):
        try:
            import cursor_ai
            # Prefer settings key; fall back to env CURSOR_API_KEY
            if api_key and not os.environ.get("CURSOR_API_KEY"):
                os.environ["CURSOR_API_KEY"] = api_key
            decision = await cursor_ai.confirm_yes_no(
                system=_CONFIRM_SYSTEM,
                user=user_prompt,
                name="trade-confirm",
            )
            if decision is None:
                _notify_ai_health(False)
                print("[AI-CONFIRM] 'cursor' unreachable — skip trade.")
                return None
            _notify_ai_health(True)
            if decision is False:
                print("[AI-CONFIRM] 'cursor' → NO — skip trade.")
                return False
            strategy = _brain_strategy_from_think(think)
            fam = _family_from_think(think, of_trap)
            thr = _ai_yes_thr_for_tf(
                timeframe,
                (of_trap or {}).get("pattern"),
                brain_strategy=strategy,
                family=fam,
            )
            side_score = _matching_side_score(of_trap, action)
            if side_score < thr:
                print(
                    f"[AI-CONFIRM] 'cursor' YES ignored — "
                    f"OF side_score {side_score:.1f} < floor {thr:.0f}"
                )
                return False
            b_ok, _, b_sc, o_sc = _dual_score_passes(action, of_trap, think, timeframe)
            if not b_ok:
                print("[AI-CONFIRM] 'cursor' YES ignored — dual score failed after YES")
                return False
            print(
                f"[AI-CONFIRM] 'cursor' → YES  "
                f"(brain={b_sc:.1f} OF={o_sc:.1f}≥{thr:.0f})"
            )
            return True
        except Exception as exc:
            print(f"[AI-CONFIRM] Cursor AI error ({exc}) — skip trade.")
            _notify_ai_health(False)
            return None

    base_url = (getattr(settings, "ai_base_url", None) or cfg["base_url"] or "").rstrip("/")
    if not base_url:
        print(f"[AI-CONFIRM] No base_url for '{provider}' — skip trade.")
        return None
    model = forced_model or getattr(settings, "ai_model", None) or cfg["model"]
    if provider == "z-ai" and not str(model or "").lower().startswith("glm-"):
        model = "glm-4.5-flash"

    now = time.time()
    if _AI_CONFIRM_LOCK.locked() or now < _AI_CONFIRM_NEXT:
        wait = max(0.0, _AI_CONFIRM_NEXT - now)
        print(
            f"[AI-CONFIRM] rate gap {wait:.0f}s — skip this setup, "
            "next dual-gate pass will confirm"
        )
        return None

    headers = {"Content-Type": "application/json"}
    if cfg["auth"] == "api-key":
        headers["api-key"] = api_key
    else:
        headers["Authorization"] = f"Bearer {api_key}"

    messages = [
        {"role": "system", "content": _CONFIRM_SYSTEM},
        {"role": "user", "content": user_prompt},
    ]

    resp = None
    try:
        async with _AI_CONFIRM_LOCK:
            _AI_CONFIRM_NEXT = time.time() + _AI_CONFIRM_GAP
            async with httpx.AsyncClient(timeout=25.0) as client:
                for attempt in range(2):
                    resp = await client.post(
                        f"{base_url}/chat/completions",
                        headers=headers,
                        json={
                            "model": model,
                            "messages": messages,
                            "max_tokens": 32,
                            "temperature": 0,
                            "thinking": {"type": "disabled"},
                        },
                    )
                    if resp.status_code != 429:
                        break
                    retry_after = 4.0
                    try:
                        ra = resp.headers.get("retry-after")
                        if ra:
                            retry_after = min(float(ra), 8.0)
                    except (TypeError, ValueError):
                        retry_after = 4.0
                    snippet = (resp.text or "")[:160].replace("\n", " ")
                    print(
                        f"[AI-CONFIRM] '{provider}' HTTP 429 "
                        f"retry {retry_after:.0f}s ({snippet})"
                    )
                    if attempt == 0:
                        await asyncio.sleep(retry_after)
                        continue
                    _AI_CONFIRM_NEXT = time.time() + max(_AI_CONFIRM_GAP, retry_after)
                    _notify_ai_health(False)
                    return None
        if resp is None or resp.status_code != 200:
            code = getattr(resp, "status_code", "?")
            snippet = ""
            try:
                snippet = (resp.text or "")[:160].replace("\n", " ")
            except Exception:
                pass
            print(f"[AI-CONFIRM] '{provider}' HTTP {code} — skip trade. {snippet}")
            _notify_ai_health(False)
            return None
        msg = resp.json()["choices"][0]["message"]
        raw = str(msg.get("content") or msg.get("reasoning_content") or "").strip().upper()
        _notify_ai_health(True)
        token = raw.replace(".", " ").replace(",", " ").split()[0] if raw else ""
        if token.startswith("NO"):
            print(f"[AI-CONFIRM] '{provider}' → NO  (raw: {raw!r}) — skip trade.")
            return False
        if token.startswith("YES"):
            strategy = _brain_strategy_from_think(think)
            fam = _family_from_think(think, of_trap)
            thr = _ai_yes_thr_for_tf(
                timeframe,
                (of_trap or {}).get("pattern"),
                brain_strategy=strategy,
                family=fam,
            )
            side_score = _matching_side_score(of_trap, action)
            if side_score < thr:
                print(
                    f"[AI-CONFIRM] '{provider}' YES ignored — "
                    f"OF side_score {side_score:.1f} < floor {thr:.0f}"
                )
                return False
            b_ok, _, b_sc, o_sc = _dual_score_passes(action, of_trap, think, timeframe)
            if not b_ok:
                print(f"[AI-CONFIRM] '{provider}' YES ignored — dual score failed after YES")
                return False
            print(
                f"[AI-CONFIRM] '{provider}' → YES  (raw: {raw!r}, "
                f"brain={b_sc:.1f} OF={o_sc:.1f}≥{thr:.0f})"
            )
            return True
        print(f"[AI-CONFIRM] '{provider}' unclear {raw!r} — skip trade.")
        return None
    except Exception as exc:
        print(f"[AI-CONFIRM] API error ({exc}) — skip trade.")
        _notify_ai_health(False)
        return None


def _notify_ai_health(ok: bool) -> None:
    try:
        from main import agent as _agent
        _agent.note_ai_result(ok)
    except Exception:
        pass


def _normalize_brain_exit(
    entry: Optional[float],
    sl: Optional[float],
    tp: Optional[float],
    action: str,
) -> tuple[Optional[float], Optional[float]]:
    """Ensure stop/target sit on the correct side of entry for LONG/SHORT."""
    if entry is None or sl is None or tp is None:
        return sl, tp
    try:
        e, s, t = float(entry), float(sl), float(tp)
    except (TypeError, ValueError):
        return sl, tp
    if e <= 0 or s <= 0 or t <= 0:
        return sl, tp

    def _valid(a: str) -> bool:
        if a == "BUY":
            return s < e and t > e
        if a == "SELL":
            return s > e and t < e
        return True

    if _valid(action):
        return s, t

    # Common inversion: stop/target swapped relative to entry.
    s2, t2 = t, s
    if action == "BUY" and s2 < e and t2 > e:
        return s2, t2
    if action == "SELL" and s2 > e and t2 < e:
        return s2, t2

    loss = 0.005
    profit = 0.005
    if action == "BUY":
        return e * (1.0 - loss), e * (1.0 + profit)
    if action == "SELL":
        return e * (1.0 + loss), e * (1.0 - profit)
    return sl, tp


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
    if entry_price is not None and sl is not None and tp is not None and ai_action in ("BUY", "SELL"):
        sl, tp = _normalize_brain_exit(entry_price, sl, tp, ai_action)
    rr = float(getattr(entry_src, "rr", 0) or 0) if entry_src else None

    # Risk plan
    plan: Optional[_b.TradePlan] = think.get("plan")
    if plan is None and entry_src is not None and ai_action != "HOLD":
        try:
            plan = _b.plan_trade(equity, risk_pct_pct, ai_action,
                                 entry_src.entry, entry_src.stop, entry_src.target)
        except Exception:
            plan = None

    # Prefer brain candle pattern for trade UI; keep OF trap as secondary.
    candle_pattern = None
    brain_strategy_name = None
    if sig is not None:
        candle_pattern = sig.patterns[0] if sig.patterns else None
        brain_strategy_name = getattr(sig, "strategy", None)

    of_signal = (of_trap or {}).get("final_signal")
    of_pattern = (of_trap or {}).get("pattern") if of_trap else None
    if of_trap and of_signal in ("LONG", "SHORT") and (
        (ai_action == "BUY" and of_signal == "LONG") or (ai_action == "SELL" and of_signal == "SHORT")
    ):
        strategy_name = brain_strategy_name or "trap_orderflow"
        if isinstance(strategy_name, str):
            strategy_name = strategy_name.replace("_", " ")
        confluences = [
            of_trap.get("primary_reason") or "",
            f"5M bias={of_trap.get('bias_5m')}",
            f"LONG_SCORE={of_trap.get('long_score')} SHORT_SCORE={of_trap.get('short_score')}",
        ]
        if trap is not None:
            confluences.append(f"structure_trap={trap.trap_type}")
        family_name = family_rules.resolve_family(candle_pattern, brain_strategy_name)
        if not family_name:
            family_name = family_rules.resolve_family(of_pattern, brain_strategy_name)
        pattern_name = family_rules.format_candle_trade_label(
            family=family_name,
            candle_pattern=candle_pattern,
            of_pattern=of_pattern,
        )
    elif stance and stance.source == "trap" and trap is not None:
        pattern_name = trap.trap_type.replace("_", " ")
        strategy_name = "trap_reverse"
        confluences = list(trap.reasons) if trap.reasons else []
        family_name = family_rules.resolve_family(candle_pattern, brain_strategy_name)
    elif sig is not None:
        pattern_name = candle_pattern or (sig.strategy.replace("_", " ") if sig.strategy else None)
        strategy_name = sig.strategy.replace("_", " ") if sig.strategy else None
        confluences = list(sig.confluence or []) + list(sig.reasons or [])
        family_name = family_rules.resolve_family(candle_pattern, brain_strategy_name)
        if family_name and candle_pattern:
            pattern_name = family_rules.format_candle_trade_label(
                family=family_name,
                candle_pattern=candle_pattern,
            )
    else:
        pattern_name = None
        strategy_name = None
        confluences = []
        family_name = None

    if not family_name and sig is not None:
        family_name = family_rules.resolve_family(
            (sig.patterns[0] if sig.patterns else None), sig.strategy
        )

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
        "candle_pattern": candle_pattern,
        "of_pattern": of_pattern,
        "family": family_name,
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
        brain_strategy = _brain_strategy_from_think(think)
        sig = think.get("signal")
        brain_side = getattr(sig, "side", None) if sig else None
        of = evaluate_trap_orderflow(
            c1, c5,
            exec_tf=_norm_tf(timeframe_key),
            brain_strategy=brain_strategy,
            brain_side=str(brain_side) if brain_side else None,
        )
        struct = think.get("trap")
        struct_side = getattr(struct, "side", None) if struct else None
        struct_type = getattr(struct, "trap_type", None) if struct else None
        merged = merge_with_structure_trap(of, struct_side, struct_type)
        return merged.to_dict()
    except Exception as exc:
        print(f"[TRAP-OF] evaluate error: {exc}")
        return None


def _gate_scalp_of_score(
    action: str,
    of_trap: Optional[dict],
    timeframe_key: str,
    think: Optional[dict] = None,
) -> str:
    """All TFs: BUY/SELL only when BOTH brain confluence and OF side score pass."""
    if action not in ("BUY", "SELL"):
        return action
    ok, msg, b_sc, o_sc = _dual_score_passes(action, of_trap, think or {}, timeframe_key)
    if not ok:
        print(f"[AI-BRAIN] dual gate blocked {action}: {msg} (brain={b_sc:.1f} OF={o_sc:.1f})")
        return "HOLD"
    return action


def _gate_1m_of_score(
    action: str,
    of_trap: Optional[dict],
    timeframe_key: str,
    think: Optional[dict] = None,
) -> str:
    return _gate_scalp_of_score(action, of_trap, timeframe_key, think=think)


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
    """Async entry: brain + order-flow set BUY/SELL; AI must confirm YES.

    Missing brain/OF/AI signal → NO_TRADE (no fail-open).
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

    # Candidate side from last-bar signal or brain+OF. Never skip dual/AI.
    fresh_pattern = _fresh_closed_candle_fire(think)
    if fresh_pattern:
        setup_action = fresh_pattern["action"]
    else:
        setup_action = _fallback_action_from_brain_and_of(think, of_trap, timeframe_key)
    setup_action = _gate_1m_of_score(setup_action or "HOLD", of_trap, timeframe_key, think=think)

    def _blocked(reason: str, *, ai_confirmation: str = "MISSING") -> Dict[str, Any]:
        side = "LONG" if setup_action == "BUY" else "SHORT" if setup_action == "SELL" else None
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
        rejected["ai_confirmation"] = ai_confirmation
        rejected["ai_driven"] = False
        if side:
            rejected["direction"] = side
        rejected["reason"] = reason
        return rejected

    ai_confirmation = "MISSING"
    if setup_action in ("BUY", "SELL"):
        if not of_trap:
            return _blocked("Backend OF signal missing — skip trade")

        of_sig = (of_trap or {}).get("final_signal")
        of_pat = str((of_trap or {}).get("pattern") or "")
        if of_sig == "NO_TRADE" or of_pat.upper().startswith("CANDLE_"):
            return _blocked(
                f"Order-flow NO_TRADE — skip | {(of_trap or {}).get('primary_reason') or of_sig or ''}"
            )

        sig = think.get("signal")
        if sig is None and think.get("trap") is None:
            return _blocked("Backend brain signal missing — skip trade")

        ok_dual, dual_msg, b_sc, o_sc = _dual_score_passes(
            setup_action, of_trap, think, timeframe_key
        )
        if not ok_dual:
            return _blocked(f"Dual score failed — {dual_msg} (brain={b_sc:.1f} OF={o_sc:.1f})")

        ai_confirmation = "SKIP"
        print(
            f"[DUAL-GATE] {setup_action} {pair} "
            f"brain={b_sc:.1f} OF={o_sc:.1f} — fire without AI"
        )

    fire_action = setup_action if setup_action in ("BUY", "SELL") and ai_confirmation == "SKIP" else "HOLD"
    out = _flatten(
        think,
        ai_action=fire_action,
        pair=pair,
        timeframe_key=timeframe_key,
        risk_pct_pct=risk_pct_pct,
        equity=float(account_balance),
        of_trap=of_trap,
    )
    out["ai_confirmation"] = ai_confirmation
    out["ai_driven"] = False
    if fire_action in ("BUY", "SELL"):
        if fresh_pattern:
            pat = fresh_pattern.get("pattern")
            strat = fresh_pattern.get("strategy")
            fam = family_rules.resolve_family(pat, strat) or out.get("family")
            out["pattern"] = pat or out.get("pattern")
            out["candle_pattern"] = pat or out.get("candle_pattern")
            out["strategy"] = strat or out.get("strategy")
            out["family"] = fam
            out["score"] = fresh_pattern.get("score") or out.get("score") or 0
        out["action"] = fire_action
        out["direction"] = "LONG" if fire_action == "BUY" else "SHORT"
        out["reason"] = f"{out.get('reason', '')} | dual-gate".strip(" |")
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
    ai_action = _gate_1m_of_score(ai_action or "HOLD", of_trap, timeframe_key, think=think)
    if ai_action in ("BUY", "SELL"):
        ok_dual, dual_msg, b_sc, o_sc = _dual_score_passes(
            ai_action, of_trap, think, timeframe_key
        )
        if not ok_dual:
            return {
                "action": "NO_TRADE",
                "reason": f"Dual score failed — {dual_msg} (brain={b_sc:.1f} OF={o_sc:.1f})",
                "engine": ENGINE_NAME,
                "entry_pattern": ENTRY_PATTERN_NAME,
                "timeframe_key": timeframe_key,
                "pair": pair,
                "ai_driven": False,
                "ai_confirmation": "SKIP",
            }
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
        "pipeline": ["brain_analysis", "orderflow_trap", "dual_gate", "risk_plan"],
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
            "no AI confirm — dual gate is the fire decision; next-candle fire; path SL/TP 0.5/0.7; "
            "flip-exit on opposite signal. "
            f"Active label: {tf_cfg.label}. Min confluence: {tf_cfg.min_score}, min R:R: {tf_cfg.min_rr}. "
            f"Order-flow conf floor: overall ≥75% / 5m traps ≥80% / other traps ≥90%. "
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
        "(min confluence score 5, min R:R 2, HTF alignment + noise guard — identical on all TFs).\n"
        "2) Order-flow TRAP DETECTION ENGINE: buy/sell trap, absorption, exhaustion,\n"
        "   fake breakout, reversal trap (effort vs result; volume & buyer/seller pressure).\n"
        "3) Dual gate (brain + order-flow) → BUY / SELL / HOLD. No AI confirm.\n"
        "4) Next-candle fire + path SL/TP 0.5%/0.7% + opposite-side flip-exit.\n"
        "5) Floors: overall≥75 / 5m trap≥80 / else trap≥90.\n"
    )


def is_scalp_timeframe(timeframe_key: str | None) -> bool:
    """1m and 5m share the same scalp entry/exit/confirm policy."""
    try:
        from timeframe_profiles import is_scalp_tf
        return is_scalp_tf(timeframe_key)
    except Exception:
        return str(timeframe_key or "").strip().lower() in ("1m", "5m", "30s")


async def run_in_thread(candles, timeframe_key, **kw):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: evaluate_live_entry(candles, timeframe_key, **kw),
    )
