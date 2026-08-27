"""Persist AI engine runtime so restarts / brief outages don't wipe open trades.

Desired behavior:
  - Engine stays ON until the user explicitly stops it
  - Open trades + season book survive backend restart
  - Connectivity loss freezes new fires; reconnect resumes from same state
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

_DEFAULT_DATA = Path(__file__).resolve().parent / "data"
DATA_DIR = Path(os.environ.get("ENGINE_RUNTIME_DATA_DIR", str(_DEFAULT_DATA)))
RUNTIME_PATH = DATA_DIR / "engine_runtime.json"

# How many consecutive AI failures before freeze
AI_FAIL_FREEZE_STREAK = int(os.environ.get("AI_FAIL_FREEZE_STREAK", "3"))
# Market feed considered stale after this many seconds without a tick
FEED_STALE_SECONDS = float(os.environ.get("FEED_STALE_SECONDS", "45"))


def _safe_trade(t: dict) -> dict:
    """JSON-safe copy of an open trade (drop non-serializable junk)."""
    out = {}
    for k, v in (t or {}).items():
        if k in ("reason",) and isinstance(v, str) and len(v) > 2000:
            out[k] = v[:2000]
            continue
        try:
            json.dumps(v)
            out[k] = v
        except TypeError:
            out[k] = str(v)
    return out


def dump_runtime(agent: Any) -> dict:
    return {
        "version": 1,
        "saved_at": time.time(),
        "trading_ready_at": float(getattr(agent, "trading_ready_at", 0) or 0),
        "boot_ui_until": float(getattr(agent, "boot_ui_until", 0) or 0),
        "momentum_gate_ready": bool(getattr(agent, "momentum_gate_ready", False)),
        "momentum_fire_pairs": list(getattr(agent, "momentum_fire_pairs", None) or []),
        "is_active": bool(getattr(agent, "is_active", False)),
        "session_hold_mode": bool(getattr(agent, "session_hold_mode", False)),
        "one_m_fee_hold": bool(getattr(agent, "one_m_fee_hold", False)),
        "connectivity_frozen": bool(getattr(agent, "connectivity_frozen", False)),
        "freeze_reason": getattr(agent, "freeze_reason", None),
        "active_pair": getattr(agent, "active_pair", "BTC/USDT"),
        "watchlist": list(getattr(agent, "watchlist", []) or []),
        "timeframe_seconds": int(getattr(agent, "timeframe_seconds", 60) or 60),
        "trade_seq": int(getattr(agent, "trade_seq", 0) or 0),
        "trades": [_safe_trade(t) for t in (getattr(agent, "trades", []) or [])],
        "trade_history": [
            _safe_trade(t) for t in (getattr(agent, "trade_history", []) or [])[-200:]
        ],
        "ai_season_id": getattr(agent, "ai_season_id", None),
        "ai_season_start_capital": getattr(agent, "ai_season_start_capital", None),
        "ai_season_started_at": getattr(agent, "ai_season_started_at", None),
        "ai_season_end_reason": getattr(agent, "ai_season_end_reason", None),
        "session_stats_frozen": bool(getattr(agent, "session_stats_frozen", False)),
        "session_stats_snapshot": dict(getattr(agent, "session_stats_snapshot", {}) or {}),
        "risk_level_pct": float(getattr(agent, "risk_level_pct", 5) or 5),
        "max_concurrent_trades": int(getattr(agent, "max_concurrent_trades", 10) or 10),
        "daily_profit_target_pct": float(getattr(agent, "daily_profit_target_pct", 0) or 0),
        "daily_target_reached": bool(getattr(agent, "daily_target_reached", False)),
        "current_capital": float(getattr(agent, "current_capital", 0) or 0),
        "starting_capital": float(getattr(agent, "starting_capital", 0) or 0),
        "pair_prices": dict(getattr(agent, "pair_prices", {}) or {}),
        "current_price": float(getattr(agent, "current_price", 0) or 0),
    }


def save_runtime(agent: Any) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        payload = dump_runtime(agent)
        tmp = RUNTIME_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp.replace(RUNTIME_PATH)
    except Exception as exc:
        print(f"[ENGINE RUNTIME] save note: {exc}")


def load_runtime() -> dict | None:
    try:
        if not RUNTIME_PATH.is_file():
            return None
        data = json.loads(RUNTIME_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as exc:
        print(f"[ENGINE RUNTIME] load note: {exc}")
        return None


def restore_runtime(agent: Any) -> dict:
    """Apply saved runtime onto agent. Returns summary for logs."""
    data = load_runtime()
    summary = {"restored": False, "trades": 0, "is_active": False}
    if not data:
        return summary

    try:
        agent.is_active = bool(data.get("is_active"))
        # Resume mid-session: trading ready immediately; do NOT re-open boot overlay.
        agent.trading_ready_at = 0.0
        agent.session_hold_mode = bool(data.get("session_hold_mode"))
        agent.one_m_fee_hold = bool(data.get("one_m_fee_hold"))
        # Never restore as frozen — force re-evaluate connectivity after boot
        agent.connectivity_frozen = False
        agent.freeze_reason = None
        agent._ai_fail_streak = 0
        agent._last_feed_ts = time.time()

        if data.get("active_pair"):
            agent.active_pair = str(data["active_pair"])
        if isinstance(data.get("watchlist"), list):
            agent.watchlist = [str(p) for p in data["watchlist"] if p]
        if isinstance(data.get("momentum_fire_pairs"), list):
            agent.momentum_fire_pairs = [str(p) for p in data["momentum_fire_pairs"] if p]
        if data.get("timeframe_seconds"):
            agent.timeframe_seconds = int(data["timeframe_seconds"])
        if data.get("trade_seq") is not None:
            agent.trade_seq = max(int(agent.trade_seq or 0), int(data["trade_seq"] or 0))

        # Boot overlay: never flash on browser refresh / container restore.
        saved_until = float(data.get("boot_ui_until") or 0)
        gate_ready = bool(data.get("momentum_gate_ready"))
        if agent.is_active and (agent.watchlist or agent.momentum_fire_pairs or gate_ready):
            agent.momentum_gate_ready = True
            agent.boot_ui_until = 0.0
            # Start hourly soft-restart clock from restore (don't fire immediately).
            agent.engine_armed_at = time.time()
        elif saved_until > time.time() and not gate_ready:
            # Mid-boot crash — keep short remaining only
            agent.boot_ui_until = saved_until
            agent.momentum_gate_ready = False
            agent.engine_armed_at = time.time()
        else:
            agent.boot_ui_until = 0.0
            agent.momentum_gate_ready = gate_ready
            if agent.is_active:
                agent.engine_armed_at = time.time()

        trades = data.get("trades") or []
        if isinstance(trades, list):
            agent.trades = [t for t in trades if isinstance(t, dict)]
        hist = data.get("trade_history") or []
        if isinstance(hist, list):
            agent.trade_history = [t for t in hist if isinstance(t, dict)]

        agent.ai_season_id = data.get("ai_season_id")
        agent.ai_season_start_capital = data.get("ai_season_start_capital")
        agent.ai_season_started_at = data.get("ai_season_started_at")
        agent.ai_season_end_reason = data.get("ai_season_end_reason")
        agent.session_stats_frozen = bool(data.get("session_stats_frozen"))
        snap = data.get("session_stats_snapshot")
        if isinstance(snap, dict):
            agent.session_stats_snapshot = snap

        if data.get("risk_level_pct") is not None:
            agent.risk_level_pct = float(data["risk_level_pct"])
        if data.get("max_concurrent_trades") is not None:
            agent.max_concurrent_trades = int(data["max_concurrent_trades"])
        if data.get("daily_profit_target_pct") is not None:
            agent.daily_profit_target_pct = float(data["daily_profit_target_pct"])
        agent.daily_target_reached = bool(data.get("daily_target_reached"))

        if data.get("current_capital") is not None:
            agent.current_capital = float(data["current_capital"])
        if data.get("starting_capital") is not None:
            agent.starting_capital = float(data["starting_capital"])
        prices = data.get("pair_prices")
        if isinstance(prices, dict) and prices:
            agent.pair_prices = {str(k): float(v) for k, v in prices.items() if v is not None}
        if data.get("current_price"):
            agent.current_price = float(data["current_price"])

        summary = {
            "restored": True,
            "trades": len(agent.trades),
            "is_active": bool(agent.is_active),
            "hold": bool(agent.session_hold_mode),
            "pair": agent.active_pair,
            "saved_at": data.get("saved_at"),
        }
        print(
            f"[ENGINE RUNTIME] Restored: active={summary['is_active']} "
            f"open_trades={summary['trades']} pair={summary['pair']}"
        )
    except Exception as exc:
        print(f"[ENGINE RUNTIME] restore error: {exc}")
        summary["error"] = str(exc)
    return summary
