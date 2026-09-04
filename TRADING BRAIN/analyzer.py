"""
The BrainMaster — top-level orchestrator.

Brings together market structure, pattern recognition, the signal engine,
risk/money management, and the ML price-direction model into one analysis
that outputs an actionable BUY / SELL / HOLD verdict.

Supports top-down (multi-timeframe) analysis: feed higher-timeframe candles
to align the lower-timeframe signal with the bigger picture (per the Bible).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .candles import Candle
from . import knowledge, patterns, structure, strategies, risk, backtest, ml


@dataclass
class AnalysisResult:
    timeframe: str
    n_candles: int
    last_close: float
    market_structure: structure.MarketStructure
    recent_patterns: List[patterns.PatternMatch]
    signals: List[strategies.Signal]
    latest_signal: Optional[strategies.Signal]
    verdict: str
    verdict_detail: str
    ml: dict
    risk_plan: Optional[risk.TradePlan] = None

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
            htf_ms = structure.analyze(higher_tf_candles)
            self.higher_tf_trend = htf_ms.trend

    # ------------------------------------------------------------------ #
    def analyze(self) -> AnalysisResult:
        candles = self.candles
        tf = strategies.TIMEFRAMES.get(self.timeframe, strategies.TIMEFRAMES["1h"])

        ms = structure.analyze(candles)
        recent = patterns.last_matches(candles, n=6)
        signals = strategies.detect_signals(candles, self.timeframe, self.higher_tf_trend)

        # the actionable signal = the most recent one (completing on the last bar(s))
        latest = signals[-1] if signals else None

        # ML price-direction bias (Isaac Madan layer)
        ml_out: dict = {"enabled": False}
        if self.use_ml:
            try:
                model = ml.PriceDirectionModel("logistic")
                model.fit(candles)
                ml_out = {"enabled": True, "model": "logistic",
                          "metrics": model.metrics,
                          "prediction": model.predict_latest(candles)}
            except Exception as e:
                ml_out = {"enabled": False, "error": str(e)}

        verdict, detail = self._decide(latest, ms, ml_out)

        plan = None
        if latest is not None:
            plan = risk.plan_trade(self.equity, self.risk_pct, latest.side,
                                   latest.entry, latest.stop, latest.target)

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
        )

    # ------------------------------------------------------------------ #
    def _decide(self, sig: Optional[strategies.Signal],
                ms: structure.MarketStructure, ml_out: dict) -> tuple:
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
    tf = strategies.TIMEFRAMES.get(res.timeframe, None)
    label = tf.label if tf else res.timeframe

    L("=" * 72)
    L(f"  CANDLESTICK BRAIN — {label} ANALYSIS")
    L("=" * 72)
    L(f"  Bars: {res.n_candles}   Last close: {res.last_close:.4g}")
    L("")

    # verdict
    v = res.verdict
    icon = {"BUY": "🟢 BUY (LONG)", "SELL": "🔴 SELL (SHORT)", "HOLD": "⚪ HOLD / NO TRADE"}[v]
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
            info = knowledge.pattern_info(pm.name)
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
