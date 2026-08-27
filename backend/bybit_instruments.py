"""Bybit linear instruments cache for watchlist/momentum scanning only.

Does not change trade entry/exit policy — only expands which symbols can be
scored into the live watchlist and supplies qtyStep / minOrderQty for lot gates.
"""
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Any

import httpx

from bybit_public import BYBIT_PUBLIC_REST, MARKET_CATEGORY

_DEFAULT_DATA = Path(__file__).resolve().parent / "data"
DATA_DIR = Path(os.environ.get("ENGINE_RUNTIME_DATA_DIR", str(_DEFAULT_DATA)))
CACHE_PATH = DATA_DIR / "bybit_instruments_linear.json"

# How often to refresh full instruments list from Bybit.
INSTRUMENTS_TTL_SEC = float(os.environ.get("BYBIT_INSTRUMENTS_TTL_SEC", str(6 * 3600)))
# Max coins to momentum-score after liquidity prefilter (keeps 7-candle burst cheap).
LIQUID_SCORE_CAP = int(os.environ.get("MOMENTUM_LIQUID_CAP", "120"))
# Min 24h turnover (USDT quote) to enter the scoring universe.
MIN_TURNOVER_USDT = float(os.environ.get("MOMENTUM_MIN_TURNOVER_USDT", "500000"))
# Lot notional must fit within this fraction of available capital.
LOT_MAX_BALANCE_FRAC = float(os.environ.get("MOMENTUM_LOT_MAX_BALANCE_FRAC", "0.15"))

_cache: dict[str, Any] = {
    "fetched_at": 0.0,
    "by_symbol": {},  # BTCUSDT -> row
    "symbol_map": {},  # BTC -> BTCUSDT (and 1000PEPE -> 1000PEPEUSDT via base display)
}


def _safe_float(v, default: float | None = None) -> float | None:
    try:
        if v is None or v == "":
            return default
        f = float(v)
        if not math.isfinite(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


def _load_disk() -> None:
    try:
        if not CACHE_PATH.is_file():
            return
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return
        by_symbol = data.get("by_symbol") or {}
        if isinstance(by_symbol, dict) and by_symbol:
            _cache["by_symbol"] = by_symbol
            _cache["symbol_map"] = data.get("symbol_map") or _rebuild_symbol_map(by_symbol)
            _cache["fetched_at"] = float(data.get("fetched_at") or 0)
            print(
                f"[INSTRUMENTS] Loaded disk cache: {len(_cache['by_symbol'])} linear symbols "
                f"(age {time.time() - _cache['fetched_at']:.0f}s)"
            )
    except Exception as exc:
        print(f"[INSTRUMENTS] disk load note: {exc}")


def _save_disk() -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "fetched_at": _cache["fetched_at"],
            "by_symbol": _cache["by_symbol"],
            "symbol_map": _cache["symbol_map"],
        }
        tmp = CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        tmp.replace(CACHE_PATH)
    except Exception as exc:
        print(f"[INSTRUMENTS] disk save note: {exc}")


def _rebuild_symbol_map(by_symbol: dict) -> dict[str, str]:
    """Map UI coin key -> Bybit symbol.

    Prefer plain baseCoin (BTC -> BTCUSDT). For 1000PEPEUSDT keep both
    PEPE and 1000PEPE keys when unambiguous.
    """
    out: dict[str, str] = {}
    # First pass: exact baseCoin when unique
    for sym, row in by_symbol.items():
        if not sym.endswith("USDT"):
            continue
        base = (row.get("baseCoin") or "").upper()
        if not base:
            continue
        # Prefer non-1000 contract when both exist for same display name
        if base not in out:
            out[base] = sym
        elif not sym.startswith("1000") and out[base].startswith("1000"):
            out[base] = sym
    # Second: strip 1000 / 10000 prefixes for UI labels (PEPE -> 1000PEPEUSDT)
    for sym, row in by_symbol.items():
        if not sym.endswith("USDT"):
            continue
        base = (row.get("baseCoin") or "").upper()
        if base.startswith("1000") and len(base) > 4:
            short = base[4:]
            if short and short not in out:
                out[short] = sym
        if base.startswith("10000") and len(base) > 5:
            short = base[5:]
            if short and short not in out:
                out[short] = sym
    return out


