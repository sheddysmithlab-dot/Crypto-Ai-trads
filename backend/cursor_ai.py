"""Cursor SDK AI — unlimited full-agent mode for confirm + profit self-improve.

When CURSOR_AI_UNLIMITED=1 (default): local agent on the repo with tools enabled —
can read/edit code, pull outside context, integrate changes aimed at higher profit /
lower loss. Sync SDK is unreliable on Windows; always use the async bridge.
"""
from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Optional

_client = None
_client_lock = asyncio.Lock()
_client_cwd: str | None = None
_improve_lock = asyncio.Lock()

_DEFAULT_MODEL = (os.environ.get("CURSOR_AI_MODEL") or os.environ.get("AI_MODEL") or "composer-2.5").strip()
_PROMPT_TIMEOUT = float(os.environ.get("CURSOR_AI_TIMEOUT_SEC", "90"))
_IMPROVE_TIMEOUT = float(os.environ.get("CURSOR_AI_IMPROVE_TIMEOUT_SEC", "600"))
_CONFIRM_TIMEOUT = float(os.environ.get("CURSOR_AI_CONFIRM_TIMEOUT_SEC", "180"))


def cursor_api_key() -> str:
    return (os.environ.get("CURSOR_API_KEY") or os.environ.get("AI_API_KEY") or "").strip()


def is_cursor_configured() -> bool:
    return bool(cursor_api_key())


def is_unlimited() -> bool:
    """Full agent power (tools + edits + external research). Default ON."""
    raw = (os.environ.get("CURSOR_AI_UNLIMITED") or "1").strip().lower()
    return raw in ("1", "true", "yes", "on")


def repo_root() -> str:
    """Project root (parent of backend/)."""
    backend = os.path.abspath(os.path.dirname(__file__))
    parent = os.path.abspath(os.path.join(backend, ".."))
    return parent if os.path.isdir(parent) else backend


async def _ensure_client():
    """Long-lived AsyncClient owning a cursor-sdk-bridge subprocess."""
    global _client, _client_cwd
    async with _client_lock:
        if _client is not None:
            return _client
        from cursor_sdk import AsyncClient

        cwd = repo_root() if is_unlimited() else os.path.abspath(os.path.dirname(__file__))
        _client = await AsyncClient.launch_bridge(workspace=cwd)
        _client_cwd = cwd
        print(f"[CURSOR-AI] bridge launched workspace={cwd} unlimited={is_unlimited()}")
        return _client


async def shutdown_cursor_client() -> None:
    global _client
    async with _client_lock:
        if _client is None:
            return
        try:
            await _client.aclose()
        except Exception as exc:
            print(f"[CURSOR-AI] bridge close: {exc}")
        _client = None


def _agent_options(*, name: str, model: str | None = None, unlimited: bool | None = None):
    from cursor_sdk import AgentOptions, CloudAgentOptions, LocalAgentOptions

    use_full = is_unlimited() if unlimited is None else bool(unlimited)
    model_id = (model or _DEFAULT_MODEL).strip() or "composer-2.5"
    opts: dict[str, Any] = {
        "api_key": cursor_api_key(),
        "model": model_id,
        "name": (name or "aitrads")[:80],
    }
    if use_full:
        # Local full agent: edit repo, run tools, inherit env (MYSQL_*, keys already in process).
        opts["local"] = LocalAgentOptions(
            cwd=repo_root(),
            # Empty / omit setting_sources = inline only; still has full local tools.
        )
        # Do NOT set disallowed_tools — unlimited.
    else:
        opts["cloud"] = CloudAgentOptions(repos=[])
    return AgentOptions(**opts)


async def run_agent(
    prompt: str,
    *,
    name: str = "aitrads-agent",
    model: str | None = None,
    timeout: float | None = None,
    unlimited: bool | None = None,
) -> Optional[str]:
    """Run one Cursor agent turn; returns final assistant text (or None)."""
    if not is_cursor_configured():
        print("[CURSOR-AI] CURSOR_API_KEY missing")
        return None
    use_full = is_unlimited() if unlimited is None else bool(unlimited)
    default_to = _IMPROVE_TIMEOUT if use_full else _PROMPT_TIMEOUT
    try:
        from cursor_sdk import AsyncAgent

        client = await _ensure_client()
        result = await asyncio.wait_for(
            AsyncAgent.prompt(
                prompt,
                _agent_options(name=name, model=model, unlimited=use_full),
                client=client,
            ),
            timeout=float(timeout if timeout is not None else default_to),
        )
        status = getattr(result, "status", None)
        if status != "finished":
            print(
                f"[CURSOR-AI] run status={status} id={getattr(result, 'id', None)} "
                f"agent={getattr(result, 'agent_id', None)}"
            )
            # Still return text if present
        text = getattr(result, "result", None)
        if text is None:
            return None
        return str(text).strip()
    except asyncio.TimeoutError:
        print(f"[CURSOR-AI] timeout after {timeout if timeout is not None else default_to}s")
        return None
    except Exception as exc:
        print(f"[CURSOR-AI] run_agent error: {exc}")
        try:
            await shutdown_cursor_client()
        except Exception:
            pass
        return None


