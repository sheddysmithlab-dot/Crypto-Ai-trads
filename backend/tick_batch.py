"""0s tick batches: 7-coin slices, OF score ≥ 40 taker fire.

Watchlist remainder (n % 7) is skipped this scan and leads the next slice.
Exits (not 1m path SL/TP):
  - take a trade once it is net green after roundtrip fees
  - hard-cut a bleed at −1.0% gross (stops a −7% hostage)
  - if 2+ in the same batch are still open and the leftover book is net > 0, exit-all
"""
from __future__ import annotations

import time
from typing import Any

from timeframe_profiles import is_tick_tf

TICK_TF = "0s"
TICK_BATCH_SIZE = 7
TICK_SCORE_MIN = 40.0
TICK_SCORE_TF = "1m"  # OF score source (Bybit has no 0s kline)
# Circuit breaker — not the 1m −0.50/−0.70 trail.
TICK_HARD_LOSS_PCT = 1.0
# Scratch filter: must clear fees by at least this many USD.
TICK_MIN_NET_USD = 0.01


def slice_watchlist(
    pairs: list[str],
    cursor: int,
    batch_size: int = TICK_BATCH_SIZE,
) -> tuple[list[list[str]], int]:
    """Take floor(n/7)*7 coins from cursor (wrap). Return batches + next cursor."""
    wl = [str(p).strip() for p in (pairs or []) if str(p).strip()]
    n = len(wl)
    if n < batch_size:
        return [], int(cursor or 0) % max(n, 1) if n else 0
    usable = (n // batch_size) * batch_size
    start = int(cursor or 0) % n
    ordered = [wl[(start + i) % n] for i in range(usable)]
    batches = [
        ordered[i : i + batch_size]
        for i in range(0, usable, batch_size)
    ]
    next_cursor = (start + usable) % n
    return batches, next_cursor


def pick_tick_side(detect: dict | None) -> tuple[str | None, float]:
    """LONG/SHORT when OF side score ≥ 40. Dual-gate fail still exposes OF scores."""
    d = detect or {}
    of = d.get("orderflow_trap") or {}
    try:
        long_s = float(of.get("long_score") or 0)
    except (TypeError, ValueError):
        long_s = 0.0
    try:
        short_s = float(of.get("short_score") or 0)
    except (TypeError, ValueError):
        short_s = 0.0
    action = str(d.get("action") or "").strip().upper()
    if action == "BUY" and long_s >= TICK_SCORE_MIN:
        return "LONG", long_s
    if action == "SELL" and short_s >= TICK_SCORE_MIN:
        return "SHORT", short_s
    if long_s >= TICK_SCORE_MIN and long_s >= short_s:
        return "LONG", long_s
    if short_s >= TICK_SCORE_MIN:
        return "SHORT", short_s
    return None, max(long_s, short_s)


def pair_has_open_tick(agent: Any, pair: str) -> bool:
    want = (pair or "").strip()
    if not want:
        return False
    for t in getattr(agent, "trades", []) or []:
        if not is_tick_tf(t.get("timeframe_key")):
            continue
        if (t.get("pair") or "").strip() == want:
            return True
    return False


def _roundtrip_fees_usd(agent: Any, trade: dict) -> tuple[float, float]:
    """(gross_usd, entry_fee + estimated taker exit)."""
    m = agent._trade_metrics(trade, for_close=False)
    gross = float(m.get("gross_usd") or 0)
    entry_fee = float(m.get("entry_fee_usd") or trade.get("entry_fee_usd") or 0)
    notional = float(trade.get("position_size") or 0)
    try:
        from main import bybit_api
        exit_pct = abs(float(bybit_api.get_taker_fee_pct()))
    except Exception:
        exit_pct = abs(float(m.get("exit_fee_pct") or 0)) or 0.0649
    est_exit = notional * (exit_pct / 100.0) if notional > 0 else 0.0
    return gross, entry_fee + est_exit


def tick_trade_exit_reason(agent: Any, trade: dict) -> str | None:
    """Per-trade 0s exit: net TP after fees, or −1% hard cut."""
    gross, fees = _roundtrip_fees_usd(agent, trade)
    m = agent._trade_metrics(trade, for_close=False)
    try:
        gross_pct = float(m.get("gross_pct") or 0)
    except (TypeError, ValueError):
        gross_pct = 0.0
    if gross_pct <= -TICK_HARD_LOSS_PCT + 1e-9:
        return (
            f"TICK_HARD_STOP | {gross_pct:.2f}% <= -{TICK_HARD_LOSS_PCT:.2f}%"
        )
    net = gross - fees
    if net > TICK_MIN_NET_USD:
        return f"TICK_NET_TP | net ${net:.2f} after fees ({gross_pct:+.2f}%)"
    return None


def book_should_exit(agent: Any, trades: list[dict]) -> bool:
    """Leftover batch is net green after estimated roundtrip fees."""
    if len(trades) < 2:
        return False
    net = 0.0
    for t in trades:
        gross, fees = _roundtrip_fees_usd(agent, t)
        net += gross - fees
    return net > TICK_MIN_NET_USD


def prune_finished_batches(agent: Any) -> None:
    """Drop assigned batches that opened 7 and are all closed."""
    assigned = dict(getattr(agent, "tick_assigned_batches", {}) or {})
    if not assigned:
        return
    open_ids = {
        t.get("batch_id")
        for t in (getattr(agent, "trades", []) or [])
        if is_tick_tf(t.get("timeframe_key")) and t.get("batch_id")
    }
    opened: dict[str, set] = {}
    for t in list(getattr(agent, "trades", []) or []) + list(getattr(agent, "trade_history", []) or []):
        if not is_tick_tf(t.get("timeframe_key")):
            continue
        bid = t.get("batch_id")
        if not bid:
            continue
        opened.setdefault(str(bid), set()).add(t.get("id"))
    keep = {}
    for bid, pairs in assigned.items():
        key = str(bid)
        n_opened = len(opened.get(key, ()))
        if n_opened >= TICK_BATCH_SIZE and key not in open_ids:
            continue
        keep[key] = list(pairs)
    agent.tick_assigned_batches = keep


def ensure_assigned_batches(agent: Any, scan_pairs: list[str]) -> list[tuple[str, list[str]]]:
    """Reuse live assignment; else slice watchlist and advance leftover cursor."""
    prune_finished_batches(agent)
    assigned = dict(getattr(agent, "tick_assigned_batches", {}) or {})
    if assigned:
        return [(bid, list(pairs)) for bid, pairs in assigned.items()]
    batches, next_cursor = slice_watchlist(scan_pairs, int(getattr(agent, "tick_batch_cursor", 0) or 0))
    if not batches:
        return []
    seq = int(getattr(agent, "tick_batch_seq", 0) or 0) + 1
    agent.tick_batch_seq = seq
    season = str(getattr(agent, "ai_season_id", None) or "s")
    fresh: dict[str, list[str]] = {}
    out: list[tuple[str, list[str]]] = []
    for i, coins in enumerate(batches):
        bid = f"0s-{season}-{seq}-{i}"
        fresh[bid] = list(coins)
        out.append((bid, list(coins)))
    agent.tick_assigned_batches = fresh
    agent.tick_batch_cursor = next_cursor
    print(
        f"[0S TICK] assigned {len(out)} batch(es) × {TICK_BATCH_SIZE} · "
        f"cursor→{next_cursor} · watch={len(scan_pairs)}"
    )
    return out


def _is_open_tick(trade: dict) -> bool:
    if not trade or trade.get("source") == "manual":
        return False
    return bool(
        is_tick_tf(trade.get("timeframe_key"))
        or trade.get("exit_mode") == "tick_batch"
    )


def close_tick_batches_if_ready(agent: Any) -> int:
    """Take net-green ticks, hard-cut bleeds, then sweep a leftover green book."""
    close_ids: set = set()
    closed_n = 0

    def _close(trade: dict, reason: str) -> None:
        nonlocal closed_n
        metrics = agent._trade_metrics(trade)
        if agent._close_single_trade(trade, metrics, reason):
            closed_n += 1
            close_ids.add(trade.get("id"))
            print(f"[0S TICK] {reason} #{trade.get('id')} {trade.get('pair')}")

    for trade in list(getattr(agent, "trades", []) or []):
        if not _is_open_tick(trade):
            continue
        reason = tick_trade_exit_reason(agent, trade)
        if reason:
            _close(trade, reason)

    groups: dict[str, list[dict]] = {}
    for t in list(getattr(agent, "trades", []) or []):
        if t.get("id") in close_ids or not _is_open_tick(t):
            continue
        bid = t.get("batch_id")
        if not bid:
            continue
        groups.setdefault(str(bid), []).append(t)

    for bid, group in groups.items():
        if not book_should_exit(agent, group):
            continue
        reason = f"TICK_BATCH_BOOK_EXIT | {bid} | leftover net > 0"
        for trade in group:
            if trade.get("id") in close_ids:
                continue
            _close(trade, reason)

    if closed_n:
        agent.trades = [t for t in agent.trades if t.get("id") not in close_ids]
        prune_finished_batches(agent)
        agent.persist_runtime(force=True)
    return closed_n


async def run_tick_scan(client: Any, agent: Any) -> None:
    """Score assigned 7-batches on 1m OF; taker-fire when score ≥ 40."""
    if not getattr(agent, "is_active", False) or getattr(agent, "emergency_triggered", False):
        return
    if getattr(agent, "connectivity_frozen", False):
        return
    scan_pairs = list(agent.get_scan_pairs() or [])
    work = ensure_assigned_batches(agent, scan_pairs)
    if not work:
        return

    from brain_adapter import evaluate_live_entry_async
    from main import (
        compute_auto_trade_plan,
        fetch_closed_candle_history,
        get_bybit_symbol,
        notifications,
        system_log,
    )

    for batch_id, coins in work:
        for pair in coins:
            if pair_has_open_tick(agent, pair):
                continue
            bybit_symbol = get_bybit_symbol(pair)
            if not bybit_symbol:
                continue
            try:
                candles = await fetch_closed_candle_history(
                    client, bybit_symbol, TICK_SCORE_TF, limit=80
                )
            except Exception as exc:
                print(f"[0S TICK] kline fail {pair}: {exc}")
                continue
            try:
                detect = await evaluate_live_entry_async(
                    candles,
                    TICK_SCORE_TF,
                    pair=pair,
                    account_balance=float(agent.get_trading_capital_base() or agent.current_capital or 0),
                    risk_pct=0.005,
                )
            except Exception as exc:
                print(f"[0S TICK] score fail {pair}: {exc}")
                continue
            side, score = pick_tick_side(detect)
            if not side:
                continue
            mark = agent.mark_price_for(pair) or (detect.get("entry") if detect else None)
            if not mark:
                continue
            agent.set_pair_mark(pair, float(mark))
            plan = compute_auto_trade_plan(agent, price=float(mark), pair=pair)
            if plan is None:
                print(f"[0S TICK] size skip {pair}")
                continue
            trade = agent.open_trade(
                side=side,
                reason=f"0s tick OF={score:.1f}≥{TICK_SCORE_MIN:.0f} batch {batch_id}",
                source="auto",
                position_size_usd=plan["position_usd"],
                qty=plan["qty"],
                entry_price=float(mark),
                bybit_symbol=bybit_symbol,
                pattern=detect.get("pattern") or "TICK_OF",
                signal_candle_time=int(time.time() * 1000),
                detect_candle_time=int(time.time() * 1000),
                taapi_action="BUY" if side == "LONG" else "SELL",
                pair=pair,
                timeframe_key=TICK_TF,
                family=detect.get("family"),
                score=score,
                confidence=detect.get("confidence"),
                strategy="tick_batch",
                brain_verdict=detect.get("brain_verdict"),
                train_context=detect,
                entry_liquidity="taker",
                batch_id=batch_id,
            )
            if not trade:
                print(
                    f"[0S TICK] fire skip {pair}: "
                    f"{getattr(agent, 'last_open_skip_reason', None)}"
                )
                continue
            trade["batch_id"] = batch_id
            trade["exit_mode"] = "tick_batch"
            print(
                f"[0S TICK] FIRE {side} {pair} score={score:.1f} "
                f"batch={batch_id} #{trade.get('id')}"
            )
            try:
                system_log.push_agent_chat(
                    f"0S FIRE {side} {pair} OF={score:.1f} batch {batch_id}",
                    status="fired",
                    details={
                        "pair": pair,
                        "side": side,
                        "score": score,
                        "batch_id": batch_id,
                    },
                )
            except Exception:
                pass
            try:
                notifications.push(
                    f"0s tick {side} {pair} · score {score:.0f} · {batch_id}",
                    "success",
                )
            except Exception:
                pass
    agent.persist_runtime()
