"""Market avg-move % from 24h High/Low range.

Formula (per 1 minute):
  mid = (high_24h + low_24h) / 2
  avg_1m_pct = (high_24h - low_24h) / mid / (24 * 60) * 100

Selected chart TF scales that 1m average by bar minutes
(1M→×1, 5M→×5, 15M→×15, 1H→×60, 1D→×1440).
"""
from __future__ import annotations

import httpx

from bybit_public import ticker_url

# UI timeframe → minutes in one bar (scale factor for 1m average)
TF_BAR_MINUTES: dict[str, int] = {
    "1M": 1,
    "5M": 5,
    "15M": 15,
    "1H": 60,
    "1D": 1440,
}

MINUTES_PER_DAY = 24 * 60  # 1440


def _normalize_timeframe(tf: str | None) -> str:
    key = (tf or "1M").strip().upper()
    return key if key in TF_BAR_MINUTES else "1M"


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


def avg_move_pct_from_24h_range(high: float, low: float, bar_minutes: int = 1) -> float | None:
    """Absolute avg % move for `bar_minutes` from 24h high/low range."""
    if high <= 0 or low <= 0 or high < low:
        return None
    mid = (high + low) / 2.0
    if mid <= 0:
        return None
    range_pct = ((high - low) / mid) * 100.0
    avg_1m = range_pct / float(MINUTES_PER_DAY)
    mins = max(1, int(bar_minutes))
    return avg_1m * mins


async def fetch_tf_move(pair_label: str, bybit_symbol: str, timeframe: str | None) -> dict:
    """Return 24h-range average move % for the active chart timeframe."""
    tf_key = _normalize_timeframe(timeframe)
    bar_minutes = TF_BAR_MINUTES[tf_key]

    async with httpx.AsyncClient(timeout=12.0) as client:
        high, low, last_price = await _fetch_ticker_24h_hl(client, bybit_symbol)

    if high is None or low is None:
        return {
            "pair": pair_label,
            "timeframe": tf_key,
            "window_label": "24h avg",
            "avg_pct": None,
            "total_pct": None,
            "display_pct": None,
            "candle_count": 0,
            "last_price": last_price,
            "high_24h": high,
            "low_24h": low,
            "bar_minutes": bar_minutes,
        }

    avg_pct = avg_move_pct_from_24h_range(high, low, bar_minutes)
    # Magnitude only (range-based); no signed direction.
    display = round(avg_pct, 4) if avg_pct is not None else None

    return {
        "pair": pair_label,
        "timeframe": tf_key,
        "window_label": "24h avg",
        "avg_pct": display,
        "total_pct": display,  # same value so older UI paths that prefer total still work
        "display_pct": display,
        "candle_count": 1,
        "last_price": last_price,
        "high_24h": high,
        "low_24h": low,
        "bar_minutes": bar_minutes,
    }
