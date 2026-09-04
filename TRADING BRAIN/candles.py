"""
Candle primitives and technical indicators.

A candlestick is defined by open / high / low / close (OHLC) over one time unit,
plus optional volume. All pattern logic in this package operates on these
primitives and the ratios derived from them.

Everything is pure Python (no numpy), so it runs anywhere Python 3.8+ runs.
"""

from __future__ import annotations

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
                # timestamps are seconds (brain_adapter divides ms by 1000)
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
