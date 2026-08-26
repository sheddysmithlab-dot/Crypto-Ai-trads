"""Fire-and-forget Bybit V5 order execution, per SYSTEM_RULES.md.

Orders are market-only for now — no exchange-side stopLoss/takeProfit. Position
management (exits, caps) is handled by the agent layer, not attached SL/TP.
"""
import json
import math
import sys

from pybit.unified_trading import HTTP

# Windows' default console codepage (cp1252) can't encode the emoji used in
# the FIRED/FAILED prints below - reconfigure defensively here too (main.py
# already does this, but this module may also be imported standalone).
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def _format_bybit_api_error(exc: Exception, *, action: str, symbol: str, qty, pattern: str = "") -> str:
    """Build a detailed log string from pybit / HTTP failures (retCode, retMsg, etc.)."""
    parts = [f"{type(exc).__name__}: {exc}"]
    for attr in ("status_code", "status", "error_code", "ret_code", "retCode"):
        val = getattr(exc, attr, None)
        if val is not None:
            parts.append(f"{attr}={val}")
    for attr in ("message", "ret_msg", "retMsg"):
        val = getattr(exc, attr, None)
        if val is not None and str(val) not in str(exc):
            parts.append(f"{attr}={val}")
    resp = getattr(exc, "response", None)
    if resp is not None:
        try:
            body = resp.json() if hasattr(resp, "json") else None
        except Exception:
            body = getattr(resp, "text", None)
        if body:
            parts.append(f"response={body if isinstance(body, str) else json.dumps(body)}")
    return (
        f"ORDER {action} {symbol} qty={qty} pattern={pattern or 'n/a'} | "
        + " | ".join(parts)
    )


def _qty_str(qty) -> str:
    """Format qty for Bybit without scientific notation / float junk."""
    try:
        q = float(qty)
    except (TypeError, ValueError):
        return str(qty)
    if not math.isfinite(q) or q <= 0:
        return str(qty)
    # Trim trailing zeros but keep enough precision for lot steps.
    text = f"{q:.10f}".rstrip("0").rstrip(".")
    return text if text else "0"


def _is_already_flat_error(err: str | None) -> bool:
    msg = (err or "").lower()
    return (
        "110017" in (err or "")
        or "position is zero" in msg
        or "position idx not exist" in msg
        or "no position to close" in msg
        or "current position is zero" in msg
    )