# Back-compat alias
async def ask_text(
    prompt: str,
    *,
    name: str = "aitrads-ask",
    model: str | None = None,
    timeout: float | None = None,
) -> Optional[str]:
    return await run_agent(prompt, name=name, model=model, timeout=timeout)


_YES_NO_RE = re.compile(r"\b(YES|NO)\b", re.IGNORECASE)


async def confirm_yes_no(
    *,
    system: str,
    user: str,
    name: str = "trade-confirm",
) -> Optional[bool]:
    """Trade confirm. Unlimited mode: agent may research/tools; last word must be YES/NO."""
    if is_unlimited():
        prompt = (
            f"{system.strip()}\n\n"
            f"{user.strip()}\n\n"
            "UNLIMITED AGENT MODE: You may use tools, read project files, MySQL-related "
            "code/config, family_engine_rules / training history, and outside market/"
            "pattern knowledge to judge this setup for EXPECTED PROFIT vs LOSS.\n"
            "You may temporarily adjust local playbook notes if clearly justified — "
            "but this call's PRIMARY job is the trade gate.\n"
            "FINAL LINE of your answer MUST be exactly one word: YES or NO "
            "(YES = take the trade, NO = skip)."
        )
        raw = await run_agent(
            prompt,
            name=name,
            timeout=_CONFIRM_TIMEOUT,
            unlimited=True,
        )
    else:
        prompt = (
            f"{system.strip()}\n\n"
            f"{user.strip()}\n\n"
            "CRITICAL: Your entire final answer must be exactly one word: YES or NO."
        )
        raw = await run_agent(
            prompt,
            name=name,
            timeout=_PROMPT_TIMEOUT,
            unlimited=False,
        )
    if not raw:
        return None
    upper = raw.strip().upper()
    # Prefer LAST YES/NO (after analysis)
    matches = list(_YES_NO_RE.finditer(upper))
    if matches:
        return matches[-1].group(1).upper() == "YES"
    token = upper.replace(".", " ").replace(",", " ").split()[0] if upper else ""
    if token.startswith("YES"):
        return True
    if token.startswith("NO"):
        return False
    print(f"[CURSOR-AI] unclear reply {raw[:200]!r}")
    return None


async def train_lesson(
    *,
    family: str,
    timeframe: str,
    stats_blurb: str,
) -> Optional[str]:
    """Legacy short lesson — delegates to full self-improve when unlimited."""
    if is_unlimited():
        summary = await self_improve_for_profit(
            family=family,
            timeframe=timeframe,
            stats_blurb=stats_blurb,
            trigger="train_lesson",
        )
        return summary
    prompt = (
        "You are training an automated crypto futures pattern engine. "
        f"Family={family} TF={timeframe}.\n"
        f"Stats:\n{stats_blurb}\n\n"
        "Write ONE short playbook lesson (max 400 chars) for when to FIRE vs SKIP. "
        "No markdown, no bullets, plain text only."
    )
    return await run_agent(
        prompt,
        name=f"train-{family}-{timeframe}"[:80],
        timeout=120.0,
        unlimited=False,
    )


