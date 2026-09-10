"""Cursor SDK AI — DISABLED for training / self-improve / issue bus.

`is_cursor_configured()` is always False. Family AI training is MySQL-only
(family_analyzer + family_engine_rules). Confirm / improve stubs return None.
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


# Cursor AI self-improve / confirm bridge is fully disabled.
# Manual 1m scale + exit policy stay locked in engine_config.
def is_cursor_configured() -> bool:
    return False


def is_unlimited() -> bool:
    return False


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
    """Removed — family training is MySQL-only (no Cursor agent)."""
    return None


async def self_improve_for_profit(
    *,
    family: str | None = None,
    timeframe: str | None = None,
    stats_blurb: str = "",
    trade: dict | None = None,
    trigger: str = "manual",
) -> Optional[str]:
    """Removed — AI training no longer runs Cursor self-improve."""
    return None


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
    """Removed — Cursor issue/self-improve bus is disabled."""
    return None


def schedule_bot_issue(**kwargs: Any) -> None:
    """Removed — Cursor issue/self-improve bus is disabled."""
    return
