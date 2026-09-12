"""0s tick batches: 7-coin slices, OF score ≥ 40 taker fire, book exit-all.

Watchlist remainder (n % 7) is skipped this scan and leads the next slice.
Exit when winner gross + winner fees > loser abs gross + loser fees.
"""
from __future__ import annotations

import time
from typing import Any

from timeframe_profiles import is_tick_tf

TICK_TF = "0s"
TICK_BATCH_SIZE = 7
TICK_SCORE_MIN = 40.0
TICK_SCORE_TF = "1m"  # OF score source (Bybit has no 0s kline)


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


def book_should_exit(agent: Any, trades: list[dict]) -> bool:
    """P + Fp > L + Fl on the batch book (open marks + estimated exit fees)."""
    if len(trades) < TICK_BATCH_SIZE:
        return False
    profit = 0.0
    fee_win = 0.0
    lose = 0.0
    fee_lose = 0.0
    for t in trades:
        gross, fees = _roundtrip_fees_usd(agent, t)
        if gross > 0:
            profit += gross
            fee_win += fees
        else:
            lose += abs(gross)
            fee_lose += fees
    return (profit + fee_win) > (lose + fee_lose)


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


def close_tick_batches_if_ready(agent: Any) -> int:
    """Market-exit every trade in a 7-fill batch when P+Fp > L+Fl."""
    groups: dict[str, list[dict]] = {}
    for t in list(getattr(agent, "trades", []) or []):
        if t.get("source") == "manual":
            continue
        if not is_tick_tf(t.get("timeframe_key")):
            continue
        bid = t.get("batch_id")
        if not bid:
            continue
        groups.setdefault(str(bid), []).append(t)
    close_ids: set = set()
    closed_n = 0
    for bid, group in groups.items():
        if len(group) < TICK_BATCH_SIZE:
            continue
        if not book_should_exit(agent, group):
            continue
        reason = f"TICK_BATCH_BOOK_EXIT | {bid} | P+Fp > L+Fl"
        for trade in group:
            metrics = agent._trade_metrics(trade)
            if agent._close_single_trade(trade, metrics, reason):
                closed_n += 1
                close_ids.add(trade.get("id"))
                print(f"[0S TICK] {reason} #{trade.get('id')} {trade.get('pair')}")
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
