"""
candlestick-brain — a deep, self-contained Python "brain" for candlestick trading.

Encodes the knowledge distilled from three source documents:

1. "38 Candlestick Patterns for Pro Traders" (Groww)  — 21 bullish + 17 bearish patterns
2. "Automated Bitcoin Trading via Machine Learning Algorithms" (Isaac Madan, Stanford CS229)
   — binomial price-direction classification with GLM / Random Forest
3. "The Candlestick Trading Bible" — market structure, top-down analysis,
   four price-action strategies (pin bar, engulfing bar, inside bar, inside-bar false breakout),
   confluence, and money management.

Pure Python standard library only — no numpy/pandas/scikit-learn required.

Sub-modules:
    candles    — Candle / CandleSeries + technical indicators
    knowledge  — the pattern knowledge base (definitions, psychology, signals)
    patterns   — quantitative candlestick pattern recognition
    structure  — market structure: trends, swings, support/resistance, trendlines
    strategies — trading strategies + confluence scoring
    risk       — money management & position sizing
    backtest   — signal backtesting engine
    ml         — binomial price-direction model (logistic regression / random forest)
    analyzer   — BrainMaster orchestrator + human-readable report
"""

__version__ = "1.0.0"
__all__ = [
    "candles", "knowledge", "patterns", "structure",
    "strategies", "risk", "backtest", "ml", "analyzer",
]
