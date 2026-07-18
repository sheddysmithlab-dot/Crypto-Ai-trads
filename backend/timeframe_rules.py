"""Per-timeframe gross TP / SL reference matrix (used by cost-aware helpers).

Live entries use Blue Box + VSA (`volume_spread_system.py`), not this matrix for
signal direction. Kept for optional cost-aware / legacy TP % lookups.
"""
from __future__ import annotations

_TIMEFRAME_RULES_PRE_SHIFT = {
    "30s": {"net_profit": 0.004, "gross_tp": 0.006, "max_allowed_sl": 0.006, "buffer": 0.0005},
    "1m": {"net_profit": 0.006, "gross_tp": 0.008, "max_allowed_sl": 0.008, "buffer": 0.0005},
    "3m": {"net_profit": 0.006, "gross_tp": 0.008, "max_allowed_sl": 0.008, "buffer": 0.0010},
    "5m": {"net_profit": 0.006, "gross_tp": 0.008, "max_allowed_sl": 0.008, "buffer": 0.0010},
    "10m": {"net_profit": 0.008, "gross_tp": 0.010, "max_allowed_sl": 0.010, "buffer": 0.0010},
    "15m": {"net_profit": 0.008, "gross_tp": 0.010, "max_allowed_sl": 0.010, "buffer": 0.0015},
    "30m": {"net_profit": 0.010, "gross_tp": 0.012, "max_allowed_sl": 0.012, "buffer": 0.0015},
    "1h": {"net_profit": 0.012, "gross_tp": 0.014, "max_allowed_sl": 0.014, "buffer": 0.0015},
    "1D": {"net_profit": 0.015, "gross_tp": 0.017, "max_allowed_sl": 0.017, "buffer": 0.0015},
}
_TIMEFRAME_RULE_SHIFT_FROM = {
    "1m": "30s",
    "3m": "1m",
    "5m": "3m",
    "10m": "5m",
    "15m": "10m",
    "30m": "15m",
    "1h": "30m",
    "1D": "1h",
}
TIMEFRAME_RULES = {
    tf: dict(_TIMEFRAME_RULES_PRE_SHIFT[src]) for tf, src in _TIMEFRAME_RULE_SHIFT_FROM.items()
}
TIMEFRAME_RULES["30s"] = dict(_TIMEFRAME_RULES_PRE_SHIFT["30s"])
