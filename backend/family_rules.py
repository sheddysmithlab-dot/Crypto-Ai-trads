"""Family engine rules — cache + lookup for self-improving playbook.

All brain candle families can seed MySQL `family_engine_rules` and override
OF floors / candle-soft / SL-TP. Labels match brain.PATTERNS family groups.
"""
from __future__ import annotations

import time
from typing import Any, Optional

import trade_db

# Canonical family labels (space-normalized) — match brain.PATTERNS + training UI.
ALL_CANDLE_FAMILIES = frozenset({
    "doji",
    "engulfing",
    "engulfing combo",
    "pin bar",
    "inside bar",
    "harami combo",
    "star",
    "tweezer",
    "belt hold",
    "piercing",
    "soldiers",
    "crows",
    "kicker",
    "separating lines",
    "three methods",
    "three line strike",
    "meeting lines",
    "ladder",
    "swallow",
})

# Kept as alias for older callers; all candle families are first-class now.
PILOT_FAMILIES = ALL_CANDLE_FAMILIES
DB_FAMILIES = ALL_CANDLE_FAMILIES

# Cache: (family, tf) -> rule dict; refresh every TTL or on invalidate.
_CACHE: dict[tuple[str, str], dict] = {}
_CACHE_ALL: list[dict] | None = None
_CACHE_TS = 0.0
_CACHE_TTL_SEC = 60.0


def invalidate_cache() -> None:
    global _CACHE, _CACHE_ALL, _CACHE_TS
    _CACHE = {}
    _CACHE_ALL = None
    _CACHE_TS = 0.0


def _refresh_if_stale() -> None:
    global _CACHE, _CACHE_ALL, _CACHE_TS
    now = time.time()
    if _CACHE_ALL is not None and (now - _CACHE_TS) < _CACHE_TTL_SEC:
        return
    rows = trade_db.fetch_family_rules()
    if not rows:
        # Keep last good cache (MySQL down / circuit) — avoid thrashing empty.
        if _CACHE_ALL is not None:
            _CACHE_TS = now
            return
        _CACHE_ALL = []
        _CACHE = {}
        _CACHE_TS = now
        return
    _CACHE_ALL = rows
    _CACHE = {}
    for row in rows:
        fam = str(row.get("family") or "").strip().lower()
        tf = str(row.get("timeframe_key") or "").strip().lower()
        if fam and tf:
            _CACHE[(fam, tf)] = dict(row)
    _CACHE_TS = now


def normalize_family(name: str | None) -> str:
    return (name or "").strip().lower().replace("_", " ").replace("-", " ")


def _canonical_family(family: str | None) -> str:
    fam = normalize_family(family)
    if not fam:
        return ""
    if fam in ALL_CANDLE_FAMILIES:
        return fam
    # Combos / multi-word before generic aliases
    if "engulf" in fam and "combo" in fam:
        return "engulfing combo"
    if "harami" in fam and "combo" in fam:
        return "harami combo"
    if "three method" in fam:
        return "three methods"
    if "three line" in fam:
        return "three line strike"
    if "separating" in fam:
        return "separating lines"
    if "belt" in fam:
        return "belt hold"
    if "meeting" in fam:
        return "meeting lines"
    if "swallow" in fam:
        return "swallow"
    if "ladder" in fam:
        return "ladder"
    if "tweezer" in fam:
        return "tweezer"
    if "soldier" in fam:
        return "soldiers"
    if "crow" in fam:
        return "crows"
    if "kicker" in fam:
        return "kicker"
    if "pierc" in fam or "dark cloud" in fam:
        return "piercing"
    if "star" in fam or "abandoned baby" in fam:
        return "star"
    if "engulf" in fam:
        return "engulfing"
    if "doji" in fam:
        return "doji"
    if "inside" in fam or fam == "harami":
        return "inside bar"
    if "pin" in fam or "hammer" in fam or "shooting" in fam or "hanging" in fam:
        return "pin bar"
    return fam


