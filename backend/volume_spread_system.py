"""Compatibility stub — provides candle parsing utilities for main.py.

The old UVSS trading logic has been replaced by brain.py.  Only the candle
format utilities and the chart-overlay stub are kept here.
"""
from __future__ import annotations

MIN_CANDLES = 30


def parse_bybit_kline(row: list | dict) -> dict:
    """Parse one Bybit kline row (list or dict) into a standard OHLCV dict."""
    if isinstance(row, (list, tuple)):
        # Bybit v5 kline: [startTime, open, high, low, close, volume, turnover]
        return {
            "close_time": int(row[0]),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]) if len(row) > 5 else 0.0,
        }
    # already a dict — normalise keys
    return {
        "close_time": int(row.get("close_time") or row.get("startTime") or 0),
        "open": float(row.get("open") or row.get("o") or 0),
        "high": float(row.get("high") or row.get("h") or 0),
        "low": float(row.get("low") or row.get("l") or 0),
        "close": float(row.get("close") or row.get("c") or 0),
        "volume": float(row.get("volume") or row.get("v") or 0),
    }


def reset_blue_box_state() -> None:
    """No-op stub (old UVSS visual state removed)."""
    pass


def build_blue_box_chart_overlay(candles: list) -> list:
    """Returns empty overlay — UVSS chart layer removed."""
    return []
