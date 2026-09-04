"""
Market structure analysis — the "context" layer.

Derived from the Candlestick Trading Bible's market-structure chapter:
  * trending vs ranging vs choppy
  * swing points (HH/HL, LH/LL)
  * horizontal support & resistance (clustered swing levels)
  * trendlines (linear regression through swing points)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from .candles import Candle, EPS


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
    highs = [s.price for s in swings if s.kind == "high"]
    lows = [s.price for s in swings if s.kind == "low"]
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
