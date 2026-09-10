"""Per-chart-timeframe trading profile: win/lose display + capital + exit ladder.

Momentum-lock maker entry / taker exit on every TF. Size and stop/profit
widen strictly: 1m < 5m < 15m < 1h < 1D.
"""
from __future__ import annotations

# Keys match SECONDS_TO_TIMEFRAME_KEY / chart UI (1m, 5m, …).
# profit / trail / soft / hard are gross %. capital_pct is trade size.
TIMEFRAME_PROFILES: dict[str, dict] = {
    "1m": {
        "win_rate": 30, "lose_rate": 70, "capital_pct": 1.5,
        "profit": 0.50, "trail": 0.10, "soft": 0.50, "hard": 0.70,
    },
    "5m": {
        "win_rate": 40, "lose_rate": 60, "capital_pct": 3.0,
        "profit": 0.70, "trail": 0.15, "soft": 0.70, "hard": 1.00,
    },
    "15m": {
        "win_rate": 60, "lose_rate": 40, "capital_pct": 7.0,
        "profit": 1.00, "trail": 0.20, "soft": 1.00, "hard": 1.40,
    },
    "1h": {
        "win_rate": 70, "lose_rate": 30, "capital_pct": 12.0,
        "profit": 1.50, "trail": 0.25, "soft": 1.50, "hard": 2.00,
    },
    "1D": {
        "win_rate": 80, "lose_rate": 20, "capital_pct": 20.0,
        "profit": 2.50, "trail": 0.40, "soft": 2.50, "hard": 3.00,
    },
    "30s": {
        "win_rate": 25, "lose_rate": 75, "capital_pct": 1.0,
        "profit": 0.40, "trail": 0.08, "soft": 0.40, "hard": 0.55,
    },
    "3m": {
        "win_rate": 35, "lose_rate": 65, "capital_pct": 2.0,
        "profit": 0.60, "trail": 0.12, "soft": 0.60, "hard": 0.85,
    },
    "10m": {
        "win_rate": 50, "lose_rate": 50, "capital_pct": 5.0,
        "profit": 0.85, "trail": 0.18, "soft": 0.85, "hard": 1.20,
    },
    "30m": {
        "win_rate": 65, "lose_rate": 35, "capital_pct": 9.0,
        "profit": 1.25, "trail": 0.22, "soft": 1.25, "hard": 1.70,
    },
}

_DEFAULT = {
    "win_rate": 50, "lose_rate": 50, "capital_pct": 1.5,
    "profit": 0.50, "trail": 0.10, "soft": 0.50, "hard": 0.70,
}

_ALIASES = {
    "1M": "1m", "5M": "5m", "15M": "15m", "1H": "1h", "1D": "1D",
    "3M": "3m", "30M": "30m", "30S": "30s", "10M": "10m",
    "1h": "1h", "1d": "1D",
}

# Maker limit entry + taker market exit on every chart TF.
MAKER_ENTRY_TFS = frozenset({"1m", "5m", "15m", "1h", "1D", "30s", "3m", "10m", "30m"})

SCALP_TFS = frozenset({"1m", "5m", "30s"})


def _canon_tf(timeframe_key: str | None) -> str:
    key = str(timeframe_key or "1m").strip()
    key = _ALIASES.get(key, _ALIASES.get(key.lower(), key))
    if key.lower() == "1d":
        return "1D"
    if key.lower() == "1h":
        return "1h"
    return key


def is_scalp_tf(timeframe_key: str | None) -> bool:
    return _canon_tf(timeframe_key).lower() in {k.lower() for k in SCALP_TFS}


def uses_maker_entry(timeframe_key: str | None) -> bool:
    key = _canon_tf(timeframe_key)
    return key in MAKER_ENTRY_TFS or key.lower() in {k.lower() for k in MAKER_ENTRY_TFS}


def get_exit_ladder(timeframe_key: str | None) -> dict:
    key = _canon_tf(timeframe_key)
    base = TIMEFRAME_PROFILES.get(key) or TIMEFRAME_PROFILES.get(key.lower()) or _DEFAULT
    profit = float(base["profit"])
    trail = float(base["trail"])
    soft = float(base["soft"])
    hard = float(base["hard"])
    return {
        "timeframe": key,
        "profit": profit,
        "trail": trail,
        "soft": soft,
        "hard": hard,
        "unlock": round(soft * 0.5, 2),
        "capital_pct": float(base["capital_pct"]),
    }


def get_timeframe_profile(timeframe_key: str) -> dict:
    key = _canon_tf(timeframe_key)
    base = TIMEFRAME_PROFILES.get(key) or _DEFAULT
    ladder = get_exit_ladder(key)
    return {
        "timeframe": key,
        "win_rate": int(base["win_rate"]),
        "lose_rate": int(base["lose_rate"]),
        "capital_pct": float(base["capital_pct"]),
        "profit": ladder["profit"],
        "trail": ladder["trail"],
        "soft": ladder["soft"],
        "hard": ladder["hard"],
    }


def capital_pct_fraction(timeframe_key: str) -> float:
    """Fraction of available capital to risk (e.g. 0.03 for 3%)."""
    return get_timeframe_profile(timeframe_key)["capital_pct"] / 100.0


_LOCKED_PROFILES = {k: dict(v) for k, v in TIMEFRAME_PROFILES.items()}


def lock_profiles() -> dict:
    """Restore the code ladder so DB/AI cannot flatten size or exits."""
    TIMEFRAME_PROFILES.clear()
    for key, row in _LOCKED_PROFILES.items():
        TIMEFRAME_PROFILES[key] = dict(row)
    return TIMEFRAME_PROFILES