async def self_improve_for_profit(
    *,
    family: str | None = None,
    timeframe: str | None = None,
    stats_blurb: str = "",
    trade: dict | None = None,
    trigger: str = "manual",
) -> Optional[str]:
    """Unlimited Cursor agent: change code/rules/integrations to raise profit / cut loss.

    Can read outside sources (via tools), edit backend files, update family playbooks,
    tune SL/TP/floors, and wire new data — goal is net expectancy improvement.
    """
    if not is_cursor_configured():
        return None
    if not is_unlimited():
        return await train_lesson(
            family=family or "unknown",
            timeframe=timeframe or "1m",
            stats_blurb=stats_blurb,
        )

    async with _improve_lock:
        fam = family or (trade or {}).get("family") or "all"
        tf = timeframe or (trade or {}).get("timeframe_key") or "1m"
        trade_bits = ""
        if trade:
            trade_bits = (
                f"\nLast trade: id={trade.get('id')} pair={trade.get('pair')} "
                f"side={trade.get('side')} pattern={trade.get('pattern')} "
                f"score={trade.get('score')} closed_reason={trade.get('closed_reason')} "
                f"peak={trade.get('peak_gross_pct')} trough={trade.get('trough_gross_pct')}\n"
            )
        prompt = f"""
You are the UNLIMITED self-improving trading engineer for the aitrads.in bot (this repo).

GOAL: Maximize net profit and minimize losses for pattern family engines.
Trigger: {trigger}
Focus family: {fam}
Focus timeframe: {tf}
Stats / context:
{stats_blurb}
{trade_bits}

YOU HAVE FULL POWER:
1. Read and edit project files under this workspace (especially backend/: trade_db.py,
   family_rules.py, family_analyzer.py, engine_config.py, trap_orderflow_engine.py,
   brain_adapter.py, main.py, .env.example — never commit secrets; use existing MYSQL_* / env).
2. Prefer changing MySQL tables when possible:
   - `engine_formulas` (global exit/OF/fire knobs — then call engine_config.reload conceptually)
   - `family_engine_rules` / `family_train_events` (per-family floors, lessons, SL/TP %)
3. Inspect family_engine_rules / family_train_events / engine_formulas and improve thresholds,
   lessons, SL/TP %, skip/fire logic, candle-soft behavior.
4. Research outside sources (web/docs/papers via tools) on candlestick families,
   risk, order-flow, exits — then INTEGRATE useful ideas into this codebase / DB.
5. Add or wire new data sources / helpers if they clearly improve expectancy.
6. Keep the live bot bootable: valid Python, no broken imports, no deleting safety
   that prevents catastrophic account wipe without replacement logic.

CONSTRAINTS (only these):
- Do not leak or print full API keys / passwords into new files committed as plaintext
  beyond existing .env patterns.
- Do not push to git remotes.
- Prefer durable improvements (code + DB rule fields) over one-off chat advice.

PROCESS:
1. Diagnose where loss / delay / wrong fires happen for this family/TF.
2. Implement concrete changes (code and/or document rule updates the bot already reads).
3. Summarize what you changed and why (final answer = short summary for logs).

Start now. Be decisive; optimize for profit expectancy.
""".strip()
        print(f"[CURSOR-AI] UNLIMITED self-improve start family={fam} tf={tf} trigger={trigger}")
        summary = await run_agent(
            prompt,
            name=f"improve-{fam}-{tf}"[:80],
            timeout=_IMPROVE_TIMEOUT,
            unlimited=True,
        )
        if summary:
            print(f"[CURSOR-AI] UNLIMITED self-improve done: {summary[:300]}")
            try:
                import family_rules
                family_rules.invalidate_cache()
            except Exception:
                pass
            try:
                import engine_config
                engine_config.reload_and_apply()
            except Exception:
                pass
        return summary


# ─── Issue bus: delay / no-fire / skip / freeze / loss → unlimited agent ─────
_ISSUE_COOLDOWN_SEC = float(os.environ.get("CURSOR_AI_ISSUE_COOLDOWN_SEC", "600"))
_last_issue_at: dict[str, float] = {}


def classify_issue(reason: str, *, category: str | None = None) -> str:
    """Map free-text reason → issue category for prompts + rate-limit keys."""
    if category:
        return str(category).strip().lower() or "other"
    r = (reason or "").lower()
    if any(
        x in r
        for x in (
            "expir", "timeout", "delay", "late", "grace", "window",
            "confirm timeout", "no green", "no red",
        )
    ):
        return "trade_delay"
    if any(
        x in r
        for x in ("no fire", "not fir", "no_trade", "hold", "skip trade", "ai-confirm", "unreachable")
    ):
        return "no_fire"
    if "freeze" in r or "stale" in r or "feed" in r:
        return "freeze"
    if "loss" in r or "sl_hit" in r or "gave_back" in r:
        return "loss"
    if "duplicate" in r or "blocked" in r or "capacity" in r or "skipped" in r:
        return "skip"
    if "size plan" in r or "open_trade" in r or "bybit" in r or "failed" in r:
        return "fire_fail"
    return "other"


