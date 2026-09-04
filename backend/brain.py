#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CANDLESTICK BRAIN -- single-file master (the "LLM brain").

Everything in ONE file: candles, indicators, pattern recognition, market
structure, the BUY/SELL signal engine, the trap & reverse (10th-man) policy,
money management, backtesting, a binomial ML price-direction model -- plus a
natural-language reasoning brain and an interactive chat.

Pure Python standard library. No dependencies.

Usage:
    python candlestick_brain.py              # multi-timeframe demo
    python candlestick_brain.py data.csv     # analyze a CSV (auto-detected columns)
    python candlestick_brain.py data.csv 1h  # CSV + timeframe
    python candlestick_brain.py --chat       # interactive Q&A brain

Sources distilled from:
    1. "38 Candlestick Patterns for Pro Traders" (Groww)
    2. "Automated Bitcoin Trading via Machine Learning Algorithms" (Isaac Madan, Stanford CS229)
    3. "The Candlestick Trading Bible"
"""
from __future__ import annotations

"""
Candle primitives and technical indicators.

A candlestick is defined by open / high / low / close (OHLC) over one time unit,
plus optional volume. All pattern logic in this package operates on these
primitives and the ratios derived from them.

Everything is pure Python (no numpy), so it runs anywhere Python 3.8+ runs.
"""


from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

EPS = 1e-12


@dataclass
class Candle:
    """A single OHLC(V) bar."""

    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    timestamp: Optional[float] = None

    # ------------------------------------------------------------------ #
    # anatomy
    # ------------------------------------------------------------------ #
    @property
    def bullish(self) -> bool:
        return self.close >= self.open

    @property
    def bearish(self) -> bool:
        return self.close < self.open

    @property
    def body(self) -> float:
        """Absolute size of the real body."""
        return abs(self.close - self.open)

    @property
    def body_top(self) -> float:
        return max(self.open, self.close)

    @property
    def body_bottom(self) -> float:
        return min(self.open, self.close)

    @property
    def upper_shadow(self) -> float:
        return self.high - self.body_top

    @property
    def lower_shadow(self) -> float:
        return self.body_bottom - self.low

    @property
    def total_range(self) -> float:
        return max(self.high - self.low, EPS)

    @property
    def midpoint(self) -> float:
        return (self.open + self.close) / 2.0

    @property
    def body_ratio(self) -> float:
        """Body size as a fraction of the full range (0..1)."""
        return self.body / self.total_range

    @property
    def upper_ratio(self) -> float:
        return self.upper_shadow / self.total_range

    @property
    def lower_ratio(self) -> float:
        return self.lower_shadow / self.total_range

    @property
    def color_sign(self) -> int:
        return 1 if self.bullish else -1

    # ------------------------------------------------------------------ #
    # classification helpers (tolerance-based)
    # ------------------------------------------------------------------ #
    def is_doji(self, tol: float = 0.1) -> bool:
        """Body is negligible relative to the range (indecision)."""
        return self.body <= tol * self.total_range

    def is_long_body(self, threshold_ratio: float = 0.5) -> bool:
        """Body dominates the range (strong conviction)."""
        return self.body_ratio >= threshold_ratio

    def is_small_body(self, threshold_ratio: float = 0.3) -> bool:
        return self.body_ratio <= threshold_ratio

    def is_marubozu(self, shadow_tol: float = 0.05) -> bool:
        """Open==Low and Close==High (bullish) or vice versa (bearish)."""
        return self.upper_ratio <= shadow_tol and self.lower_ratio <= shadow_tol

    def __repr__(self) -> str:
        d = "B" if self.bullish else "b"
        return (
            f"Candle({d} O={self.open:.4g} H={self.high:.4g} "
            f"L={self.low:.4g} C={self.close:.4g})"
        )


class CandleSeries:
    """A list-like series of candles with precomputed indicator helpers."""

    def __init__(self, candles: Sequence[Candle]):
        self.candles: List[Candle] = list(candles)

    # -- sequence protocol -------------------------------------------------
    def __len__(self) -> int:
        return len(self.candles)

    def __getitem__(self, i):
        return self.candles[i]

    def __iter__(self):
        return iter(self.candles)

    @property
    def closes(self) -> List[float]:
        return [c.close for c in self.candles]

    @property
    def opens(self) -> List[float]:
        return [c.open for c in self.candles]

    @property
    def highs(self) -> List[float]:
        return [c.high for c in self.candles]

    @property
    def lows(self) -> List[float]:
        return [c.low for c in self.candles]

    @property
    def volumes(self) -> List[float]:
        return [c.volume for c in self.candles]

    # ------------------------------------------------------------------ #
    # indicators
    # ------------------------------------------------------------------ #
    def sma(self, period: int) -> List[Optional[float]]:
        """Simple moving average; None until enough data."""
        out: List[Optional[float]] = []
        closes = self.closes
        s = 0.0
        for i, c in enumerate(closes):
            s += c
            if i >= period:
                s -= closes[i - period]
            out.append(s / period if i >= period - 1 else None)
        return out

    def ema(self, period: int) -> List[Optional[float]]:
        """Exponential moving average."""
        out: List[Optional[float]] = []
        closes = self.closes
        k = 2.0 / (period + 1.0)
        prev = None
        for i, c in enumerate(closes):
            if i == 0:
                prev = c
                out.append(None)
            else:
                prev = prev + k * (c - prev)
                out.append(prev)
        return out

    def rolling(self, period: int) -> List[Optional[List[float]]]:
        """Rolling windows of `closes` ending at each index."""
        closes = self.closes
        out: List[Optional[List[float]]] = []
        for i in range(len(closes)):
            if i < period - 1:
                out.append(None)
            else:
                out.append(closes[i - period + 1 : i + 1])
        return out

    def bollinger(self, period: int = 20, mult: float = 2.0):
        """
        Returns (middle, upper, lower) lists (None-padded).
        Uses population std over the rolling window.
        """
        closes = self.closes
        n = len(closes)
        mid: List[Optional[float]] = []
        up: List[Optional[float]] = []
        lo: List[Optional[float]] = []
        for i in range(n):
            if i < period - 1:
                mid.append(None)
                up.append(None)
                lo.append(None)
                continue
            window = closes[i - period + 1 : i + 1]
            m = sum(window) / period
            var = sum((x - m) ** 2 for x in window) / period
            sd = var ** 0.5
            mid.append(m)
            up.append(m + mult * sd)
            lo.append(m - mult * sd)
        return mid, up, lo

    def true_range(self) -> List[Optional[float]]:
        tr: List[Optional[float]] = []
        for i, c in enumerate(self.candles):
            if i == 0:
                tr.append(c.high - c.low)
            else:
                pc = self.candles[i - 1].close
                tr.append(
                    max(c.high - c.low, abs(c.high - pc), abs(c.low - pc))
                )
        return tr

    def atr(self, period: int = 14) -> List[Optional[float]]:
        """Average True Range (Wilder's smoothing)."""
        tr = self.true_range()
        out: List[Optional[float]] = []
        for i in range(len(self.candles)):
            if i < period - 1:
                out.append(None)
            elif i == period - 1:
                out.append(sum(tr[:period]) / period)
            else:
                out.append((out[-1] * (period - 1) + tr[i]) / period)
        return out

    def rsi(self, period: int = 14) -> List[Optional[float]]:
        """Relative Strength Index (Wilder)."""
        closes = self.closes
        out: List[Optional[float]] = [None] * len(closes)
        if len(closes) < period + 1:
            return out
        gains = 0.0
        losses = 0.0
        for i in range(1, period + 1):
            d = closes[i] - closes[i - 1]
            gains += max(d, 0.0)
            losses += max(-d, 0.0)
        avg_g = gains / period
        avg_l = losses / period
        out[period] = 100.0 if avg_l == 0 else 100.0 - 100.0 / (1 + avg_g / avg_l)
        for i in range(period + 1, len(closes)):
            d = closes[i] - closes[i - 1]
            g = max(d, 0.0)
            l = max(-d, 0.0)
            avg_g = (avg_g * (period - 1) + g) / period
            avg_l = (avg_l * (period - 1) + l) / period
            out[i] = 100.0 if avg_l == 0 else 100.0 - 100.0 / (1 + avg_g / avg_l)
        return out

    @staticmethod
    def _ema_of(values: Sequence[Optional[float]], period: int) -> List[Optional[float]]:
        """EMA with SMA seed; skips leading Nones in `values`."""
        n = len(values)
        out: List[Optional[float]] = [None] * n
        first = next((i for i, v in enumerate(values) if v is not None), None)
        if first is None:
            return out
        seed_end = first + period - 1
        if seed_end >= n:
            return out
        window = [values[i] for i in range(first, seed_end + 1)]
        if any(v is None for v in window):
            return out
        prev = sum(window) / period  # type: ignore[arg-type]
        out[seed_end] = prev
        k = 2.0 / (period + 1.0)
        for i in range(seed_end + 1, n):
            v = values[i]
            if v is None:
                continue
            prev = prev + k * (v - prev)
            out[i] = prev
        return out

    def macd(
        self, fast: int = 12, slow: int = 26, signal: int = 9
    ) -> Tuple[List[Optional[float]], List[Optional[float]], List[Optional[float]]]:
        """
        MACD line, signal line, and histogram (None-padded until enough bars).
        Defaults: 12 / 26 / 9.
        """
        closes: List[Optional[float]] = list(self.closes)
        ema_fast = self._ema_of(closes, fast)
        ema_slow = self._ema_of(closes, slow)
        macd_line: List[Optional[float]] = []
        for i in range(len(closes)):
            if ema_fast[i] is None or ema_slow[i] is None:
                macd_line.append(None)
            else:
                macd_line.append(ema_fast[i] - ema_slow[i])  # type: ignore[operator]
        signal_line = self._ema_of(macd_line, signal)
        hist: List[Optional[float]] = []
        for i in range(len(closes)):
            if macd_line[i] is None or signal_line[i] is None:
                hist.append(None)
            else:
                hist.append(macd_line[i] - signal_line[i])  # type: ignore[operator]
        return macd_line, signal_line, hist

    def vwap(self, reset_daily: bool = True) -> List[Optional[float]]:
        """
        Volume-weighted average price.
        When reset_daily=True and timestamps exist, resets each UTC day.
        Falls back to cumulative VWAP when volume/timestamps are missing.
        """
        out: List[Optional[float]] = []
        cum_pv = 0.0
        cum_v = 0.0
        last_day: Optional[int] = None
        for c in self.candles:
            if reset_daily and c.timestamp is not None and c.timestamp > 0:
                ts = c.timestamp
                if ts > 1e12:
                    ts = ts / 1000.0
                day = int(ts // 86400)
                if last_day is not None and day != last_day:
                    cum_pv = 0.0
                    cum_v = 0.0
                last_day = day
            typical = (c.high + c.low + c.close) / 3.0
            vol = c.volume if c.volume and c.volume > 0 else 1.0
            cum_pv += typical * vol
            cum_v += vol
            out.append(cum_pv / cum_v if cum_v > 0 else None)
        return out

    def returns(self, period: int = 1) -> List[Optional[float]]:
        """Simple percentage return over `period` bars ending at i."""
        closes = self.closes
        out: List[Optional[float]] = []
        for i in range(len(closes)):
            if i < period:
                out.append(None)
            else:
                base = closes[i - period]
                out.append((closes[i] - base) / base if base else 0.0)
        return out

    def momentum(self, period: int = 10) -> List[Optional[float]]:
        """price[i] - price[i-period]."""
        closes = self.closes
        out: List[Optional[float]] = []
        for i in range(len(closes)):
            out.append(None if i < period else closes[i] - closes[i - period])
        return out


def fibonacci_levels(start: float, end: float) -> dict:
    """Fibonacci retracement levels between two price extremes."""
    diff = end - start
    return {
        "0.0": start,
        "0.236": start + 0.236 * diff,
        "0.382": start + 0.382 * diff,
        "0.5": start + 0.5 * diff,
        "0.618": start + 0.618 * diff,
        "0.786": start + 0.786 * diff,
        "1.0": end,
    }


def nearest(value: float, levels: Sequence[float], tol_pct: float = 0.5):
    """Return (level, distance_pct) of the closest level within tolerance, else None."""
    if not levels:
        return None
    best = min(levels, key=lambda x: abs(x - value))
    dist_pct = abs(best - value) / (abs(value) + EPS) * 100.0
    if dist_pct <= tol_pct:
        return best, dist_pct
    return None



"""
The knowledge base — the "memory" of the brain.

Every candlestick pattern is recorded with its direction, type (reversal vs
continuation), candle count, a plain-language definition, the psychology
behind its formation, and what it signals. This is distilled from the three
source documents and is what powers the human-readable report.

A pattern entry is a dict with keys:
    direction : "bullish" | "bearish" | "neutral"
    kind      : "reversal" | "continuation" | "both" | "indecision"
    candles   : int (number of candles in the pattern)
    family    : grouping label
    definition: plain-language anatomy
    psychology: why the pattern forms (buyer/seller battle)
    signal    : what it tells the trader
    confirm   : what strengthens / confirms the signal
    source    : provenance
"""

PATTERNS: dict = {
    # ================================================================== #
    #  BULLISH REVERSAL / CONTINUATION
    # ================================================================== #
    "bullish_engulfing": {
        "direction": "bullish", "kind": "reversal", "candles": 2,
        "family": "engulfing",
        "definition": "A small bearish candle fully engulfed (body and wicks) by a larger bullish candle.",
        "psychology": "Sellers were in control, but buyers overwhelmed them and closed above the prior open.",
        "signal": "Strong buying pressure; reversal up (more powerful at the end of a downtrend — a capitulation bottom).",
        "confirm": "Occurs at support / demand zone, in an oversold area, or with trend confluence.",
        "source": "38 Patterns + Candlestick Trading Bible",
    },
    "hammer": {
        "direction": "bullish", "kind": "reversal", "candles": 1,
        "family": "pin bar",
        "definition": "Small body near the top with a long lower shadow (>=2x body) and little upper shadow.",
        "psychology": "Sellers pushed price down but buyers rejected the move and closed near the open/high.",
        "signal": "Bullish reversal at the bottom of a downtrend — buyers becoming dominant.",
        "confirm": "Form in a downtrend, near support, with the trend, or at a Fibonacci level.",
        "source": "38 Patterns + Candlestick Trading Bible (pin bar)",
    },
    "inverted_hammer": {
        "direction": "bullish", "kind": "reversal", "candles": 1,
        "family": "pin bar",
        "definition": "Small body near the bottom with a long upper shadow and little/no lower shadow.",
        "psychology": "Buyers tried to push price higher during the session (rejection test of sellers).",
        "signal": "Potential bullish reversal; needs bullish confirmation on the next candle.",
        "confirm": "Followed by a strong bullish candle; occurs at a downtrend bottom.",
        "source": "38 Patterns",
    },
    "morning_star": {
        "direction": "bullish", "kind": "reversal", "candles": 3,
        "family": "star",
        "definition": "Long bearish candle, then a small-body candle (gap down), then a long bullish candle closing into the first body.",
        "psychology": "Sellers lose momentum, indecision, then buyers take over decisively.",
        "signal": "Strong bullish reversal out of a downtrend.",
        "confirm": "Third candle closes above the midpoint of the first candle's body.",
        "source": "38 Patterns + Candlestick Trading Bible",
    },
    "piercing_line": {
        "direction": "bullish", "kind": "reversal", "candles": 2,
        "family": "piercing",
        "definition": "Bearish candle then a bullish candle that opens below the prior close and closes above the midpoint (50%) of the prior bearish body.",
        "psychology": "Buyers step in and reclaim more than half of the sellers' prior push.",
        "signal": "Bullish reversal signal — buyers stepping in to reverse the downtrend.",
        "confirm": "Close above 50% (ideally above 60%) of the prior bearish body.",
        "source": "38 Patterns",
    },
    "bullish_harami": {
        "direction": "bullish", "kind": "reversal", "candles": 2,
        "family": "inside bar",
        "definition": "A small bullish candle completely contained within the body of the previous large bearish candle.",
        "psychology": "Selling pressure fades; market enters indecision/consolidation.",
        "signal": "Possible bullish reversal; also a continuation pause in a strong trend.",
        "confirm": "At a downtrend bottom; or in an uptrend it is a continuation entry.",
        "source": "38 Patterns + Candlestick Trading Bible (inside bar)",
    },
    "three_white_soldiers": {
        "direction": "bullish", "kind": "reversal", "candles": 3,
        "family": "soldiers",
        "definition": "Three consecutive long bullish candles with small wicks, each opening within the prior body and closing higher.",
        "psychology": "Sustained, unrelenting buying pressure.",
        "signal": "Downtrend-to-uptrend shift; strong bullish momentum.",
        "confirm": "Occurs after a downtrend or consolidation.",
        "source": "38 Patterns",
    },
    "dragonfly_doji": {
        "direction": "bullish", "kind": "reversal", "candles": 1,
        "family": "doji",
        "definition": "Open/high/close at nearly the same price with a long lower shadow and almost no body.",
        "psychology": "Sellers pushed price down but buyers drove it right back — strong rejection.",
        "signal": "Bullish reversal at a downtrend bottom / support.",
        "confirm": "Near support/demand; often mistaken for a hammer (doji has ~no body).",
        "source": "38 Patterns + Candlestick Trading Bible",
    },
    "bullish_abandoned_baby": {
        "direction": "bullish", "kind": "reversal", "candles": 3,
        "family": "star",
        "definition": "Long bearish candle, a doji that gaps down, then a long bullish candle that gaps up (leaving the doji isolated).",
        "psychology": "Complete sentiment shift from bearish to bullish across the gap.",
        "signal": "Strong bullish reversal — a significant shift in sentiment.",
        "confirm": "The doji's wicks do not overlap the bodies on either side (true gap).",
        "source": "38 Patterns",
    },
    "three_inside_up": {
        "direction": "bullish", "kind": "reversal", "candles": 3,
        "family": "harami-combo",
        "definition": "Large bearish candle, a small bullish candle closing above its 50% level, then a bullish candle closing above the first candle's open.",
        "psychology": "Sellers exhaust, buyers stage a two-step reclaim.",
        "signal": "Potential bullish reversal (confirmed on the third candle).",
        "confirm": "Third candle closes above first candle's open.",
        "source": "38 Patterns",
    },
    "three_outside_up": {
        "direction": "bullish", "kind": "reversal", "candles": 3,
        "family": "engulfing-combo",
        "definition": "Bearish candle, a bullish candle that engulfs it, then another bullish candle that closes higher.",
        "psychology": "Engulfing reversal followed by confirmation buying.",
        "signal": "Confirms the strength of a bullish reversal.",
        "confirm": "Third candle closes higher than the second.",
        "source": "38 Patterns",
    },
    "bullish_kicker": {
        "direction": "bullish", "kind": "reversal", "candles": 2,
        "family": "kicker",
        "definition": "Long bearish candle then an even longer bullish candle that opens higher than the prior close and rises more.",
        "psychology": "Sudden, violent takeover by buyers after a bearish day.",
        "signal": "Strong reversal in market sentiment — buyers suddenly in control.",
        "confirm": "Gap up open above the prior candle's close.",
        "source": "38 Patterns",
    },
    "tweezer_bottom": {
        "direction": "bullish", "kind": "reversal", "candles": 2,
        "family": "tweezer",
        "definition": "Two candles (bearish then bullish) with matching/equal lows.",
        "psychology": "Sellers fail to push lower on the second attempt — support holds.",
        "signal": "The market has found a support level; bullish reversal.",
        "confirm": "Equal lows near a known support level.",
        "source": "38 Patterns + Candlestick Trading Bible",
    },
    "rising_three_methods": {
        "direction": "bullish", "kind": "continuation", "candles": 5,
        "family": "three-methods",
        "definition": "Long bullish candle, three small bearish candles within its range, then a long bullish candle closing above the first high.",
        "psychology": "A brief pause/consolidation inside an uptrend before the next leg up.",
        "signal": "Uptrend likely to continue — buyers still in control.",
        "confirm": "Fifth candle closes above the first candle's high.",
        "source": "38 Patterns",
    },
    "concealing_baby_swallow": {
        "direction": "bullish", "kind": "reversal", "candles": 4,
        "family": "swallow",
        "definition": "Two long bearish candles, a gap-down small candle, then a long bearish candle that fully engulfs the small candle (rare).",
        "psychology": "Selling pressure is decreasing in a downtrend — exhaustion.",
        "signal": "Potential bullish reversal as the downtrend loses steam.",
        "confirm": "Rare pattern; combine with support and momentum divergence.",
        "source": "38 Patterns",
    },
    "bullish_mat_hold": {
        "direction": "bullish", "kind": "continuation", "candles": 5,
        "family": "three-methods",
        "definition": "Long bullish candle, small bearish candles that drift lower but stay in range, then a long bullish candle closing above the first high.",
        "psychology": "Brief pause/consolidation in an uptrend before continuation.",
        "signal": "Uptrend continuation after a shallow pullback.",
        "confirm": "Final candle closes above the first candle's high.",
        "source": "38 Patterns",
    },
    "bullish_separating_lines": {
        "direction": "bullish", "kind": "continuation", "candles": 2,
        "family": "separating-lines",
        "definition": "A bearish candle followed by a bullish candle that opens at the same level as the bearish open.",
        "psychology": "Bulls resume control from the same opening level after a pause.",
        "signal": "Uptrend continues after a brief pause.",
        "confirm": "Second candle opens at/above the prior open.",
        "source": "38 Patterns",
    },
    "bullish_belt_hold": {
        "direction": "bullish", "kind": "reversal", "candles": 1,
        "family": "belt-hold",
        "definition": "A single long bullish candle that opens at (or near) its low and closes near its high with little lower shadow.",
        "psychology": "Strong buying from the open — bulls dominate the whole session.",
        "signal": "Strong buying pressure; reversal from a downtrend to an uptrend.",
        "confirm": "Appears at the bottom of a downtrend.",
        "source": "38 Patterns",
    },
    "bullish_three_line_strike": {
        "direction": "bullish", "kind": "continuation", "candles": 4,
        "family": "three-line-strike",
        "definition": "Three consecutive bullish candles, then a long bearish candle that opens higher and closes below the first candle's open.",
        "psychology": "A sharp bearish flush (stop-hunt) that does not change the uptrend.",
        "signal": "Price resumes upward after a brief bearish flush.",
        "confirm": "Next candle resumes upward.",
        "source": "38 Patterns",
    },
    "ladder_bottom": {
        "direction": "bullish", "kind": "reversal", "candles": 5,
        "family": "ladder",
        "definition": "Three consecutive long bearish candles, a small bearish/bullish candle, then a long bullish candle.",
        "psychology": "Bearish trend ends as buying pressure starts to take control.",
        "signal": "Bullish reversal; downtrend ending.",
        "confirm": "Final bullish candle closes strongly.",
        "source": "38 Patterns",
    },
    "meeting_lines": {
        "direction": "bullish", "kind": "reversal", "candles": 2,
        "family": "meeting-lines",
        "definition": "A long bearish candle followed by a long bullish candle that opens lower but closes at the same level as the bearish close.",
        "psychology": "Buyers meet sellers at the same price — shift from selling to buying pressure.",
        "signal": "Bullish reversal at a downtrend.",
        "confirm": "Second close matches the prior close (shared level).",
        "source": "38 Patterns",
    },

    # ================================================================== #
    #  BEARISH REVERSAL / CONTINUATION
    # ================================================================== #
    "bearish_engulfing": {
        "direction": "bearish", "kind": "reversal", "candles": 2,
        "family": "engulfing",
        "definition": "A small bullish candle fully engulfed by a larger bearish candle.",
        "psychology": "Buyers were in control, but sellers overwhelmed them and closed below the prior open.",
        "signal": "Sellers take control; bearish reversal at the end of an uptrend.",
        "confirm": "Occurs at resistance / supply, or after an extended uptrend.",
        "source": "38 Patterns + Candlestick Trading Bible",
    },
    "hanging_man": {
        "direction": "bearish", "kind": "reversal", "candles": 1,
        "family": "pin bar",
        "definition": "A single candle with a small body and a long lower shadow at the top of an uptrend.",
        "psychology": "Sellers pushed price down intraday — first sign of selling pressure.",
        "signal": "Selling pressure increasing; the uptrend may be ending.",
        "confirm": "Appears at the top of an uptrend; confirm with a bearish follow-through.",
        "source": "38 Patterns",
    },
    "shooting_star": {
        "direction": "bearish", "kind": "reversal", "candles": 1,
        "family": "pin bar",
        "definition": "Small body near the low with a long upper shadow (>=2x body) and little/no lower shadow.",
        "psychology": "Buyers pushed price up but sellers rejected it back down.",
        "signal": "Bearish reversal at the top of an uptrend — sellers taking over.",
        "confirm": "Near resistance/supply; upper shadow >= 2x body (per the Bible).",
        "source": "38 Patterns + Candlestick Trading Bible (bearish pin bar)",
    },
    "evening_star": {
        "direction": "bearish", "kind": "reversal", "candles": 3,
        "family": "star",
        "definition": "Long bullish candle, a small-body candle (gap up), then a long bearish candle closing well into the first body.",
        "psychology": "Buyers lose momentum, indecision, then sellers take over.",
        "signal": "Uptrend losing momentum; a downtrend may be starting.",
        "confirm": "Third candle closes into the first candle's body.",
        "source": "38 Patterns + Candlestick Trading Bible",
    },
    "bearish_harami": {
        "direction": "bearish", "kind": "reversal", "candles": 2,
        "family": "inside bar",
        "definition": "A small bearish candle fully contained within the body of the previous large bullish candle.",
        "psychology": "Buying pressure weakens; market consolidates.",
        "signal": "Buying pressure weakening — reversal to the downside may be coming.",
        "confirm": "At the top of an uptrend; or a continuation pause in a downtrend.",
        "source": "38 Patterns + Candlestick Trading Bible (inside bar)",
    },
    "three_black_crows": {
        "direction": "bearish", "kind": "reversal", "candles": 3,
        "family": "crows",
        "definition": "Three consecutive long red candles with small wicks, each closing lower.",
        "psychology": "Strong, steady, sustained selling pressure.",
        "signal": "Continuation/reversal to the downside — sellers firmly in control.",
        "confirm": "Occurs after an uptrend or at resistance.",
        "source": "38 Patterns",
    },
    "gravestone_doji": {
        "direction": "bearish", "kind": "reversal", "candles": 1,
        "family": "doji",
        "definition": "Open/close/low at nearly the same price with a long upper shadow and almost no body.",
        "psychology": "Buyers pushed price up but sellers drove it right back — rejection at supply.",
        "signal": "Bulls losing momentum; bearish reversal at a resistance level.",
        "confirm": "Must occur near resistance for reliability (per the Bible).",
        "source": "Candlestick Trading Bible",
    },
    "bearish_abandoned_baby": {
        "direction": "bearish", "kind": "reversal", "candles": 3,
        "family": "star",
        "definition": "Long bullish candle, a doji that gaps up, then a long bearish candle that gaps down.",
        "psychology": "Sharp reversal from bullish to bearish sentiment.",
        "signal": "Sharp bearish reversal — the beginning of a downtrend.",
        "confirm": "The doji's wicks do not overlap neighboring bodies (true gap).",
        "source": "38 Patterns",
    },
    "three_inside_down": {
        "direction": "bearish", "kind": "reversal", "candles": 3,
        "family": "harami-combo",
        "definition": "Large bullish candle, a small bearish candle within it, then a bearish candle closing lower.",
        "psychology": "Sellers gain dominance over buyers across two steps.",
        "signal": "Confirms a bearish reversal; potential downtrend.",
        "confirm": "Third candle closes below the first candle's low.",
        "source": "38 Patterns",
    },
    "three_outside_down": {
        "direction": "bearish", "kind": "reversal", "candles": 3,
        "family": "engulfing-combo",
        "definition": "Bullish candle, a bearish candle that engulfs it, then another bearish candle closing lower.",
        "psychology": "Engulfing reversal followed by confirmation selling.",
        "signal": "Confirms the strength of a bearish reversal.",
        "confirm": "Third candle closes lower than the second.",
        "source": "38 Patterns",
    },
    "bearish_kicker": {
        "direction": "bearish", "kind": "reversal", "candles": 2,
        "family": "kicker",
        "definition": "Long bullish candle then a long bearish candle that opens below the prior open and closes lower.",
        "psychology": "Dramatic shift in market sentiment — sudden seller takeover.",
        "signal": "Strong reversal to the downside.",
        "confirm": "Gap down open below the prior candle's open.",
        "source": "38 Patterns",
    },
    "tweezer_top": {
        "direction": "bearish", "kind": "reversal", "candles": 2,
        "family": "tweezer",
        "definition": "Two candles (bullish then bearish) with matching/equal highs.",
        "psychology": "Buyers fail to push higher on the second attempt — resistance holds.",
        "signal": "Upward momentum weakening; bearish reversal likely.",
        "confirm": "Matching highs at a known resistance level.",
        "source": "38 Patterns + Candlestick Trading Bible",
    },
    "bearish_mat_hold": {
        "direction": "bearish", "kind": "continuation", "candles": 5,
        "family": "three-methods",
        "definition": "Long bearish candle, smaller bullish candles that drift up but stay in range, then a long bearish candle closing below the first low.",
        "psychology": "Brief pause before the downtrend continues.",
        "signal": "Downtrend continuation after a shallow pullback.",
        "confirm": "Final candle closes below the first candle's low.",
        "source": "38 Patterns",
    },
    "bearish_separating_lines": {
        "direction": "bearish", "kind": "continuation", "candles": 2,
        "family": "separating-lines",
        "definition": "A bullish candle followed by a bearish candle that opens at the same level as the bullish open.",
        "psychology": "Bears resume control from the same opening level after a pause.",
        "signal": "Downtrend continues after a brief pause.",
        "confirm": "Second candle opens at/below the prior open.",
        "source": "38 Patterns",
    },
    "bearish_belt_hold": {
        "direction": "bearish", "kind": "reversal", "candles": 1,
        "family": "belt-hold",
        "definition": "A single long bearish candle that opens at (or near) its high and closes near its low with little upper shadow.",
        "psychology": "Strong selling from the open — bears dominate the whole session.",
        "signal": "Strong selling pressure; reversal from an uptrend to a downtrend.",
        "confirm": "Appears at the top of an uptrend.",
        "source": "38 Patterns",
    },
    "bearish_three_line_strike": {
        "direction": "bearish", "kind": "continuation", "candles": 4,
        "family": "three-line-strike",
        "definition": "Three consecutive bearish candles, then a long bullish candle that opens lower and closes above the first candle's open.",
        "psychology": "A sharp bullish flush (stop-hunt) that does not change the downtrend.",
        "signal": "Short pullback; the downtrend continues.",
        "confirm": "Next candle resumes downward.",
        "source": "38 Patterns",
    },
    "upside_gap_two_crows": {
        "direction": "bearish", "kind": "reversal", "candles": 3,
        "family": "crows",
        "definition": "A long green candle, then two small red candles that gap up; the second red candle closes below the first red candle's close.",
        "psychology": "Buyers stall after a gap; sellers begin to press.",
        "signal": "Potential reversal or brief consolidation before the downtrend continues.",
        "confirm": "Second red candle closes below the first red close.",
        "source": "38 Patterns",
    },
    "dark_cloud_cover": {
        "direction": "bearish", "kind": "reversal", "candles": 2,
        "family": "piercing",
        "definition": "A long green candle then a red candle that opens above the prior high but closes below the midpoint of the green body.",
        "psychology": "Buyers push to new highs but sellers overwhelm and reclaim more than half.",
        "signal": "The uptrend might be over; a downtrend could begin.",
        "confirm": "Second close below the 50% midpoint of the first body.",
        "source": "38 Patterns",
    },
    "bearish_doji_star": {
        "direction": "bearish", "kind": "reversal", "candles": 2,
        "family": "star",
        "definition": "A long bullish candle followed by a doji (very small body) near its high.",
        "psychology": "Indecision after a strong up move — buyers hesitate.",
        "signal": "Potential downtrend if followed by a bearish candle.",
        "confirm": "A bearish candle follows the doji to confirm reversal.",
        "source": "38 Patterns",
    },
    "doji": {
        "direction": "neutral", "kind": "indecision", "candles": 1,
        "family": "doji",
        "definition": "Open and close at (nearly) the same price — equality between buyers and sellers.",
        "psychology": "Market indecision; no one is in control.",
        "signal": "Potential reversal when it appears at the top/bottom of a trend.",
        "confirm": "Combine with key levels or a confirmation candle.",
        "source": "Candlestick Trading Bible",
    },
}


def pattern_info(name: str):
    """Return the knowledge entry for a pattern name (or None)."""
    return PATTERNS.get(name)


def pattern_names() -> list:
    return list(PATTERNS.keys())


def bullish_patterns() -> list:
    return [n for n, d in PATTERNS.items() if d["direction"] == "bullish"]


def bearish_patterns() -> list:
    return [n for n, d in PATTERNS.items() if d["direction"] == "bearish"]


def reversal_patterns() -> list:
    return [n for n, d in PATTERNS.items() if d["kind"] in ("reversal", "both")]


def continuation_patterns() -> list:
    return [n for n, d in PATTERNS.items() if d["kind"] in ("continuation", "both")]


# --------------------------------------------------------------------------
#  PER-PATTERN TRADING INSTRUCTIONS (buy/sell, long/short, inverse/fade)
# --------------------------------------------------------------------------
def trading_instructions(name: str) -> dict:
    """Concrete trade plan for a pattern: action, entry, stop, target, inverse."""
    p = PATTERNS.get(name)
    if not p:
        return {}
    d = p["direction"]
    k = p["kind"]
    fam = p["family"]
    long_side = d == "bullish"
    short_side = d == "bearish"

    if d == "neutral":
        action = "WAIT (no directional bias)"
        entry = "wait for the next candle to confirm direction, then trade that direction"
        stop = "beyond the doji's extreme (high/low)"
        target = "next support/resistance in the confirmed direction"
    else:
        action = "BUY (LONG)" if long_side else "SELL (SHORT)"

    # entry / stop / target by pattern family
    if fam == "pin bar":
        entry = "on the close of the pin bar (aggressive) or a 50% retrace of its range (conservative)"
        stop = "beyond the long tail (below the low for long / above the high for short)"
        target = "next resistance (long) / support (short)"
    elif fam == "inside bar":
        entry = "on the breakout of the mother bar, in the trend direction"
        stop = "beyond the mother bar (below its low / above its high)"
        target = "next support/resistance level"
    elif fam == "engulfing":
        entry = "on the close of the engulfing candle"
        stop = "beyond the engulfing pattern (below its low / above its high)"
        target = "next support/resistance level"
    elif fam == "doji":
        entry = "after a confirmation candle, in its direction"
        stop = "beyond the doji's extreme"
        target = "next support/resistance"
    elif fam == "three-methods":
        entry = "on the breakout above the first candle's high (long) / below its low (short)"
        stop = "beyond the consolidation range"
        target = "measured move = first-candle range projected from the breakout"
    elif fam == "three-line-strike":
        entry = "on the next candle, in the original trend direction"
        stop = "beyond the strike candle"
        target = "next support/resistance in the trend direction"
    elif fam == "soldiers":
        entry = "on the close of the third candle"
        stop = "below the first candle's low"
        target = "next resistance"
    elif fam == "crows":
        entry = "on the close of the third candle"
        stop = "above the first candle's high"
        target = "next support"
    elif fam == "star":
        entry = "on the close of the third candle"
        stop = "beyond the whole 3-candle pattern"
        target = "next support/resistance"
    elif fam in ("kicker", "belt-hold"):
        entry = "on the close of the kicker/belt candle (strong, aggressive entry)"
        stop = "beyond the candle's extreme"
        target = "next support/resistance"
    elif fam in ("tweezer", "meeting-lines", "separating-lines", "piercing",
                 "swallow", "ladder", "harami-combo", "engulfing-combo"):
        entry = "on the close of the final candle of the pattern"
        stop = "below the pattern low (long) / above the pattern high (short)"
        target = "next resistance (long) / support (short)"
    else:
        entry = "on the close of the final candle of the pattern"
        stop = "below the pattern low (long) / above the pattern high (short)"
        target = "next resistance (long) / support (short)"

    # inverse (fade / trap) instruction
    if long_side:
        inverse = ("Inverse: if price closes back BELOW the pattern low, the setup failed — "
                   "fade it: SELL/SHORT the failure (trap).")
    elif short_side:
        inverse = ("Inverse: if price closes back ABOVE the pattern high, the setup failed — "
                   "fade it: BUY/LONG the failure (trap).")
    else:
        inverse = "Inverse: trade the breakout whichever way the confirmation candle breaks."

    return {
        "name": name,
        "action": action,
        "entry": entry,
        "stop": stop,
        "target": target,
        "inverse": inverse,
        "kind": k,
    }


def trade_summary(name: str) -> str:
    t = trading_instructions(name)
    if not t:
        return name
    return (f"{t['action']} — enter {t['entry']}; stop {t['stop']}; target {t['target']}. "
            f"{t['inverse']}")


# --------------------------------------------------------------------------
#  STRATEGIES (from the Candlestick Trading Bible)
# --------------------------------------------------------------------------
STRATEGIES = {
    "pin_bar": {
        "patterns": ["hammer", "shooting_star", "inverted_hammer", "hanging_man"],
        "definition": "A candle with a small body and a long tail (>=2x body) showing rejection.",
        "entry": "Aggressive: enter on close of the pin bar. Conservative: enter on a 50% retracement of its range.",
        "stop": "Beyond the long tail (above the tail for shorts, below for longs).",
        "target": "Next support (for shorts) / resistance (for longs) level.",
        "rules": [
            "Trade on 4H / daily time frames (not 5-min).",
            "With the trend is more powerful than counter-trend.",
            "Longer tail = stronger rejection = more powerful.",
            "Rejection at a key level (S/R, MA, Fib, supply/demand) is essential.",
        ],
    },
    "engulfing_bar": {
        "patterns": ["bullish_engulfing", "bearish_engulfing"],
        "definition": "Second body fully engulfs the first body (Nison's criteria: clear trend, opposite bodies, full engulf).",
        "entry": "On the close of the engulfing candle.",
        "stop": "Below/above the engulfing pattern.",
        "target": "Next support/resistance level.",
        "rules": [
            "Requires a clearly definable trend (per Steve Nison).",
            "The two real bodies must be opposite colors.",
            "Trade with the trend; use with MA (8/21), Fibonacci 50%/61%, trendlines.",
            "Sideways-market variants: from S/R, breakouts, or false breakouts.",
        ],
    },
    "inside_bar": {
        "patterns": ["bullish_harami", "bearish_harami"],
        "definition": "A small bar completely inside the previous (mother) bar — consolidation/indecision.",
        "entry": "On the breakout of the mother bar (safest), in the direction of the trend.",
        "stop": "Beyond the mother candle.",
        "target": "Next support/resistance level.",
        "rules": [
            "Bulkowski: bearish inside bar in a bull market reverses ~65% of the time; bullish continuation ~52%.",
            "Trade the dominant trend on bigger time frames.",
            "Trade only from key levels; find confluence.",
        ],
    },
    "inside_bar_false_breakout": {
        "patterns": ["bullish_harami", "bearish_harami"],
        "definition": "Price breaks out of the inside/mother bar then quickly reverses back inside — a stop-hunt / bull or bear trap.",
        "entry": "After the close of the reversal bar (the trap is sprung).",
        "stop": "Beyond the reversal bar.",
        "target": "Next support/resistance level; can offer very high R:R.",
        "rules": [
            "Exploits institutional stop-loss hunting (liquidity grabs).",
            "Bullish FB at a downtrend bottom = bullish reversal; bearish FB at an uptrend top = bearish reversal.",
            "Best at 50%/61% Fibonacci, 21 MA, S/R, trendlines, or horizontal range levels.",
        ],
    },
}


# --------------------------------------------------------------------------
#  MARKET STRUCTURE + CONFLUENCE + MONEY MANAGEMENT (from the Bible)
# --------------------------------------------------------------------------
MARKET_STRUCTURE = {
    "uptrend": "A repeating pattern of higher highs (HH) and higher lows (HL).",
    "downtrend": "A repeating pattern of lower highs (LH) and lower lows (LL).",
    "ranging": "Price moves horizontally between definable support and resistance (>=2 touches each).",
    "choppy": "No clear direction, tight noisy range, no identifiable boundaries — stay away.",
    "trend_share": "Trends occur ~30% of the time; markets range ~70% of the time.",
}

CONFLUENCE_FACTORS = {
    "trend": "Is the signal in line with the dominant trend? (most important factor)",
    "support_resistance": "Is price at a horizontal S/R level?",
    "supply_demand": "Is price at a supply/demand zone (institutional levels)?",
    "moving_average_8": "Is price at the 8 EMA/SMA dynamic level?",
    "moving_average_21": "Is price at the 21 SMA dynamic level (author's favorite)?",
    "fibonacci": "Is price at the 50% or 61% Fibonacci retracement?",
    "trendline": "Is price at a drawn trendline?",
    "bollinger": "Is price at the upper/lower Bollinger band (range markets)?",
    "rsi": "Is RSI(14) oversold/overbought or showing momentum on the trade side?",
    "macd": "Is MACD(12/26/9) aligned (line vs signal) with the trade side?",
    "vwap": "Is price at VWAP (session mean) as dynamic support/resistance?",
    "vwap_bias": "Is price on the correct side of VWAP for directional bias?",
    "timeframe_alignment": "Do the higher time frames agree (top-down analysis)?",
}

MONEY_MANAGEMENT = {
    "max_risk_per_trade": "Risk no more than 2% of equity per trade (1% for beginners).",
    "min_risk_reward": "Minimum 1:2 risk:reward — only take trades that can win >= 2x the risk.",
    "position_sizing": "Size in dollars-at-risk, not pips: units = (equity * risk%) / stop_distance.",
    "stop_loss": "Always place a protective stop; never use mental stops.",
    "afford": "Never risk money you cannot afford to lose; start small.",
    "edge_math": "At 1:3 R:R you can lose 70% of trades and still be profitable.",
    "example_1_2": "10 trades, 1:2 R:R, risk $100 each: 5 wins + 5 losses => +$500 net.",
    "example_1_3": "10 trades, 1:3 R:R: 7 losses (-$1400) + 3 wins (+$1800) => +$400 net.",
}

TOP_DOWN = {
    "primary_timeframes": ["1H", "4H", "Daily"],
    "analysis_order": "Weekly -> Daily -> 4H (or Daily -> 1H). Start big, then drill down.",
    "weekly_big_picture": ["Key S/R levels", "Market structure (trend/range/choppy)", "Previous candle"],
    "entry_timeframe": ["Market condition", "Key levels", "Price-action signal (pin bar / engulfing / inside bar)"],
    "rule": "Never trade a signal that fights a higher-timeframe level.",
}



"""
Quantitative candlestick pattern recognition.

`detect_at(candles, i)` returns every pattern that *completes* on candle index `i`.
`scan(candles)` runs detection across the whole series.

Rules are quantitative (body / wick ratios, engulf relations, gaps) using the
definitions encoded in `knowledge.py`. Thresholds are module constants so they
can be tuned.
"""


from dataclasses import dataclass, field
from typing import List, Optional, Sequence


# --- tuning thresholds -----------------------------------------------------
DOJI_BODY_TOL = 0.10       # body <= 10% of range => doji
SMALL_BODY = 0.30          # body <= 30% of range => "small body"
LONG_BODY = 0.50           # body >= 50% of range => "long body"
PIN_TAIL_MULT = 2.0        # tail >= 2x body => pin bar (per the Bible)
SMALL_WICK = 0.35          # wick <= 35% of body => "small wick" (marubozu-ish)
EQ_TOL = 0.15              # "equal" level tolerance as fraction of range
GAP_TOL = 0.02             # small overlap tolerance for "gaps" (fraction of range)


@dataclass
class PatternMatch:
    name: str
    index: int
    direction: str          # "bullish" | "bearish" | "neutral"
    kind: str               # "reversal" | "continuation" | "indecision"
    family: str
    confidence: float = 1.0
    note: str = ""

    def __repr__(self) -> str:
        return f"PatternMatch({self.name} @ {self.index}, {self.direction}/{self.kind})"


# --------------------------------------------------------------------------
#  helpers
# --------------------------------------------------------------------------
def _prev(candles: Sequence[Candle], i: int, k: int) -> Optional[Candle]:
    j = i - k
    return candles[j] if j >= 0 else None


def _context_trend(candles: Sequence[Candle], i: int, lookback: int = 5) -> str:
    """Crude directional context using closes over the preceding bars."""
    if i < 1:
        return "flat"
    base = max(0, i - lookback)
    start = candles[base].close
    end = candles[i - 1].close
    if end > start * 1.002:
        return "up"
    if end < start * 0.998:
        return "down"
    return "flat"


def _eq(a: float, b: float, ref_range: float) -> bool:
    return abs(a - b) <= EQ_TOL * max(ref_range, EPS)


def _gap(a_low: float, a_high: float, b_low: float, b_high: float,
         ref_range: float) -> bool:
    """True if candle range [b] sits entirely above range [a] (gap up), no overlap."""
    return b_low > a_high - GAP_TOL * ref_range


def _body_engulfs(outer: Candle, inner: Candle) -> bool:
    return outer.body_top >= inner.body_top and outer.body_bottom <= inner.body_bottom


def _long(c: Candle) -> bool:
    return c.body_ratio >= LONG_BODY


def _small(c: Candle) -> bool:
    return c.body_ratio <= SMALL_BODY


# --------------------------------------------------------------------------
#  single-candle detectors
# --------------------------------------------------------------------------
def _detect_doji(cs, i):
    c = cs[i]
    if c.is_doji(DOJI_BODY_TOL):
        return [PatternMatch("doji", i, "neutral", "indecision", "doji",
                             note="open == close, indecision")]
    return []


def _detect_dragonfly_doji(cs, i):
    c = cs[i]
    if c.is_doji(DOJI_BODY_TOL) and c.lower_shadow >= 2.0 * c.total_range * 0.3 \
            and c.upper_shadow <= 0.15 * c.total_range:
        return [PatternMatch("dragonfly_doji", i, "bullish", "reversal", "doji")]
    return []


def _detect_gravestone_doji(cs, i):
    c = cs[i]
    if c.is_doji(DOJI_BODY_TOL) and c.upper_shadow >= 0.3 * c.total_range \
            and c.lower_shadow <= 0.15 * c.total_range:
        return [PatternMatch("gravestone_doji", i, "bearish", "reversal", "doji")]
    return []


def _detect_pin_bars(cs, i):
    """Hammer / hanging man (long lower shadow) and shooting star / inverted hammer."""
    c = cs[i]
    out = []
    if c.body <= EPS:
        return out
    # a pin bar has one long tail (>=2x body) and the opposite tail small
    # relative to the long tail (not relative to the tiny body).
    lower_long = c.lower_shadow >= PIN_TAIL_MULT * c.body
    upper_long = c.upper_shadow >= PIN_TAIL_MULT * c.body
    ctx = _context_trend(cs, i)
    if lower_long and c.upper_shadow <= 0.5 * c.lower_shadow:
        if ctx == "up":
            out.append(PatternMatch("hanging_man", i, "bearish", "reversal", "pin bar"))
        else:
            out.append(PatternMatch("hammer", i, "bullish", "reversal", "pin bar"))
    if upper_long and c.lower_shadow <= 0.5 * c.upper_shadow:
        if ctx == "down":
            out.append(PatternMatch("inverted_hammer", i, "bullish", "reversal", "pin bar"))
        else:
            out.append(PatternMatch("shooting_star", i, "bearish", "reversal", "pin bar"))
    return out


def _detect_belt_hold(cs, i):
    c = cs[i]
    out = []
    rng = c.total_range
    if c.bullish and _long(c) and c.lower_shadow <= 0.15 * rng \
            and c.upper_shadow <= 0.3 * rng:
        out.append(PatternMatch("bullish_belt_hold", i, "bullish", "reversal", "belt-hold"))
    if c.bearish and _long(c) and c.upper_shadow <= 0.15 * rng \
            and c.lower_shadow <= 0.3 * rng:
        out.append(PatternMatch("bearish_belt_hold", i, "bearish", "reversal", "belt-hold"))
    return out


# --------------------------------------------------------------------------
#  two-candle detectors
# --------------------------------------------------------------------------
def _detect_engulfing(cs, i):
    p = _prev(cs, i, 1)
    c = cs[i]
    if not p:
        return []
    out = []
    if p.bearish and c.bullish and _body_engulfs(c, p) and c.body > p.body:
        out.append(PatternMatch("bullish_engulfing", i, "bullish", "reversal", "engulfing"))
    if p.bullish and c.bearish and _body_engulfs(c, p) and c.body > p.body:
        out.append(PatternMatch("bearish_engulfing", i, "bearish", "reversal", "engulfing"))
    return out


def _detect_piercing_darkcloud(cs, i):
    p = _prev(cs, i, 1)
    c = cs[i]
    if not p:
        return []
    out = []
    mid = (p.open + p.close) / 2.0
    if p.bearish and c.bullish and c.open < p.close and mid < c.close < p.open:
        out.append(PatternMatch("piercing_line", i, "bullish", "reversal", "piercing"))
    if p.bullish and c.bearish and c.open > p.close and p.open < c.close < mid:
        out.append(PatternMatch("dark_cloud_cover", i, "bearish", "reversal", "piercing"))
    return out


def _detect_harami(cs, i):
    p = _prev(cs, i, 1)
    c = cs[i]
    if not p:
        return []
    out = []
    if p.bearish and c.bullish and _body_engulfs(p, c) and c.body < p.body:
        out.append(PatternMatch("bullish_harami", i, "bullish", "both", "inside bar"))
    if p.bullish and c.bearish and _body_engulfs(p, c) and c.body < p.body:
        out.append(PatternMatch("bearish_harami", i, "bearish", "both", "inside bar"))
    return out


def _detect_kicker(cs, i):
    p = _prev(cs, i, 1)
    c = cs[i]
    if not p:
        return []
    out = []
    if p.bearish and c.bullish and c.open > p.open and _long(p) and _long(c):
        out.append(PatternMatch("bullish_kicker", i, "bullish", "reversal", "kicker"))
    if p.bullish and c.bearish and c.open < p.open and _long(p) and _long(c):
        out.append(PatternMatch("bearish_kicker", i, "bearish", "reversal", "kicker"))
    return out


def _detect_tweezers(cs, i):
    p = _prev(cs, i, 1)
    c = cs[i]
    if not p:
        return []
    out = []
    rng = max(p.total_range, c.total_range)
    if p.bearish and c.bullish and _eq(p.low, c.low, rng):
        out.append(PatternMatch("tweezer_bottom", i, "bullish", "reversal", "tweezer"))
    if p.bullish and c.bearish and _eq(p.high, c.high, rng):
        out.append(PatternMatch("tweezer_top", i, "bearish", "reversal", "tweezer"))
    return out


def _detect_separating_lines(cs, i):
    p = _prev(cs, i, 1)
    c = cs[i]
    if not p:
        return []
    out = []
    rng = max(p.total_range, c.total_range)
    if p.bearish and c.bullish and _eq(p.open, c.open, rng):
        out.append(PatternMatch("bullish_separating_lines", i, "bullish", "continuation", "separating-lines"))
    if p.bullish and c.bearish and _eq(p.open, c.open, rng):
        out.append(PatternMatch("bearish_separating_lines", i, "bearish", "continuation", "separating-lines"))
    return out


def _detect_meeting_lines(cs, i):
    p = _prev(cs, i, 1)
    c = cs[i]
    if not p:
        return []
    if p.bearish and c.bullish and c.open < p.close and _eq(p.close, c.close, p.total_range):
        return [PatternMatch("meeting_lines", i, "bullish", "reversal", "meeting-lines")]
    return []


def _detect_bearish_doji_star(cs, i):
    p = _prev(cs, i, 1)
    c = cs[i]
    if not p:
        return []
    if p.bullish and _long(p) and c.is_doji(DOJI_BODY_TOL) \
            and c.low >= p.midpoint:
        return [PatternMatch("bearish_doji_star", i, "bearish", "reversal", "star")]
    return []


# --------------------------------------------------------------------------
#  three-candle detectors
# --------------------------------------------------------------------------
def _detect_stars(cs, i):
    """Morning star / evening star / abandoned baby (3 candles)."""
    c0 = _prev(cs, i, 2)
    c1 = _prev(cs, i, 1)
    c2 = cs[i]
    if not (c0 and c1 and c2):
        return []
    out = []
    mid0 = (c0.open + c0.close) / 2.0
    rng = max(c0.total_range, c1.total_range, c2.total_range)
    # morning star
    if c0.bearish and _long(c0) and _small(c1) and c2.bullish and _long(c2) \
            and c2.close > mid0:
        out.append(PatternMatch("morning_star", i, "bullish", "reversal", "star"))
        if c1.high < c0.low - GAP_TOL * rng and c2.low > c1.high + GAP_TOL * rng:
            out.append(PatternMatch("bullish_abandoned_baby", i, "bullish", "reversal", "star"))
    # evening star
    if c0.bullish and _long(c0) and _small(c1) and c2.bearish and _long(c2) \
            and c2.close < mid0:
        out.append(PatternMatch("evening_star", i, "bearish", "reversal", "star"))
        if c1.low > c0.high + GAP_TOL * rng and c2.high < c1.low - GAP_TOL * rng:
            out.append(PatternMatch("bearish_abandoned_baby", i, "bearish", "reversal", "star"))
    return out


def _detect_soldiers_crows(cs, i):
    c0 = _prev(cs, i, 2)
    c1 = _prev(cs, i, 1)
    c2 = cs[i]
    if not (c0 and c1 and c2):
        return []
    out = []
    if all(c.bullish and _long(c) for c in (c0, c1, c2)) \
            and c1.open < c0.close and c2.open < c1.close \
            and c1.close > c0.close and c2.close > c1.close:
        out.append(PatternMatch("three_white_soldiers", i, "bullish", "reversal", "soldiers"))
    if all(c.bearish and _long(c) for c in (c0, c1, c2)) \
            and c1.open > c0.close and c2.open > c1.close \
            and c1.close < c0.close and c2.close < c1.close:
        out.append(PatternMatch("three_black_crows", i, "bearish", "reversal", "crows"))
    return out


def _detect_inside_updown(cs, i):
    c0 = _prev(cs, i, 2)
    c1 = _prev(cs, i, 1)
    c2 = cs[i]
    if not (c0 and c1 and c2):
        return []
    out = []
    mid0 = (c0.open + c0.close) / 2.0
    if c0.bearish and c1.bullish and c1.close > mid0 and c2.bullish and c2.close > c0.open:
        out.append(PatternMatch("three_inside_up", i, "bullish", "reversal", "harami-combo"))
    if c0.bullish and c1.bearish and c1.close < mid0 and c2.bearish and c2.close < c0.open:
        out.append(PatternMatch("three_inside_down", i, "bearish", "reversal", "harami-combo"))
    return out


def _detect_outside_updown(cs, i):
    c0 = _prev(cs, i, 2)
    c1 = _prev(cs, i, 1)
    c2 = cs[i]
    if not (c0 and c1 and c2):
        return []
    out = []
    if c0.bearish and c1.bullish and _body_engulfs(c1, c0) and c2.bullish and c2.close > c1.close:
        out.append(PatternMatch("three_outside_up", i, "bullish", "reversal", "engulfing-combo"))
    if c0.bullish and c1.bearish and _body_engulfs(c1, c0) and c2.bearish and c2.close < c1.close:
        out.append(PatternMatch("three_outside_down", i, "bearish", "reversal", "engulfing-combo"))
    return out


def _detect_upside_gap_two_crows(cs, i):
    c0 = _prev(cs, i, 2)
    c1 = _prev(cs, i, 1)
    c2 = cs[i]
    if not (c0 and c1 and c2):
        return []
    if c0.bullish and _long(c0) and c1.bearish and c2.bearish \
            and c1.open > c0.close and c2.open > c1.open and c2.close < c1.close \
            and c2.close > c0.close:
        return [PatternMatch("upside_gap_two_crows", i, "bearish", "reversal", "crows")]
    return []


# --------------------------------------------------------------------------
#  4 & 5-candle detectors
# --------------------------------------------------------------------------
def _detect_three_methods(cs, i):
    """Rising/falling three methods & mat hold (5 candles)."""
    c0 = _prev(cs, i, 4)
    c1 = _prev(cs, i, 3)
    c2 = _prev(cs, i, 2)
    c3 = _prev(cs, i, 1)
    c4 = cs[i]
    if not (c0 and c1 and c2 and c3 and c4):
        return []
    out = []
    # rising three methods / bullish mat hold
    if c0.bullish and _long(c0):
        smalls = [c1, c2, c3]
        if all((s.high <= c0.high and s.low >= c0.low) for s in smalls) \
                and c4.bullish and c4.close > c0.high:
            # rising three methods requires small candles be bearish & not break low
            if all(s.bearish for s in smalls):
                out.append(PatternMatch("rising_three_methods", i, "bullish", "continuation", "three-methods"))
            out.append(PatternMatch("bullish_mat_hold", i, "bullish", "continuation", "three-methods"))
    # falling three methods / bearish mat hold
    if c0.bearish and _long(c0):
        smalls = [c1, c2, c3]
        if all((s.low >= c0.low and s.high <= c0.high) for s in smalls) \
                and c4.bearish and c4.close < c0.low:
            if all(s.bullish for s in smalls):
                out.append(PatternMatch("falling_three_methods", i, "bearish", "continuation", "three-methods"))
            out.append(PatternMatch("bearish_mat_hold", i, "bearish", "continuation", "three-methods"))
    return out


def _detect_three_line_strike(cs, i):
    c0 = _prev(cs, i, 3)
    c1 = _prev(cs, i, 2)
    c2 = _prev(cs, i, 1)
    c3 = cs[i]
    if not (c0 and c1 and c2 and c3):
        return []
    out = []
    if all(c.bullish for c in (c0, c1, c2)) and c3.bearish and _long(c3) \
            and c3.open > c2.close and c3.close < c0.open:
        out.append(PatternMatch("bullish_three_line_strike", i, "bullish", "continuation", "three-line-strike"))
    if all(c.bearish for c in (c0, c1, c2)) and c3.bullish and _long(c3) \
            and c3.open < c2.close and c3.close > c0.open:
        out.append(PatternMatch("bearish_three_line_strike", i, "bearish", "continuation", "three-line-strike"))
    return out


def _detect_ladder_bottom(cs, i):
    c0 = _prev(cs, i, 4)
    c1 = _prev(cs, i, 3)
    c2 = _prev(cs, i, 2)
    c3 = _prev(cs, i, 1)
    c4 = cs[i]
    if not (c0 and c1 and c2 and c3 and c4):
        return []
    if all(c.bearish and _long(c) for c in (c0, c1, c2)) \
            and _small(c3) and c4.bullish and _long(c4):
        return [PatternMatch("ladder_bottom", i, "bullish", "reversal", "ladder")]
    return []


def _detect_concealing_baby_swallow(cs, i):
    c0 = _prev(cs, i, 3)
    c1 = _prev(cs, i, 2)
    c2 = _prev(cs, i, 1)
    c3 = cs[i]
    if not (c0 and c1 and c2 and c3):
        return []
    if c0.bearish and _long(c0) and c1.bearish and _long(c1) \
            and c2.high < c1.low and c3.bearish and _body_engulfs(c3, c2):
        return [PatternMatch("concealing_baby_swallow", i, "bullish", "reversal", "swallow")]
    return []


# --------------------------------------------------------------------------
#  registry + engine
# --------------------------------------------------------------------------
_DETECTORS = [
    _detect_doji,
    _detect_dragonfly_doji,
    _detect_gravestone_doji,
    _detect_pin_bars,
    _detect_belt_hold,
    _detect_engulfing,
    _detect_piercing_darkcloud,
    _detect_harami,
    _detect_kicker,
    _detect_tweezers,
    _detect_separating_lines,
    _detect_meeting_lines,
    _detect_bearish_doji_star,
    _detect_stars,
    _detect_soldiers_crows,
    _detect_inside_updown,
    _detect_outside_updown,
    _detect_upside_gap_two_crows,
    _detect_three_methods,
    _detect_three_line_strike,
    _detect_ladder_bottom,
    _detect_concealing_baby_swallow,
]


def detect_at(candles: Sequence[Candle], i: int) -> List[PatternMatch]:
    """All patterns completing on candle index i."""
    if i < 0 or i >= len(candles):
        return []
    found: List[PatternMatch] = []
    for det in _DETECTORS:
        try:
            found.extend(det(candles, i))
        except Exception:
            continue
    return found


def scan(candles: Sequence[Candle], start: int = 0, end: Optional[int] = None) -> List[PatternMatch]:
    """Scan the series for all pattern matches."""
    end = len(candles) if end is None else min(end, len(candles))
    matches: List[PatternMatch] = []
    for i in range(max(start, 0), end):
        matches.extend(detect_at(candles, i))
    return matches


def last_matches(candles: Sequence[Candle], n: int = 5) -> List[PatternMatch]:
    """Patterns completing on the most recent `n` candles."""
    start = max(0, len(candles) - n)
    return scan(candles, start=start)



"""
Market structure analysis — the "context" layer.

Derived from the Candlestick Trading Bible's market-structure chapter:
  * trending vs ranging vs choppy
  * swing points (HH/HL, LH/LL)
  * horizontal support & resistance (clustered swing levels)
  * trendlines (linear regression through swing points)
"""


from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple



@dataclass
class Swing:
    index: int
    price: float
    kind: str  # "high" | "low"


@dataclass
class Level:
    price: float
    touches: int
    kind: str  # "support" | "resistance"


@dataclass
class TrendLine:
    slope: float
    intercept: float
    kind: str  # "support" | "resistance"

    def value_at(self, index: int) -> float:
        return self.slope * index + self.intercept


@dataclass
class MarketStructure:
    trend: str = "unknown"      # "uptrend" | "downtrend" | "ranging" | "choppy"
    trend_strength: float = 0.0
    swings: List[Swing] = field(default_factory=list)
    supports: List[Level] = field(default_factory=list)
    resistances: List[Level] = field(default_factory=list)
    support_line: Optional[TrendLine] = None
    resistance_line: Optional[TrendLine] = None
    note: str = ""


def find_swings(candles: Sequence[Candle], left: int = 2, right: int = 2) -> List[Swing]:
    """Fractal swing points. A bar is a swing high/low if it is the extreme of its window."""
    swings: List[Swing] = []
    n = len(candles)
    for i in range(left, n - right):
        c = candles[i]
        is_high = all(c.high >= candles[i - k].high for k in range(1, left + 1)) and \
                  all(c.high >= candles[i + k].high for k in range(1, right + 1))
        is_low = all(c.low <= candles[i - k].low for k in range(1, left + 1)) and \
                 all(c.low <= candles[i + k].low for k in range(1, right + 1))
        if is_high and is_low:
            # tie — prefer the one with the larger extreme distance
            if c.high - c.low > 0:
                is_low = False
        if is_high:
            swings.append(Swing(i, c.high, "high"))
        if is_low:
            swings.append(Swing(i, c.low, "low"))
    swings.sort(key=lambda s: s.index)
    return swings


def _merge_swings(swings: Sequence[Swing]) -> List[Swing]:
    """Collapse adjacent same-kind swings, keeping the more extreme one."""
    out: List[Swing] = []
    for s in swings:
        if out and out[-1].kind == s.kind:
            prev = out[-1]
            if (s.kind == "high" and s.price > prev.price) or \
               (s.kind == "low" and s.price < prev.price):
                out[-1] = s
            continue
        out.append(s)
    return out


def classify_trend(candles: Sequence[Candle], swings: Optional[Sequence[Swing]] = None) -> MarketStructure:
    """Classify the market using swing-point structure (HH/HL vs LH/LL)."""
    ms = MarketStructure()
    if swings is None:
        swings = find_swings(candles)
    sw = _merge_swings(swings)

    highs = [s for s in sw if s.kind == "high"][-4:]
    lows = [s for s in sw if s.kind == "low"][-4:]

    if len(highs) >= 2 and len(lows) >= 2:
        hh = highs[-1].price > highs[-2].price
        hl = lows[-1].price > lows[-2].price
        lh = highs[-1].price < highs[-2].price
        ll = lows[-1].price < lows[-2].price
        if hh and hl:
            ms.trend = "uptrend"
        elif lh and ll:
            ms.trend = "downtrend"
        else:
            ms.trend = "ranging"
        # strength = net direction of last few swings
        hp = [s.price for s in highs]
        lp = [s.price for s in lows]
        ms.trend_strength = round((hp[-1] - hp[0]) / max(abs(hp[0]), EPS) +
                                  (lp[-1] - lp[0]) / max(abs(lp[0]), EPS), 4)
    else:
        # not enough swings — fall back to a close-based slope
        if len(candles) >= 10:
            closes = [c.close for c in candles]
            half = len(closes) // 2
            start = sum(closes[:half]) / half
            end = sum(closes[half:]) / (len(closes) - half)
            change = (end - start) / max(abs(start), EPS)
            if change > 0.01:
                ms.trend = "uptrend"
            elif change < -0.01:
                ms.trend = "downtrend"
            else:
                ms.trend = "ranging"
            ms.trend_strength = round(change, 4)
        else:
            ms.trend = "unknown"

    # choppy detection: very narrow, overlapping swings with no clear direction
    if ms.trend == "ranging" and highs and lows:
        hr = max(h.price for h in highs) - min(h.price for h in highs)
        lr = max(l.price for l in lows) - min(l.price for l in lows)
        avg = candles[-1].close
        if (hr + lr) / 2 < 0.002 * avg:  # extremely tight range
            ms.trend = "choppy"

    ms.swings = sw
    return ms


def cluster_levels(prices: Sequence[float], pct: float = 0.003) -> List[Tuple[float, int]]:
    """Cluster a list of near-equal prices into (level, touches)."""
    prices = sorted(prices)
    clusters: List[List[float]] = []
    for p in prices:
        if clusters and abs(p - clusters[-1][-1]) / max(abs(clusters[-1][-1]), EPS) <= pct:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    return [(sum(c) / len(c), len(c)) for c in clusters if len(c) >= 1]


def support_resistance(swings: Sequence[Swing], pct: float = 0.003) -> Tuple[List[Level], List[Level]]:
    sw = _merge_swings(swings)
    highs = [s.price for s in sw if s.kind == "high"]
    lows = [s.price for s in sw if s.kind == "low"]
    resistances = [Level(p, t, "resistance") for p, t in cluster_levels(highs, pct)]
    supports = [Level(p, t, "support") for p, t in cluster_levels(lows, pct)]
    return supports, resistances


def _linreg(xs: Sequence[float], ys: Sequence[float]) -> Tuple[float, float]:
    """Least-squares: y = slope * x + intercept."""
    n = len(xs)
    if n == 0:
        return 0.0, 0.0
    if n == 1:
        return 0.0, ys[0]
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    slope = num / den if den > EPS else 0.0
    intercept = my - slope * mx
    return slope, intercept


def build_trendlines(swings: Sequence[Swing], min_points: int = 2) -> Tuple[Optional[TrendLine], Optional[TrendLine]]:
    highs = [s for s in swings if s.kind == "high"][-5:]
    lows = [s for s in swings if s.kind == "low"][-5:]
    support_line = None
    resistance_line = None
    if len(lows) >= min_points:
        s, ic = _linreg([s.index for s in lows], [s.price for s in lows])
        support_line = TrendLine(s, ic, "support")
    if len(highs) >= min_points:
        s, ic = _linreg([s.index for s in highs], [s.price for s in highs])
        resistance_line = TrendLine(s, ic, "resistance")
    return support_line, resistance_line


def analyze(candles: Sequence[Candle], pct: float = 0.003) -> MarketStructure:
    """Full market-structure analysis."""
    swings = find_swings(candles)
    ms = classify_trend(candles, swings)
    supports, resistances = support_resistance(swings, pct)
    ms.supports = supports
    ms.resistances = resistances
    ms.support_line, ms.resistance_line = build_trendlines(swings)
    # keep only meaningful levels (touches >= 2 is ideal, but retain 1-touch majors too)
    ms.supports = [s for s in supports if s.touches >= 1]
    ms.resistances = [r for r in resistances if r.touches >= 1]
    return ms


def nearest_level(price: float, levels: Sequence[Level], tol_pct: float = 0.5) -> Optional[Level]:
    """Closest level within tolerance, or None."""
    if not levels:
        return None
    best = min(levels, key=lambda l: abs(l.price - price))
    if abs(best.price - price) / max(abs(price), EPS) <= tol_pct:
        return best
    return None



"""
The signal engine — turns recognized patterns + market context into concrete
BUY/SELL (LONG/SHORT) signals.

Implements the Candlestick Trading Bible's four price-action strategies
(pin bar, engulfing bar, inside bar, inside-bar false breakout) with:
  * trend + level + signal confluence scoring
  * entry / stop-loss / profit-target
  * risk:reward validation (minimum 1:2)
  * per-timeframe calibration (1m / 5m / 15m / 1h / 1d)

Every signal carries its confluence reasons so the trader can see *why* it fired.
"""


from dataclasses import dataclass, field
from typing import List, Optional, Sequence



# --------------------------------------------------------------------------
#  timeframe calibration
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class TimeframeConfig:
    key: str
    label: str
    min_score: float          # minimum confluence score to emit a signal
    min_rr: float             # minimum risk:reward to accept a trade
    require_higher_tf: bool   # require top-down alignment
    noise_guard: bool         # apply extra noise filtering on small timeframes
    note: str


TIMEFRAMES = {
    # Unified rulebook: every chart TF (5m → 1d) uses the same trading thresholds as 1m.
    # Labels stay TF-specific; confluence / R:R / HTF / noise guards do not loosen on higher TFs.
    "1m": TimeframeConfig(
        "1m", "1-Minute", min_score=6.0, min_rr=2.0,
        require_higher_tf=True, noise_guard=True,
        note="Reference scalp rulebook. Strong confluence + HTF alignment required.",
    ),
    "5m": TimeframeConfig(
        "5m", "5-Minute", min_score=6.0, min_rr=2.0,
        require_higher_tf=True, noise_guard=True,
        note="Same trading rules as 1m (confluence + HTF + noise guard).",
    ),
    "15m": TimeframeConfig(
        "15m", "15-Minute", min_score=6.0, min_rr=2.0,
        require_higher_tf=True, noise_guard=True,
        note="Same trading rules as 1m (confluence + HTF + noise guard).",
    ),
    "1h": TimeframeConfig(
        "1h", "1-Hour", min_score=6.0, min_rr=2.0,
        require_higher_tf=True, noise_guard=True,
        note="Same trading rules as 1m (confluence + HTF + noise guard).",
    ),
    "1d": TimeframeConfig(
        "1d", "1-Day", min_score=6.0, min_rr=2.0,
        require_higher_tf=False, noise_guard=True,
        note="Same 1m confluence/R:R/noise rules; HTF optional (no weekly series wired).",
    ),
}


@dataclass
class Signal:
    side: str                 # "BUY" | "SELL"
    strategy: str             # pin_bar | engulfing | inside_bar | inside_bar_false_breakout
    patterns: List[str]
    index: int
    timestamp: Optional[float]
    entry: float
    stop: float
    target: float
    risk: float               # |entry - stop|
    reward: float             # |target - entry|
    rr: float
    score: float
    confidence: float
    confluence: List[str]
    reasons: List[str]
    timeframe: str

    @property
    def direction(self) -> str:
        return "LONG" if self.side == "BUY" else "SHORT"

    def __repr__(self) -> str:
        return (f"Signal({self.side}/{self.direction} {self.strategy} @ {self.index} "
                f"entry={self.entry:.4g} stop={self.stop:.4g} target={self.target:.4g} "
                f"RR={self.rr:.2f} score={self.score:.1f})")


# --------------------------------------------------------------------------
#  context (computed once per series)
# --------------------------------------------------------------------------
@dataclass
class Context:
    series: CandleSeries
    ms: MarketStructure
    sma8: List[Optional[float]]
    sma21: List[Optional[float]]
    sma200: List[Optional[float]]
    boll_mid: List[Optional[float]]
    boll_up: List[Optional[float]]
    boll_lo: List[Optional[float]]
    atr: List[Optional[float]]
    rsi: List[Optional[float]]
    macd_line: List[Optional[float]]
    macd_signal: List[Optional[float]]
    macd_hist: List[Optional[float]]
    vwap: List[Optional[float]]
    fib50: Optional[float]
    fib618: Optional[float]
    higher_tf_trend: Optional[str] = None


def build_context(candles: Sequence[Candle], higher_tf_trend: Optional[str] = None) -> Context:
    series = CandleSeries(candles)
    ms = analyze(candles)
    sma8 = series.sma(8)
    sma21 = series.sma(21)
    sma200 = series.sma(200)
    bmid, bup, blo = series.bollinger(20, 2.0)
    atr = series.atr(14)
    rsi = series.rsi(14)
    macd_line, macd_signal, macd_hist = series.macd(12, 26, 9)
    vwap = series.vwap(reset_daily=True)

    # Fibonacci retracement pivot from last major swing low/high
    fib50 = fib618 = None
    lows = [s for s in ms.swings if s.kind == "low"]
    highs = [s for s in ms.swings if s.kind == "high"]
    if lows and highs:
        lo = min(s.price for s in lows[-3:]) if len(lows) >= 1 else lows[-1].price
        hi = max(s.price for s in highs[-3:]) if len(highs) >= 1 else highs[-1].price
        if hi > lo:
            levels = fibonacci_levels(lo, hi)
            fib50 = levels["0.5"]
            fib618 = levels["0.618"]

    return Context(
        series, ms, sma8, sma21, sma200, bmid, bup, blo, atr,
        rsi, macd_line, macd_signal, macd_hist, vwap,
        fib50, fib618, higher_tf_trend,
    )


def _near(price: float, level: Optional[float], tol_pct: float = 0.35) -> bool:
    if level is None:
        return False
    return abs(price - level) / max(abs(price), EPS) <= tol_pct


# --------------------------------------------------------------------------
#  confluence scoring
# --------------------------------------------------------------------------
_CONFLUENCE_WEIGHTS = {
    "trend": 2.0,
    "counter_trend_reversal": 1.5,
    "support": 2.0,
    "resistance": 2.0,
    "ma21": 1.0,
    "ma8": 1.0,
    "fibonacci": 1.0,
    "trendline": 1.0,
    "bollinger": 1.0,
    "rsi": 1.5,
    "macd": 1.5,
    "vwap": 1.0,
    "vwap_bias": 1.0,
    "higher_timeframe": 2.0,
}


def _confluence(ctx: Context, i: int, side: str, pattern_kind: str) -> tuple:
    """Return (hit_factors list, weighted score, reasons list)."""
    c = ctx.series[i]
    price = c.close
    ms = ctx.ms
    hits: List[str] = []
    reasons: List[str] = []

    # trend alignment
    trend_ok = (ms.trend == "uptrend" and side == "BUY") or \
               (ms.trend == "downtrend" and side == "SELL")
    if trend_ok:
        hits.append("trend")
        reasons.append(f"signal in line with the {ms.trend}")

    # counter-trend reversal context (reversal pattern at end of opposite trend)
    if pattern_kind == "reversal":
        if side == "BUY" and ms.trend == "downtrend":
            hits.append("counter_trend_reversal")
            reasons.append("bullish reversal at the end of a downtrend")
        elif side == "SELL" and ms.trend == "uptrend":
            hits.append("counter_trend_reversal")
            reasons.append("bearish reversal at the end of an uptrend")

    # horizontal S/R
    sup = nearest_level(price, ms.supports)
    res = nearest_level(price, ms.resistances)
    if side == "BUY" and sup is not None:
        hits.append("support")
        reasons.append(f"near support {sup.price:.4g} ({sup.touches} touch)")
    if side == "SELL" and res is not None:
        hits.append("resistance")
        reasons.append(f"near resistance {res.price:.4g} ({res.touches} touch)")

    # dynamic MAs
    if _near(price, ctx.sma21[i]):
        hits.append("ma21")
        reasons.append("at the 21 SMA (dynamic S/R)")
    if _near(price, ctx.sma8[i]):
        hits.append("ma8")
        reasons.append("at the 8 SMA")

    # Fibonacci
    if ctx.fib50 and ctx.fib618:
        if _near(price, ctx.fib50) or _near(price, ctx.fib618):
            hits.append("fibonacci")
            reasons.append("at the 50%/61% Fibonacci retracement")

    # trendline
    tl = ctx.ms.support_line if side == "BUY" else ctx.ms.resistance_line
    if tl is not None and _near(price, tl.value_at(i), tol_pct=0.5):
        hits.append("trendline")
        reasons.append(f"at the {tl.kind} trendline")

    # Bollinger (range markets)
    band = ctx.boll_lo[i] if side == "BUY" else ctx.boll_up[i]
    if band is not None and _near(price, band, tol_pct=0.4):
        hits.append("bollinger")
        reasons.append("at the Bollinger band")

    # RSI(14) — oversold/overbought or momentum side of 50
    rsi_v = ctx.rsi[i] if i < len(ctx.rsi) else None
    rsi_prev = ctx.rsi[i - 1] if i > 0 and i - 1 < len(ctx.rsi) else None
    if rsi_v is not None:
        if side == "BUY" and rsi_v <= 40:
            hits.append("rsi")
            reasons.append(f"RSI({rsi_v:.0f}) oversold / bounce zone")
        elif side == "SELL" and rsi_v >= 60:
            hits.append("rsi")
            reasons.append(f"RSI({rsi_v:.0f}) overbought / fade zone")
        elif side == "BUY" and rsi_v >= 50 and rsi_prev is not None and rsi_v > rsi_prev:
            hits.append("rsi")
            reasons.append(f"RSI({rsi_v:.0f}) rising above 50 (bullish momentum)")
        elif side == "SELL" and rsi_v <= 50 and rsi_prev is not None and rsi_v < rsi_prev:
            hits.append("rsi")
            reasons.append(f"RSI({rsi_v:.0f}) falling below 50 (bearish momentum)")

    # MACD(12/26/9) — line vs signal alignment
    ml = ctx.macd_line[i] if i < len(ctx.macd_line) else None
    ms_line = ctx.macd_signal[i] if i < len(ctx.macd_signal) else None
    if ml is not None and ms_line is not None:
        if side == "BUY" and ml > ms_line:
            hits.append("macd")
            reasons.append("MACD bullish (line above signal)")
        elif side == "SELL" and ml < ms_line:
            hits.append("macd")
            reasons.append("MACD bearish (line below signal)")

    # VWAP — touch as dynamic mean + directional bias
    vw = ctx.vwap[i] if i < len(ctx.vwap) else None
    if vw is not None:
        if _near(price, vw, tol_pct=0.35):
            hits.append("vwap")
            reasons.append("at VWAP (session mean)")
        if side == "BUY" and price >= vw:
            hits.append("vwap_bias")
            reasons.append("price above VWAP (bullish bias)")
        elif side == "SELL" and price <= vw:
            hits.append("vwap_bias")
            reasons.append("price below VWAP (bearish bias)")

    # higher timeframe alignment
    if ctx.higher_tf_trend:
        aligns = (ctx.higher_tf_trend == "uptrend" and side == "BUY") or \
                 (ctx.higher_tf_trend == "downtrend" and side == "SELL")
        if aligns:
            hits.append("higher_timeframe")
            reasons.append(f"aligned with higher-timeframe {ctx.higher_tf_trend}")

    score = sum(_CONFLUENCE_WEIGHTS[h] for h in hits)
    return hits, round(score, 2), reasons


# --------------------------------------------------------------------------
#  entry / stop / target helpers
# --------------------------------------------------------------------------
def _target_from_level(entry: float, side: str, ctx: Context, i: int,
                       fallback_rr: float) -> float:
    """Prefer the next structural level as target; else use R:R multiple of risk."""
    ms = ctx.ms
    if side == "BUY":
        res = [l.price for l in ms.resistances if l.price > entry]
        if res:
            return min(res)
    else:
        sup = [l.price for l in ms.supports if l.price < entry]
        if sup:
            return max(sup)
    # fallback handled by caller
    return entry


# --------------------------------------------------------------------------
#  strategy signal builders
# --------------------------------------------------------------------------
def _pin_bar_signal(ctx: Context, i: int, pm: PatternMatch, tf: TimeframeConfig) -> Optional[Signal]:
    c = ctx.series[i]
    side = "BUY" if pm.direction == "bullish" else "SELL"
    buffer = 0.1 * c.total_range
    if side == "BUY":
        entry = c.close
        stop = c.low - buffer
        risk = entry - stop
        tgt = _target_from_level(entry, side, ctx, i, tf.min_rr)
        if tgt <= entry:
            tgt = entry + tf.min_rr * risk
    else:
        entry = c.close
        stop = c.high + buffer
        risk = stop - entry
        tgt = _target_from_level(entry, side, ctx, i, tf.min_rr)
        if tgt >= entry:
            tgt = entry - tf.min_rr * risk
    if risk <= EPS:
        return None
    reward = abs(tgt - entry)
    rr = reward / risk
    hits, score, reasons = _confluence(ctx, i, side, pm.kind)
    return Signal(side, "pin_bar", [pm.name], i, c.timestamp, entry, stop, tgt,
                  risk, reward, round(rr, 2), score,
                  min(score / 12.0, 1.0), hits, reasons, tf.key)


def _engulfing_signal(ctx: Context, i: int, pm: PatternMatch, tf: TimeframeConfig) -> Optional[Signal]:
    c = ctx.series[i]
    p = ctx.series[i - 1] if i - 1 >= 0 else None
    if p is None:
        return None
    side = "BUY" if pm.direction == "bullish" else "SELL"
    buffer = 0.1 * c.total_range
    if side == "BUY":
        entry = c.close
        stop = min(c.low, p.low) - buffer
        risk = entry - stop
        tgt = _target_from_level(entry, side, ctx, i, tf.min_rr)
        if tgt <= entry:
            tgt = entry + tf.min_rr * risk
    else:
        entry = c.close
        stop = max(c.high, p.high) + buffer
        risk = stop - entry
        tgt = _target_from_level(entry, side, ctx, i, tf.min_rr)
        if tgt >= entry:
            tgt = entry - tf.min_rr * risk
    if risk <= EPS:
        return None
    reward = abs(tgt - entry)
    rr = reward / risk
    hits, score, reasons = _confluence(ctx, i, side, pm.kind)
    return Signal(side, "engulfing_bar", [pm.name], i, c.timestamp, entry, stop, tgt,
                  risk, reward, round(rr, 2), score,
                  min(score / 12.0, 1.0), hits, reasons, tf.key)


def _inside_bar_signal(ctx: Context, i: int, pm: PatternMatch, tf: TimeframeConfig) -> Optional[Signal]:
    c = ctx.series[i]
    mother = ctx.series[i - 1] if i - 1 >= 0 else None
    if mother is None:
        return None
    # trade the breakout of the mother bar in the direction of the trend
    ms = ctx.ms
    if ms.trend == "uptrend":
        side = "BUY"
    elif ms.trend == "downtrend":
        side = "SELL"
    else:
        # ranging: use the pattern's own directional hint
        side = "BUY" if pm.direction == "bullish" else "SELL"
    buffer = 0.1 * mother.total_range
    if side == "BUY":
        entry = mother.high
        stop = mother.low - buffer
        risk = entry - stop
        tgt = _target_from_level(entry, side, ctx, i, tf.min_rr)
        if tgt <= entry:
            tgt = entry + tf.min_rr * risk
    else:
        entry = mother.low
        stop = mother.high + buffer
        risk = stop - entry
        tgt = _target_from_level(entry, side, ctx, i, tf.min_rr)
        if tgt >= entry:
            tgt = entry - tf.min_rr * risk
    if risk <= EPS:
        return None
    reward = abs(tgt - entry)
    rr = reward / risk
    hits, score, reasons = _confluence(ctx, i, side, pm.kind)
    return Signal(side, "inside_bar", [pm.name], i, c.timestamp, entry, stop, tgt,
                  risk, reward, round(rr, 2), score,
                  min(score / 12.0, 1.0), hits, reasons, tf.key)


def _false_breakout_signal(ctx: Context, i: int, tf: TimeframeConfig) -> Optional[Signal]:
    """Detect an inside-bar false breakout completing on bar i.

    mother (i-2) -> inside bar (i-1) -> break bar (i) that reverses back inside.
    """
    if i - 2 < 0:
        return None
    mother = ctx.series[i - 2]
    inside = ctx.series[i - 1]
    c = ctx.series[i]
    # inside bar validity: inside's range within mother's range
    if not (inside.high <= mother.high and inside.low >= mother.low):
        return None

    bullish_fb = c.low < mother.low and c.close >= mother.low  # broke down, reclaimed
    bearish_fb = c.high > mother.high and c.close <= mother.high  # broke up, rejected

    if not (bullish_fb or bearish_fb):
        return None

    side = "BUY" if bullish_fb else "SELL"
    buffer = 0.1 * c.total_range
    if side == "BUY":
        entry = c.close
        stop = c.low - buffer
        risk = entry - stop
        tgt = _target_from_level(entry, side, ctx, i, tf.min_rr)
        if tgt <= entry:
            tgt = entry + tf.min_rr * risk
    else:
        entry = c.close
        stop = c.high + buffer
        risk = stop - entry
        tgt = _target_from_level(entry, side, ctx, i, tf.min_rr)
        if tgt >= entry:
            tgt = entry - tf.min_rr * risk
    if risk <= EPS:
        return None
    reward = abs(tgt - entry)
    rr = reward / risk
    hits, score, reasons = _confluence(ctx, i, side, "reversal")
    # false breakouts are inherently a trap-reversal; add a reason
    reasons.append("inside-bar false breakout (stop-hunt / liquidity grab)")
    return Signal(side, "inside_bar_false_breakout", ["inside_bar"], i, c.timestamp,
                  entry, stop, tgt, risk, reward, round(rr, 2), score,
                  min(score / 12.0, 1.0), hits, reasons, tf.key)


def _classic_pattern_signal(ctx: Context, i: int, pm: PatternMatch,
                             tf: TimeframeConfig) -> Optional[Signal]:
    """Generic builder for star / soldiers / crows / kicker families.

    Mirrors trading_instructions() family rules numerically. Reuses the
    existing _confluence() scoring and _target_from_level() target logic so
    no new scoring/planning code is introduced.
    Skips neutral patterns (doji) — those need a confirm candle (out of scope).
    """
    if pm.direction == "neutral":
        return None  # doji needs confirmation; not in this subset

    c = ctx.series[i]
    side = "BUY" if pm.direction == "bullish" else "SELL"
    fam = pm.family
    n = int(PATTERNS.get(pm.name, {}).get("candles") or 1)
    start = max(0, i - n + 1)
    pat_candles = ctx.series[start : i + 1]
    if not pat_candles:
        return None

    buffer = 0.1 * c.total_range
    pat_low = min(k.low for k in pat_candles)
    pat_high = max(k.high for k in pat_candles)

    if fam in ("kicker", "belt-hold"):
        entry = c.close
        if side == "BUY":
            stop = c.low - buffer
        else:
            stop = c.high + buffer
    elif fam == "soldiers":
        entry = c.close
        stop = pat_candles[0].low - buffer   # first candle low
    elif fam == "crows":
        entry = c.close
        stop = pat_candles[0].high + buffer   # first candle high
    else:  # star + abandoned baby + doji_star
        entry = c.close
        stop = (pat_low - buffer) if side == "BUY" else (pat_high + buffer)

    risk = abs(entry - stop)
    if risk <= EPS:
        return None
    tgt = _target_from_level(entry, side, ctx, i, tf.min_rr)
    if side == "BUY" and tgt <= entry:
        tgt = entry + tf.min_rr * risk
    if side == "SELL" and tgt >= entry:
        tgt = entry - tf.min_rr * risk
    reward = abs(tgt - entry)
    rr = reward / risk
    if rr < tf.min_rr:
        return None

    hits, score, reasons = _confluence(ctx, i, side, pm.kind)
    # small family bonus for multi-candle conviction
    if fam in ("star", "soldiers", "crows") and n >= 3:
        score += 1.0
    if fam == "kicker" and c.body >= 0.5 * c.total_range:
        score += 1.0
    if fam == "belt-hold" and c.body >= 0.5 * c.total_range:
        score += 1.0

    return Signal(side, "classic_pattern", [pm.name], i, c.timestamp,
                  entry, stop, tgt, risk, reward, round(rr, 2), score,
                  min(score / 12.0, 1.0), hits, reasons, tf.key)


# --------------------------------------------------------------------------
#  main engine
# --------------------------------------------------------------------------
_STRATEGY_MAP = {
    "pin bar": _pin_bar_signal,
    "engulfing": _engulfing_signal,
    "inside bar": _inside_bar_signal,
    # classic families (star/soldiers/crows/kicker + all others via fallback below)
    "star": _classic_pattern_signal,
    "soldiers": _classic_pattern_signal,
    "crows": _classic_pattern_signal,
    "kicker": _classic_pattern_signal,
    "piercing": _classic_pattern_signal,
    "doji": _classic_pattern_signal,
    "harami-combo": _classic_pattern_signal,
    "engulfing-combo": _classic_pattern_signal,
    "tweezer": _classic_pattern_signal,
    "three-methods": _classic_pattern_signal,
    "swallow": _classic_pattern_signal,
    "separating-lines": _classic_pattern_signal,
    "belt-hold": _classic_pattern_signal,
    "three-line-strike": _classic_pattern_signal,
    "ladder": _classic_pattern_signal,
    "meeting-lines": _classic_pattern_signal,
}


def detect_signals(candles: Sequence[Candle], timeframe: str = "1h",
                   higher_tf_trend: Optional[str] = None,
                   lookback: Optional[int] = None) -> List[Signal]:
    """Generate actionable BUY/SELL signals for the series.

    `timeframe` selects calibration (1m/5m/15m/1h/1d).
    `higher_tf_trend` is the trend from the next-higher timeframe (top-down).
    """
    tf = TIMEFRAMES.get(timeframe, TIMEFRAMES["1h"])
    ctx = build_context(candles, higher_tf_trend)
    n = len(candles)
    signals: List[Signal] = []

    # scan the whole series (or a recent window) for all pattern families
    start = 0 if lookback is None else max(0, n - lookback)
    for i in range(start, n):
        # 1) false breakout (structural, detected separately)
        fb = _false_breakout_signal(ctx, i, tf)
        if fb:
            signals.append(fb)

        # 2) pattern-based strategies — specialized builders first, else classic
        for pm in detect_at(candles, i):
            builder = _STRATEGY_MAP.get(pm.family, _classic_pattern_signal)
            sig = builder(ctx, i, pm, tf)
            if sig:
                signals.append(sig)

    # filter by timeframe thresholds
    filtered: List[Signal] = []
    for s in signals:
        if s.score < tf.min_score:
            continue
        if s.rr < tf.min_rr:
            continue
        if tf.require_higher_tf and ctx.higher_tf_trend is None:
            # no top-down info => skip small-TF signals rather than guess
            continue
        filtered.append(s)

    filtered.sort(key=lambda s: (s.index, -s.score))
    return filtered


def latest_signals(candles: Sequence[Candle], timeframe: str = "1h",
                   higher_tf_trend: Optional[str] = None,
                   bars: int = 20) -> List[Signal]:
    """Signals from the most recent `bars` candles."""
    return detect_signals(candles, timeframe, higher_tf_trend, lookback=bars)



"""
Smart-money "trap & reverse" policy — the contrarian (10th-man) layer.

Where most retail traders chase a breakout, institutions do the opposite:
they push price through a key level to grab liquidity (stop-loss hunting),
then reverse it. This module detects those traps and produces the *reverse*
trade — selling into bull traps, buying bear traps and springs.

Trap types implemented:
    bull_trap        : breaks above resistance, closes back below (failed breakout up)
    bear_trap        : breaks below support, closes back above (failed breakout down)
    stop_hunt_short  : wick above a swing high, close back below (liquidity grab)
    stop_hunt_long   : wick below a swing low, close back above (liquidity grab)
    spring           : Wyckoff bullish — false breakdown then strong reclaim
    upthrust         : Wyckoff bearish — false breakout then strong rejection

Each signal carries a "crowd action" (what retail is doing) and a
"smart action" (what we do as the 10th man).
"""


from dataclasses import dataclass, field
from typing import List, Optional, Sequence



@dataclass
class TrapSignal:
    side: str                 # "BUY" | "SELL"
    trap_type: str
    index: int
    timestamp: Optional[float]
    entry: float
    stop: float
    target: float
    risk: float
    reward: float
    rr: float
    score: float
    confluence: List[str]
    reasons: List[str]
    crowd_action: str
    smart_action: str
    timeframe: str

    @property
    def direction(self) -> str:
        return "LONG" if self.side == "BUY" else "SHORT"

    def __repr__(self) -> str:
        return (f"TrapSignal({self.side}/{self.direction} {self.trap_type} @ {self.index} "
                f"RR={self.rr:.2f} score={self.score:.1f})")


def _recent_swing(ms: MarketStructure, i: int, kind: str) -> Optional[Swing]:
    cands = [s for s in ms.swings if s.index < i and s.kind == kind]
    return cands[-1] if cands else None


def _level(price: float, ms: MarketStructure, side: str) -> Optional[Level]:
    return nearest_level(price, ms.resistances if side == "SELL" else ms.supports)


def _build(entry: float, stop: float, target: float, side: str) -> tuple:
    risk = abs(entry - stop)
    reward = abs(target - entry)
    rr = reward / risk if risk > EPS else 0.0
    return risk, reward, rr


def _make_signal(ctx: Context, i: int, side: str, trap_type: str,
                 entry: float, stop: float, target: float,
                 crowd_action: str, smart_action: str,
                 tf: TimeframeConfig,
                 extra_reasons: Optional[List[str]] = None) -> TrapSignal:
    hits, score, reasons = _confluence(ctx, i, side, "reversal")
    if extra_reasons:
        reasons.extend(extra_reasons)
    risk, reward, rr = _build(entry, stop, target, side)
    c = ctx.series[i]
    return TrapSignal(side, trap_type, i, c.timestamp, entry, stop, target,
                      risk, reward, round(rr, 2), score, hits, reasons,
                      crowd_action, smart_action, tf.key)


def detect_traps(candles: Sequence[Candle], timeframe: str = "1h",
                 higher_tf_trend: Optional[str] = None,
                 lookback: Optional[int] = None) -> List[TrapSignal]:
    tf = TIMEFRAMES.get(timeframe, TIMEFRAMES["1h"])
    ctx = build_context(candles, higher_tf_trend)
    ms = ctx.ms
    n = len(candles)
    out: List[TrapSignal] = []

    start = 0 if lookback is None else max(0, n - lookback)
    for i in range(max(1, start), n):
        c = candles[i]
        p = candles[i - 1]
        buf = 0.1 * c.total_range

        swing_high = _recent_swing(ms, i, "high")
        swing_low = _recent_swing(ms, i, "low")

        # ---- bull trap / stop-hunt short (bearish reverse) ------------------
        if swing_high is not None:
            level_hi = swing_high.price
            broke_up = c.high > level_hi
            prev_closed_above = p.close > level_hi
            closed_back = c.close < level_hi
            if broke_up and closed_back:
                # failed breakout (classic bull trap) vs single-bar stop hunt
                trap_type = "bull_trap" if prev_closed_above else "stop_hunt_short"
                stop = max(level_hi, c.high) + buf
                target = None
                # next support target
                sup = [s.price for s in ms.supports if s.price < c.close]
                if sup:
                    target = max(sup)
                if target is None or target >= c.close:
                    target = c.close - tf.min_rr * (stop - c.close)
                sig = _make_signal(
                    ctx, i, "SELL", trap_type, c.close, stop, target,
                    crowd_action="retail buys the breakout above resistance",
                    smart_action="sell the failed breakout (fade the crowd)",
                    tf=tf,
                    extra_reasons=[f"price broke above {level_hi:.4g} then closed back below — {trap_type.replace('_', ' ')}"],
                )
                out.append(sig)

        # ---- bear trap / stop-hunt long (bullish reverse) -------------------
        if swing_low is not None:
            level_lo = swing_low.price
            broke_dn = c.low < level_lo
            prev_closed_below = p.close < level_lo
            closed_back = c.close > level_lo
            if broke_dn and closed_back:
                trap_type = "bear_trap" if prev_closed_below else "stop_hunt_long"
                stop = min(level_lo, c.low) - buf
                target = None
                res = [r.price for r in ms.resistances if r.price > c.close]
                if res:
                    target = min(res)
                if target is None or target <= c.close:
                    target = c.close + tf.min_rr * (c.close - stop)
                sig = _make_signal(
                    ctx, i, "BUY", trap_type, c.close, stop, target,
                    crowd_action="retail sells the breakdown below support",
                    smart_action="buy the failed breakdown (fade the crowd)",
                    tf=tf,
                    extra_reasons=[f"price broke below {level_lo:.4g} then closed back above — {trap_type.replace('_', ' ')}"],
                )
                out.append(sig)

        # ---- Wyckoff spring (bullish) / upthrust (bearish) ------------------
        if swing_low is not None and c.low < swing_low.price and c.close > c.open:
            # false breakdown with strong bullish reclaim
            reclaim = c.close >= c.midpoint + 0.5 * c.body
            if reclaim and c.close > swing_low.price:
                stop = min(swing_low.price, c.low) - buf
                res = [r.price for r in ms.resistances if r.price > c.close]
                target = min(res) if res else c.close + tf.min_rr * (c.close - stop)
                out.append(_make_signal(
                    ctx, i, "BUY", "spring", c.close, stop, target,
                    crowd_action="retail shorts the support break",
                    smart_action="buy the spring (institutional accumulation)",
                    tf=tf,
                    extra_reasons=["Wyckoff spring — false breakdown then strong reclaim"],
                ))

        if swing_high is not None and c.high > swing_high.price and c.close < c.open:
            reject = c.close <= c.midpoint - 0.5 * c.body
            if reject and c.close < swing_high.price:
                stop = max(swing_high.price, c.high) + buf
                sup = [s.price for s in ms.supports if s.price < c.close]
                target = max(sup) if sup else c.close - tf.min_rr * (stop - c.close)
                out.append(_make_signal(
                    ctx, i, "SELL", "upthrust", c.close, stop, target,
                    crowd_action="retail buys the resistance break",
                    smart_action="sell the upthrust (institutional distribution)",
                    tf=tf,
                    extra_reasons=["Wyckoff upthrust — false breakout then strong rejection"],
                ))

    # filter by timeframe thresholds
    filtered = [s for s in out if s.score >= tf.min_score and s.rr >= tf.min_rr]
    if tf.require_higher_tf and higher_tf_trend is None:
        filtered = []
    filtered.sort(key=lambda s: (s.index, -s.score))
    return filtered


# --------------------------------------------------------------------------
#  the 10th-man policy: combine standard signals + traps into one stance
# --------------------------------------------------------------------------
@dataclass
class SmartStance:
    action: str                 # "BUY" | "SELL" | "HOLD"
    source: str                 # "trap" | "signal" | "none"
    trap: Optional[TrapSignal] = None
    signal: Optional[Signal] = None
    narrative: str = ""


class SmartTradePolicy:
    """Soft 10th-man: standard pattern preferred; trap only without conflict or alone."""

    def __init__(self, candles: Sequence[Candle], timeframe: str = "1h",
                 higher_tf_trend: Optional[str] = None):
        self.candles = list(candles)
        self.timeframe = timeframe
        self.higher_tf_trend = higher_tf_trend

    def evaluate(self) -> SmartStance:
        traps = detect_traps(self.candles, self.timeframe, self.higher_tf_trend)
        signals = detect_signals(self.candles, self.timeframe,
                                            self.higher_tf_trend, lookback=10)

        latest_trap = traps[-1] if traps else None
        latest_sig = signals[-1] if signals else None
        last_i = len(self.candles) - 1
        fresh_trap = (
            latest_trap is not None and latest_trap.index == last_i
        )
        recent_trap = (
            latest_trap is not None and latest_trap.index >= last_i - 2
        )

        # Soft 10th-man: if a standard signal exists, prefer it.
        # Trap may annotate / confluence when same side; never override opposite side.
        if latest_sig is not None:
            if recent_trap and latest_trap.side == latest_sig.side:
                narrative = (
                    f"Soft 10th-man confluence: {latest_trap.trap_type.replace('_', ' ')} "
                    f"+ {latest_sig.strategy} both {latest_sig.side}."
                )
                return SmartStance(
                    latest_sig.side, "signal", latest_trap, latest_sig, narrative
                )
            if recent_trap and latest_trap.side != latest_sig.side:
                narrative = (
                    f"Soft 10th-man: standard {latest_sig.strategy} preferred over "
                    f"conflicting {latest_trap.trap_type.replace('_', ' ')} "
                    f"(crowd fade demoted)."
                )
                return SmartStance(
                    latest_sig.side, "signal", latest_trap, latest_sig, narrative
                )
            narrative = (
                f"Following standard {latest_sig.strategy} signal (soft 10th-man)."
            )
            return SmartStance(latest_sig.side, "signal", None, latest_sig, narrative)

        # No standard signal — allow a fresh trap only (not a 3-bar lookback override).
        if fresh_trap:
            narrative = (
                f"Soft 10th-man (no standard signal): {latest_trap.smart_action}. "
                f"While the crowd {latest_trap.crowd_action}, we take the opposite side."
            )
            return SmartStance(latest_trap.side, "trap", latest_trap, None, narrative)

        if latest_trap is not None:
            ago = last_i - latest_trap.index
            narrative = (
                f"Last trap ({latest_trap.trap_type.replace('_', ' ')}) was {ago} bar(s) ago — "
                f"not fresh; standing aside (soft 10th-man)."
            )
            return SmartStance("HOLD", "none", latest_trap, None, narrative)

        return SmartStance(
            "HOLD", "none", None, None,
            "No trap and no qualifying signal — stand aside.",
        )


def latest_trap(candles: Sequence[Candle], timeframe: str = "1h",
                higher_tf_trend: Optional[str] = None) -> Optional[TrapSignal]:
    traps = detect_traps(candles, timeframe, higher_tf_trend, lookback=10)
    return traps[-1] if traps else None



"""
Money management & position sizing (from the Candlestick Trading Bible).

Core rules encoded here:
  * risk no more than 2% of equity per trade (1% for beginners)
  * minimum 1:2 risk:reward
  * size positions in dollars-at-risk, not pips
  * always use a protective stop loss
"""


from dataclasses import dataclass
from typing import List, Optional, Sequence



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



"""
Signal backtesting engine.

Simulates each signal as an independent trade (entry at the next bar's open),
walks forward until stop-loss or profit-target is hit, and computes the usual
performance statistics: win rate, profit factor, expectancy, max drawdown.

Pure Python. No compounding by default (equal risk per trade) — this isolates
signal quality from money management.
"""


from dataclasses import dataclass, field
from typing import List, Optional, Sequence



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



"""
Binomial price-direction model — the machine-learning layer.

Inspired by "Automated Bitcoin Trading via Machine Learning Algorithms"
(Isaac Madan, Shaurya Saluja, Aojia Zhao — Stanford CS229). That paper framed
price prediction as *binomial classification* (predict the SIGN of the next
price change) using logistic regression / GLM and random forests, and reported
sensitivity / specificity / precision / accuracy.

This module reproduces that idea in pure Python:
  * feature engineering from OHLCV (returns, RSI, momentum, ATR, MA distance,
    candle anatomy, volatility)
  * logistic regression (gradient descent) and a random forest (CART bagging)
  * the same sensitivity / specificity / precision / accuracy report

No scikit-learn required.
"""


import math
import random
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple



# --------------------------------------------------------------------------
#  feature engineering
# --------------------------------------------------------------------------
def _std(values: Sequence[float]) -> float:
    n = len(values)
    if n == 0:
        return 0.0
    m = sum(values) / n
    return (sum((x - m) ** 2 for x in values) / n) ** 0.5


def build_features(candles: Sequence[Candle]) -> Tuple[List[List[float]], List[int], List[int]]:
    """Return (X, y, indices). X rows are feature vectors; y is sign(next change)."""
    series = CandleSeries(candles)
    n = len(candles)
    closes = series.closes
    rsi = series.rsi(14)
    atr = series.atr(14)
    sma21 = series.sma(21)
    sma200 = series.sma(200)

    X: List[List[float]] = []
    y: List[int] = []
    indices: List[int] = []

    for i in range(21, n - 1):  # need 21 lookback and 1 forward bar
        c = candles[i]
        ret1 = (closes[i] - closes[i - 1]) / closes[i - 1] if closes[i - 1] else 0.0
        ret3 = (closes[i] - closes[i - 3]) / closes[i - 3] if i >= 3 and closes[i - 3] else 0.0
        ret5 = (closes[i] - closes[i - 5]) / closes[i - 5] if i >= 5 and closes[i - 5] else 0.0
        ret10 = (closes[i] - closes[i - 10]) / closes[i - 10] if i >= 10 and closes[i - 10] else 0.0
        mom10 = closes[i] - closes[i - 10] if i >= 10 else 0.0
        atr_ratio = atr[i] / closes[i] if atr[i] and closes[i] else 0.0
        d21 = (closes[i] - sma21[i]) / closes[i] if sma21[i] and closes[i] else 0.0
        d200 = (closes[i] - sma200[i]) / closes[i] if sma200[i] and closes[i] else 0.0
        vol20 = _std([(closes[k] - closes[k - 1]) / closes[k - 1]
                      for k in range(i - 19, i + 1) if closes[k - 1]])

        features = [
            ret1, ret3, ret5, ret10,
            (rsi[i] - 50.0) / 50.0 if rsi[i] is not None else 0.0,
            mom10 / closes[i] if closes[i] else 0.0,
            atr_ratio, d21, d200,
            c.body_ratio, c.upper_ratio, c.lower_ratio,
            1.0 if c.is_doji(0.1) else 0.0,
            vol20,
        ]
        label = 1 if closes[i + 1] > closes[i] else 0
        X.append(features)
        y.append(label)
        indices.append(i)

    return X, y, indices


# --------------------------------------------------------------------------
#  scaler
# --------------------------------------------------------------------------
class StandardScaler:
    def __init__(self):
        self.mean = []
        self.std = []

    def fit(self, X: Sequence[Sequence[float]]):
        m = len(X)
        n = len(X[0])
        self.mean = [sum(row[j] for row in X) / m for j in range(n)]
        self.std = [_std([row[j] for row in X]) for j in range(n)]
        self.std = [s if s > EPS else 1.0 for s in self.std]
        return self

    def transform(self, X: Sequence[Sequence[float]]) -> List[List[float]]:
        return [[(row[j] - self.mean[j]) / self.std[j] for j in range(len(row))]
                for row in X]


# --------------------------------------------------------------------------
#  logistic regression (gradient descent)
# --------------------------------------------------------------------------
class LogisticRegression:
    def __init__(self, lr: float = 0.1, l2: float = 0.01, max_iter: int = 400):
        self.lr = lr
        self.l2 = l2
        self.max_iter = max_iter
        self.weights: List[float] = []

    @staticmethod
    def _sigmoid(z: float) -> float:
        if z >= 0:
            return 1.0 / (1.0 + math.exp(-z))
        e = math.exp(z)
        return e / (1.0 + e)

    def fit(self, X: Sequence[Sequence[float]], y: Sequence[int]):
        m = len(X)
        n = len(X[0])
        self.weights = [0.0] * (n + 1)  # + bias
        for _ in range(self.max_iter):
            # forward pass
            probs = []
            for row in X:
                z = self.weights[0] + sum(self.weights[j + 1] * row[j] for j in range(n))
                probs.append(self._sigmoid(z))
            # gradients
            grad = [0.0] * (n + 1)
            for i in range(m):
                err = probs[i] - y[i]
                grad[0] += err
                for j in range(n):
                    grad[j + 1] += err * X[i][j]
            for j in range(n + 1):
                grad[j] = grad[j] / m + (0.0 if j == 0 else self.l2 * self.weights[j])
            # update
            for j in range(n + 1):
                self.weights[j] -= self.lr * grad[j]
        return self

    def predict_proba(self, X: Sequence[Sequence[float]]) -> List[float]:
        n = len(self.weights) - 1
        out = []
        for row in X:
            z = self.weights[0] + sum(self.weights[j + 1] * row[j] for j in range(n))
            out.append(self._sigmoid(z))
        return out

    def predict(self, X: Sequence[Sequence[float]]) -> List[int]:
        return [1 if p >= 0.5 else 0 for p in self.predict_proba(X)]


# --------------------------------------------------------------------------
#  decision tree (CART) + random forest
# --------------------------------------------------------------------------
class _TreeNode:
    def __init__(self):
        self.feature: Optional[int] = None
        self.threshold: Optional[float] = None
        self.left = None
        self.right = None
        self.value: int = 0
        self.is_leaf = True


def _gini(y: Sequence[int]) -> float:
    m = len(y)
    if m == 0:
        return 0.0
    p1 = sum(y) / m
    p0 = 1.0 - p1
    return 1.0 - (p0 * p0 + p1 * p1)


class DecisionTree:
    def __init__(self, max_depth: int = 4, min_samples_split: int = 5,
                 max_features: Optional[int] = None, seed: int = 0):
        self.max_depth = max_depth
        self.min_samples_split = min_samples_split
        self.max_features = max_features
        self.rng = random.Random(seed)
        self.root = None

    def fit(self, X: Sequence[Sequence[float]], y: Sequence[int]):
        self.root = self._build(list(range(len(X))), X, y, 0)
        return self

    def _feature_pool(self, n_features: int) -> List[int]:
        if self.max_features is None or self.max_features >= n_features:
            return list(range(n_features))
        return self.rng.sample(range(n_features), self.max_features)

    def _build(self, idx: List[int], X, y, depth: int) -> _TreeNode:
        node = _TreeNode()
        node.value = 1 if sum(y[i] for i in idx) * 2 >= len(idx) else 0
        if depth >= self.max_depth or len(idx) < self.min_samples_split or _gini([y[i] for i in idx]) == 0.0:
            return node

        n_features = len(X[0])
        best = None  # (gini_gain, feature, threshold, left_idx, right_idx)
        for f in self._feature_pool(n_features):
            values = sorted({X[i][f] for i in idx})
            if len(values) < 2:
                continue
            for k in range(len(values) - 1):
                thr = (values[k] + values[k + 1]) / 2.0
                left = [i for i in idx if X[i][f] <= thr]
                right = [i for i in idx if X[i][f] > thr]
                if not left or not right:
                    continue
                g = (len(left) / len(idx)) * _gini([y[i] for i in left]) + \
                    (len(right) / len(idx)) * _gini([y[i] for i in right])
                if best is None or g < best[0]:
                    best = (g, f, thr, left, right)

        if best is None:
            return node
        _, f, thr, left, right = best
        node.is_leaf = False
        node.feature = f
        node.threshold = thr
        node.left = self._build(left, X, y, depth + 1)
        node.right = self._build(right, X, y, depth + 1)
        return node

    def _predict_one(self, node: _TreeNode, row: Sequence[float]) -> int:
        while not node.is_leaf:
            if row[node.feature] <= node.threshold:
                node = node.left
            else:
                node = node.right
        return node.value

    def predict(self, X: Sequence[Sequence[float]]) -> List[int]:
        return [self._predict_one(self.root, row) for row in X]


class RandomForest:
    def __init__(self, n_estimators: int = 15, max_depth: int = 4,
                 min_samples_split: int = 5, seed: int = 0):
        self.n_estimators = n_estimators
        self.max_depth = max_depth
        self.min_samples_split = min_samples_split
        self.seed = seed
        self.trees: List[DecisionTree] = []

    def fit(self, X: Sequence[Sequence[float]], y: Sequence[int]):
        m = len(X)
        n = len(X[0])
        max_features = max(1, int(math.sqrt(n)))
        for t in range(self.n_estimators):
            rng = random.Random(self.seed + t)
            # bootstrap sample
            sample_idx = [rng.randrange(m) for _ in range(m)]
            Xb = [X[i] for i in sample_idx]
            yb = [y[i] for i in sample_idx]
            tree = DecisionTree(self.max_depth, self.min_samples_split,
                                max_features, seed=self.seed + t)
            tree.fit(Xb, yb)
            self.trees.append(tree)
        return self

    def predict(self, X: Sequence[Sequence[float]]) -> List[int]:
        votes = [0] * len(X)
        for tree in self.trees:
            preds = tree.predict(X)
            for i, p in enumerate(preds):
                votes[i] += 1 if p == 1 else -1
        return [1 if v >= 0 else 0 for v in votes]


# --------------------------------------------------------------------------
#  metrics (matching the Madan paper's vocabulary)
# --------------------------------------------------------------------------
def classification_report(y_true: Sequence[int], y_pred: Sequence[int]) -> dict:
    tp = tn = fp = fn = 0
    for a, b in zip(y_true, y_pred):
        if a == 1 and b == 1:
            tp += 1
        elif a == 0 and b == 0:
            tn += 1
        elif a == 0 and b == 1:
            fp += 1
        elif a == 1 and b == 0:
            fn += 1
    accuracy = (tp + tn) / len(y_true) if y_true else 0.0
    sensitivity = tp / (tp + fn) if (tp + fn) else 0.0   # TPR
    specificity = tn / (tn + fp) if (tn + fp) else 0.0   # TNR
    precision = tp / (tp + fp) if (tp + fp) else 0.0     # PPV
    return {
        "accuracy": round(accuracy, 4),
        "sensitivity": round(sensitivity, 4),
        "specificity": round(specificity, 4),
        "precision": round(precision, 4),
        "n": len(y_true),
    }


def train_test_split(X, y, test_ratio: float = 0.3) -> Tuple:
    """Chronological split (no shuffling — respects time order)."""
    split = int(len(X) * (1 - test_ratio))
    return X[:split], X[split:], y[:split], y[split:]


# --------------------------------------------------------------------------
#  high-level model
# --------------------------------------------------------------------------
class PriceDirectionModel:
    """Predicts the sign of the next price change (binomial classification)."""

    def __init__(self, model: str = "logistic"):
        if model not in ("logistic", "forest"):
            raise ValueError("model must be 'logistic' or 'forest'")
        self.model_name = model
        self.model = None
        self.scaler = StandardScaler()
        self.metrics: dict = {}
        self.horizon: int = 1

    def fit(self, candles: Sequence[Candle], horizon: int = 1,
            test_ratio: float = 0.3):
        self.horizon = horizon
        X, y, idx = build_features(candles)
        if len(X) < 50:
            raise ValueError("not enough data to train (need >= 50 feature rows)")
        Xtr, Xte, ytr, yte = train_test_split(X, y, test_ratio)
        self.scaler.fit(Xtr)
        Xtr_s = self.scaler.transform(Xtr)
        Xte_s = self.scaler.transform(Xte)
        if self.model_name == "logistic":
            self.model = LogisticRegression()
        else:
            self.model = RandomForest()
        self.model.fit(Xtr_s, ytr)
        preds = self.model.predict(Xte_s)
        self.metrics = classification_report(yte, preds)
        return self

    def predict_latest(self, candles: Sequence[Candle]) -> dict:
        """Classify the most recent candle: will the next bar close higher?"""
        X, y, idx = build_features(candles)
        if not X:
            return {"error": "not enough data"}
        latest = X[-1]
        latest_s = self.scaler.transform([latest])
        proba = 1.0
        if hasattr(self.model, "predict_proba"):
            proba = self.model.predict_proba(latest_s)[0]
        label = self.model.predict(latest_s)[0]
        return {
            "label": "UP" if label == 1 else "DOWN",
            "probability_up": round(proba, 4),
            "index": idx[-1],
        }



"""
The BrainMaster — top-level orchestrator.

Brings together market structure, pattern recognition, the signal engine,
risk/money management, and the ML price-direction model into one analysis
that outputs an actionable BUY / SELL / HOLD verdict.

Supports top-down (multi-timeframe) analysis: feed higher-timeframe candles
to align the lower-timeframe signal with the bigger picture (per the Bible).
"""


from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence



@dataclass
class AnalysisResult:
    timeframe: str
    n_candles: int
    last_close: float
    market_structure: MarketStructure
    recent_patterns: List[PatternMatch]
    signals: List[Signal]
    latest_signal: Optional[Signal]
    verdict: str
    verdict_detail: str
    ml: dict
    risk_plan: Optional[TradePlan] = None
    traps: List = field(default_factory=list)
    latest_trap: Optional = None
    smart_stance: Optional = None

    def __repr__(self) -> str:
        return f"AnalysisResult({self.timeframe}, verdict={self.verdict})"


class BrainMaster:
    """Analyze one timeframe, optionally aligned with a higher timeframe."""

    def __init__(self, candles: Sequence[Candle], timeframe: str = "1h",
                 higher_tf_candles: Optional[Sequence[Candle]] = None,
                 equity: float = 10000.0, risk_pct: float = 1.0,
                 use_ml: bool = True):
        self.candles = list(candles)
        self.timeframe = timeframe
        self.equity = equity
        self.risk_pct = risk_pct
        self.use_ml = use_ml
        self.higher_tf_trend: Optional[str] = None
        if higher_tf_candles:
            htf_ms = analyze(higher_tf_candles)
            self.higher_tf_trend = htf_ms.trend

    # ------------------------------------------------------------------ #
    def analyze(self) -> AnalysisResult:
        candles = self.candles
        tf = TIMEFRAMES.get(self.timeframe, TIMEFRAMES["1h"])

        ms = analyze(candles)
        recent = last_matches(candles, n=6)
        signals = detect_signals(candles, self.timeframe, self.higher_tf_trend)

        # the actionable signal = the most recent one (completing on the last bar(s))
        latest = signals[-1] if signals else None

        # ML price-direction bias (Isaac Madan layer)
        ml_out: dict = {"enabled": False}
        if self.use_ml:
            try:
                model = PriceDirectionModel("logistic")
                model.fit(candles)
                ml_out = {"enabled": True, "model": "logistic",
                          "metrics": model.metrics,
                          "prediction": model.predict_latest(candles)}
            except Exception as e:
                ml_out = {"enabled": False, "error": str(e)}

        verdict, detail = self._decide(latest, ms, ml_out)

        plan = None
        if latest is not None:
            plan = plan_trade(self.equity, self.risk_pct, latest.side,
                                   latest.entry, latest.stop, latest.target)

        # trap & reverse (10th-man) policy
        policy = SmartTradePolicy(candles, self.timeframe, self.higher_tf_trend)
        stance = policy.evaluate()
        traps_list = detect_traps(candles, self.timeframe, self.higher_tf_trend)
        latest_trap = traps_list[-1] if traps_list else None

        return AnalysisResult(
            timeframe=self.timeframe,
            n_candles=len(candles),
            last_close=candles[-1].close,
            market_structure=ms,
            recent_patterns=recent,
            signals=signals,
            latest_signal=latest,
            verdict=verdict,
            verdict_detail=detail,
            ml=ml_out,
            risk_plan=plan,
            traps=traps_list,
            latest_trap=latest_trap,
            smart_stance=stance,
        )

    # ------------------------------------------------------------------ #
    def _decide(self, sig: Optional[Signal],
                ms: MarketStructure, ml_out: dict) -> tuple:
        if sig is None:
            return "HOLD", "no qualifying setup on the latest bar (insufficient confluence or R:R)."
        side = "BUY/LONG" if sig.side == "BUY" else "SELL/SHORT"
        detail = (
            f"{side} via {sig.strategy.replace('_', ' ')} "
            f"({', '.join(sig.patterns)}) — confluence score {sig.score:.1f}, "
            f"R:R {sig.rr:.1f}:1."
        )
        if ml_out.get("enabled") and ml_out.get("prediction"):
            detail += f" ML bias: {ml_out['prediction'].get('label', '?')}."
        return sig.side, detail


# --------------------------------------------------------------------------
#  multi-timeframe analysis
# --------------------------------------------------------------------------
TIMEFRAME_ORDER = ["1d", "1h", "15m", "5m", "1m"]


def analyze_timeframes(candles_map: Dict[str, Sequence[Candle]],
                       equity: float = 10000.0, risk_pct: float = 1.0,
                       use_ml: bool = True) -> Dict[str, AnalysisResult]:
    """Top-down analysis across all provided timeframes.

    `candles_map` maps timeframe key -> candle series. Higher timeframes are
    used to align the lower ones (1d aligns 1h, 1h aligns 15m, etc.).
    """
    results: Dict[str, AnalysisResult] = {}
    higher: Optional[Sequence[Candle]] = None
    for tf in TIMEFRAME_ORDER:
        if tf not in candles_map:
            continue
        bm = BrainMaster(candles_map[tf], timeframe=tf,
                         higher_tf_candles=higher,
                         equity=equity, risk_pct=risk_pct, use_ml=use_ml)
        results[tf] = bm.analyze()
        higher = candles_map[tf]
    return results


# --------------------------------------------------------------------------
#  report rendering
# --------------------------------------------------------------------------
def _fmt_price(v: Optional[float], nd: int = 4) -> str:
    return "—" if v is None else f"{v:.{nd}g}"


def render_report(res: AnalysisResult) -> str:
    lines: List[str] = []
    L = lines.append
    ms = res.market_structure
    tf = TIMEFRAMES.get(res.timeframe, None)
    label = tf.label if tf else res.timeframe

    L("=" * 72)
    L(f"  CANDLESTICK BRAIN — {label} ANALYSIS")
    L("=" * 72)
    L(f"  Bars: {res.n_candles}   Last close: {res.last_close:.4g}")
    L("")

    # verdict
    v = res.verdict
    icon = {"BUY": "BUY (LONG)", "SELL": "SELL (SHORT)", "HOLD": "HOLD / NO TRADE"}[v]
    L(f"  >>> VERDICT: {icon}")
    L(f"      {res.verdict_detail}")
    L("")

    # market structure
    L("-" * 72)
    L("  MARKET STRUCTURE")
    L("-" * 72)
    L(f"  Condition : {ms.trend.upper()}   (strength {ms.trend_strength})")
    if ms.supports:
        L("  Supports  : " + ", ".join(f"{s.price:.4g}({s.touches})" for s in ms.supports[-4:]))
    if ms.resistances:
        L("  Resistanc : " + ", ".join(f"{r.price:.4g}({r.touches})" for r in ms.resistances[-4:]))
    if ms.support_line:
        L(f"  Support TL: slope {ms.support_line.slope:.5g}  (value now {ms.support_line.value_at(res.n_candles-1):.4g})")
    if ms.resistance_line:
        L(f"  Resist  TL: slope {ms.resistance_line.slope:.5g}  (value now {ms.resistance_line.value_at(res.n_candles-1):.4g})")
    L("")

    # recent patterns
    L("-" * 72)
    L("  RECENT PATTERNS")
    L("-" * 72)
    if res.recent_patterns:
        for pm in res.recent_patterns[-8:]:
            info = pattern_info(pm.name)
            kind = info["kind"] if info else pm.kind
            L(f"  [{pm.index:>3}] {pm.name:<26} {pm.direction:<8} {kind}")
    else:
        L("  (none detected in the last 6 bars)")
    L("")

    # signal
    L("-" * 72)
    L("  SIGNAL")
    L("-" * 72)
    if res.latest_signal:
        s = res.latest_signal
        L(f"  Action    : {s.side} / {s.direction}")
        L(f"  Strategy  : {s.strategy.replace('_', ' ')}   ({', '.join(s.patterns)})")
        L(f"  Entry     : {s.entry:.4g}")
        L(f"  Stop-loss : {s.stop:.4g}")
        L(f"  Target    : {s.target:.4g}")
        L(f"  Risk/Rew  : 1 : {s.rr:.2f}   (score {s.score:.1f}/12)")
        L(f"  Confluence: {', '.join(s.confluence) if s.confluence else '—'}")
        if s.reasons:
            for r in s.reasons:
                L(f"      • {r}")
    else:
        L("  No qualifying signal on the latest bar.")
    L("")

    # trap & reverse (10th-man) policy
    L("-" * 72)
    L("  TRAP & REVERSE POLICY (10TH-MAN / SMART MONEY)")
    L("-" * 72)
    if res.smart_stance is not None:
        st = res.smart_stance
        L(f"  Smart stance : {st.action}")
        L(f"  {st.narrative}")
    if res.latest_trap:
        t = res.latest_trap
        L(f"  Trap         : {t.trap_type.replace('_', ' ')}")
        L(f"  Reverse trade: {t.side} / {t.direction}")
        L(f"  Entry / Stop / Target : {t.entry:.4g} / {t.stop:.4g} / {t.target:.4g}")
        L(f"  Risk/Rew     : 1 : {t.rr:.2f}   (score {t.score:.1f}/12)")
        L(f"  Crowd action : {t.crowd_action}")
        L(f"  Smart action : {t.smart_action}")
        if t.reasons:
            for r in t.reasons:
                L(f"      • {r}")
    elif res.traps:
        L(f"  {len(res.traps)} trap(s) detected earlier in the series; none fresh on the last bars.")
    else:
        L("  No trap / fake-breakout pattern detected in the recent window.")
    L("")

    # money management
    if res.risk_plan:
        p = res.risk_plan
        equity = p.risk_amount / (p.risk_pct / 100.0) if p.risk_pct else 0.0
        L("-" * 72)
        L("  MONEY MANAGEMENT")
        L("-" * 72)
        L(f"  Equity        : ${equity:,.0f}")
        L(f"  Risk per trade: {p.risk_pct:.1f}%  =>  ${p.risk_amount:.2f}")
        L(f"  Position size : {p.units:.3f} units (${p.risk_amount:.2f} / {p.risk:.4g} stop)")
        L(f"  If target hit : +${p.reward / p.risk * p.risk_amount:.2f}")
        L("")

    # ML
    if res.ml.get("enabled"):
        m = res.ml
        L("-" * 72)
        L("  ML PRICE-DIRECTION MODEL (binomial, sign of next change)")
        L("-" * 72)
        met = m.get("metrics", {})
        L(f"  Accuracy    : {met.get('accuracy', '—')}")
        L(f"  Sensitivity : {met.get('sensitivity', '—')}   Specificity : {met.get('specificity', '—')}")
        L(f"  Precision   : {met.get('precision', '—')}     (test n={met.get('n', '—')})")
        pred = m.get("prediction", {})
        if pred:
            L(f"  Next-bar bias: {pred.get('label')}  (P(up)={pred.get('probability_up')})")
        L("")

    L("  " + "=" * 68)
    return "\n".join(lines)




# =====================================================================
#  BRAIN — the natural-language reasoning layer (the "LLM brain")
# =====================================================================
# Everything above this line is the merged engine. This section adds a
# single `Brain` object that "thinks" through the market and *explains*
# its decision in plain English (chain-of-thought style), plus an
# interactive chat loop where you can ask it questions.
# =====================================================================

import csv
import math
import os
import random
import sys

_TIMEFRAME_ORDER = ["1m", "5m", "15m", "1h", "1d"]


# ---------------------------------------------------------------------
#  natural-language renderer
# ---------------------------------------------------------------------
def _nl_condition(ms):
    if ms.trend == "uptrend":
        return f"the market is in an UPTREND (strength {ms.trend_strength}) — it is making higher highs and higher lows"
    if ms.trend == "downtrend":
        return f"the market is in a DOWNTREND (strength {ms.trend_strength}) — it is making lower highs and lower lows"
    if ms.trend == "choppy":
        return "the market is CHOPPY — no clear direction and tight noise, so I would stand aside"
    return f"the market is RANGING (strength {ms.trend_strength}) — price is bouncing sideways between defined levels"


def _nl_levels(ms):
    parts = []
    if ms.supports:
        parts.append("support at " + ", ".join(f"{s.price:.4g}" for s in ms.supports[-3:]))
    if ms.resistances:
        parts.append("resistance at " + ", ".join(f"{r.price:.4g}" for r in ms.resistances[-3:]))
    return "; ".join(parts) if parts else "no obvious horizontal levels"


def _nl_signal(sig):
    if not sig:
        return None
    return (f"I have a {sig.side}/LONG signal" if sig.side == "BUY" else f"I have a {sig.side}/SHORT signal")


def render_reason(res):
    """Turn a structured analysis into a chain-of-thought narrative."""
    L = []
    a = L.append
    tf = res.get("timeframe", "1h")
    label = TIMEFRAMES.get(tf).label if tf in TIMEFRAMES else tf
    a(f"Let me read the {label} chart ({res['n']} bars, last close {res['last_close']:.4g}).")
    a("")

    ms = res["structure"]
    a(f"1) Market structure — {_nl_condition(ms)}.")
    lv = _nl_levels(ms)
    a(f"   Key levels: {lv}.")

    pats = res["patterns"]
    if pats:
        a("")
        a(f"2) Patterns — I can see near the close: " +
          ", ".join(f"{p.name} ({p.direction})" for p in pats[-6:]) + ".")
        a("   How to trade them:")
        seen = set()
        for p in reversed(pats):
            if p.name in seen:
                continue
            seen.add(p.name)
            t = trading_instructions(p.name)
            if t:
                a(f"     - {p.name}: {t['action']} — enter {t['entry']}.")
                a(f"         Stop {t['stop']}; target {t['target']}.")
                a(f"         {t['inverse']}")
            if len(seen) >= 3:
                break
    else:
        a("")
        a("2) Patterns — nothing conclusive on the last few bars.")

    a("")
    sig = res["signal"]
    if sig:
        a(f"3) Signal — {sig.side} (LONG)" if sig.side == "BUY" else f"3) Signal — {sig.side} (SHORT)")
        a(f"   Strategy: {sig.strategy.replace('_', ' ')} via {', '.join(sig.patterns)}.")
        a(f"   Entry {sig.entry:.4g} / stop {sig.stop:.4g} / target {sig.target:.4g}  (R:R 1:{sig.rr:.2f}, confluence {sig.score:.1f}/12).")
        for r in sig.reasons:
            a(f"     - {r}")
    else:
        a("3) Signal — no qualifying setup on the latest bar (insufficient confluence or R:R).")

    a("")
    stance = res["stance"]
    trap = res["trap"]
    a("4) Trap & reverse check (10th man) — " + stance.narrative)
    if trap:
        a(f"   Most recent trap: {trap.trap_type.replace('_',' ')} -> reverse trade {trap.side}/{trap.direction} "
          f"at {trap.entry:.4g} (R:R 1:{trap.rr:.2f}). Crowd: {trap.crowd_action}. Smart: {trap.smart_action}.")

    a("")
    if res["plan"]:
        p = res["plan"]
        a(f"5) Money management — risking {p.risk_pct:.1f}% (${p.risk_amount:,.2f}) on ${res['equity']:,.0f} equity "
          f"= {p.units:,.2f} units. If the target hits: +${p.reward / p.risk * p.risk_amount:,.2f}.")
    else:
        a("5) Money management — no position to size (HOLD).")

    a("")
    ml = res["ml"]
    if ml.get("metrics"):
        met = ml["metrics"]
        a(f"6) ML model — predicts the next bar {ml['prediction'].get('label')} "
          f"(P(up)={ml['prediction'].get('probability_up')}). Test accuracy {met.get('accuracy')}.")
    else:
        a("6) ML model — unavailable for this series.")

    a("")
    verdict = res["verdict"]
    detail = res["verdict_detail"]
    if verdict == "HOLD":
        a(f"MY CALL: HOLD / NO TRADE. {detail}")
    else:
        a(f"MY CALL: {verdict} ({'LONG' if verdict == 'BUY' else 'SHORT'}). {detail}")
    return "\n".join(L)


# ---------------------------------------------------------------------
#  the Brain object
# ---------------------------------------------------------------------
class Brain:
    """A self-contained, LLM-style trading brain.

    Feed it candles (as a dict of timeframe -> list[Candle], or a single
    list) and it will reason and explain its decisions in plain English.
    """

    def __init__(self, data=None, equity=10000.0, risk_pct=1.0):
        if data is None:
            data = {}
        if isinstance(data, dict):
            self.data = data
        else:  # a plain list -> assume 1h
            self.data = {"1h": list(data)}
        self.equity = equity
        self.risk_pct = risk_pct
        self._cache = {}

    # -- helpers ---------------------------------------------------------
    def _higher_tf_trend(self, tf):
        if tf not in _TIMEFRAME_ORDER:
            return None
        i = _TIMEFRAME_ORDER.index(tf)
        for htf in _TIMEFRAME_ORDER[i + 1:]:
            if htf in self.data:
                return analyze(self.data[htf]).trend
        return None

    # -- core analysis ----------------------------------------------------
    def think(self, tf="1h"):
        candles = self.data[tf]
        htf_trend = self._higher_tf_trend(tf)
        ms = analyze(candles)
        signals = detect_signals(candles, tf, htf_trend)
        traps_list = detect_traps(candles, tf, htf_trend)
        stance = SmartTradePolicy(candles, tf, htf_trend).evaluate()
        pats = last_matches(candles, 6)

        latest_sig = signals[-1] if signals else None
        latest_trap = traps_list[-1] if traps_list else None

        ml_out = {}
        try:
            m = PriceDirectionModel("logistic")
            m.fit(candles)
            ml_out = {"metrics": m.metrics, "prediction": m.predict_latest(candles)}
        except Exception as e:
            ml_out = {"error": str(e)}

        plan = None
        if latest_sig:
            plan = plan_trade(self.equity, self.risk_pct, latest_sig.side,
                              latest_sig.entry, latest_sig.stop, latest_sig.target)

        # final verdict (trap stance takes priority when it is a fresh trap)
        if stance.source == "trap":
            verdict = stance.action
            detail = stance.narrative
        elif latest_sig is not None:
            verdict = latest_sig.side
            detail = (f"{latest_sig.side} via {latest_sig.strategy.replace('_', ' ')} "
                      f"(confluence {latest_sig.score:.1f}, R:R 1:{latest_sig.rr:.2f}).")
        else:
            verdict = "HOLD"
            detail = "no qualifying setup."

        res = {
            "timeframe": tf,
            "n": len(candles),
            "last_close": candles[-1].close,
            "structure": ms,
            "patterns": pats,
            "signal": latest_sig,
            "signals": signals,
            "trap": latest_trap,
            "traps": traps_list,
            "stance": stance,
            "ml": ml_out,
            "plan": plan,
            "verdict": verdict,
            "verdict_detail": detail,
            "equity": self.equity,
        }
        self._cache[tf] = res
        return res

    def reason(self, tf="1h"):
        return render_reason(self.think(tf))

    # -- Q&A --------------------------------------------------------------
    def answer(self, question, tf="1h"):
        q = question.lower().strip()
        res = self.think(tf)
        sig = res["signal"]
        trap = res["trap"]
        ms = res["structure"]

        if any(k in q for k in ("buy", "long", "entry", "should i enter")):
            if sig and sig.side == "BUY":
                return (f"BUY/LONG: entry {sig.entry:.4g}, stop {sig.stop:.4g}, target {sig.target:.4g} "
                        f"(R:R 1:{sig.rr:.2f}, score {sig.score:.1f}).")
            return "No bullish setup right now — I'd stay flat."
        if any(k in q for k in ("sell", "short", "exit short")):
            if sig and sig.side == "SELL":
                return (f"SELL/SHORT: entry {sig.entry:.4g}, stop {sig.stop:.4g}, target {sig.target:.4g} "
                        f"(R:R 1:{sig.rr:.2f}, score {sig.score:.1f}).")
            return "No bearish setup right now — I'd stay flat."
        if any(k in q for k in ("trap", "reverse", "smart", "10th", "tenth", "fade")):
            if trap:
                return (f"10th-man view: {trap.smart_action}. Reverse trade {trap.side}/{trap.direction} "
                        f"entry {trap.entry:.4g}, stop {trap.stop:.4g}, target {trap.target:.4g} "
                        f"(R:R 1:{trap.rr:.2f}).")
            return "No trap / fake-breakout pattern on the latest bars."
        if any(k in q for k in ("trend", "market", "structure", "condition")):
            return f"Market structure: {_nl_condition(ms)}. {_nl_levels(ms)}."
        if any(k in q for k in ("risk", "size", "position", "money")):
            if res["plan"]:
                p = res["plan"]
                return (f"Risk {p.risk_pct:.1f}% = ${p.risk_amount:,.2f}; position {p.units:,.2f} units; "
                        f"target reward +${p.reward / p.risk * p.risk_amount:,.2f}.")
            return "No trade, so no position sizing."
        if any(k in q for k in ("ml", "model", "predict", "machine")):
            if res["ml"].get("metrics"):
                return (f"ML: next bar {res['ml']['prediction'].get('label')} "
                        f"(P(up)={res['ml']['prediction'].get('probability_up')}); "
                        f"accuracy {res['ml']['metrics'].get('accuracy')}.")
            return "ML model unavailable."
        if any(k in q for k in ("how to trade", "instruction", "trade plan", "inverse", "entry", "stop", "target")):
            if res["patterns"]:
                lines = ["Here's how I'd trade the recent patterns:"]
                seen = set()
                for p in reversed(res["patterns"]):
                    if p.name in seen:
                        continue
                    seen.add(p.name)
                    t = trading_instructions(p.name)
                    if t:
                        lines.append(f"- {p.name}: {t['action']} — enter {t['entry']}.")
                        lines.append(f"    Stop {t['stop']}; target {t['target']}.")
                        lines.append(f"    {t['inverse']}")
                    if len(seen) >= 3:
                        break
                return "\n".join(lines)
            return "No recent patterns to give trade instructions for."
        if any(k in q for k in ("pattern", "signal", "setup")):
            if res["patterns"]:
                return "Recent patterns: " + ", ".join(p.name for p in res["patterns"][-6:]) + "."
            return "No recent patterns."
        if any(k in q for k in ("explain", "why", "reason", "think", "analyze")):
            return self.reason(tf)
        if any(k in q for k in ("help", "?")):
            return ("Ask me: buy / sell / trend / trap / risk / ml / patterns / explain. "
                    "Example: 'should I buy?' or 'what is the smart money doing?'")
        # default: full reasoning
        return self.reason(tf)

    # -- interactive chat -------------------------------------------------
    def chat(self, tf="1h"):
        print("=" * 70)
        print("  CANDLESTICK BRAIN — interactive (type 'help', 'quit' to exit)")
        print("=" * 70)
        while True:
            try:
                q = input("\nYou: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nBye.")
                break
            if not q:
                continue
            if q.lower() in ("quit", "exit", "q"):
                print("Brain: bye.")
                break
            print("\nBrain: " + self.answer(q, tf))


# ---------------------------------------------------------------------
#  synthetic data + CSV (self-contained demo)
# ---------------------------------------------------------------------
def generate_synthetic(n=30000, start=100.0, seed=42):
    rng = random.Random(seed)
    closes = [start]
    drift = 0.0
    reg = rng.randint(80, 250)
    for i in range(n - 1):
        if i % reg == 0:
            drift = rng.choice([0.0006, -0.0006, 0.0, 0.0])
            reg = rng.randint(80, 250)
        closes.append(closes[-1] * (1.0 + rng.gauss(drift, 0.0016)))
    rng2 = random.Random(7)
    candles = []
    for i, c in enumerate(closes):
        prev = closes[i - 1] if i else c
        o = prev * (1.0 + rng2.gauss(0.0, 0.0004))
        hi = max(o, c) + abs(c) * 0.0015 * rng2.random()
        lo = min(o, c) - abs(c) * 0.0015 * rng2.random()
        if rng2.random() < 0.04:
            if rng2.random() < 0.5:
                lo = min(o, c) - abs(c) * 0.006 * rng2.random()
            else:
                hi = max(o, c) + abs(c) * 0.006 * rng2.random()
        candles.append(Candle(o, hi, lo, c, rng2.uniform(100, 10000), timestamp=float(i)))
    return candles


def _resample(candles, factor):
    out = []
    for i in range(0, len(candles), factor):
        chunk = candles[i:i + factor]
        if not chunk:
            continue
        out.append(Candle(chunk[0].open, max(c.high for c in chunk),
                          min(c.low for c in chunk), chunk[-1].close,
                          sum(c.volume for c in chunk), chunk[0].timestamp))
    return out


def _demo_data():
    base = generate_synthetic(30000, 100.0, 42)
    daily = generate_synthetic(400, 100.0, 99)
    return {
        "1m": base[-4000:],
        "5m": _resample(base, 5),
        "15m": _resample(base, 15),
        "1h": _resample(base, 60),
        "1d": daily,
    }


_ALIASES = {
    "time": ["timestamp", "date", "datetime", "time"],
    "open": ["open", "o"],
    "high": ["high", "h"],
    "low": ["low", "l"],
    "close": ["close", "c", "adj close", "adjusted close"],
    "volume": ["volume", "vol", "v"],
}


def load_csv(path):
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append({k.strip().lower(): v for k, v in r.items()})
    if not rows:
        raise ValueError("empty CSV")

    def find(names):
        for n in names:
            if n in rows[0]:
                return rows[0][n]
        return None

    time_col = find(_ALIASES["time"])
    open_col = find(_ALIASES["open"])
    high_col = find(_ALIASES["high"])
    low_col = find(_ALIASES["low"])
    close_col = find(_ALIASES["close"])
    vol_col = find(_ALIASES["volume"])
    if not (open_col and high_col and low_col and close_col):
        raise ValueError("could not detect OHLC columns")

    candles = []
    for i, r in enumerate(rows):
        try:
            candles.append(Candle(float(r[open_col]), float(r[high_col]),
                                  float(r[low_col]), float(r[close_col]),
                                  float(r[vol_col]) if vol_col else 0.0, i))
        except (ValueError, KeyError):
            continue
    return candles


# ---------------------------------------------------------------------
#  entry point
# ---------------------------------------------------------------------
def _main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = sys.argv[1:]
    if args and args[0] in ("--chat", "-c"):
        Brain(_demo_data()).chat()
    elif args and args[0].lower().endswith(".csv"):
        tf = args[1] if len(args) > 1 else "1h"
        print(Brain({tf: load_csv(args[0])}).reason(tf))
    else:
        data = _demo_data()
        b = Brain(data)
        for tf in _TIMEFRAME_ORDER:
            if tf in data:
                print(b.reason(tf))
                print()
        # quick backtest on 1h
        sigs = detect_signals(data["1h"], timeframe="1h")
        bt = backtest(data["1h"], sigs)
        m = bt.metrics
        print("=" * 70)
        print("  1H BACKTEST — all qualifying signals, equal-risk sizing")
        print("=" * 70)
        print(f"  Signals: {len(sigs)}  trades: {m.get('trades')}  win rate: {m.get('win_rate', 0) * 100:.1f}%  "
              f"profit factor: {m.get('profit_factor')}  expectancy: {m.get('expectancy_r')} R")


if __name__ == "__main__":
    _main()
