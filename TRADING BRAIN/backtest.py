"""
Signal backtesting engine.

Simulates each signal as an independent trade (entry at the next bar's open),
walks forward until stop-loss or profit-target is hit, and computes the usual
performance statistics: win rate, profit factor, expectancy, max drawdown.

Pure Python. No compounding by default (equal risk per trade) — this isolates
signal quality from money management.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from .candles import Candle, EPS
from .strategies import Signal


@dataclass
class TradeResult:
    signal: Signal
    entry_index: int
    exit_index: int
    entry: float
    exit: float
    pnl_r: float        # result in R multiples
    pnl: float          # result in dollars (equal-risk sizing)
    outcome: str        # "win" | "loss" | "breakeven"


@dataclass
class BacktestResult:
    trades: List[TradeResult] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def summary(self) -> dict:
        return self.metrics


def _simulate(candles: Sequence[Candle], sig: Signal, fee_pct: float = 0.0,
              risk_amount: float = 100.0) -> Optional[TradeResult]:
    n = len(candles)
    if sig.index + 1 >= n:
        return None  # no forward bar to enter on

    entry_index = sig.index + 1
    entry = candles[entry_index].open  # realistic fill at next open

    # preserve the signal's risk/reward offsets around the new entry
    risk = sig.risk
    reward = sig.reward
    if sig.side == "BUY":
        stop = entry - risk
        target = entry + reward
    else:
        stop = entry + risk
        target = entry - reward

    exit_index = entry_index
    exit_price = entry
    outcome = "breakeven"

    for j in range(entry_index + 1, n):
        c = candles[j]
        if sig.side == "BUY":
            if c.low <= stop:
                exit_price = stop
                exit_index = j
                outcome = "loss"
                break
            if c.high >= target:
                exit_price = target
                exit_index = j
                outcome = "win"
                break
        else:
            if c.high >= stop:
                exit_price = stop
                exit_index = j
                outcome = "loss"
                break
            if c.low <= target:
                exit_price = target
                exit_index = j
                outcome = "win"
                break
        exit_price = c.close
        exit_index = j
        outcome = "win" if _in_profit(sig.side, entry, exit_price) else "loss"
    else:
        # ran out of bars — close at last close
        exit_index = n - 1
        exit_price = candles[-1].close
        outcome = "win" if _in_profit(sig.side, entry, exit_price) else "loss"

    # PnL
    if sig.side == "BUY":
        raw_pnl = exit_price - entry
    else:
        raw_pnl = entry - exit_price
    pnl_r = raw_pnl / risk if risk > EPS else 0.0
    fee = fee_pct / 100.0 * entry
    pnl_r -= (2 * fee) / risk if risk > EPS else 0.0  # round-trip fee in R
    pnl = pnl_r * risk_amount
    return TradeResult(sig, entry_index, exit_index, entry, exit_price,
                       round(pnl_r, 4), round(pnl, 2), outcome)


def _in_profit(side: str, entry: float, exit_price: float) -> bool:
    return (exit_price > entry) if side == "BUY" else (exit_price < entry)


def backtest(candles: Sequence[Candle], signals: Sequence[Signal],
             fee_pct: float = 0.0, risk_amount: float = 100.0) -> BacktestResult:
    trades: List[TradeResult] = []
    for sig in signals:
        tr = _simulate(candles, sig, fee_pct, risk_amount)
        if tr:
            trades.append(tr)

    res = BacktestResult(trades=trades)
    res.metrics = _metrics(trades)
    return res


def _metrics(trades: Sequence[TradeResult]) -> dict:
    if not trades:
        return {"trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
                "expectancy_r": 0.0, "total_r": 0.0, "max_drawdown_r": 0.0,
                "final_equity": 0.0}
    wins = [t for t in trades if t.pnl_r > 0]
    losses = [t for t in trades if t.pnl_r <= 0]
    gross_win = sum(t.pnl_r for t in wins)
    gross_loss = abs(sum(t.pnl_r for t in losses))
    win_rate = len(wins) / len(trades)
    expectancy_r = sum(t.pnl_r for t in trades) / len(trades)
    total_r = sum(t.pnl_r for t in trades)

    # equity curve in R (equal risk per trade)
    eq = [0.0]
    for t in trades:
        eq.append(eq[-1] + t.pnl_r)
    peak = eq[0]
    max_dd = 0.0
    for v in eq:
        peak = max(peak, v)
        max_dd = max(max_dd, peak - v)

    # consecutive losses
    max_consec_losses = cur = 0
    for t in trades:
        if t.pnl_r <= 0:
            cur += 1
            max_consec_losses = max(max_consec_losses, cur)
        else:
            cur = 0

    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 4),
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else float("inf"),
        "expectancy_r": round(expectancy_r, 4),
        "total_r": round(total_r, 2),
        "max_drawdown_r": round(max_dd, 2),
        "max_consecutive_losses": max_consec_losses,
        "avg_win_r": round(sum(t.pnl_r for t in wins) / len(wins), 3) if wins else 0.0,
        "avg_loss_r": round(sum(t.pnl_r for t in losses) / len(losses), 3) if losses else 0.0,
    }
