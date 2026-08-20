"""24-hour OHLC chart data cache for the frontend chart panel.

Fetches Bybit linear ticker (24h high/low) + recent 1h klines per pair.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict

import httpx

from bybit_public import fetch_kline_rows, fetch_ticker_last_price

_INTERVAL = "60"  # 1h Bybit kline interval
_LIMIT = 25  # 24 candles + 1 forming bar
_REFRESH_SECS = 300  # refresh cache every 5 minutes


async def _fetch_ticker_24h(client: httpx.AsyncClient, bybit_symbol: str) -> dict:
    """Linear USDT perpetual 24h ticker fields."""
    try:
        resp = await client.get(
            "https://api.bybit.com/v5/market/tickers",
            params={"category": "linear", "symbol": bybit_symbol},
        )
        if resp.status_code != 200:
            return {}
        item = (resp.json().get("result") or {}).get("list") or []
        if not item:
            return {}
        t = item[0]
        high = float(t.get("highPrice24h") or 0)
        low = float(t.get("lowPrice24h") or 0)
        last = float(t.get("lastPrice") or 0)
        return {
            "high": high if high > 0 else None,
            "low": low if low > 0 else None,
            "last_price": last if last > 0 else None,
        }
    except Exception as exc:
        print(f"[CHART-24H] ticker error {bybit_symbol}: {exc}")
        return {}


class Chart24hStore:
    def __init__(self):
        self._cache: Dict[str, dict] = {}
        self.updated_at: float | None = None

    def get_pair(self, pair_label: str) -> dict:
        return self._cache.get(pair_label, {})

    def get_snapshot(self) -> dict:
        return {
            "updated_at": self.updated_at,
            "pairs": {k: v for k, v in self._cache.items()},
        }

    async def ensure_pair(self, pair_label: str, bybit_symbol: str) -> dict:
        existing = self._cache.get(pair_label)
        # Refresh if missing high/low (old cache shape) or empty
        if existing and existing.get("high") is not None and existing.get("low") is not None:
            return existing
        return await self._fetch_pair(pair_label, bybit_symbol)

    async def _fetch_pair(self, pair_label: str, bybit_symbol: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                ticker = await _fetch_ticker_24h(client, bybit_symbol)
                rows = await fetch_kline_rows(client, bybit_symbol, _INTERVAL, _LIMIT)
                if ticker.get("last_price") is None:
                    last = await fetch_ticker_last_price(client, bybit_symbol)
                    if last:
                        ticker["last_price"] = last

            candles = []
            highs = []
            lows = []
            if rows:
                for r in reversed(rows[1:]):  # drop forming bar
                    h = float(r[2])
                    lo = float(r[3])
                    candles.append(
                        {
                            "close_time": int(r[0]),
                            "open": float(r[1]),
                            "high": h,
                            "low": lo,
                            "close": float(r[4]),
                            "volume": float(r[5]) if len(r) > 5 else 0.0,
                        }
                    )
                    if h > 0:
                        highs.append(h)
                    if lo > 0:
                        lows.append(lo)

            high = ticker.get("high")
            low = ticker.get("low")
            if high is None and highs:
                high = max(highs)
            if low is None and lows:
                low = min(lows)

            entry = {
                "pair": pair_label,
                "symbol": bybit_symbol,
                "high": high,
                "low": low,
                "last_price": ticker.get("last_price"),
                "candles": candles,
                "fetched_at": time.time(),
            }
            self._cache[pair_label] = entry
            self.updated_at = time.time()
            return entry
        except Exception as exc:
            print(f"[CHART-24H] fetch error for {bybit_symbol}: {exc}")
            return {}


chart_24h_store = Chart24hStore()


async def chart_24h_refresh_loop(symbol_map: dict) -> None:
    """Background task: keep 24h cache fresh for all mapped pairs."""
    while True:
        for pair_label, bybit_symbol in symbol_map.items():
            try:
                await chart_24h_store._fetch_pair(pair_label, bybit_symbol)
            except Exception as exc:
                print(f"[CHART-24H] refresh error {pair_label}: {exc}")
            await asyncio.sleep(0.5)
        await asyncio.sleep(_REFRESH_SECS)
