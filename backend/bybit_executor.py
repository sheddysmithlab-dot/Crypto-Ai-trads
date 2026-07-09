"""Fire-and-forget Bybit V5 order execution, per SYSTEM_RULES.md.

Orders are market-only for now — no exchange-side stopLoss/takeProfit. Position
management (exits, caps) is handled by the agent layer, not attached SL/TP.
"""
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

    def execute_trade(self, signal_payload, qty, price_decimals=None):
        """ Fires ONE market order off an evaluate_trade() payload
        ({"action", "symbol", "entry", "sl", "tp", "pattern"}). SL/TP from the
        signal are ignored for now — no exchange-side stops on the order.
        No retry on failure - a rejected order is reported and left alone. """
        action = signal_payload["action"]
        symbol = signal_payload["symbol"]

        side = "Buy" if action == "BUY" else "Sell"

        try:
            # pybit raises InvalidRequestError (bad params / Bybit-side rejection,
            # e.g. insufficient margin) or FailedRequestError (network/HTTP failure)
            # on any non-success response - a plain except below catches both.
            self.session.place_order(
                category="linear",          # USDT Perpetuals
                symbol=symbol,
                side=side,
                orderType="Market",
                qty=str(qty),
            )
            self.last_error = None
            pattern = signal_payload.get("pattern", "")
            print(f"✅ ORDER FIRED: {action} {symbol} | pattern={pattern} | qty={qty}")
            return True, None
        except Exception as e:
            self.last_error = str(e)
            print(f"❌ ORDER FAILED: {e}")
            return False, str(e)

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
            self.session.place_order(
                category="linear",
                symbol=symbol,
                side=close_side,
                orderType="Market",
                qty=str(qty),
                reduceOnly=True,
            )
            self.last_error = None
            print(
                f"✅ CLOSE FIRED: {close_side} {symbol} | qty={qty} | "
                f"trade #{trade.get('id')} ({side})"
            )
            return True, None
        except Exception as exc:
            self.last_error = str(exc)
            print(f"❌ CLOSE FAILED #{trade.get('id')} {symbol}: {exc}")
            return False, str(exc)
