"""Timeframe-scoped market move stats (avg % per candle over a lookback window).

Each chart timeframe maps to a lookback window and label shown in the UI:
  1M  → 1 hour average
  5M  → 1 hour average
  15M → 2 hour average
  1H  → 1 day average
  1D  → 7 day average
"""
from __future__ import annotations

import time

import httpx

from bybit_public import fetch_kline_rows, fetch_ticker_last_price

# UI timeframe key → Bybit kline interval + lookback seconds + display label
TF_MOVE_CONFIG: dict[str, dict] = {
    "1M": {"interval": "1", "lookback_seconds": 3600, "window_label": "1hr"},
    "5M": {"interval": "5", "lookback_seconds": 3600, "window_label": "1hr"},
    "15M": {"interval": "15", "lookback_seconds": 7200, "window_label": "2hr"},
    "1H": {"interval": "60", "lookback_seconds": 86400, "window_label": "1Day"},
    "1D": {"interval": "D", "lookback_seconds": 7 * 86400, "window_label": "7Day"},
}

INTERVAL_SECONDS = {
    "1": 60,
    "5": 300,
    "15": 900,
    "60": 3600,
    "D": 86400,
}


def _normalize_timeframe(tf: str | None) -> str:
    key = (tf or "1M").strip().upper()
    return key if key in TF_MOVE_CONFIG else "1M"


def _candles_needed(interval: str, lookback_seconds: int) -> int:
    bar_sec = INTERVAL_SECONDS.get(interval, 60)
    return max(2, min(200, (lookback_seconds // bar_sec) + 2))


def _parse_candles(raw_rows: list[list], cutoff_ms: int) -> list[dict]:
    candles = []
    for row in reversed(raw_rows):
        ts = int(row[0])
        if ts < cutoff_ms:
            continue
        open_p = float(row[1])
        close_p = float(row[4])
        if open_p <= 0:
            continue
        pct = ((close_p - open_p) / open_p) * 100.0
        candles.append({"time": ts // 1000, "open": open_p, "close": close_p, "pct": pct})
    return candles


async def fetch_tf_move(pair_label: str, bybit_symbol: str, timeframe: str | None) -> dict:
    """Return avg signed % move per candle and total window % change."""
    tf_key = _normalize_timeframe(timeframe)
    cfg = TF_MOVE_CONFIG[tf_key]
    interval = cfg["interval"]
    lookback = cfg["lookback_seconds"]
    cutoff_ms = int((time.time() - lookback) * 1000)
    limit = _candles_needed(interval, lookback)

    async with httpx.AsyncClient(timeout=12.0) as client:
        raw_rows = await fetch_kline_rows(client, bybit_symbol, interval, limit)
        last_price = await fetch_ticker_last_price(client, bybit_symbol)

    candles = _parse_candles(raw_rows, cutoff_ms)
    if not candles:
        return {
            "pair": pair_label,
            "timeframe": tf_key,
            "window_label": cfg["window_label"],
            "avg_pct": None,
            "total_pct": None,
            "candle_count": 0,
            "last_price": last_price,
        }

    avg_pct = sum(c["pct"] for c in candles) / len(candles)
    first_open = candles[0]["open"]
    last_close = last_price if last_price and last_price > 0 else candles[-1]["close"]
    total_pct = ((last_close - first_open) / first_open) * 100.0 if first_open > 0 else None

    return {
        "pair": pair_label,
        "timeframe": tf_key,
        "window_label": cfg["window_label"],
        "avg_pct": round(avg_pct, 4),
        "total_pct": round(total_pct, 4) if total_pct is not None else None,
        "candle_count": len(candles),
        "last_price": last_close,
    }