def get_instrument(bybit_symbol: str | None) -> dict | None:
    if not bybit_symbol:
        return None
    if not _cache["by_symbol"]:
        _load_disk()
    return _cache["by_symbol"].get(str(bybit_symbol).upper())


def qty_step(bybit_symbol: str | None) -> float | None:
    row = get_instrument(bybit_symbol)
    if not row:
        return None
    return _safe_float(row.get("qtyStep"))


def min_order_qty(bybit_symbol: str | None) -> float | None:
    row = get_instrument(bybit_symbol)
    if not row:
        return None
    return _safe_float(row.get("minOrderQty")) or qty_step(bybit_symbol)


def resolve_symbol(pair_or_coin: str | None) -> str | None:
    """BTC/USDT or BTC -> BTCUSDT from cache (None if unknown)."""
    if not pair_or_coin:
        return None
    if not _cache["by_symbol"]:
        _load_disk()
    raw = str(pair_or_coin).strip().upper().replace("-", "/")
    if raw.endswith("USDT") and "/" not in raw:
        return raw if raw in _cache["by_symbol"] else None
    coin = raw.split("/")[0]
    return _cache["symbol_map"].get(coin)


def symbol_map_for_momentum() -> dict[str, str]:
    """coin -> bybit symbol for scoring. Empty if cache not loaded yet."""
    if not _cache["symbol_map"]:
        _load_disk()
    return dict(_cache["symbol_map"] or {})


