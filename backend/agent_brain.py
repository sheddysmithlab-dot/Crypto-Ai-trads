"""Unified AI agent brain — pattern → Bible → ML → fire context.

Merges three knowledge packs into one decision enrichment used by auto_buy_loop
and AI confirmation. Lookups are in-RAM (microsecond).
"""
from __future__ import annotations

from typing import Any

from candlestick_bible_memory import fetch_bible, search_bible
from ml_trading_memory import fetch_ml, memory_stats as ml_stats
from volume_spread_system import PATTERN_BIBLE_KEY, PATTERN_LABELS


PIPELINE_STEPS = (
    "1_detect_candle_pattern",
    "2_read_bible_section",
    "3_ml_cost_aware_gate",
    "4_fire_trade",
)


def enrich_signal(result: dict[str, Any], *, max_bible_chars: int = 1200, max_ml_chars: int = 900) -> dict[str, Any]:
    """Attach Bible text + ML takeaways to a pattern decision (no I/O beyond RAM)."""
    out = dict(result)
    pattern = result.get("pattern")
    bible_key = result.get("bible_key") or PATTERN_BIBLE_KEY.get(str(pattern or ""), "")
    bible_hit = fetch_bible(bible_key or str(pattern or ""), max_chars=max_bible_chars) if (bible_key or pattern) else {"ok": False}
    if not bible_hit.get("ok") and pattern:
        # fallback search by label
        label = PATTERN_LABELS.get(str(pattern), str(pattern))
        found = search_bible(label, limit=1)
        if found:
            bible_hit = fetch_bible(found[0]["id"], max_chars=max_bible_chars)

    ml_hit = fetch_ml("cost aware", max_chars=max_ml_chars)
    ml_takeaways = (ml_stats().get("takeaways") or [])[:5]

    out["brain"] = {
        "pipeline": list(PIPELINE_STEPS),
        "pattern_label": PATTERN_LABELS.get(str(pattern or ""), pattern),
        "bible": {
            "ok": bool(bible_hit.get("ok")),
            "id": bible_hit.get("id"),
            "title": bible_hit.get("title"),
            "fetch_ns": bible_hit.get("fetch_ns"),
            "text": bible_hit.get("text") if bible_hit.get("ok") else None,
        },
        "ml": {
            "ok": bool(ml_hit.get("ok")),
            "id": ml_hit.get("id"),
            "title": ml_hit.get("title"),
            "fetch_ns": ml_hit.get("fetch_ns"),
            "text": ml_hit.get("text") if ml_hit.get("ok") else None,
            "takeaways": ml_takeaways,
            "gate": "cost_aware_magnitude_vs_fees",
        },
    }
    return out


def brain_chat_summary(enriched: dict[str, Any]) -> str:
    """One-line System Log summary of the brain pipeline."""
    action = enriched.get("action")
    pattern = enriched.get("pattern")
    label = PATTERN_LABELS.get(str(pattern or ""), pattern or "n/a")
    bible = (enriched.get("brain") or {}).get("bible") or {}
    strength = enriched.get("strength")
    if action in ("BUY", "SELL"):
        return (
            f"Brain: detect={label} -> Bible[{bible.get('id') or 'n/a'}] "
            f"-> ML cost-aware -> {action} (strength={strength})"
        )
    return f"Brain: no pattern — {enriched.get('reason', 'skip')}"


def strategy_system_blurb() -> str:
    return (
        "AI AGENT BRAIN PIPELINE (merged 3 PDFs):\n"
        "1) DETECT — closed-candle patterns (engulfing, pin/hammer/star, inside bar, "
        "morning/evening star, harami, tweezers, doji, soldiers/crows, belt, marubozu).\n"
        "2) BIBLE — fetch matching Candlestick Trading Bible section from RAM "
        "(pin bar / engulfing / inside bar strategies preferred).\n"
        "3) ML — cost-aware gate: only fire when remaining edge & candle range clear "
        "λ×round-trip fee hurdle (arXiv:2606.00060). Naive every-signal trading is banned.\n"
        "4) FIRE — BUY→LONG / SELL→SHORT with SL sizing + profit lock exits.\n"
        "Conflicts (bull+bear same bar) = NO_TRADE. Prefer confluence with trend/local slope."
    )
