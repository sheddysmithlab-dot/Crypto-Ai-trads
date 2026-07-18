"""Legacy CLI heartbeat — unused by the FastAPI app (`main.py`).

Live bot path: `main.py` → `auto_buy_loop` → `evaluate_uvss` (Bybit public klines).
This module is kept only so old scripts that import it do not crash.
"""
from __future__ import annotations


class TradingEngine:
    def __init__(self, *args, **kwargs):
        print(
            "[DEPRECATED] main_engine.TradingEngine is unused. "
            "Run uvicorn main:app — Blue Box/VSA + Bybit linear are in main.py."
        )
        self.current_symbol = "BTCUSDT"
        self.current_interval = "5m"
        self.last_processed_timestamp = 0

    def update_settings(self, symbol, interval):
        self.current_symbol = symbol
        self.current_interval = interval
        self.last_processed_timestamp = 0

    def run(self):
        print("[DEPRECATED] TradingEngine.run() does nothing. Use main.py auto_buy_loop.")


if __name__ == "__main__":
    print("Use: uvicorn main:app --host 0.0.0.0 --port 8000")
