"""Fire-and-forget Bybit V5 order execution, per SYSTEM_RULES.md.

Orders are market-only for now — no exchange-side stopLoss/takeProfit. Position
management (exits, caps) is handled by the agent layer, not attached SL/TP.
"""
import json
import sys

from pybit.unified_trading import HTTP

# Windows' default console codepage (cp1252) can't encode the emoji used in
# the FIRED/FAILED prints below - reconfigure defensively here too (main.py
# already does this, but this module is also imported/run standalone via
# main_engine.py, which doesn't), so a bare `python main_engine.py` never
# crashes on its very first order print.
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
                qty=str(qty),
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

    def close_position(self, trade: dict) -> tuple[bool, str | None]:
        """Market reduce-only close for a tracked linear perpetual position."""
        symbol = trade.get("bybit_symbol")
        qty = trade.get("qty")
        if not symbol or qty is None:
            self.last_error = "Missing bybit_symbol or qty on trade record"
            return False, self.last_error

        side = trade.get("side", "LONG")
        close_side = "Sell" if side == "LONG" else "Buy"

        try:
            resp = self.session.place_order(
                category="linear",
                symbol=symbol,
                side=close_side,
                orderType="Market",
                qty=str(qty),
                reduceOnly=True,
            )
            ok, api_err = self._check_place_order_response(
                resp,
                action=f"CLOSE-{close_side}",
                symbol=symbol,
                qty=qty,
                pattern=f"trade#{trade.get('id')}",
            )
            if not ok:
                self.last_error = api_err
                print(f"❌ CLOSE FAILED #{trade.get('id')} {symbol}: {api_err}")
                return False, api_err

            self.last_error = None
            print(
                f"✅ CLOSE FIRED: {close_side} {symbol} | qty={qty} | "
                f"trade #{trade.get('id')} ({side})"
            )
            return True, None
        except Exception as exc:
            err = _format_bybit_api_error(
                exc,
                action=f"CLOSE-{close_side}",
                symbol=symbol,
                qty=qty,
                pattern=f"trade#{trade.get('id')}",
            )
            self.last_error = err
            print(f"❌ CLOSE FAILED #{trade.get('id')} {symbol}: {err}")
            return False, err
