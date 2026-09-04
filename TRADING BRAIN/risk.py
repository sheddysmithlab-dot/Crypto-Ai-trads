"""
Money management & position sizing (from the Candlestick Trading Bible).

Core rules encoded here:
  * risk no more than 2% of equity per trade (1% for beginners)
  * minimum 1:2 risk:reward
  * size positions in dollars-at-risk, not pips
  * always use a protective stop loss
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from .candles import EPS


@dataclass
class TradePlan:
    side: str
    entry: float
    stop: float
    target: float
    risk: float
    reward: float
    rr: float
    risk_amount: float
    units: float
    risk_pct: float


def risk_reward(entry: float, stop: float, target: float) -> tuple:
    """Return (risk, reward, rr) in absolute price terms."""
    risk = abs(entry - stop)
    reward = abs(target - entry)
    rr = reward / risk if risk > EPS else 0.0
    return risk, reward, rr


def validate_rr(entry: float, stop: float, target: float, min_rr: float = 2.0) -> bool:
    _, _, rr = risk_reward(entry, stop, target)
    return rr >= min_rr


def position_size(equity: float, risk_pct: float, stop_distance: float,
                  value_per_unit: float = 1.0) -> float:
    """
    Units to trade so that a stop-out loses exactly `risk_pct` of equity.
    `value_per_unit` = $ P&L per 1.0 price move per unit (e.g. pip/lot value).
    """
    if stop_distance <= EPS:
        return 0.0
    risk_amount = equity * (risk_pct / 100.0)
    return risk_amount / (stop_distance * value_per_unit)


def plan_trade(equity: float, risk_pct: float, side: str,
               entry: float, stop: float, target: float,
               value_per_unit: float = 1.0) -> TradePlan:
    risk, reward, rr = risk_reward(entry, stop, target)
    risk_amount = equity * (risk_pct / 100.0)
    units = risk_amount / (risk * value_per_unit) if risk > EPS else 0.0
    return TradePlan(side, entry, stop, target, risk, reward, rr,
                     risk_amount, units, risk_pct)


def plan_signals(equity: float, risk_pct: float,
                 signals: Sequence, value_per_unit: float = 1.0) -> List[TradePlan]:
    """Build a TradePlan for each signal using the given risk budget."""
    plans = []
    for s in signals:
        plans.append(plan_trade(equity, risk_pct, s.side, s.entry, s.stop, s.target,
                                value_per_unit))
    return plans


# --------------------------------------------------------------------------
#  the book's worked examples (for the report)
# --------------------------------------------------------------------------
def edge_math_examples() -> list:
    """Show how R:R keeps you profitable even with a losing win-rate."""
    return [
        ("1:2 R:R, 50% win rate", "10 trades, risk $100: 5 wins (+$1000) - 5 losses (-$500) = +$500"),
        ("1:3 R:R, 30% win rate", "10 trades, risk $200: 3 wins (+$1800) - 7 losses (-$1400) = +$400"),
    ]
