"""Orchestration heartbeat - the agent's main loop, per SYSTEM_RULES.md.

Ties together Step 2 (taapi_scanner) and Step 3 (bybit_executor): on every new
closed candle, scans patterns, evaluates the trade math, and fires an order.
"""
import os

from api_secrets import get_taapi_secret
import time
import threading

import requests

from bybit_executor import BybitAgent
from taapi_scanner import fetch_taapi_signals, evaluate_trade

# Bybit's /v5/market/kline `interval` param uses its own codes (plain minute
# numbers + "D"), not the "30s"/"1m"/"1D" style used by TIMEFRAME_RULES/TAAPI.
# "30s" and "10m" have no native Bybit kline granularity - they fall back to
# the closest available one (1m / 5m). Flagging this: a "10m" candle here is
# actually a 5m candle, and "30s" is actually a 1m candle, until Bybit adds
# those granularities natively.
BYBIT_KLINE_INTERVAL = {
    "30s": "1",
    "1m": "1",
    "3m": "3",
    "5m": "5",
    "10m": "5",
    "15m": "15",
    "30m": "30",
    "1h": "60",
    "1D": "D",
}


# Auto-order sizing: 2% of total wallet equity per TAAPI-fired trade.
AUTO_TRADE_CAPITAL_PCT = 0.02


