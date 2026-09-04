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

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from .candles import Candle, CandleSeries, EPS, fibonacci_levels, nearest
from . import knowledge, patterns, structure


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
    "1m": TimeframeConfig("1m", "1-Minute", min_score=6.0, min_rr=2.0,
                          require_higher_tf=True, noise_guard=True,
                          note="Highest noise. Require strong confluence + top-down alignment."),
    "5m": TimeframeConfig("5m", "5-Minute", min_score=5.0, min_rr=2.0,
                          require_higher_tf=True, noise_guard=True,
                          note="High noise. Trade only with the higher-timeframe trend."),
    "15m": TimeframeConfig("15m", "15-Minute", min_score=4.0, min_rr=2.0,
                           require_higher_tf=True, noise_guard=True,
                           note="Moderate noise. Favor setups aligned with 1H structure."),
    "1h": TimeframeConfig("1h", "1-Hour", min_score=3.0, min_rr=2.0,
                          require_higher_tf=False, noise_guard=False,
                          note="Primary price-action timeframe (per the Bible)."),
    "1d": TimeframeConfig("1d", "1-Day", min_score=3.0, min_rr=2.0,
                          require_higher_tf=False, noise_guard=False,
                          note="Cleanest signals; highest reliability. Use weekly top-down."),
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
    ms: structure.MarketStructure
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
    ms = structure.analyze(candles)
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
    sup = structure.nearest_level(price, ms.supports)
    res = structure.nearest_level(price, ms.resistances)
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
def _pin_bar_signal(ctx: Context, i: int, pm: patterns.PatternMatch, tf: TimeframeConfig) -> Optional[Signal]:
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


def _engulfing_signal(ctx: Context, i: int, pm: patterns.PatternMatch, tf: TimeframeConfig) -> Optional[Signal]:
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


def _inside_bar_signal(ctx: Context, i: int, pm: patterns.PatternMatch, tf: TimeframeConfig) -> Optional[Signal]:
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


# --------------------------------------------------------------------------
#  main engine
# --------------------------------------------------------------------------
_STRATEGY_MAP = {
    "pin bar": _pin_bar_signal,
    "engulfing": _engulfing_signal,
    "inside bar": _inside_bar_signal,
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

    # scan the whole series (or a recent window) for the 4 strategies
    start = 0 if lookback is None else max(0, n - lookback)
    for i in range(start, n):
        # 1) false breakout (structural, detected separately)
        fb = _false_breakout_signal(ctx, i, tf)
        if fb:
            signals.append(fb)

        # 2) pattern-based strategies
        for pm in patterns.detect_at(candles, i):
            builder = _STRATEGY_MAP.get(pm.family)
            if builder is None:
                continue
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
