"""Post-close family analyzer — outcomes, fault tags, capped rule updates."""
from __future__ import annotations

import time
from typing import Any, Optional

import family_rules
import trade_db

MIN_SAMPLES = int(__import__("os").environ.get("FAMILY_TRAIN_MIN_SAMPLES", "20"))
MAX_OF_DELTA = float(__import__("os").environ.get("FAMILY_TRAIN_MAX_OF_DELTA", "5"))
OF_FLOOR_MIN = float(__import__("os").environ.get("FAMILY_TRAIN_OF_FLOOR_MIN", "40"))
OF_FLOOR_MAX = float(__import__("os").environ.get("FAMILY_TRAIN_OF_FLOOR_MAX", "90"))
RETRAIN_EVERY = int(__import__("os").environ.get("FAMILY_TRAIN_EVERY_N", "20"))

# Unlimited Cursor mode: looser auto rule caps (agent also edits code freely).
if (__import__("os").environ.get("CURSOR_AI_UNLIMITED") or "1").strip().lower() in (
    "1", "true", "yes", "on",
):
    MIN_SAMPLES = int(__import__("os").environ.get("FAMILY_TRAIN_MIN_SAMPLES", "5"))
    MAX_OF_DELTA = float(__import__("os").environ.get("FAMILY_TRAIN_MAX_OF_DELTA", "15"))
    RETRAIN_EVERY = int(__import__("os").environ.get("FAMILY_TRAIN_EVERY_N", "5"))


def _outcome_from_metrics(metrics: dict) -> str:
    try:
        gross = float(metrics.get("gross_pct") or 0)
    except (TypeError, ValueError):
        return "unknown"
    if gross > 0.05:
        return "win"
    if gross < -0.05:
        return "loss"
    return "breakeven"


def _fault_tags(trade: dict, metrics: dict, outcome: str) -> list[str]:
    tags: list[str] = []
    reason = str(trade.get("closed_reason") or metrics.get("closed_reason") or "").lower()
    score = trade.get("score")
    family = trade.get("family")
    tf = trade.get("timeframe_key") or "1m"
    rule = family_rules.get_rule(family, tf) if family else None
    floor = float(rule["min_of_score"]) if rule and rule.get("min_of_score") is not None else None

    peak = trade.get("peak_gross_pct")
    trough = trade.get("trough_gross_pct")
    try:
        peak_f = float(peak) if peak is not None else None
    except (TypeError, ValueError):
        peak_f = None
    try:
        trough_f = float(trough) if trough is not None else None
    except (TypeError, ValueError):
        trough_f = None

    if outcome == "loss":
        if peak_f is not None and peak_f >= 0.25:
            tags.append("gave_back_profit")
        if trough_f is not None and trough_f <= -0.5 and "sl" in reason:
            tags.append("sl_hit")
        if floor is not None and score is not None:
            try:
                if float(score) < floor + 5:
                    tags.append("weak_score")
            except (TypeError, ValueError):
                pass
        if "flip" in reason:
            tags.append("flip_exit")
        if "emergency" in reason:
            tags.append("emergency")
    if outcome == "win" and peak_f is not None:
        try:
            gross = float(metrics.get("gross_pct") or 0)
            if peak_f - gross >= 0.2:
                tags.append("left_profit_on_table")
        except (TypeError, ValueError):
            pass
    opened = trade.get("opened_at")
    closed = time.time()
    try:
        if opened and (closed - float(opened)) < 15:
            tags.append("very_fast_exit")
        if opened and (closed - float(opened)) > 3600:
            tags.append("long_hold")
    except (TypeError, ValueError):
        pass
    return tags


def _lesson_text(family: str, outcome: str, tags: list[str], metrics: dict) -> str:
    gross = metrics.get("gross_pct")
    parts = [f"{family} {outcome}"]
    if gross is not None:
        try:
            parts.append(f"gross={float(gross):+.2f}%")
        except (TypeError, ValueError):
            pass
    if tags:
        parts.append("tags=" + ",".join(tags[:6]))
    return "; ".join(parts)[:500]


def on_trade_closed(trade: dict, metrics: dict, reason: str = "") -> Optional[dict]:
    """Finalize train event + maybe retrain family rules. Returns update info or None."""
    family = trade.get("family") or family_rules.resolve_family(
        trade.get("pattern"), trade.get("strategy")
    )
    if not family:
        return None

    outcome = _outcome_from_metrics(metrics)
    tags = _fault_tags(trade, metrics, outcome)
    lesson = _lesson_text(family, outcome, tags, metrics)
    mfe = trade.get("peak_gross_pct")
    mae = trade.get("trough_gross_pct")
    try:
        net = float(metrics.get("net_usd") or 0)
    except (TypeError, ValueError):
        net = None

    try:
        tid = int(trade.get("id"))
    except (TypeError, ValueError):
        tid = None
    if tid is not None:
        trade_db.finalize_train_event_for_trade(
            tid,
            outcome=outcome,
            closed_reason=reason or trade.get("closed_reason"),
            mfe_pct=float(mfe) if mfe is not None else None,
            mae_pct=float(mae) if mae is not None else None,
            net_pnl_usd=net,
            fault_tags=tags,
            lesson=lesson,
        )

    if not family_rules.is_pilot_family(family):
        return None

    tf = (trade.get("timeframe_key") or "1m").strip().lower() or "1m"
    return maybe_retrain_family(family, tf)


