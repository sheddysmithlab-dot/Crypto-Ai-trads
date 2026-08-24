"""TF market avg-move % from High/Low over a lookback window.

  mid = (high + low) / 2
  range_pct = (high - low) / mid * 100
  avg_tf_pct = range_pct / (lookback_minutes / bar_minutes)
             = range_pct * bar_minutes / lookback_minutes

Windows:
  1M  → 1h lookback
  5M  → 4h lookback
  15M → 10h lookback
  1H  → 24h lookback
  1D  → 7d lookback
"""
from __future__ import annotations

import time

import httpx

from bybit_public import fetch_kline_rows, fetch_ticker_last_price, ticker_url

# UI TF → bar minutes, lookback minutes, window label, kline fetch plan
TF_MOVE_CONFIG: dict[str, dict] = {
    "1M": {
        "bar_minutes": 1,
        "lookback_minutes": 60,  # 1h
        "window_label": "1h avg",
        "kline_interval": "1",
        "kline_limit": 70,
    },
    "5M": {
        "bar_minutes": 5,
        "lookback_minutes": 240,  # 4h
        "window_label": "4h avg",
        "kline_interval": "5",
        "kline_limit": 60,
    },
    "15M": {
        "bar_minutes": 15,
        "lookback_minutes": 600,  # 10h
        "window_label": "10h avg",
        "kline_interval": "15",
        "kline_limit": 50,
    },
    "1H": {
        "bar_minutes": 60,
        "lookback_minutes": 1440,  # 24h
        "window_label": "24h avg",
        "kline_interval": "60",
        "kline_limit": 30,
    },
    "1D": {
        "bar_minutes": 1440,
        "lookback_minutes": 7 * 1440,  # 7d
        "window_label": "7d avg",
        "kline_interval": "D",
        "kline_limit": 10,
    },
}


def _normalize_timeframe(tf: str | None) -> str:
    key = (tf or "1M").strip().upper()
    return key if key in TF_MOVE_CONFIG else "1M"


def avg_move_pct_from_range(
    high: float, low: float, *, bar_minutes: int, lookback_minutes: int
) -> float | None:
    """Avg % move for one TF bar from High/Low over lookback window."""
    if high <= 0 or low <= 0 or high < low:
        return None
    mid = (high + low) / 2.0
    if mid <= 0:
        return None
    lookback = max(1, int(lookback_minutes))
    bar = max(1, int(bar_minutes))
    range_pct = ((high - low) / mid) * 100.0
    return range_pct * bar / lookback


def _hl_from_klines(raw_rows: list[list], cutoff_ms: int) -> tuple[float | None, float | None, int]:
    """Max high / min low of candles whose start >= cutoff. Rows are newest-first."""
    high: float | None = None
    low: float | None = None
    count = 0
    for row in raw_rows:
        try:
            ts = int(row[0])
            if ts < cutoff_ms:
                continue
            h = float(row[2])
            l = float(row[3])
        except (TypeError, ValueError, IndexError):
            continue
        if h <= 0 or l <= 0:
            continue
        high = h if high is None else max(high, h)
        low = l if low is None else min(low, l)
        count += 1
    return high, low, count


async def _fetch_ticker_24h_hl(
    client: httpx.AsyncClient, bybit_symbol: str
) -> tuple[float | None, float | None, float | None]:
    resp = await client.get(ticker_url(bybit_symbol))
    if resp.status_code != 200:
        return None, None, None
    item = ((resp.json().get("result") or {}).get("list") or [{}])[0]
    try:
        high = float(item.get("highPrice24h") or 0)
        low = float(item.get("lowPrice24h") or 0)
        last = float(item.get("lastPrice") or 0)
    except (TypeError, ValueError):
        return None, None, None
    return (
        high if high > 0 else None,
        low if low > 0 else None,
        last if last > 0 else None,
    )


async def fetch_tf_move(pair_label: str, bybit_symbol: str, timeframe: str | None) -> dict:
    """Return lookback-window average move % for the active chart timeframe."""
    tf_key = _normalize_timeframe(timeframe)
    cfg = TF_MOVE_CONFIG[tf_key]
    bar_minutes = int(cfg["bar_minutes"])
    lookback_minutes = int(cfg["lookback_minutes"])
    window_label = str(cfg["window_label"])
    cutoff_ms = int((time.time() - lookback_minutes * 60) * 1000)

    async with httpx.AsyncClient(timeout=12.0) as client:
        last_price = await fetch_ticker_last_price(client, bybit_symbol)
        high: float | None = None
        low: float | None = None
        candle_count = 0

        # 1H / 24h: ticker 24h H/L is authoritative and cheap
        if tf_key == "1H":
            th, tl, tlast = await _fetch_ticker_24h_hl(client, bybit_symbol)
            if tlast:
                last_price = tlast
            if th and tl:
                high, low = th, tl
                candle_count = 1

        if high is None or low is None:
            try:
                raw = await fetch_kline_rows(
                    client,
                    bybit_symbol,
                    str(cfg["kline_interval"]),
                    int(cfg["kline_limit"]),
                )
                high, low, candle_count = _hl_from_klines(raw, cutoff_ms)
            except Exception:
                high, low, candle_count = None, None, 0

    if high is None or low is None:
        return {
            "pair": pair_label,
            "timeframe": tf_key,
            "window_label": window_label,
            "avg_pct": None,
            "total_pct": None,
            "display_pct": None,
            "candle_count": 0,
            "last_price": last_price,
            "high": high,
            "low": low,
            "bar_minutes": bar_minutes,
            "lookback_minutes": lookback_minutes,
        }

    avg_pct = avg_move_pct_from_range(
        high, low, bar_minutes=bar_minutes, lookback_minutes=lookback_minutes
    )
    display = round(avg_pct, 4) if avg_pct is not None else None

    return {
        "pair": pair_label,
        "timeframe": tf_key,
        "window_label": window_label,
        "avg_pct": display,
        "total_pct": display,
        "display_pct": display,
        "candle_count": candle_count,
        "last_price": last_price,
        "high": high,
        "low": low,
        "bar_minutes": bar_minutes,
        "lookback_minutes": lookback_minutes,
    }
