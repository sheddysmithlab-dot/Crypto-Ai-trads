"""24-hour OHLC chart data cache for the frontend chart panel.

Fetches and caches Bybit 1h klines for the last 24 hours per pair.
"""
from __future__ import annotations

import asyncio
import time
from typing import Dict, Any

import httpx

from bybit_public import fetch_kline_rows

_INTERVAL = "60"       # 1h Bybit kline interval
_LIMIT = 25            # 24 candles + 1 forming bar
_REFRESH_SECS = 300    # refresh cache every 5 minutes


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
        if pair_label in self._cache:
            return self._cache[pair_label]
        return await self._fetch_pair(pair_label, bybit_symbol)

    async def _fetch_pair(self, pair_label: str, bybit_symbol: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                rows = await fetch_kline_rows(client, bybit_symbol, _INTERVAL, _LIMIT)
            if not rows:
                return {}
            candles = []
            for r in reversed(rows[1:]):   # drop forming bar, reverse to oldest-first
                candles.append({
                    "close_time": int(r[0]),
                    "open": float(r[1]),
                    "high": float(r[2]),
                    "low": float(r[3]),
                    "close": float(r[4]),
                    "volume": float(r[5]) if len(r) > 5 else 0.0,
                })
            entry = {
                "pair": pair_label,
                "symbol": bybit_symbol,
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