class TradingEngine:
    """ The agent's heartbeat: polls Bybit for the latest closed candle, scans
    it for patterns, evaluates the trade math, and fires orders. """

    def __init__(self, api_key, api_secret, taapi_key, testnet=True, taapi_exchange="bybit"):
        # The authenticated BybitAgent is ONLY for order placement (Step 3).
        # Market data (get_closed_candle_ohlc) uses Bybit's public REST
        # endpoint directly - zero API keys needed for chart data.
        self.bybit_agent = BybitAgent(api_key, api_secret, testnet=testnet)
        self.taapi_key = taapi_key
        # NOTE: TAAPI's Bybit exchange data is paid-plan only - use "binance"
        # here if still on a free TAAPI plan.
        self.taapi_exchange = taapi_exchange

        self.current_symbol = "BTCUSDT"
        self.current_interval = "5m"
        self.last_processed_timestamp = 0

    @staticmethod
    def _to_taapi_symbol(bybit_symbol):
        """ TAAPI expects "BTC/USDT"; Bybit's own symbols have no separator ("BTCUSDT").
        This bot only trades USDT pairs, so a straight suffix split is enough. """
        if bybit_symbol.endswith("USDT"):
            return f"{bybit_symbol[:-4]}/USDT"
        return bybit_symbol

    def get_closed_candle_ohlc(self):
        """ Reads the last 2 klines from Bybit's PUBLIC market-data endpoint
        (plain requests GET, no authentication / API keys involved) and returns
        the PREVIOUS one - index 0 is the still-forming current candle, index 1
        is the last fully closed candle. """
        bybit_interval = BYBIT_KLINE_INTERVAL.get(self.current_interval, "1")
        url = (
            f"https://api.bybit.com/v5/market/kline?category=linear"
            f"&symbol={self.current_symbol}&interval={bybit_interval}&limit=2"
        )
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        candles = resp.json()["result"]["list"]  # newest-first: [0]=forming, [1]=last closed
        closed_candle = candles[1]

        # Bybit's documented kline row is [startTime, open, high, low, close,
        # volume, turnover] - so open=[1], high=[2], low=[3], close=[4]
        # ([5] is VOLUME, not close). There's no explicit "close time" field -
        # the closed candle's startTime is a unique, strictly-increasing id per
        # candle, which is all last_processed_timestamp needs in order to
        # detect "this is a NEW closed candle".
        return {
            "open": float(closed_candle[1]),
            "high": float(closed_candle[2]),
            "low": float(closed_candle[3]),
            "close": float(closed_candle[4]),
            "close_time": int(closed_candle[0]),
        }

    def update_settings(self, symbol, interval):
        """ Frontend hook: switches symbol/interval on the fly.

        CRITICAL RULE: reset last_processed_timestamp on a change so the engine
        re-evaluates immediately - extended here to cover a SYMBOL change too,
        not just interval: Bybit's kline boundaries are wall-clock aligned
        across symbols, so switching symbol alone (same interval) without a
        reset could silently skip evaluating the new symbol until the next
        candle boundary, since the old timestamp would still compare as "newer". """
        changed = symbol != self.current_symbol or interval != self.current_interval
        self.current_symbol = symbol
        self.current_interval = interval
        if changed:
            self.last_processed_timestamp = 0
            print(f"[ENGINE] Settings changed -> now watching {symbol} @ {interval}, re-evaluating immediately.")

    def _compute_auto_qty(self, entry_price: float) -> float | None:
        equity = self.bybit_agent.fetch_usdt_equity()
        if equity is None or equity <= 0 or entry_price <= 0:
            return None
        notional_usd = round(equity * AUTO_TRADE_CAPITAL_PCT, 2)
        qty = round(notional_usd / entry_price, 3)
        return qty if qty > 0 else None

    def start_heartbeat(self):
        """ Infinite loop: smart-sleeps per timeframe, then checks for a new
        closed candle and runs the full scan -> evaluate -> execute pipeline. """
        print("[ENGINE] Heartbeat started.")
        while True:
            try:
                # Smart sleep - avoids spamming the API on fast timeframes.
                if self.current_interval in ("30s", "1m"):
                    time.sleep(5)
                elif self.current_interval in ("5m", "15m"):
                    time.sleep(15)
                else:
                    time.sleep(30)  # 3m/10m/30m/1h/1D - not spec'd explicitly, safe default

                candle = self.get_closed_candle_ohlc()
                close_time = candle["close_time"]

                if close_time > self.last_processed_timestamp:
                    self.last_processed_timestamp = close_time
                    print(f"🔄 New {self.current_interval} candle detected for {self.current_symbol}. Scanning patterns...")

                    taapi_symbol = self._to_taapi_symbol(self.current_symbol)
                    signals = fetch_taapi_signals(
                        taapi_symbol, self.current_interval, self.taapi_exchange, self.taapi_key
                    )
                    result = evaluate_trade(signals, self.current_interval, candle["high"], candle["low"])

                    if result["action"] in ("BUY", "SELL"):
                        # evaluate_trade() doesn't carry a symbol in its payload -
                        # execute_trade() needs one, so it's added here.
                        result["symbol"] = self.current_symbol
                        entry_price = float(result.get("entry") or candle["close"])
                        qty = self._compute_auto_qty(entry_price)
                        if qty is None:
                            print("[ENGINE] Order skipped — could not size at 2% of total capital.")
                            continue
                        bybit_action = "SELL" if result["action"] == "BUY" else "BUY"
                        fired, err = self.bybit_agent.execute_trade({**result, "action": bybit_action}, qty=qty)
                        if not fired:
                            print(f"[ENGINE] Order failed: {err}")
                    else:
                        print(f"[ENGINE] {result['action']}: {result['reason']}")

            except Exception as e:
                # One bad API call must never kill the whole engine.
                print(f"[ENGINE] Heartbeat error (continuing): {e}")


# ==========================================
# MOCK FRONTEND - simulates the dashboard's symbol/timeframe selector via stdin.
# ==========================================
if __name__ == "__main__":
    engine = TradingEngine(
        api_key=os.environ.get("BYBIT_API_KEY", ""),
        api_secret=os.environ.get("BYBIT_API_SECRET", ""),
        taapi_key=get_taapi_secret(),
        testnet=True,
    )

    heartbeat_thread = threading.Thread(target=engine.start_heartbeat, daemon=True)
    heartbeat_thread.start()

    print("Mock frontend - type '<SYMBOL> <INTERVAL>' to switch (e.g. 'ETHUSDT 1m'), or 'quit' to exit.")
    while True:
        user_input = input("> ").strip()
        if user_input.lower() == "quit":
            break
        parts = user_input.split()
        if len(parts) == 2:
            engine.update_settings(parts[0].upper(), parts[1])
        else:
            print("Format: <SYMBOL> <INTERVAL>")
