"""Momentum watchlist gate: keep only pairs whose MARKET avg% clears TF threshold.

Uses the same fetch_tf_move metric as the header MARKET readout.
Scan/watchlist only — does not change trade entry/exit policy.

HARD INSTRUCTION: refreshing / replacing / editing the watchlist (including every
Nth candle) must NEVER close, exit, or drop related OPEN trades. Fire list only
gates NEW entries; open positions keep path TP/SL until their own exit rules fire.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from chart_tf_move import fetch_tf_move

# Strict: avg_pct must be greater than these floors (same units as MARKET %).
MOMENTUM_MIN_AVG_PCT: dict[str, float] = {
    "1m": 0.030,
    "5m": 0.050,
    "15m": 0.15,
    "1h": 0.35,
    "1d": 5.0,
    "1D": 5.0,
}

MOMENTUM_REFRESH_EVERY_N_CANDLES = 7
# Parallel kline fetches during universe score (burst only every N candles / boot).
SCORE_CONCURRENCY = 10

# engine key (1m) → UI key for fetch_tf_move (1M)
_ENGINE_TO_UI_TF: dict[str, str] = {
    "1m": "1M",
    "30s": "1M",
    "5m": "5M",
    "15m": "15M",
    "1h": "1H",
    "1d": "1D",
    "1D": "1D",
}

ProgressCb = Callable[[int, int, str], Awaitable[None] | None]


def normalize_engine_tf(tf_key: str | None) -> str:
    raw = (tf_key or "1m").strip()
    low = raw.lower()
    if low == "1d":
        return "1D"
    return low


def ui_timeframe_for_engine(tf_key: str | None) -> str:
    eng = normalize_engine_tf(tf_key)
    return _ENGINE_TO_UI_TF.get(eng, "1M")


def momentum_threshold_pct(tf_key: str | None) -> float:
    eng = normalize_engine_tf(tf_key)
    return float(MOMENTUM_MIN_AVG_PCT.get(eng, MOMENTUM_MIN_AVG_PCT["1m"]))


async def _maybe_progress(cb: ProgressCb | None, done: int, total: int, stage: str) -> None:
    if cb is None:
        return
    try:
        ret = cb(done, total, stage)
        if asyncio.iscoroutine(ret):
            await ret
    except Exception:
        pass


async def score_universe(
    symbol_map: dict[str, str],
    engine_tf: str,
    *,
    progress_cb: ProgressCb | None = None,
    lot_ok: Callable[[str, str], bool] | None = None,
) -> list[dict[str, Any]]:
    """Score every mapped coin; returns list of {pair, symbol, avg_pct, passed}.

    Optional ``lot_ok(coin, bybit_symbol)`` filters unaffordable min lots before scoring.
    """
    ui_tf = ui_timeframe_for_engine(engine_tf)
    thr = momentum_threshold_pct(engine_tf)
    items = [(coin, sym) for coin, sym in (symbol_map or {}).items() if coin and sym]
    if lot_ok is not None:
        filtered = []
        for coin, sym in items:
            try:
                if lot_ok(coin, sym):
                    filtered.append((coin, sym))
            except Exception:
                filtered.append((coin, sym))
        items = filtered

    total = len(items)
    await _maybe_progress(progress_cb, 0, total, "scoring")
    if total == 0:
        return []

    sem = asyncio.Semaphore(SCORE_CONCURRENCY)
    done_count = 0
    lock = asyncio.Lock()

    async def _one(coin: str, bybit_symbol: str) -> dict[str, Any]:
        nonlocal done_count
        pair = f"{coin}/USDT"
        async with sem:
            move: dict = {}
            avg_f = None
            try:
                move = await fetch_tf_move(pair, bybit_symbol, ui_tf)
                avg = move.get("display_pct")
                if avg is None:
                    avg = move.get("avg_pct")
                avg_f = float(avg) if avg is not None else None
            except Exception as exc:
                print(f"[MOMENTUM] score fail {pair}: {exc}")
                move = {}
            passed = avg_f is not None and avg_f > thr
            async with lock:
                done_count += 1
                if done_count == 1 or done_count == total or done_count % 10 == 0:
                    await _maybe_progress(progress_cb, done_count, total, "scoring")
            return {
                "pair": pair,
                "symbol": bybit_symbol,
                "avg_pct": round(avg_f, 4) if avg_f is not None else None,
                "passed": passed,
                "threshold": thr,
                "window_label": move.get("window_label"),
            }

    rows = await asyncio.gather(*[_one(c, s) for c, s in items])
    rows = list(rows)
    rows.sort(
        key=lambda r: (not r["passed"], -(r["avg_pct"] if r["avg_pct"] is not None else -1.0))
    )
    await _maybe_progress(progress_cb, total, total, "done")
    return rows


async def build_momentum_watchlist(
    *,
    symbol_map: dict[str, str],
    engine_tf: str,
    active_pair: str | None,
    max_pairs: int,
    progress_cb: ProgressCb | None = None,
    lot_ok: Callable[[str, str], bool] | None = None,
) -> dict[str, Any]:
    """Return qualified pairs + scores for watchlist rewrite."""
    thr = momentum_threshold_pct(engine_tf)
    await _maybe_progress(progress_cb, 0, max(1, len(symbol_map or {})), "universe")
    scores = await score_universe(
        symbol_map, engine_tf, progress_cb=progress_cb, lot_ok=lot_ok
    )
    qualified = [r["pair"] for r in scores if r["passed"]]
    if max_pairs > 0:
        qualified = qualified[: int(max_pairs)]

    fire_pairs = list(qualified)
    watchlist = list(qualified)
    active = (active_pair or "").strip()
    if active and active not in watchlist:
        # Chart focus always docked; may not be fire-eligible.
        watchlist = [active] + watchlist
        if max_pairs > 0:
            watchlist = watchlist[: int(max_pairs)]

    passed_set = {r["pair"] for r in scores if r["passed"]}
    skipped = [r["pair"] for r in scores if not r["passed"]]

    return {
        "threshold": thr,
        "engine_tf": normalize_engine_tf(engine_tf),
        "ui_tf": ui_timeframe_for_engine(engine_tf),
        "scores": scores,
        "qualified": fire_pairs,
        "watchlist": watchlist,
        "skipped": skipped,
        "passed_set": passed_set,
        "quiet": len(fire_pairs) == 0,
        "scored": len(scores),
    }
