"""Build polished fire_trade_engine.py from pettern-4.txt Chapters 1–7."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(r"c:\Users\PC\Desktop\aitrads.in")
src = (ROOT / "pettern -4.txt").read_text(encoding="utf-8")
start = src.find("class SignalType")
end = src.find("# CHAPTER 8: MACHINE LEARNING", start)
body = src[start:end]
body = body.replace("class TradeSignal:", "class EnsembleTradeSignal:")

# Ensure PatternResult has pattern_low/high + post_init
if "pattern_low" not in body.split("class PatternResult")[1][:900]:
    body = body.replace(
        "additional_features: Dict = field(default_factory=dict)",
        "additional_features: Dict = field(default_factory=dict)\n"
        "    pattern_low: float = 0.0\n"
        "    pattern_high: float = 0.0",
        1,
    )

old_to_signal = '''    def to_signal_value(self) -> int:
        """Signal को numeric value में convert करें"""
        return self.signal.value
'''
new_to_signal = '''    def __post_init__(self):
        if self.candlesticks and (not self.pattern_low or not self.pattern_high):
            self.pattern_low = min(c.low for c in self.candlesticks)
            self.pattern_high = max(c.high for c in self.candlesticks)

    def to_signal_value(self) -> int:
        return self.signal.value
'''
if old_to_signal in body:
    body = body.replace(old_to_signal, new_to_signal, 1)

header = '''"""Fire Trade Engine v3.1 — polished from pettern-4 (Chapters 1–7).

Live path: candlestick patterns + shadow psychology + market structure +
EMA/MACD/ADX/RSI confluence → LONG/SHORT with ATR-padded SL and 1:2 TP.

ML/DQN chapters intentionally omitted (no torch/tensorflow/lightgbm in live bot).
"""
from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Tuple, Union, Callable

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
logger = logging.getLogger("FireTradeEngine")

'''

footer = Path(ROOT / "backend" / "_fire_engine_footer.py").read_text(encoding="utf-8")
out = header + body + "\n" + footer
(ROOT / "backend" / "fire_trade_engine.py").write_text(out, encoding="utf-8")
print("OK", len(out))