def _issue_allowed(key: str) -> bool:
    import time as _time

    now = _time.time()
    last = _last_issue_at.get(key, 0.0)
    if now - last < _ISSUE_COOLDOWN_SEC:
        return False
    _last_issue_at[key] = now
    return True


async def report_bot_issue(
    *,
    reason: str,
    category: str | None = None,
    pair: str | None = None,
    timeframe: str | None = None,
    family: str | None = None,
    pattern: str | None = None,
    side: str | None = None,
    detect: dict | None = None,
    trade: dict | None = None,
    extra: dict | None = None,
    force: bool = False,
) -> Optional[str]:
    """Any bot problem (delay, no fire, skip, freeze, loss…) → unlimited Cursor fix.

    Rate-limited per category+family (default 10 min) so the agent is not flooded.
    """
    if not is_cursor_configured() or not is_unlimited():
        return None
    cat = classify_issue(reason, category=category)
    fam = family or (detect or {}).get("family") or (trade or {}).get("family") or "all"
    tf = (
        timeframe
        or (detect or {}).get("timeframe_key")
        or (trade or {}).get("timeframe_key")
        or "1m"
    )
    key = f"{cat}|{fam}|{tf}"
    if not force and not _issue_allowed(key):
        print(f"[CURSOR-AI] issue cooldown skip {key}: {reason[:120]}")
        return None

    bits = {
        "category": cat,
        "reason": reason,
        "pair": pair,
        "timeframe": tf,
        "family": fam,
        "pattern": pattern or (detect or {}).get("pattern") or (trade or {}).get("pattern"),
        "side": side or (detect or {}).get("direction") or (trade or {}).get("side"),
        "score": (detect or {}).get("score") or (trade or {}).get("score"),
        "ai_confirmation": (detect or {}).get("ai_confirmation"),
        "brain_verdict": (detect or {}).get("brain_verdict"),
        "extra": extra or {},
    }
    focus = {
        "trade_delay": (
            "PRIORITY: trades are DELAYED or confirm window expires before fire. "
            "Fix scalp confirm timing, fire_grace, pending queue expiry, green/red confirm logic, "
            "AI confirm latency, or anything that makes entries late / miss the candle."
        ),
        "no_fire": (
            "PRIORITY: setups detected but NOT FIRING (NO_TRADE / AI NO / gates). "
            "Loosen or retune floors, candle-soft, dual-score, AI confirm path so valid "
            "profitable setups actually fire without spam."
        ),
        "skip": (
            "PRIORITY: too many SKIPS (blocked / capacity / duplicate / policy). "
            "Fix false skips that kill expectancy; keep only skips that prevent real loss."
        ),
        "freeze": (
            "PRIORITY: engine FREEZE / stale feed / connectivity pausing entries. "
            "Harden reconnect, unfreeze, and keep fires healthy when feed recovers."
        ),
        "loss": (
            "PRIORITY: losing trades. Fix entry/SL/TP/family rules so this loss class stops."
        ),
        "fire_fail": (
            "PRIORITY: fire attempted but open failed (size/Bybit/open_trade). Fix sizing/execution."
        ),
        "other": (
            "PRIORITY: general bot fault. Diagnose and patch so trading stays profitable and reliable."
        ),
    }.get(cat, "Diagnose and fix.")

    blurb = (
        f"ISSUE CATEGORY: {cat}\n"
        f"{focus}\n"
        f"Details JSON:\n{bits}\n"
        "Fix delay / no-fire / wrong-skip / loss — whatever blocks profit. "
        "Edit code + family rules as needed; research outside if useful."
    )
    return await self_improve_for_profit(
        family=str(fam),
        timeframe=str(tf),
        stats_blurb=blurb,
        trade=trade,
        trigger=f"issue:{cat}",
    )


def schedule_bot_issue(**kwargs: Any) -> None:
    """Fire-and-forget from sync or async code (uses running loop if any)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        print("[CURSOR-AI] schedule_bot_issue: no running loop — dropped")
        return

    async def _run():
        try:
            summary = await report_bot_issue(**kwargs)
            if summary:
                try:
                    from system_log import system_log

                    system_log.push(
                        "ai",
                        f"Cursor fixed issue ("
                        f"{kwargs.get('category') or classify_issue(str(kwargs.get('reason') or ''))})",
                        {
                            "summary": summary[:400],
                            "reason": str(kwargs.get("reason") or "")[:200],
                        },
                    )
                except Exception:
                    pass
        except Exception as exc:
            print(f"[CURSOR-AI] schedule_bot_issue error: {exc}")

    loop.create_task(_run())
