"""Legacy shim — TAAPI.io pattern scans were removed.

Live signals: `volume_spread_system.evaluate_uvss` (Bybit linear klines).
`TIMEFRAME_RULES` re-exported for `trading_policy` cost-aware helpers.
"""
from timeframe_rules import TIMEFRAME_RULES

# Kept so old imports do not crash; engine is permanently off.
PATTERN_TRADE_POLICIES_ENABLED = False
TAAPI_PAUSED = True
TAAPI_PATTERNS = {}


def fetch_taapi_signals(symbol, interval, exchange, api_key):
    """No-op — TAAPI removed. Returns empty signal list."""
    return []


def evaluate_trade(signals_list, interval, candle_high, candle_low):
    """No-op — TAAPI removed. Use evaluate_uvss in volume_spread_system."""
    return {
        "action": "NO_TRADE",
        "reason": "TAAPI pattern engine removed — Blue Box / VSA runs in main.py",
    }


__all__ = [
    "TIMEFRAME_RULES",
    "PATTERN_TRADE_POLICIES_ENABLED",
    "TAAPI_PAUSED",
    "TAAPI_PATTERNS",
    "fetch_taapi_signals",
    "evaluate_trade",
]
