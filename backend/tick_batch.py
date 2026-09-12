"""0s tick batches: 7-coin slices, OF score ≥ 40 taker fire.

Watchlist remainder (n % 7) is skipped this scan and leads the next slice.
Exits are BATCH-ONLY (never a single trade):
  - wait until all 7 are open
  - hard-stop the book at −1.00% combined gross
  - profit trail arms at +0.30% (no cap); giveback −0.30% from peak
    then exit-all when winner profit+Bybit fees > loser loss+Bybit fees
"""
from __future__ import annotations

import time
from typing import Any

from timeframe_profiles import is_tick_tf

TICK_TF = "0s"
TICK_BATCH_SIZE = 7
TICK_SCORE_MIN = 40.0
TICK_SCORE_TF = "1m"  # OF score source (Bybit has no 0s kline)
TICK_BATCH_HARD_PCT = 1.0
TICK_BATCH_TRAIL_ARM_PCT = 0.30
TICK_BATCH_TRAIL_GIVEBACK_PCT = 0.30


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


def pair_used_in_batch(agent: Any, pair: str, batch_id: str) -> bool:
    """One fill per coin per batch — do not re-fire after TP/hard-stop."""
    want = (pair or "").strip()
    bid = str(batch_id or "")
    if not want or not bid:
        return False
    for t in list(getattr(agent, "trades", []) or []) + list(
        getattr(agent, "trade_history", []) or []
    ):
        if not is_tick_tf(t.get("timeframe_key")):
            continue
        if (t.get("pair") or "").strip() != want:
            continue
        if str(t.get("batch_id") or "") == bid:
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


def batch_book_stats(agent: Any, trades: list[dict]) -> dict:
    """Combined 7-trade book: winner pile vs loser pile + Bybit fees, plus $weighted %."""
    profit = 0.0
    lose = 0.0
    fee_win = 0.0
    fee_lose = 0.0
    gross_usd = 0.0
    notional = 0.0
    for t in trades:
        g, fees = _roundtrip_fees_usd(agent, t)
        gross_usd += g
        notional += float(t.get("position_size") or 0)
        if g > 0:
            profit += g
            fee_win += fees
        else:
            lose += abs(g)
            fee_lose += fees
    pct = (gross_usd / notional * 100.0) if notional > 0 else 0.0
    return {
        "profit": profit,
        "lose": lose,
        "fee_win": fee_win,
        "fee_lose": fee_lose,
        "gross_usd": gross_usd,
        "notional": notional,
        "gross_pct": pct,
        "book_beats": (profit + fee_win) > (lose + fee_lose),
    }


def _batch_peaks(agent: Any) -> dict:
    peaks = getattr(agent, "tick_batch_peaks", None)
    if not isinstance(peaks, dict):
        peaks = {}
        agent.tick_batch_peaks = peaks
    return peaks


def _stamp_batch_ui(trades: list[dict], st: dict, peak: float) -> None:
    trail_line = (
        peak - TICK_BATCH_TRAIL_GIVEBACK_PCT
        if peak >= TICK_BATCH_TRAIL_ARM_PCT
        else None
    )
    for t in trades:
        t["batch_gross_pct"] = round(float(st["gross_pct"]), 4)
        t["batch_peak_gross_pct"] = round(float(peak), 4)
        t["batch_trail_line_pct"] = (
            round(float(trail_line), 4) if trail_line is not None else None
        )
        t["batch_book_ready"] = bool(st["book_beats"])


def batch_exit_reason(agent: Any, bid: str, trades: list[dict]) -> str | None:
    """All-or-nothing 7-trade exits. None until the batch is full."""
    st = batch_book_stats(agent, trades)
    pct = float(st["gross_pct"])
    peaks = _batch_peaks(agent)
    peak = float(peaks.get(bid) or 0)
    if pct > peak:
        peak = pct
        peaks[bid] = pct
    _stamp_batch_ui(trades, st, peak)
    if len(trades) < TICK_BATCH_SIZE:
        return None

    if pct <= -TICK_BATCH_HARD_PCT + 1e-9:
        return (
            f"TICK_BATCH_HARD_STOP | {bid} | "
            f"book {pct:.2f}% <= -{TICK_BATCH_HARD_PCT:.2f}%"
        )

    armed = peak >= TICK_BATCH_TRAIL_ARM_PCT - 1e-9
    if not armed:
        return None
    trail_line = peak - TICK_BATCH_TRAIL_GIVEBACK_PCT
    if pct <= trail_line + 1e-9 and st["book_beats"]:
        return (
            f"TICK_BATCH_PROFIT_TRAIL | {bid} | "
            f"peak {peak:.2f}% giveback {TICK_BATCH_TRAIL_GIVEBACK_PCT:.2f}% "
            f"now {pct:.2f}% · P+Fp > L+Fl"
        )
    return None


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
    peaks = _batch_peaks(agent)
    agent.tick_batch_peaks = {k: v for k, v in peaks.items() if k in keep}


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
    """Exit a full 7-trade batch together — never a single tick fill."""
    close_ids: set = set()
    closed_n = 0

    groups: dict[str, list[dict]] = {}
    for t in list(getattr(agent, "trades", []) or []):
        if not _is_open_tick(t):
            continue
        bid = t.get("batch_id")
        if not bid:
            continue
        groups.setdefault(str(bid), []).append(t)

    for bid, group in groups.items():
        reason = batch_exit_reason(agent, bid, group)
        if not reason:
            continue
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
            if pair_used_in_batch(agent, pair, batch_id):
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