async def ensure_instruments(client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Refresh instruments if stale. Returns summary."""
    now = time.time()
    if _cache["by_symbol"] and (now - float(_cache["fetched_at"] or 0)) < INSTRUMENTS_TTL_SEC:
        return {"ok": True, "cached": True, "count": len(_cache["by_symbol"])}

    own = client is None
    if own:
        client = httpx.AsyncClient(timeout=30.0)
    try:
        by_symbol: dict[str, dict] = {}
        cursor = None
        while True:
            params = {"category": MARKET_CATEGORY, "status": "Trading", "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            resp = await client.get(
                f"{BYBIT_PUBLIC_REST}/v5/market/instruments-info",
                params=params,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"instruments-info HTTP {resp.status_code}")
            data = resp.json()
            if data.get("retCode") != 0:
                raise RuntimeError(data.get("retMsg") or "instruments-info error")
            result = data.get("result") or {}
            for row in result.get("list") or []:
                sym = (row.get("symbol") or "").upper()
                if not sym or not sym.endswith("USDT"):
                    continue
                lot = row.get("lotSizeFilter") or {}
                price_f = row.get("priceFilter") or {}
                by_symbol[sym] = {
                    "symbol": sym,
                    "baseCoin": (row.get("baseCoin") or "").upper(),
                    "quoteCoin": (row.get("quoteCoin") or "").upper(),
                    "status": row.get("status"),
                    "qtyStep": lot.get("qtyStep"),
                    "minOrderQty": lot.get("minOrderQty"),
                    "maxOrderQty": lot.get("maxOrderQty"),
                    "minNotionalValue": lot.get("minNotionalValue"),
                    "tickSize": price_f.get("tickSize"),
                }
            cursor = result.get("nextPageCursor") or None
            if not cursor:
                break

        if not by_symbol:
            return {"ok": False, "error": "empty instruments list", "count": 0}

        _cache["by_symbol"] = by_symbol
        _cache["symbol_map"] = _rebuild_symbol_map(by_symbol)
        _cache["fetched_at"] = now
        _save_disk()
        print(
            f"[INSTRUMENTS] Synced {len(by_symbol)} linear USDT perps "
            f"({len(_cache['symbol_map'])} UI keys)"
        )
        return {"ok": True, "cached": False, "count": len(by_symbol)}
    except Exception as exc:
        print(f"[INSTRUMENTS] sync failed: {exc}")
        if not _cache["by_symbol"]:
            _load_disk()
        return {
            "ok": bool(_cache["by_symbol"]),
            "error": str(exc),
            "count": len(_cache["by_symbol"]),
            "cached": True,
        }
    finally:
        if own and client is not None:
            await client.aclose()


async def build_liquid_symbol_map(
    client: httpx.AsyncClient,
    *,
    fallback_map: dict[str, str] | None = None,
    cap: int | None = None,
    min_turnover: float | None = None,
) -> dict[str, str]:
    """Top liquid USDT linear perps as coin->symbol for momentum scoring.

    Falls back to ``fallback_map`` (hardcoded 20) if tickers fail.
    """
    await ensure_instruments(client)
    cap_n = int(cap if cap is not None else LIQUID_SCORE_CAP)
    min_to = float(min_turnover if min_turnover is not None else MIN_TURNOVER_USDT)
    fallback = dict(fallback_map or {})

    try:
        resp = await client.get(
            f"{BYBIT_PUBLIC_REST}/v5/market/tickers",
            params={"category": MARKET_CATEGORY},
        )
        if resp.status_code != 200:
            raise RuntimeError(f"tickers HTTP {resp.status_code}")
        data = resp.json()
        if data.get("retCode") != 0:
            raise RuntimeError(data.get("retMsg") or "tickers error")
        rows = []
        for item in (data.get("result") or {}).get("list") or []:
            sym = (item.get("symbol") or "").upper()
            if not sym.endswith("USDT"):
                continue
            if sym not in _cache["by_symbol"]:
                continue
            turnover = _safe_float(item.get("turnover24h"), 0.0) or 0.0
            last = _safe_float(item.get("lastPrice"), 0.0) or 0.0
            if turnover < min_to or last <= 0:
                continue
            rows.append((turnover, sym, last))
        rows.sort(key=lambda x: -x[0])
        rows = rows[: max(1, cap_n)]

        out: dict[str, str] = {}
        for _to, sym, last in rows:
            inst = _cache["by_symbol"].get(sym) or {}
            base = (inst.get("baseCoin") or sym.replace("USDT", "")).upper()
            # Prefer short UI key when PEPE maps to 1000PEPEUSDT
            coin_key = base
            if base.startswith("1000") and len(base) > 4:
                coin_key = base[4:]
            elif base.startswith("10000") and len(base) > 5:
                coin_key = base[5:]
            if coin_key in out:
                continue
            out[coin_key] = sym
            # stash last price on instrument for lot gate
            inst["lastPrice"] = last
            _cache["by_symbol"][sym] = inst

        if out:
            print(f"[INSTRUMENTS] Liquid universe: {len(out)} symbols (turnover≥{min_to:g})")
            return out
    except Exception as exc:
        print(f"[INSTRUMENTS] liquid filter failed, using fallback: {exc}")

    if fallback:
        print(f"[INSTRUMENTS] Using fallback map ({len(fallback)} symbols)")
        return fallback
    return symbol_map_for_momentum()


def lot_affordable(
    bybit_symbol: str | None,
    *,
    available_capital: float,
    last_price: float | None = None,
    max_frac: float | None = None,
) -> bool:
    """True if min lot notional fits within max_frac of available capital."""
    if available_capital is None or available_capital <= 0:
        return False
    sym = (bybit_symbol or "").upper()
    inst = get_instrument(sym) or {}
    lot = min_order_qty(sym) or qty_step(sym)
    px = last_price if last_price and last_price > 0 else _safe_float(inst.get("lastPrice"))
    if lot is None or lot <= 0 or px is None or px <= 0:
        return True  # don't block if unknown — trade sizer still gates later
    notional = float(lot) * float(px)
    frac = float(max_frac if max_frac is not None else LOT_MAX_BALANCE_FRAC)
    return notional <= float(available_capital) * max(0.01, frac)


# Load disk cache at import so restarts keep symbols warm.
_load_disk()