def resolve_family(
    pattern: str | None = None,
    strategy: str | None = None,
) -> Optional[str]:
    """Map pattern/strategy string → knowledge family label (doji, engulfing, …)."""
    pat = (pattern or "").strip()
    if pat:
        try:
            import brain as _b
            info = _b.pattern_info(pat)
            if not info:
                key = pat.lower().replace(" ", "_").replace("-", "_")
                info = _b.pattern_info(key)
            if info and info.get("family"):
                return _canonical_family(str(info["family"])) or None
        except Exception:
            pass
        low = pat.lower().replace("-", "_")
        # Strip OF/structure suffixes for family resolve
        base = low.split("+", 1)[0].strip()
        if base.startswith("candle_"):
            base = base[7:]
        hit = _canonical_family(base.replace("_", " "))
        if hit in ALL_CANDLE_FAMILIES:
            return hit
        if "doji" in low:
            return "doji"
        if "engulf" in low and "combo" in low:
            return "engulfing combo"
        if "engulf" in low:
            return "engulfing"
        if "hammer" in low or "shooting" in low or "hanging" in low or "pin" in low:
            return "pin bar"
        if "three_inside" in low or "harami_combo" in low:
            return "harami combo"
        if "inside" in low or "harami" in low:
            return "inside bar"
        if "tweezer" in low:
            return "tweezer"
        if "morning_star" in low or "evening_star" in low or "abandoned_baby" in low:
            return "star"
        if "belt" in low:
            return "belt hold"
        if "separating" in low:
            return "separating lines"
        if "piercing" in low or "dark_cloud" in low:
            return "piercing"
        if "soldier" in low:
            return "soldiers"
        if "crow" in low:
            return "crows"
        if "kicker" in low:
            return "kicker"

    strat = (strategy or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not strat:
        return None
    if "engulf" in strat:
        return "engulfing"
    if "doji" in strat:
        return "doji"
    if "pin" in strat:
        return "pin bar"
    if "inside" in strat:
        return "inside bar"
    if "tweezer" in strat:
        return "tweezer"
    if "belt" in strat:
        return "belt hold"
    if "star" in strat:
        return "star"
    if strat in ("classic_pattern",):
        # classic_pattern covers star/soldiers/doji/… — need pattern for exact family
        return None
    return None


def is_pilot_family(family: str | None) -> bool:
    """True when this family uses MySQL rule overrides (seeded or any DB row)."""
    fam = _canonical_family(family)
    if not fam:
        return False
    if fam in DB_FAMILIES:
        return True
    _refresh_if_stale()
    return any(f == fam for (f, _) in _CACHE.keys())


def get_rule(family: str | None, timeframe_key: str | None) -> Optional[dict]:
    if not family:
        return None
    fam = _canonical_family(family)
    if not fam:
        return None
    tf = (timeframe_key or "1m").strip().lower() or "1m"
    _refresh_if_stale()
    hit = _CACHE.get((fam, tf))
    if hit:
        return hit
    # Fallback: any TF for this family (candle_soft / lesson)
    for (f, _), row in _CACHE.items():
        if f == fam:
            return row
    return None


def list_rules() -> list[dict]:
    _refresh_if_stale()
    return list(_CACHE_ALL or [])


def effective_of_floor(
    timeframe_key: str | None,
    pattern: str | None = None,
    *,
    brain_strategy: str | None = None,
    family: str | None = None,
) -> Optional[float]:
    """DB min_of_score when family has a rule row, else None (caller uses env default)."""
    fam = family or resolve_family(pattern, brain_strategy)
    rule = get_rule(fam, timeframe_key)
    if not rule or rule.get("min_of_score") is None:
        return None
    try:
        return float(rule["min_of_score"])
    except (TypeError, ValueError):
        return None


def effective_brain_floors(
    timeframe_key: str | None,
    *,
    family: str | None = None,
    pattern: str | None = None,
    brain_strategy: str | None = None,
) -> tuple[Optional[float], Optional[float]]:
    """Return (min_brain_score, min_rr) overrides or (None, None)."""
    fam = family or resolve_family(pattern, brain_strategy)
    rule = get_rule(fam, timeframe_key)
    if not rule:
        return None, None
    min_sc = rule.get("min_brain_score")
    min_rr = rule.get("min_rr")
    try:
        sc = float(min_sc) if min_sc is not None else None
    except (TypeError, ValueError):
        sc = None
    try:
        rr = float(min_rr) if min_rr is not None else None
    except (TypeError, ValueError):
        rr = None
    return sc, rr


def effective_candle_soft(
    *,
    family: str | None = None,
    pattern: str | None = None,
    brain_strategy: str | None = None,
    timeframe_key: str | None = None,
) -> Optional[bool]:
    fam = family or resolve_family(pattern, brain_strategy)
    rule = get_rule(fam, timeframe_key or "1m")
    if not rule or rule.get("candle_soft") is None:
        return None
    return bool(int(rule.get("candle_soft") or 0))


def effective_sl_tp_pct(
    *,
    family: str | None,
    timeframe_key: str | None,
) -> tuple[Optional[float], Optional[float]]:
    """Return (sl_pct, tp_pct) for auto arm when set on DB rule."""
    rule = get_rule(family, timeframe_key)
    if not rule:
        return None, None
    sl = rule.get("sl_pct")
    tp = rule.get("tp_pct")
    try:
        sl_f = float(sl) if sl is not None else None
    except (TypeError, ValueError):
        sl_f = None
    try:
        tp_f = float(tp) if tp is not None else None
    except (TypeError, ValueError):
        tp_f = None
    return sl_f, tp_f


def playbook_lines(
    *,
    family: str | None,
    timeframe_key: str | None,
    pattern: str | None = None,
) -> list[str]:
    """Short lines for LLM YES/NO brief."""
    fam = family or resolve_family(pattern, None)
    if not fam:
        return []
    rule = get_rule(fam, timeframe_key)
    if not rule:
        return []
    lines = [f"FAMILY PLAYBOOK ({fam} / {timeframe_key or '?'}):"]
    if rule.get("min_of_score") is not None:
        lines.append(f"- min OF score: {float(rule['min_of_score']):.0f}")
    if rule.get("min_brain_score") is not None:
        lines.append(f"- min brain score: {float(rule['min_brain_score']):.1f}")
    if rule.get("min_rr") is not None:
        lines.append(f"- min R:R: {float(rule['min_rr']):.2f}")
    sc = int(rule.get("sample_count") or 0)
    wr = rule.get("win_rate")
    if sc > 0 and wr is not None:
        lines.append(f"- history: win_rate={float(wr)*100:.1f}% over {sc} samples (v{rule.get('version', 1)})")
    lesson = (rule.get("lesson_text") or "").strip()
    if lesson:
        lines.append(f"- lesson: {lesson[:400]}")
    return lines


def format_candle_trade_label(
    *,
    family: str | None = None,
    candle_pattern: str | None = None,
    of_pattern: str | None = None,
) -> str:
    """Human trade label: prefer candle pattern; fall back to family / OF."""
    candle = (candle_pattern or "").strip()
    fam = _canonical_family(family) if family else ""
    if candle and not candle.upper().startswith(("FAKE_", "BUY_TRAP", "SELL_TRAP", "REVERSAL_", "EXHAUSTION", "ABSORPTION", "IMBALANCE", "QUALIFIED_", "RAW_", "STRUCTURE_", "BALANCED", "NONE")):
        if "+" in candle and any(
            t in candle.upper()
            for t in ("FAKE_BREAKOUT", "BUY_TRAP", "SELL_TRAP", "STOP_HUNT", "STRUCTURE_")
        ):
            pass  # OF composite — prefer family/candle below
        else:
            nice = candle.replace("_", " ").replace("-", " ").strip()
            if fam and fam.lower() not in nice.lower():
                return f"{fam}: {nice}"
            return nice
    if fam:
        return fam
    ofp = (of_pattern or "").strip()
    if ofp:
        return ofp.replace("_", " ")
    return "candle pattern"


def event_context_from_detect(detect: dict | None) -> dict[str, Any]:
    d = detect or {}
    of_trap = d.get("orderflow_trap") if isinstance(d.get("orderflow_trap"), dict) else {}
    return {
        "pattern": d.get("pattern"),
        "candle_pattern": d.get("candle_pattern"),
        "family": d.get("family"),
        "strategy": d.get("strategy"),
        "score": d.get("score"),
        "confidence": d.get("confidence"),
        "brain_verdict": d.get("brain_verdict"),
        "market_structure": d.get("market_structure"),
        "trap_type": d.get("trap_type"),
        "ai_confirmation": d.get("ai_confirmation"),
        "of_long": of_trap.get("long_score"),
        "of_short": of_trap.get("short_score"),
        "of_pattern": of_trap.get("pattern") or d.get("of_pattern"),
        "of_signal": of_trap.get("final_signal"),
        "reason": (d.get("reason") or "")[:300],
    }
