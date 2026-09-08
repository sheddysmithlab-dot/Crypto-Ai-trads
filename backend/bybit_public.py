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


def _ticker_item(payload: dict) -> dict:
    rows = (payload.get("result") or {}).get("list") or []
    if not rows or not isinstance(rows[0], dict):
        return {}
    return rows[0]


async def fetch_ticker_quote(client: httpx.AsyncClient, symbol: str) -> dict | None:
    """Public linear ticker: last, bid1, ask1. None if the request fails."""
    resp = await client.get(ticker_url(symbol))
    if resp.status_code != 200:
        return None
    item = _ticker_item(resp.json())
    last = sanitize_price(item.get("lastPrice"))
    bid = sanitize_price(item.get("bid1Price"))
    ask = sanitize_price(item.get("ask1Price"))
    if last is None and bid is None and ask is None:
        return None
    return {"last": last, "bid": bid, "ask": ask}


async def fetch_ticker_last_price(client: httpx.AsyncClient, symbol: str) -> float | None:
    quote = await fetch_ticker_quote(client, symbol)
    if not quote:
        return None
    return quote.get("last")


async def fetch_kline_rows(
    client: httpx.AsyncClient,
    symbol: str,
    interval: str,
    limit: int,
) -> list[list]:
    resp = await client.get(kline_url(symbol, interval, limit))
    resp.raise_for_status()
    return resp.json()["result"]["list"]
