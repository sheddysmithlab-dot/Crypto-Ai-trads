"""Public Bybit linear (USDT perpetual) market data — no API keys required.

All chart candles, tickers, and signal klines use these endpoints.
Authenticated keys are only needed for order placement (testnet/live executor).
"""
from __future__ import annotations

import math

import httpx

BYBIT_PUBLIC_REST = "https://api.bybit.com"
BYBIT_PUBLIC_WS_LINEAR = "wss://stream.bybit.com/v5/public/linear"
MARKET_CATEGORY = "linear"


def kline_url(symbol: str, interval: str, limit: int) -> str:
    return (
        f"{BYBIT_PUBLIC_REST}/v5/market/kline"
        f"?category={MARKET_CATEGORY}&symbol={symbol}&interval={interval}&limit={limit}"
    )


def ticker_url(symbol: str) -> str:
    return (
        f"{BYBIT_PUBLIC_REST}/v5/market/tickers"
        f"?category={MARKET_CATEGORY}&symbol={symbol}"
    )


def sanitize_price(price) -> float | None:
    try:
        value = float(price)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return value


async def fetch_ticker_last_price(client: httpx.AsyncClient, symbol: str) -> float | None:
    resp = await client.get(ticker_url(symbol))
    if resp.status_code != 200:
        return None
    item = resp.json().get("result", {}).get("list", [{}])[0]
    return sanitize_price(item.get("lastPrice"))


async def fetch_kline_rows(
    client: httpx.AsyncClient,
    symbol: str,
    interval: str,
    limit: int,
) -> list[list]:
    resp = await client.get(kline_url(symbol, interval, limit))
    resp.raise_for_status()
    return resp.json()["result"]["list"]