def maybe_retrain_family(family: str, timeframe_key: str) -> Optional[dict]:
    """Auto-adjust min_of_score with safety caps when enough samples exist."""
    if not family_rules.is_pilot_family(family):
        return None
    rule = family_rules.get_rule(family, timeframe_key) or trade_db.get_family_rule(
        family, timeframe_key
    )
    if not rule:
        return None
    if int(rule.get("locked") or 0):
        return None

    events = trade_db.fetch_closed_train_events(
        family=family, timeframe_key=timeframe_key, limit=max(MIN_SAMPLES * 3, 60)
    )
    n = len(events)
    if n < MIN_SAMPLES:
        # Still refresh sample stats lightly
        if n > 0:
            wins = sum(1 for e in events if e.get("outcome") == "win")
            wr = wins / n
            trade_db.update_family_rule(
                family, timeframe_key, sample_count=n, win_rate=wr
            )
            family_rules.invalidate_cache()
        return None

    # Retrain only every RETRAIN_EVERY closed samples (by sample_count milestone)
    prev_count = int(rule.get("sample_count") or 0)
    if n < prev_count + RETRAIN_EVERY and prev_count >= MIN_SAMPLES:
        wins = sum(1 for e in events if e.get("outcome") == "win")
        trade_db.update_family_rule(
            family, timeframe_key, sample_count=n, win_rate=wins / n
        )
        family_rules.invalidate_cache()
        return None

    wins = sum(1 for e in events if e.get("outcome") == "win")
    losses = sum(1 for e in events if e.get("outcome") == "loss")
    wr = wins / n if n else 0.0

    # Avg R proxy from net_pnl_usd sign magnitude (coarse)
    pnls = []
    for e in events:
        try:
            pnls.append(float(e.get("net_pnl_usd") or 0))
        except (TypeError, ValueError):
            pass
    avg_r = (sum(pnls) / len(pnls)) if pnls else None

    weak = sum(
        1
        for e in events
        if e.get("outcome") == "loss"
        and isinstance(e.get("fault_tags"), (str, list))
        and (
            "weak_score" in (e.get("fault_tags") if isinstance(e.get("fault_tags"), list)
                             else str(e.get("fault_tags")))
        )
    )
    gave_back = sum(
        1
        for e in events
        if "gave_back_profit" in str(e.get("fault_tags") or "")
    )

    old_floor = float(rule.get("min_of_score") or 50)
    new_floor = old_floor
    lesson_extra = ""

    # Rolling window vs previous win_rate → rollback if worse after a raise
    prev_wr = rule.get("prev_win_rate")
    prev_floor = rule.get("prev_min_of_score")
    if (
        prev_wr is not None
        and prev_floor is not None
        and float(prev_floor) < old_floor
        and wr + 1e-9 < float(prev_wr) - 0.03
    ):
        new_floor = float(prev_floor)
        lesson_extra = (
            f" Rollback min_of_score {old_floor:.0f}→{new_floor:.0f} "
            f"(win_rate {wr:.1%} < prev {float(prev_wr):.1%})."
        )
    elif wr < 0.45 or weak >= max(3, n // 5):
        new_floor = min(OF_FLOOR_MAX, old_floor + min(MAX_OF_DELTA, 5.0))
        lesson_extra = (
            f" Raised OF floor {old_floor:.0f}→{new_floor:.0f} "
            f"(win_rate={wr:.1%}, weak_score_losses={weak})."
        )
    elif wr > 0.58 and losses <= n * 0.35 and old_floor > OF_FLOOR_MIN + 5:
        new_floor = max(OF_FLOOR_MIN, old_floor - min(MAX_OF_DELTA, 3.0))
        lesson_extra = (
            f" Eased OF floor {old_floor:.0f}→{new_floor:.0f} "
            f"(win_rate={wr:.1%} healthy)."
        )

    new_floor = max(OF_FLOOR_MIN, min(OF_FLOOR_MAX, new_floor))
    # Cap per-version move
    if abs(new_floor - old_floor) > MAX_OF_DELTA:
        new_floor = old_floor + MAX_OF_DELTA if new_floor > old_floor else old_floor - MAX_OF_DELTA

    base_lesson = (rule.get("lesson_text") or "").split(" | auto:")[0].strip()
    if gave_back >= max(3, n // 6):
        lesson_extra += " Prefer tighter trail / earlier BE when MFE was green."
    new_lesson = (base_lesson + (" | auto:" + lesson_extra if lesson_extra else ""))[:800]

    bumped = abs(new_floor - old_floor) >= 0.5
    fields: dict[str, Any] = {
        "sample_count": n,
        "win_rate": wr,
        "avg_r": avg_r,
        "lesson_text": new_lesson,
    }
    if bumped:
        fields["prev_min_of_score"] = old_floor
        fields["prev_win_rate"] = float(prev_wr) if prev_wr is not None else wr
        fields["min_of_score"] = new_floor
        fields["version"] = int(rule.get("version") or 1) + 1

    trade_db.update_family_rule(family, timeframe_key, **fields)
    family_rules.invalidate_cache()

    info = {
        "family": family,
        "timeframe_key": timeframe_key,
        "sample_count": n,
        "win_rate": wr,
        "old_min_of_score": old_floor,
        "new_min_of_score": new_floor if bumped else old_floor,
        "version": fields.get("version", rule.get("version")),
        "bumped": bumped,
        "lesson_extra": lesson_extra.strip(),
    }
    if bumped:
        print(
            f"[FAMILY-TRAIN] {family}/{timeframe_key} "
            f"min_of_score {old_floor:.0f}→{new_floor:.0f} "
            f"wr={wr:.1%} n={n} v{info['version']}"
        )
    return info
