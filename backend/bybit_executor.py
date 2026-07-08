"""Fire-and-forget Bybit V5 order execution, per SYSTEM_RULES.md.

BOUNDARY REMINDER (NO EARLY EXIT rule from Step 1): stopLoss/takeProfit are
attached directly to the order itself, so Bybit's matching engine - not this
agent - is what closes the position. Once execute_trade() places the order
successfully, this module's job for that trade is 100% done: no polling, no
trailing, no early exit, no re-checking the position afterwards.
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
        self.session = HTTP(
            testnet=testnet,
            api_key=api_key,
            api_secret=api_secret,
        )

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
        ({"action", "symbol", "entry", "sl", "tp", "pattern"}), with SL/TP
        attached exchange-side. No retry on failure - a rejected order is
        reported and left alone, per the STRICT ERROR HANDLING rule.

        `price_decimals` auto-detects from the SL/TP price magnitude when left
        as None (so a $100k BTC order and a $0.02 altcoin order each get a
        sane precision) - pass an explicit value to override for a symbol
        whose real tick size you know. """
        action = signal_payload["action"]
        symbol = signal_payload["symbol"]
        sl = signal_payload["sl"]
        tp = signal_payload["tp"]

        if price_decimals is None:
            price_decimals = self._auto_price_decimals(max(sl, tp))

        side = "Buy" if action == "BUY" else "Sell"
        # Bybit requires SL/TP as strings, formatted to the pair's price precision.
        sl_str = f"{sl:.{price_decimals}f}"
        tp_str = f"{tp:.{price_decimals}f}"

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
                stopLoss=sl_str,
                takeProfit=tp_str,
                slTriggerBy="LastPrice",
                tpTriggerBy="LastPrice",
            )
            print(f"✅ ORDER FIRED: {action} {symbol} | SL: {sl_str} | TP: {tp_str}")
            return True
        except Exception as e:
            print(f"❌ ORDER FAILED: {e}")
            # No retry - a rejected/failed order is reported and left alone.
            return False
