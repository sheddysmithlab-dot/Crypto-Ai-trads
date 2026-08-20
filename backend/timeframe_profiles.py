"""Per-chart-timeframe trading profile: win/lose display rates + capital risk %.

UI chart TF buttons map here; backend auto-sizing uses capital_pct.
"""
from __future__ import annotations

# Keys match SECONDS_TO_TIMEFRAME_KEY / chart UI (1m, 5m, …).
TIMEFRAME_PROFILES: dict[str, dict] = {
    "1m": {"win_rate": 30, "lose_rate": 70, "capital_pct": 3.0},
    "5m": {"win_rate": 50, "lose_rate": 50, "capital_pct": 7.0},
    "15m": {"win_rate": 60, "lose_rate": 40, "capital_pct": 10.0},
    "1h": {"win_rate": 70, "lose_rate": 30, "capital_pct": 15.0},
    "1D": {"win_rate": 80, "lose_rate": 20, "capital_pct": 20.0},
    # Chart may not show these; keep sane fallbacks for engine keys.
    "30s": {"win_rate": 25, "lose_rate": 75, "capital_pct": 2.0},
    "3m": {"win_rate": 40, "lose_rate": 60, "capital_pct": 5.0},
    "10m": {"win_rate": 55, "lose_rate": 45, "capital_pct": 8.0},
    "30m": {"win_rate": 65, "lose_rate": 35, "capital_pct": 12.0},
}

_DEFAULT = {"win_rate": 50, "lose_rate": 50, "capital_pct": 7.0}


def get_timeframe_profile(timeframe_key: str) -> dict:
    key = (timeframe_key or "1m").strip()
    # Accept UI labels too: 1M, 5M, 1H, 1D
    aliases = {
        "1M": "1m",
        "5M": "5m",
        "15M": "15m",
        "1H": "1h",
        "1D": "1D",
        "3M": "3m",
        "30M": "30m",
        "30S": "30s",
        "10M": "10m",
    }
    key = aliases.get(key, key)
    base = TIMEFRAME_PROFILES.get(key) or _DEFAULT
    return {
        "timeframe": key,
        "win_rate": int(base["win_rate"]),
        "lose_rate": int(base["lose_rate"]),
        "capital_pct": float(base["capital_pct"]),
    }


def capital_pct_fraction(timeframe_key: str) -> float:
    """Fraction of available capital to risk (e.g. 0.03 for 3%)."""
    return get_timeframe_profile(timeframe_key)["capital_pct"] / 100.0
