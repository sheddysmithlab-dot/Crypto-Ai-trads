"""Load / apply live engine formulas from MySQL `engine_formulas`.

DB is source of truth when MySQL is connected. Code/env defaults remain fallbacks.
Call `reload_and_apply()` after `trade_db.init_db()` on startup (and after Cursor edits).
"""
from __future__ import annotations

import time
from typing import Any, Optional

import trade_db

_CACHE: dict[str, Any] = {}
_CACHE_META: dict[str, dict] = {}
_CACHE_TS = 0.0
_TTL = 30.0


def invalidate() -> None:
    global _CACHE, _CACHE_META, _CACHE_TS
    _CACHE = {}
    _CACHE_META = {}
    _CACHE_TS = 0.0


def _parse_row(row: dict) -> Any:
    vtype = (row.get("value_type") or "number").strip().lower()
    if vtype == "bool":
        n = row.get("value_num")
        if n is not None:
            return bool(float(n))
        t = str(row.get("value_text") or "").strip().lower()
        return t in ("1", "true", "yes", "on")
    if vtype == "text":
        return row.get("value_text")
    if vtype == "json":
        raw = row.get("value_json")
        if isinstance(raw, (dict, list)):
            return raw
        if isinstance(raw, str) and raw.strip():
            import json
            try:
                return json.loads(raw)
            except Exception:
                return raw
        return raw
    # number
    try:
        return float(row.get("value_num"))
    except (TypeError, ValueError):
        return None


def refresh(force: bool = False) -> dict[str, Any]:
    global _CACHE, _CACHE_META, _CACHE_TS
    now = time.time()
    if not force and _CACHE and (now - _CACHE_TS) < _TTL:
        return dict(_CACHE)
    rows = trade_db.fetch_engine_formulas()
    cache: dict[str, Any] = {}
    meta: dict[str, dict] = {}
    for row in rows:
        key = str(row.get("formula_key") or "").strip()
        if not key:
            continue
        cache[key] = _parse_row(row)
        meta[key] = dict(row)
    if cache:
        _CACHE = cache
        _CACHE_META = meta
        _CACHE_TS = now
        print(f"[ENGINE-DB] loaded {len(cache)} formulas from MySQL")
    return dict(_CACHE)


def get(key: str, default: Any = None) -> Any:
    if not _CACHE:
        refresh()
    if key in _CACHE and _CACHE[key] is not None:
        return _CACHE[key]
    return default


def get_float(key: str, default: float) -> float:
    v = get(key, default)
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def get_bool(key: str, default: bool) -> bool:
    v = get(key, default)
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(default)


def list_all() -> list[dict]:
    refresh()
    return [dict(v) for v in _CACHE_META.values()]


