"""Persist rolling 24-hour chart snapshots (high/low + 5m candles) on the backend.

Each refresh fetches the latest 24h window from Bybit for every mapped pair,
replaces the on-disk file entirely (old data removed), and runs every 24 hours.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx

DATA_DIR = Path(__file__).parent / "data"
DATA_FILE = DATA_DIR / "chart_24h.json"
REFRESH_INTERVAL_SECONDS = 24 * 3600
KLINE_INTERVAL = "5"
KLINE_LIMIT = 288  # 5m bars ~= 24 hours


class Chart24hStore:
    def __init__(self):
        self._data: dict = {"updated_at": None, "pairs": {}}
        self._load()

    @property
    def updated_at(self):
        return self._data.get("updated_at")

    def _load(self):
        if not DATA_FILE.exists():
            return
        try:
            with open(DATA_FILE, encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict) and "pairs" in loaded:
                self._data = loaded
        except Exception as exc:
            print(f"[CHART 24H] Could not load {DATA_FILE}: {exc}")

    def _save(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(DATA_FILE, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh)

    def is_stale(self) -> bool:
        ts = self._data.get("updated_at")
        if not ts:
            return True
        return (time.time() - float(ts)) >= REFRESH_INTERVAL_SECONDS

    def get_pair(self, pair_label: str):
        return self._data.get("pairs", {}).get(pair_label)

    def get_snapshot(self):
        return self._data

    async def _fetch_pair(self, client: httpx.AsyncClient, pair_label: str, bybit_symbol: str):
        ticker_url = f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={bybit_symbol}"
        kline_url = (
            f"https://api.bybit.com/v5/market/kline?category=spot&symbol={bybit_symbol}"
            f"&interval={KLINE_INTERVAL}&limit={KLINE_LIMIT}"
        )

        ticker_resp = await client.get(ticker_url)
        ticker_resp.raise_for_status()
        ticker_item = ticker_resp.json().get("result", {}).get("list", [{}])[0]

        kline_resp = await client.get(kline_url)
        kline_resp.raise_for_status()
        raw_candles = kline_resp.json().get("result", {}).get("list", [])

        cutoff = int(time.time()) - 86400
        candles = []
        for row in reversed(raw_candles):
            bar_time = int(int(row[0]) / 1000)
            if bar_time < cutoff:
                continue
            candles.append({
                "time": bar_time,
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            })

        high = float(ticker_item.get("highPrice24h") or 0)
        low = float(ticker_item.get("lowPrice24h") or 0)
        last = float(ticker_item.get("lastPrice") or 0)

        if candles:
            high = max(high, max(c["high"] for c in candles))
            low = min(low, min(c["low"] for c in candles)) if low > 0 else min(c["low"] for c in candles)

        return {
            "pair": pair_label,
            "high": high,
            "low": low,
            "last_price": last,
            "candles": candles,
        }

    async def refresh_all(self, bybit_symbol_map: dict):
        """Fetch latest 24h data for all pairs and replace stored snapshot."""
        new_pairs = {}
        errors = []

        async with httpx.AsyncClient(timeout=12.0) as client:
            for base, bybit_symbol in bybit_symbol_map.items():
                pair_label = f"{base}/USDT"
                try:
                    new_pairs[pair_label] = await self._fetch_pair(client, pair_label, bybit_symbol)
                except Exception as exc:
                    errors.append(f"{pair_label}: {exc}")
                    print(f"[CHART 24H] Failed to refresh {pair_label}: {exc}")

        self._data = {"updated_at": time.time(), "pairs": new_pairs}
        self._save()
        print(
            f"[CHART 24H] Snapshot saved ({len(new_pairs)} pairs). "
            f"Next refresh in {REFRESH_INTERVAL_SECONDS // 3600}h."
            + (f" Errors: {len(errors)}" if errors else "")
        )


chart_24h_store = Chart24hStore()


async def chart_24h_refresh_loop(bybit_symbol_map: dict):
    if chart_24h_store.is_stale():
        await chart_24h_store.refresh_all(bybit_symbol_map)
    while True:
        await asyncio.sleep(REFRESH_INTERVAL_SECONDS)
        await chart_24h_store.refresh_all(bybit_symbol_map)