class BybitAgent:
    def __init__(self, api_key, api_secret, testnet=True):
        """ testnet=True by default - callers must opt in to mainnet explicitly. """
        self.testnet = testnet
        self.last_error = None
        self.session = HTTP(
            testnet=testnet,
            api_key=api_key,
            api_secret=api_secret,
        )

    def fetch_usdt_equity(self) -> float | None:
        """ Total unified-account equity (USDT) for position sizing. """
        try:
            resp = self.session.get_wallet_balance(accountType="UNIFIED")
            if resp.get("retCode") != 0:
                self.last_error = resp.get("retMsg", "wallet balance error")
                return None
            accounts = resp.get("result", {}).get("list", [])
            if not accounts:
                self.last_error = "No wallet data returned"
                return None
            equity = float(accounts[0].get("totalEquity", 0))
            return equity if equity > 0 else None
        except Exception as exc:
            self.last_error = str(exc)
            return None

    def fetch_linear_open_positions(self) -> list[dict] | None:
        """All open USDT-linear positions (size > 0). None on API failure."""
        try:
            resp = self.session.get_positions(category="linear", settleCoin="USDT")
        except Exception as exc:
            print(f"[BYBIT] list positions failed: {exc}")
            self.last_error = str(exc)
            return None
        if not isinstance(resp, dict) or resp.get("retCode", 0) != 0:
            print(f"[BYBIT] list positions error: {resp}")
            self.last_error = (resp or {}).get("retMsg") if isinstance(resp, dict) else "positions error"
            return None
        out = []
        for row in (resp.get("result") or {}).get("list") or []:
            try:
                size = float(row.get("size") or 0)
            except (TypeError, ValueError):
                size = 0.0
            if size <= 0:
                continue
            out.append(
                {
                    "symbol": (row.get("symbol") or "").strip(),
                    "side": (row.get("side") or "").strip(),  # Buy / Sell
                    "size": size,
                    "positionIdx": row.get("positionIdx"),
                    "avgPrice": row.get("avgPrice"),
                    "unrealisedPnl": row.get("unrealisedPnl"),
                }
            )
        return out

    @staticmethod
    def _auto_price_decimals(price):
        """ Rough price-precision guess from magnitude alone - a stand-in for
        querying Bybit's real per-symbol tick size (instruments-info endpoint,
        not yet wired up). Good enough for testnet experimentation across very
        differently-priced pairs (BTC ~$100k vs a sub-$1 altcoin); look up the
        real tick size per symbol before risking mainnet orders. """
        if price >= 100:
            return 2
        if price >= 1:
            return 4
        return 6

    @staticmethod
    def _check_place_order_response(resp: dict, *, action: str, symbol: str, qty, pattern: str) -> tuple[bool, str | None]:
        if not isinstance(resp, dict):
            return True, None
        ret_code = resp.get("retCode", 0)
        if ret_code == 0:
            return True, None
        ret_msg = resp.get("retMsg", "unknown Bybit error")
        err = (
            f"ORDER {action} {symbol} qty={qty} pattern={pattern or 'n/a'} | "
            f"retCode={ret_code} | retMsg={ret_msg} | raw={json.dumps(resp)}"
        )
        return False, err

    def execute_trade(self, signal_payload, qty, price_decimals=None):
        """ Fires ONE market order off an evaluate_trade() payload
        ({"action", "symbol", "entry", "sl", "tp", "pattern"}). SL/TP from the
        signal are ignored for now — no exchange-side stops on the order.
        No retry on failure - a rejected order is reported and left alone. """
        action = signal_payload["action"]
        symbol = signal_payload["symbol"]
        pattern = signal_payload.get("pattern", "")

        side = "Buy" if action == "BUY" else "Sell"

        try:
            resp = self.session.place_order(
                category="linear",          # USDT Perpetuals
                symbol=symbol,
                side=side,
                orderType="Market",
                qty=_qty_str(qty),
            )
            ok, api_err = self._check_place_order_response(
                resp, action=action, symbol=symbol, qty=qty, pattern=pattern
            )
            if not ok:
                self.last_error = api_err
                print(f"❌ ORDER FAILED: {api_err}")
                return False, api_err

            self.last_error = None
            print(f"✅ ORDER FIRED: {action} {symbol} | pattern={pattern} | qty={qty}")
            return True, None
        except Exception as exc:
            err = _format_bybit_api_error(
                exc, action=action, symbol=symbol, qty=qty, pattern=pattern
            )
            self.last_error = err
            print(f"❌ ORDER FAILED: {err}")
            return False, err

    def _fetch_open_position(self, symbol: str, side: str) -> tuple[str, dict | None]:
        """Return (status, row). status is 'ok' or 'error'; row is None when flat."""
        try:
            resp = self.session.get_positions(category="linear", symbol=symbol)
        except Exception as exc:
            print(f"[BYBIT] get_positions failed {symbol}: {exc}")
            return "error", None
        if not isinstance(resp, dict) or resp.get("retCode", 0) != 0:
            print(f"[BYBIT] get_positions error {symbol}: {resp}")
            return "error", None
        rows = (resp.get("result") or {}).get("list") or []
        want_long = (side or "LONG").upper() == "LONG"
        for row in rows:
            try:
                size = float(row.get("size") or 0)
            except (TypeError, ValueError):
                size = 0.0
            if size <= 0:
                continue
            pos_side = (row.get("side") or "").strip()
            if want_long and pos_side == "Buy":
                return "ok", row
            if (not want_long) and pos_side == "Sell":
                return "ok", row
        return "ok", None

    def close_position(self, trade: dict, qty: float | None = None) -> tuple[bool, str | None]:
        """Market reduce-only close for a tracked linear perpetual position.

        Resolves live size + positionIdx from Bybit when possible so hedge-mode
        and lot-step mismatches do not block force-close.
        """
        symbol = trade.get("bybit_symbol")
        side = trade.get("side", "LONG")
        close_side = "Sell" if side == "LONG" else "Buy"
        pattern = f"trade#{trade.get('id')}"

        if not symbol:
            self.last_error = "Missing bybit_symbol on trade record"
            return False, self.last_error

        status, live = self._fetch_open_position(symbol, side)
        position_idx = None
        close_qty = qty if qty is not None else trade.get("qty")

        if status == "ok" and live is None:
            print(f"⚠️ CLOSE SKIP (no live position) #{trade.get('id')} {symbol}")
            self.last_error = None
            return True, None

        if live is not None:
            try:
                live_size = float(live.get("size") or 0)
            except (TypeError, ValueError):
                live_size = 0.0
            if live_size > 0:
                close_qty = live_size
            raw_idx = live.get("positionIdx")
            if raw_idx is not None:
                try:
                    position_idx = int(raw_idx)
                except (TypeError, ValueError):
                    position_idx = None

        if close_qty is None:
            self.last_error = "Missing qty on trade record and no live Bybit size"
            return False, self.last_error

        if position_idx is None:
            # Prefer one-way (0); hedge accounts still succeed on the 1/2 retry.
            idx_order = (0, 1 if side == "LONG" else 2)
        else:
            idx_order = (position_idx, 0) if position_idx != 0 else (0, 1 if side == "LONG" else 2)

        seen = set()
        for idx in idx_order:
            if idx in seen:
                continue
            seen.add(idx)
            ok, err = self._place_close_order(
                symbol=symbol,
                close_side=close_side,
                close_qty=close_qty,
                position_idx=idx,
                pattern=pattern,
                trade_id=trade.get("id"),
                side=side,
            )
            if ok:
                return True, None
            if _is_already_flat_error(err):
                print(f"⚠️ CLOSE TREATED FLAT #{trade.get('id')} {symbol}: {err}")
                self.last_error = None
                return True, None
            msg_l = (err or "").lower()
            if "position idx" in msg_l or "110025" in (err or "") or "10001" in (err or ""):
                self.last_error = err
                continue
            self.last_error = err
            print(f"❌ CLOSE FAILED #{trade.get('id')} {symbol}: {err}")
            return False, err

        print(f"❌ CLOSE FAILED #{trade.get('id')} {symbol}: {self.last_error}")
        return False, self.last_error

    def _place_close_order(
        self,
        *,
        symbol: str,
        close_side: str,
        close_qty,
        position_idx: int,
        pattern: str,
        trade_id,
        side: str,
    ) -> tuple[bool, str | None]:
        qty_s = _qty_str(close_qty)
        try:
            kwargs = dict(
                category="linear",
                symbol=symbol,
                side=close_side,
                orderType="Market",
                qty=qty_s,
                reduceOnly=True,
                positionIdx=int(position_idx),
            )
            resp = self.session.place_order(**kwargs)
            ok, api_err = self._check_place_order_response(
                resp,
                action=f"CLOSE-{close_side}",
                symbol=symbol,
                qty=qty_s,
                pattern=pattern,
            )
            if ok:
                self.last_error = None
                print(
                    f"✅ CLOSE FIRED: {close_side} {symbol} | qty={qty_s} | "
                    f"idx={position_idx} | trade #{trade_id} ({side})"
                )
                return True, None
            return False, api_err
        except Exception as exc:
            err = _format_bybit_api_error(
                exc,
                action=f"CLOSE-{close_side}",
                symbol=symbol,
                qty=qty_s,
                pattern=pattern,
            )
            return False, err