def apply_to_runtime() -> dict[str, Any]:
    """Patch live module globals used by the trading engine."""
    refresh(force=True)
    applied: dict[str, Any] = {}

    def _set(mod, name: str, key: str | None = None) -> None:
        k = key or name
        if k not in _CACHE or _CACHE[k] is None:
            return
        if not hasattr(mod, name):
            return
        val = _CACHE[k]
        cur = getattr(mod, name)
        if isinstance(cur, bool):
            setattr(mod, name, bool(val) if not isinstance(val, bool) else val)
        elif isinstance(cur, int) and not isinstance(cur, bool):
            try:
                setattr(mod, name, int(float(val)))
            except (TypeError, ValueError):
                return
        else:
            try:
                setattr(mod, name, float(val) if isinstance(cur, float) else val)
            except (TypeError, ValueError):
                setattr(mod, name, val)
        applied[f"{mod.__name__}.{name}"] = getattr(mod, name)

    def _set_cls(cls, name: str, key: str | None = None) -> None:
        k = key or name
        if k not in _CACHE or _CACHE[k] is None:
            return
        if not hasattr(cls, name):
            return
        val = _CACHE[k]
        cur = getattr(cls, name)
        try:
            if isinstance(cur, bool):
                setattr(cls, name, bool(val) if not isinstance(val, bool) else val)
            elif isinstance(cur, int) and not isinstance(cur, bool):
                setattr(cls, name, int(float(val)))
            else:
                setattr(cls, name, float(val))
        except (TypeError, ValueError):
            return
        applied[f"{cls.__module__}.{cls.__name__}.{name}"] = getattr(cls, name)

    try:
        import trap_orderflow_engine as toe

        for name in (
            "THR_SCORE",
            "THR_SCORE_5M",
            "THR_SCORE_1M",
            "THR_SCORE_CLASSIC_PATTERN",
            "THR_SCORE_ENGULFING",
            "THR_SCORE_INSIDE_BAR",
            "THR_SCORE_IMBALANCE_1M",
            "THR_SCORE_TRAP",
            "THR_SCORE_TRAP_5M",
            "THR_SCORE_TRAP_1M",
            "THR_PRESSURE",
            "THR_RV_VOL",
            "STRUCTURE_OPPOSITE_PENALTY",
            "THR_PRICE_EFFORT",
            "THR_UPPER_WICK",
            "THR_LOWER_WICK",
            "THR_BODY_RATIO",
            "THR_Z_ACT",
            "THR_Z_EXHAUST",
            "THR_Z_TVOL",
            "THR_FAKE_WICK",
            "THR_BREAK_ATR",
            "THR_BALANCED",
            "THR_RV_PRICE_WEAK",
            "SCORE_FLOOR_EPS",
            "LOOKBACK",
        ):
            _set(toe, name)
        if "CANDLE_ONLY_FIRE" in _CACHE:
            toe.CANDLE_ONLY_FIRE_ENABLED = get_bool("CANDLE_ONLY_FIRE", False)
            applied["trap_orderflow_engine.CANDLE_ONLY_FIRE_ENABLED"] = toe.CANDLE_ONLY_FIRE_ENABLED
        if hasattr(toe, "FLOW_LOOKBACK_CANDLES") and "LOOKBACK" in _CACHE:
            try:
                toe.FLOW_LOOKBACK_CANDLES = int(float(_CACHE["LOOKBACK"]))
                applied["trap_orderflow_engine.FLOW_LOOKBACK_CANDLES"] = toe.FLOW_LOOKBACK_CANDLES
            except (TypeError, ValueError):
                pass
    except Exception as exc:
        print(f"[ENGINE-DB] trap_orderflow apply note: {exc}")

    try:
        import main as m

        for name in (
            "PROFIT_LOCK_PCT",
            "PROFIT_TRAIL_GIVEBACK_PCT",
            "PROFIT_TRAIL_FIRST_GIVEBACK_PCT",
            "LOSS_PROTECT_PCT",
            "LOSS_BAND_PCT",
            "LOSS_RECOVERY_RETRACE_PCT",
            "LOSS_LOCK_CLEAR_PCT",
            "LOSS_PROTECT_PCT_1M",
            "LOSS_BAND_PCT_1M",
            "PROFIT_HARD_PCT_1M",
            "PROFIT_LOCK_PCT_1M",
            "FLIP_EXIT_MIN_GROSS_PCT",
            "MICRO_CAP_LOSS_ARM_PCT",
            "MICRO_CAP_LOSS_BAND_PCT",
            "MICRO_CAP_HARD_STOP_PCT",
            "MAX_CONCURRENT_TRADES_DEFAULT",
            "MAX_SAME_SIDE_AUTO_PER_PAIR",
            "MIN_CONFIRM_BODY_PCT",
            "ONE_M_CONFIRM_SKIP_TICKS",
            "ONE_M_MIN_BARS_BETWEEN_FIRES",
            "ONE_M_CONFIRM_MAX_BARS",
            "ONE_M_MAX_CONCURRENT",
            "ENGINE_BOOT_MAX_SEC",
            "RECONCILE_GRACE_SECONDS",
            "SKIP_FIRST_DETECT",
            "SKIP_FIRST_DETECT_SCALP",
        ):
            key = name
            if name == "MAX_CONCURRENT_TRADES_DEFAULT":
                key = "MAX_CONCURRENT_TRADES"
            _set(m, name, key)

        if hasattr(m, "PROFIT_LOCK_PCT") and hasattr(m, "PROFIT_TRAIL_GIVEBACK_PCT"):
            m.PATH_TP_WIDE_PCT = float(m.PROFIT_LOCK_PCT) + float(m.PROFIT_TRAIL_GIVEBACK_PCT)
            m.FIXED_EXIT_PROFIT_PCT = float(m.PROFIT_LOCK_PCT)
            m.PATH_TP_TIGHT_PCT = float(m.PROFIT_LOCK_PCT)
            m.PATH_SL_TIGHT_PCT = float(m.LOSS_PROTECT_PCT)
            m.LOSS_EMERGENCY_PCT = float(m.LOSS_BAND_PCT)
            m.PATH_SL_WIDE_PCT = float(m.LOSS_BAND_PCT)
            m.FIXED_EXIT_LOSS_PCT = float(m.LOSS_PROTECT_PCT)
            applied["main.PATH_TP_WIDE_PCT"] = m.PATH_TP_WIDE_PCT

        agent_cls = getattr(m, "AITradingAgent", None)
        if agent_cls is not None:
            for name in (
                "STRICT_EXIT_HARD_TARGET_PCT",
                "STRICT_EXIT_MIN_LOCK_PCT",
                "STRICT_EXIT_FLUCTUATION_X_PCT",
                "STRICT_EXIT_TRAIL_MULTIPLIER",
                "STRICT_EXIT_MAX_LOSS_PCT",
                "STRUCTURE_SL_BUFFER_PCT",
                "STRUCTURE_SL_MIN_DISTANCE_PCT",
                "STRUCTURE_SL_GRACE_SEC",
            ):
                _set_cls(agent_cls, name)

            hs_map = {
                "30s": "HARD_STOP_30S",
                "1m": "HARD_STOP_1M",
                "3m": "HARD_STOP_3M",
                "5m": "HARD_STOP_5M",
                "10m": "HARD_STOP_10M",
                "15m": "HARD_STOP_15M",
                "30m": "HARD_STOP_30M",
                "1h": "HARD_STOP_1H",
                "1D": "HARD_STOP_1D",
            }
            if hasattr(agent_cls, "HARD_STOP_PCT_BY_TF") and isinstance(
                agent_cls.HARD_STOP_PCT_BY_TF, dict
            ):
                updated = dict(agent_cls.HARD_STOP_PCT_BY_TF)
                for tf_key, formula_key in hs_map.items():
                    if formula_key in _CACHE and _CACHE[formula_key] is not None:
                        try:
                            updated[tf_key] = float(_CACHE[formula_key])
                        except (TypeError, ValueError):
                            pass
                agent_cls.HARD_STOP_PCT_BY_TF = updated
                applied["main.AITradingAgent.HARD_STOP_PCT_BY_TF"] = updated
    except Exception as exc:
        print(f"[ENGINE-DB] main apply note: {exc}")

    try:
        import timeframe_profiles as tfp

        profiles = get("TIMEFRAME_PROFILES")
        if isinstance(profiles, dict) and profiles:
            merged = dict(getattr(tfp, "TIMEFRAME_PROFILES", {}) or {})
            for k, v in profiles.items():
                if isinstance(v, dict):
                    merged[str(k)] = {
                        "win_rate": int(v.get("win_rate", 50)),
                        "lose_rate": int(v.get("lose_rate", 50)),
                        "capital_pct": float(v.get("capital_pct", 7.0)),
                    }
            tfp.TIMEFRAME_PROFILES = merged
            applied["timeframe_profiles.TIMEFRAME_PROFILES"] = list(merged.keys())
    except Exception as exc:
        print(f"[ENGINE-DB] timeframe_profiles apply note: {exc}")

    try:
        import family_rules as fr

        fr.invalidate_cache()
        applied["family_rules.cache"] = "invalidated"
    except Exception as exc:
        print(f"[ENGINE-DB] family_rules apply note: {exc}")

    print(f"[ENGINE-DB] applied {len(applied)} runtime knobs from MySQL")
    return applied


def reload_and_apply() -> dict[str, Any]:
    invalidate()
    return apply_to_runtime()
