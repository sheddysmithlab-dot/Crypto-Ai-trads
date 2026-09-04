"""
Quantitative candlestick pattern recognition.

`detect_at(candles, i)` returns every pattern that *completes* on candle index `i`.
`scan(candles)` runs detection across the whole series.

Rules are quantitative (body / wick ratios, engulf relations, gaps) using the
definitions encoded in `knowledge.py`. Thresholds are module constants so they
can be tuned.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from .candles import Candle, EPS
from . import knowledge

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
    lower_long = c.lower_shadow >= PIN_TAIL_MULT * c.body
    upper_long = c.upper_shadow >= PIN_TAIL_MULT * c.body
    ctx = _context_trend(cs, i)
    if lower_long and c.upper_shadow <= 0.5 * c.body:
        if ctx == "down":
            out.append(PatternMatch("hammer", i, "bullish", "reversal", "pin bar"))
        elif ctx == "up":
            out.append(PatternMatch("hanging_man", i, "bearish", "reversal", "pin bar"))
        else:
            out.append(PatternMatch("hammer", i, "bullish", "reversal", "pin bar"))
    if upper_long and c.lower_shadow <= 0.5 * c.body:
        if ctx == "up":
            out.append(PatternMatch("shooting_star", i, "bearish", "reversal", "pin bar"))
        elif ctx == "down":
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
