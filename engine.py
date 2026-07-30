"""Fire Trade Engine v3.1 — polished from pettern-4 (Chapters 1–7).

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

class SignalType(Enum):
    """Trading signal types"""
    STRONG_BUY = 2
    BUY = 1
    NEUTRAL = 0
    SELL = -1
    STRONG_SELL = -2


class PatternCategory(Enum):
    """Pattern categorization"""
    SINGLE_CANDLE = auto()
    TWO_CANDLE = auto()
    THREE_CANDLE = auto()
    MULTI_CANDLE = auto()


class TrendDirection(Enum):
    """Trend direction"""
    UP = "up"
    DOWN = "down"
    SIDEWAYS = "sideways"
    UNKNOWN = "unknown"


class SegmentType(Enum):
    """Trend segment types"""
    IMPULSIVE = "impulsive"      # Strong move in trend direction
    RETRACEMENT = "retracement"  # Counter-trend pullback
    CONSOLIDATION = "consolidation"  # Sideways movement


class TradingAction(Enum):
    """Trading actions for DQN"""
    BUY = 1
    HOLD = 0
    SELL = -1


@dataclass
class Candlestick:
    """
    जापानी कैंडलस्टिक - OHLCV डेटा का प्रतिनिधित्व करता है
    
    Anatomy:
    - Real Body: Open और Close के बीच का भरा हुआ हिस्सा
    - Upper Shadow: High से लेकर Body तक की पतली लकीर
    - Lower Shadow: Low से लेकर Body तक की पतली लकीर
    
    Psychology:
    - Bullish (Close > Open): खरीदारों का नियंत्रण
    - Bearish (Close < Open): विक्रेताओं का नियंत्रण
    - Doji (Close ≈ Open): अनिश्चितता, कोई नियंत्रण नहीं
    """
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    index: int = 0  # Position in the data array
    
    # ===== COMPUTED PROPERTIES =====
    
    @property
    def body(self) -> float:
        """Real body की लंबाई"""
        return abs(self.close - self.open)
    
    @property
    def upper_shadow(self) -> float:
        """Upper shadow - session high पर rejection का संकेत"""
        return self.high - max(self.open, self.close)
    
    @property
    def lower_shadow(self) -> float:
        """Lower shadow - session low पर rejection का संकेत"""
        return min(self.open, self.close) - self.low
    
    @property
    def is_bullish(self) -> bool:
        """Close > Open = खरीदारों का नियंत्रण"""
        return self.close > self.open
    
    @property
    def is_bearish(self) -> bool:
        """Close < Open = विक्रेताओं का नियंत्रण"""
        return self.close < self.open
    
    @property
    def is_doji(self) -> bool:
        """Open ≈ Close = अनिश्चितता (body < 10% of range)"""
        if self.total_range == 0:
            return True
        return self.body < self.total_range * 0.1
    
    @property
    def total_range(self) -> float:
        """कैंडल की कुल प्राइस रेंज"""
        return self.high - self.low
    
    @property
    def body_ratio(self) -> float:
        """Body / Total Range ratio"""
        if self.total_range == 0:
            return 0.0
        return self.body / self.total_range
    
    @property
    def upper_shadow_ratio(self) -> float:
        """Upper Shadow / Total Range ratio"""
        if self.total_range == 0:
            return 0.0
        return self.upper_shadow / self.total_range
    
    @property
    def lower_shadow_ratio(self) -> float:
        """Lower Shadow / Total Range ratio"""
        if self.total_range == 0:
            return 0.0
        return self.lower_shadow / self.total_range
    
    @property
    def body_position(self) -> float:
        """
        Body की position कैंडल रेंज में
        Returns: -1 (bottom) से 1 (top) के बीच
        """
        if self.total_range == 0:
            return 0.0
        midpoint = (self.high + self.low) / 2
        body_midpoint = (self.open + self.close) / 2
        return (body_midpoint - midpoint) / (self.total_range / 2)
    
    @property
    def body_top(self) -> float:
        """Body का ऊपरी सिरा"""
        return max(self.open, self.close)
    
    @property
    def body_bottom(self) -> float:
        """Body का निचला सिरा"""
        return min(self.open, self.close)
    
    @property
    def mid_price(self) -> float:
        """कैंडल का मध्य मूल्य"""
        return (self.high + self.low) / 2
    
    @property
    def typical_price(self) -> float:
        """Typical Price = (High + Low + Close) / 3"""
        return (self.high + self.low + self.close) / 3
    
    @property
    def is_marubozu(self) -> bool:
        """Marubozu - कोई shadow नहीं, पूरी body"""
        return self.upper_shadow_ratio < 0.05 and self.lower_shadow_ratio < 0.05
    
    @property
    def is_hammer_like(self) -> bool:
        """Hammer-like - लंबी lower shadow, छोटी upper shadow"""
        return (self.lower_shadow_ratio > 0.6 and 
                self.upper_shadow_ratio < 0.2 and
                self.body_ratio < 0.4)
    
    @property
    def is_shooting_star_like(self) -> bool:
        """Shooting Star-like - लंबी upper shadow, छोटी lower shadow"""
        return (self.upper_shadow_ratio > 0.6 and 
                self.lower_shadow_ratio < 0.2 and
                self.body_ratio < 0.4)
    
    # ===== METHODS =====
    
    def to_ohlcv_array(self) -> np.ndarray:
        """OHLCV numpy array में convert करें"""
        return np.array([self.open, self.high, self.low, self.close, self.volume])
    
    def to_feature_vector(self) -> np.ndarray:
        """पैटर्न recognition के लिए feature vector"""
        return np.array([
            self.body_ratio,
            self.upper_shadow_ratio,
            self.lower_shadow_ratio,
            self.body_position,
            1.0 if self.is_bullish else 0.0,
            1.0 if self.is_doji else 0.0,
            self.body / (self.mid_price if self.mid_price > 0 else 1)
        ])
    
    def __repr__(self) -> str:
        direction = "🟢" if self.is_bullish else "🔴" if self.is_bearish else "⚪"
        return f"{direction} {self.timestamp} O:{self.open:.2f} H:{self.high:.2f} L:{self.low:.2f} C:{self.close:.2f}"


@dataclass
class PatternResult:
    """पैटर्न detection का result"""
    pattern_type: str
    pattern_category: PatternCategory
    signal: SignalType
    confidence: float  # 0.0 to 1.0
    start_index: int
    end_index: int
    description: str
    psychology: str
    candlesticks: List[Candlestick]
    volume_confirmation: bool = False
    trend_context: str = ""
    additional_features: Dict = field(default_factory=dict)
    pattern_low: float = 0.0
    pattern_high: float = 0.0
    
    def __post_init__(self):
        if self.candlesticks and (not self.pattern_low or not self.pattern_high):
            self.pattern_low = min(c.low for c in self.candlesticks)
            self.pattern_high = max(c.high for c in self.candlesticks)

    def to_signal_value(self) -> int:
        return self.signal.value


@dataclass
class SwingPoint:
    """Swing High या Swing Low"""
    index: int
    price: float
    point_type: str  # 'high' or 'low'
    timestamp: datetime
    strength: float = 1.0  # कितना strong swing है
    surrounding_candles: List[Candlestick] = field(default_factory=list)
    
    @property
    def is_high(self) -> bool:
        return self.point_type == 'high'
    
    @property
    def is_low(self) -> bool:
        return self.point_type == 'low'


@dataclass
class TrendSegment:
    """प्राइस मूवमेंट का एक segment"""
    start_index: int
    end_index: int
    start_price: float
    end_price: float
    segment_type: SegmentType
    direction: TrendDirection
    price_change: float = 0.0
    price_change_pct: float = 0.0
    duration: int = 0
    volatility: float = 0.0
    volume_profile: float = 0.0
    
    def __post_init__(self):
        self.price_change = self.end_price - self.start_price
        if self.start_price > 0:
            self.price_change_pct = (self.price_change / self.start_price) * 100
        self.duration = self.end_index - self.start_index


@dataclass
class MarketState:
    """वर्तमान मार्केट की स्थिति"""
    structure: TrendDirection
    trend_strength: float  # 0.0 to 1.0
    volatility: float
    volume_trend: str  # 'increasing', 'decreasing', 'stable'
    recent_patterns: List[PatternResult] = field(default_factory=list)
    swing_points: List[SwingPoint] = field(default_factory=list)
    support_levels: List[float] = field(default_factory=list)
    resistance_levels: List[float] = field(default_factory=list)
    current_segment: Optional[TrendSegment] = None
    
    def is_trending(self) -> bool:
        return self.structure in [TrendDirection.UP, TrendDirection.DOWN]
    
    def is_ranging(self) -> bool:
        return self.structure == TrendDirection.SIDEWAYS


@dataclass
class EnsembleTradeSignal:
    """फाइनल ट्रेड सिग्नल"""
    timestamp: datetime
    action: TradingAction
    confidence: float
    source_signals: Dict[str, Tuple[int, float]]  # source -> (signal, weight)
    pattern_signals: List[PatternResult] = field(default_factory=list)
    technical_signals: Dict[str, float] = field(default_factory=dict)
    ml_signals: Dict[str, float] = field(default_factory=dict)
    dqn_signal: int = 0
    risk_score: float = 0.0  # 0.0 (safe) to 1.0 (risky)
    suggested_position_size: float = 1.0  # % of portfolio
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    reasoning: str = ""


# ============================================================================
# CHAPTER 2: CANDLESTICK BODY SIZE CLASSIFICATION
# ============================================================================

class CandleBodySize(Enum):
    """कैंडल body size classification"""
    MARUBOZU = "marubozu"  # कोई shadow नहीं
    LONG = "long"          # Strong buying/selling pressure
    MEDIUM = "medium"      # Moderate pressure
    SHORT = "short"        # Little activity
    DOJI = "doji"          # Indecision


def classify_body_size(candle: Candlestick, 
                       lookback: List[Candlestick] = None,
                       threshold_marubozu: float = 0.95,
                       threshold_long: float = 0.6,
                       threshold_short: float = 0.3) -> CandleBodySize:
    """
    कैंडल body size classify करें
    
    Long bodies = Strong buying या selling pressure
    Short bodies = Little buying या selling activity
    Doji = Indecision
    
    Args:
        candle: Classify करने वाली candle
        lookback: Relative comparison के लिए previous candles
        threshold_marubozu: Marubozu detection threshold
        threshold_long: Long body threshold
        threshold_short: Short body threshold
    
    Returns:
        CandleBodySize enum
    """
    if candle.is_doji:
        return CandleBodySize.DOJI
    
    if candle.is_marubozu:
        return CandleBodySize.MARUBOZU
    
    # Optional: Relative comparison with lookback
    if lookback and len(lookback) > 0:
        avg_body_ratio = np.mean([c.body_ratio for c in lookback[-10:] if c.total_range > 0])
        if avg_body_ratio > 0:
            relative_size = candle.body_ratio / avg_body_ratio
            if relative_size > 1.5:
                return CandleBodySize.LONG
            elif relative_size < 0.5:
                return CandleBodySize.SHORT
    
    # Absolute thresholds
    if candle.body_ratio >= threshold_long:
        return CandleBodySize.LONG
    elif candle.body_ratio <= threshold_short:
        return CandleBodySize.SHORT
    else:
        return CandleBodySize.MEDIUM


# ============================================================================
# CHAPTER 3: SHADOW PSYCHOLOGY ANALYZER
# ============================================================================

class ShadowPsychologyAnalyzer:
    """
    Upper और Lower shadows हमें trading session के बारे में 
    important information देते हैं:
    
    - Long Upper Shadow + Short Lower Shadow: 
      Buyers ने price को ऊपर धकेला लेकिन sellers ने 
      price को open के पास वापस ला दिया
    
    - Long Lower Shadow + Short Upper Shadow:
      Sellers ने price को नीचे धकेला लेकिन buyers ने 
      price को वापस ऊपर ला दिया
    
    - Short Shadows: Trading open और close के पास confined रहा
    """
    
    def analyze(self, candle: Candlestick, market_context: MarketState = None) -> Dict:
        """
        Single candle की shadow psychology analyze करें
        
        Returns:
            Dictionary with analysis results
        """
        analysis = {
            'candle_direction': 'bullish' if candle.is_bullish else 'bearish',
            'upper_shadow_significance': 'low',
            'lower_shadow_significance': 'low',
            'psychology': '',
            'rejection_level': None,
            'buying_pressure': 0.0,
            'selling_pressure': 0.0,
            'dominant_force': 'neutral',
            'signal_implication': SignalType.NEUTRAL
        }
        
        # ===== UPPER SHADOW ANALYSIS =====
        if candle.upper_shadow_ratio > 0.5:
            analysis['upper_shadow_significance'] = 'high'
            analysis['rejection_level'] = 'resistance'
            analysis['selling_pressure'] = candle.upper_shadow_ratio
            
            if candle.is_bearish:
                analysis['psychology'] = (
                    "Buyers pushed price higher but were OVERWHELMED by sellers. "
                    "Strong rejection from resistance level. Bears taking control. "
                    "This is a bearish signal - sellers showed superior strength."
                )
                analysis['dominant_force'] = 'sellers'
                analysis['signal_implication'] = SignalType.SELL
            else:
                analysis['psychology'] = (
                    "Buyers pushed higher but faced selling pressure. "
                    "Some profit taking occurred but buyers still in control. "
                    "Watch for potential reversal if followed by bearish candle."
                )
                analysis['dominant_force'] = 'buyers_weak'
                analysis['signal_implication'] = SignalType.NEUTRAL
        
        # ===== LOWER SHADOW ANALYSIS =====
        if candle.lower_shadow_ratio > 0.5:
            analysis['lower_shadow_significance'] = 'high'
            analysis['rejection_level'] = 'support'
            analysis['buying_pressure'] = candle.lower_shadow_ratio
            
            if candle.is_bullish:
                analysis['psychology'] = (
                    "Sellers pushed price lower but were OVERWHELMED by buyers. "
                    "Strong rejection from support level. Bulls taking control. "
                    "This is a bullish signal - buyers showed superior strength."
                )
                analysis['dominant_force'] = 'buyers'
                analysis['signal_implication'] = SignalType.BUY
            else:
                analysis['psychology'] = (
                    "Sellers pushed lower but faced buying pressure. "
                    "Some short covering occurred but sellers still in control. "
                    "Watch for potential reversal if followed by bullish candle."
                )
                analysis['dominant_force'] = 'sellers_weak'
                analysis['signal_implication'] = SignalType.NEUTRAL
        
        # ===== SHORT SHADOWS - STRONG CONVICTION =====
        if candle.upper_shadow_ratio < 0.15 and candle.lower_shadow_ratio < 0.15:
            analysis['psychology'] = (
                "Trading action confined near open and close. "
                "Strong directional conviction with minimal rejection. "
            )
            if candle.is_bullish:
                analysis['dominant_force'] = 'buyers_strong'
                analysis['buying_pressure'] = 0.8
                analysis['signal_implication'] = SignalType.BUY
            else:
                analysis['dominant_force'] = 'sellers_strong'
                analysis['selling_pressure'] = 0.8
                analysis['signal_implication'] = SignalType.SELL
        
        # ===== MARKET CONTEXT INTEGRATION =====
        if market_context:
            analysis = self._integrate_context(analysis, candle, market_context)
        
        return analysis
    
    def _integrate_context(self, analysis: Dict, candle: Candlestick, 
                          context: MarketState) -> Dict:
        """Market context के साथ psychology integrate करें"""
        
        # Uptrend में bearish rejection = stronger signal
        if context.structure == TrendDirection.UP:
            if analysis['rejection_level'] == 'resistance':
                analysis['psychology'] += " [IN UPTREND - Higher probability reversal signal]"
                analysis['signal_implication'] = SignalType.STRONG_SELL
        
        # Downtrend में bullish rejection = stronger signal
        elif context.structure == TrendDirection.DOWN:
            if analysis['rejection_level'] == 'support':
                analysis['psychology'] += " [IN DOWNTREND - Higher probability reversal signal]"
                analysis['signal_implication'] = SignalType.STRONG_BUY
        
        # Support/Resistance levels के साथ confirmation
        if context.support_levels and candle.low <= min(context.support_levels) * 1.001:
            if analysis['lower_shadow_significance'] == 'high':
                analysis['psychology'] += " [AT SUPPORT LEVEL - Confirmed]"
        
        if context.resistance_levels and candle.high >= max(context.resistance_levels) * 0.999:
            if analysis['upper_shadow_significance'] == 'high':
                analysis['psychology'] += " [AT RESISTANCE LEVEL - Confirmed]"
        
        return analysis
    
    def analyze_sequence(self, candles: List[Candlestick]) -> List[Dict]:
        """Candle sequence की shadow psychology analyze करें"""
        results = []
        for i, candle in enumerate(candles):
            context = None
            if i >= 20:
                # Build market context from previous candles
                structure_analyzer = MarketStructureAnalyzer()
                structure, info = structure_analyzer.identify_market_structure(candles[:i])
                context = MarketState(structure=structure, trend_strength=0.5, volatility=0.5)
            results.append(self.analyze(candle, context))
        return results


# ============================================================================
# CHAPTER 4: COMPREHENSIVE CANDLESTICK PATTERN DETECTOR
# ============================================================================

class CandlestickPatternDetector:
    """
    कैंडलस्टिक पैटर्न ट्रेडिंग के सबसे powerful concepts में से एक हैं।
    वे simple, easy to identify, और very profitable setups हैं।
    
    Research confirms करता है कि candlestick patterns की 
    high predictive value है और positive results produce कर सकते हैं।
    
    ⚠️ Remember: ये कोई holy grail नहीं है। 
    कुछ trades lose होंगे - ये इस game का हिस्सा है।
    
    Supported Patterns:
    ━━━━━━━━━━━━━━━━━━━
    SINGLE CANDLE:
    - Doji, Dragonfly Doji, Gravestone Doji
    - Hammer, Inverted Hammer, Shooting Star
    - Bullish Marubozu, Bearish Marubozu
    
    TWO CANDLE:
    - Bullish Engulfing, Bearish Engulfing
    - Bullish Harami, Bearish Harami
    - Tweezers Top, Tweezers Bottom
    - Inside Bar, Outside Bar
    
    THREE CANDLE:
    - Morning Star, Evening Star
    - Three White Soldiers, Three Black Crows
    - Bullish Abandoned Baby, Bearish Abandoned Baby
    """
    
    def __init__(self,
                 engulfing_min_body_ratio: float = 0.5,
                 doji_threshold: float = 0.1,
                 shadow_multiplier: float = 2.0,
                 tweezers_tolerance: float = 0.001,
                 volume_confirmation_threshold: float = 1.3):
        """
        Args:
            engulfing_min_body_ratio: Engulfing candle के लिए minimum body ratio
            doji_threshold: Doji detection threshold (body/range ratio)
            shadow_multiplier: Hammer/Shooting Star के लिए minimum shadow/body ratio
            tweezers_tolerance: Tweezers pattern के लिए price tolerance
            volume_confirmation_threshold: Volume confirmation threshold
        """
        self.engulfing_min_body_ratio = engulfing_min_body_ratio
        self.doji_threshold = doji_threshold
        self.shadow_multiplier = shadow_multiplier
        self.tweezers_tolerance = tweezers_tolerance
        self.volume_confirmation_threshold = volume_confirmation_threshold
        
        # Psychology analyzer
        self.psychology_analyzer = ShadowPsychologyAnalyzer()
    
    def detect_all_patterns(self, candles: List[Candlestick]) -> List[PatternResult]:
        """सभी candlestick patterns detect करें"""
        if len(candles) < 3:
            return []
        
        patterns = []
        
        for i in range(len(candles)):
            # ===== SINGLE CANDLE PATTERNS =====
            single_patterns = [
                self.detect_doji,
                self.detect_dragonfly_doji,
                self.detect_gravestone_doji,
                self.detect_hammer,
                self.detect_inverted_hammer,
                self.detect_shooting_star,
                self.detect_bullish_marubozu,
                self.detect_bearish_marubozu,
            ]
            
            for detect_fn in single_patterns:
                pattern = detect_fn(candles, i)
                if pattern:
                    patterns.append(pattern)
            
            # ===== TWO CANDLE PATTERNS =====
            if i >= 1:
                two_candle_patterns = [
                    self.detect_bullish_engulfing,
                    self.detect_bearish_engulfing,
                    self.detect_bullish_harami,
                    self.detect_bearish_harami,
                    self.detect_tweezers_top,
                    self.detect_tweezers_bottom,
                    self.detect_inside_bar,
                    self.detect_outside_bar,
                ]
                
                for detect_fn in two_candle_patterns:
                    pattern = detect_fn(candles, i)
                    if pattern:
                        patterns.append(pattern)
            
            # ===== THREE CANDLE PATTERNS =====
            if i >= 2:
                three_candle_patterns = [
                    self.detect_morning_star,
                    self.detect_evening_star,
                    self.detect_three_white_soldiers,
                    self.detect_three_black_crows,
                    self.detect_bullish_abandoned_baby,
                    self.detect_bearish_abandoned_baby,
                ]
                
                for detect_fn in three_candle_patterns:
                    pattern = detect_fn(candles, i)
                    if pattern:
                        patterns.append(pattern)
        
        return patterns
    
    def _check_volume_confirmation(self, candles: List[Candlestick], 
                                    current_idx: int,
                                    prev_idx: int = None) -> bool:
        """Volume confirmation check करें"""
        if prev_idx is None:
            prev_idx = max(0, current_idx - 1)
        
        current_vol = candles[current_idx].volume
        prev_vol = candles[prev_idx].volume
        
        if prev_vol == 0:
            return False
        
        return current_vol >= prev_vol * self.volume_confirmation_threshold
    
    def _get_trend_context(self, candles: List[Candlestick], 
                           end_idx: int, lookback: int = 5) -> str:
        """पैटर्न के trend context determine करें"""
        start_idx = max(0, end_idx - lookback)
        recent_candles = candles[start_idx:end_idx]
        
        if not recent_candles:
            return "unknown"
        
        bullish_count = sum(1 for c in recent_candles if c.is_bullish)
        bearish_count = sum(1 for c in recent_candles if c.is_bearish)
        
        if bullish_count > bearish_count * 1.5:
            return "uptrend"
        elif bearish_count > bullish_count * 1.5:
            return "downtrend"
        else:
            return "ranging"
    
    # =========================================================================
    # SINGLE CANDLE PATTERNS
    # =========================================================================
    
    def detect_doji(self, candles: List[Candlestick], 
                    index: int) -> Optional[PatternResult]:
        """
        Doji Pattern:
        ━━━━━━━━━━━━━━━
        Market opens और closes same price पर।
        Buyers और sellers के बीच equality और indecision indicate करता है।
        कोई भी market control में नहीं है।
        
        Uptrend/Downtrend में: Likely reversal indicate करता है।
        अक्सर big moves के बाद resting period के दौरान मिलता है।
        """
        candle = candles[index]
        
        if not candle.is_doji:
            return None
        
        # Trend context determine करें
        trend_ctx = self._get_trend_context(candles, index)
        
        signal = SignalType.NEUTRAL
        if trend_ctx == "uptrend":
            signal = SignalType.SELL
        elif trend_ctx == "downtrend":
            signal = SignalType.BUY
        
        # Confidence calculation
        confidence = 0.5
        if candle.total_range > 0:  # Shadows हैं (flat line नहीं)
            confidence += 0.15
        if trend_ctx in ["uptrend", "downtrend"]:
            confidence += 0.15
        if self._check_volume_confirmation(candles, index, index-1):
            confidence += 0.1
        
        return PatternResult(
            pattern_type="Doji",
            pattern_category=PatternCategory.SINGLE_CANDLE,
            signal=signal,
            confidence=min(confidence, 1.0),
            start_index=index,
            end_index=index,
            description="Doji: Open equals close, indecision in the market",
            psychology=(
                "Opening price is the same as closing price. Market didn't decide which "
                "direction to take. Buyers unable to keep price higher, sellers push prices "
                "back to opening price. Prior trend is losing strength."
            ),
            candlesticks=[candle],
            volume_confirmation=self._check_volume_confirmation(candles, index),
            trend_context=trend_ctx
        )
    
    def detect_dragonfly_doji(self, candles: List[Candlestick], 
                               index: int) -> Optional[PatternResult]:
        """
        Dragonfly Doji Pattern:
        ━━━━━━━━━━━━━━━━━━━━━━━
        Bullish reversal pattern.
        Open, High, और Close same या approximately same price पर हैं।
        Long lower tail characterize करता है।
        
        Shows resistance of buyers और attempt to push market up।
        Long lower tail suggests supply और demand nearing balance।
        Possible major turning point indicate करता है।
        """
        candle = candles[index]
        
        # Open, High, Close approximately equal होने चाहिए
        high_max = max(candle.open, candle.high, candle.close)
        low_min = min(candle.open, candle.high, candle.close)
        if (high_max - low_min) > candle.total_range * 0.1:
            return None
        
        # Long lower shadow होना चाहिए
        if candle.lower_shadow_ratio < 0.6:
            return None
        
        # Minimal upper shadow होना चाहिए
        if candle.upper_shadow_ratio > 0.2:
            return None
        
        trend_ctx = self._get_trend_context(candles, index)
        
        confidence = 0.6
        if candle.lower_shadow_ratio > 0.8:
            confidence += 0.15
        if candle.lower_shadow > candle.body * 3:
            confidence += 0.1
        if trend_ctx == "downtrend":
            confidence += 0.1
        if self._check_volume_confirmation(candles, index):
            confidence += 0.05
        
        return PatternResult(
            pattern_type="Dragonfly Doji",
            pattern_category=PatternCategory.SINGLE_CANDLE,
            signal=SignalType.BUY,
            confidence=min(confidence, 1.0),
            start_index=index,
            end_index=index,
            description="Dragonfly Doji: Long lower tail shows buyer resistance",
            psychology=(
                "Long lower tail suggests forces of supply and demand are nearing balance. "
                "Direction of trend may be nearing a major turning point. High buying "
                "pressure in the area indicates support and demand."
            ),
            candlesticks=[candle],
            volume_confirmation=self._check_volume_confirmation(candles, index),
            trend_context=trend_ctx
        )
    
    def detect_gravestone_doji(self, candles: List[Candlestick], 
                                index: int) -> Optional[PatternResult]:
        """
        Gravestone Doji Pattern:
        ━━━━━━━━━━━━━━━━━━━━━━━━
        Dragonfly Doji का bearish version।
        Open और Close same या approximately same price पर हैं।
        Long upper tail characterize करता है।
        
        Indicates market testing powerful supply या resistance area।
        Buyers pushed prices higher लेकिन sellers overwhelmed them।
        Bulls losing momentum, market ready for reversal।
        """
        candle = candles[index]
        
        # Open, Low, Close approximately equal होने चाहिए
        high_max = max(candle.open, candle.low, candle.close)
        low_min = min(candle.open, candle.low, candle.close)
        if (high_max - low_min) > candle.total_range * 0.1:
            return None
        
        # Long upper shadow होना चाहिए
        if candle.upper_shadow_ratio < 0.6:
            return None
        
        # Minimal lower shadow होना चाहिए
        if candle.lower_shadow_ratio > 0.2:
            return None
        
        trend_ctx = self._get_trend_context(candles, index)
        
        confidence = 0.6
        if candle.upper_shadow_ratio > 0.8:
            confidence += 0.15
        if candle.upper_shadow > candle.body * 3:
            confidence += 0.1
        if trend_ctx == "uptrend":
            confidence += 0.1
        if self._check_volume_confirmation(candles, index):
            confidence += 0.05
        
        return PatternResult(
            pattern_type="Gravestone Doji",
            pattern_category=PatternCategory.SINGLE_CANDLE,
            signal=SignalType.SELL,
            confidence=min(confidence, 1.0),
            start_index=index,
            end_index=index,
            description="Gravestone Doji: Long upper tail shows seller rejection",
            psychology=(
                "Buyers were able to push prices well above the open, but later sellers "
                "overwhelmed the market pushing price back down. This is a sign that bulls "
                "are losing their momentum and the market is ready for a reversal."
            ),
            candlesticks=[candle],
            volume_confirmation=self._check_volume_confirmation(candles, index),
            trend_context=trend_ctx
        )
    
    def detect_hammer(self, candles: List[Candlestick], 
                      index: int) -> Optional[PatternResult]:
        """
        Hammer (Pin Bar) Pattern:
        ━━━━━━━━━━━━━━━━━━━━━━━━
        Open, High, Close roughly same price पर होते हैं।
        Long lower shadow characterize करता है।
        
        Bullish rejection from buyers indicate करता है।
        Downtrend के bottom पर reversal pattern।
        Sellers push lower लेकिन buyers reject करते हैं।
        Long shadow represents high buying pressure।
        """
        candle = candles[index]
        
        # Long lower shadow (at least 2x body)
        if candle.body == 0 or candle.lower_shadow < candle.body * self.shadow_multiplier:
            return None
        
        # Upper shadow small होना चाहिए
        if candle.upper_shadow_ratio > 0.2:
            return None
        
        # Body upper portion में होना चाहिए
        if candle.body_position < 0.3:
            return None
        
        trend_ctx = self._get_trend_context(candles, index)
        
        confidence = 0.6
        if candle.lower_shadow_ratio > 0.7:
            confidence += 0.1
        if candle.body_ratio < 0.3:
            confidence += 0.1
        if candle.is_bullish:
            confidence += 0.1
        if trend_ctx == "downtrend":
            confidence += 0.1
        if self._check_volume_confirmation(candles, index):
            confidence += 0.05
        
        return PatternResult(
            pattern_type="Hammer",
            pattern_category=PatternCategory.SINGLE_CANDLE,
            signal=SignalType.BUY,
            confidence=min(confidence, 1.0),
            start_index=index,
            end_index=index,
            description="Hammer (Pin Bar): Long lower shadow shows strong buyer rejection",
            psychology=(
                "Sellers pushed market lower after the open, but got rejected by buyers. "
                "Market closes higher than the lowest price. Long shadow represents high "
                "buying pressure - buying power was more powerful than selling pressure."
            ),
            candlesticks=[candle],
            volume_confirmation=self._check_volume_confirmation(candles, index),
            trend_context=trend_ctx,
            additional_features={
                'lower_shadow_pct': candle.lower_shadow_ratio * 100,
                'body_position': candle.body_position
            }
        )
    
    def detect_inverted_hammer(self, candles: List[Candlestick], 
                                index: int) -> Optional[PatternResult]:
        """
        Inverted Hammer Pattern:
        ━━━━━━━━━━━━━━━━━━━━━━━━
        Hammer का उल्टा version।
        Long upper shadow और small lower shadow।
        Bullish signal लेकिन confirmation की जरूरत है।
        """
        candle = candles[index]
        
        if candle.body == 0 or candle.upper_shadow < candle.body * self.shadow_multiplier:
            return None
        
        if candle.lower_shadow_ratio > 0.2:
            return None
        
        if candle.body_position > -0.3:
            return None
        
        trend_ctx = self._get_trend_context(candles, index)
        
        confidence = 0.5  # Lower than hammer - needs confirmation
        if candle.upper_shadow_ratio > 0.7:
            confidence += 0.1
        if candle.is_bullish:
            confidence += 0.1
        if trend_ctx == "downtrend":
            confidence += 0.1
        
        return PatternResult(
            pattern_type="Inverted Hammer",
            pattern_category=PatternCategory.SINGLE_CANDLE,
            signal=SignalType.BUY,
            confidence=min(confidence, 1.0),
            start_index=index,
            end_index=index,
            description="Inverted Hammer: Long upper shadow, needs bullish confirmation",
            psychology=(
                "Sellers initially in control, but buyers attempted to push price higher. "
                "The long upper shadow shows buying interest, but this pattern requires "
                "confirmation from the next candle to be valid."
            ),
            candlesticks=[candle],
            volume_confirmation=self._check_volume_confirmation(candles, index),
            trend_context=trend_ctx
        )
    
    def detect_shooting_star(self, candles: List[Candlestick], 
                              index: int) -> Optional[PatternResult]:
        """
        Shooting Star (Bearish Pin Bar) Pattern:
        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        Open, Low, Close roughly same price पर होते हैं।
        Small body और long upper shadow।
        Shadow real body से twice लंबी होनी चाहिए।
        
        Hammer का bearish version।
        Buyers tried to push higher लेकिन got rejected।
        Resistance level के पास high probability setup।
        """
        candle = candles[index]
        
        # Long upper shadow (at least 2x body)
        if candle.body == 0 or candle.upper_shadow < candle.body * self.shadow_multiplier:
            return None
        
        # Lower shadow small होना चाहिए
        if candle.lower_shadow_ratio > 0.2:
            return None
        
        # Body lower portion में होना चाहिए
        if candle.body_position > -0.3:
            return None
        
        trend_ctx = self._get_trend_context(candles, index)
        
        confidence = 0.6
        if candle.upper_shadow_ratio > 0.7:
            confidence += 0.1
        if candle.body_ratio < 0.3:
            confidence += 0.1
        if candle.is_bearish:
            confidence += 0.1
        if trend_ctx == "uptrend":
            confidence += 0.1
        if self._check_volume_confirmation(candles, index):
            confidence += 0.05
        
        return PatternResult(
            pattern_type="Shooting Star",
            pattern_category=PatternCategory.SINGLE_CANDLE,
            signal=SignalType.SELL,
            confidence=min(confidence, 1.0),
            start_index=index,
            end_index=index,
            description="Shooting Star: Long upper shadow shows strong seller rejection",
            psychology=(
                "Buyers tried to push market higher, but got rejected by selling pressure. "
                "Formation at end of uptrend indicates end of the uptrend move and "
                "beginning of a new downtrend."
            ),
            candlesticks=[candle],
            volume_confirmation=self._check_volume_confirmation(candles, index),
            trend_context=trend_ctx,
            additional_features={
                'upper_shadow_pct': candle.upper_shadow_ratio * 100,
                'body_position': candle.body_position
            }
        )
    
    def detect_bullish_marubozu(self, candles: List[Candlestick], 
                                 index: int) -> Optional[PatternResult]:
        """
        Bullish Marubozu Pattern:
        ━━━━━━━━━━━━━━━━━━━━━━━━━
        No shadows, full bullish body।
        Extreme buying pressure indicate करता है।
        """
        candle = candles[index]
        
        if not candle.is_bullish or not candle.is_marubozu:
            return None
        
        confidence = 0.7
        if self._check_volume_confirmation(candles, index):
            confidence += 0.2
        
        return PatternResult(
            pattern_type="Bullish Marubozu",
            pattern_category=PatternCategory.SINGLE_CANDLE,
            signal=SignalType.STRONG_BUY,
            confidence=min(confidence, 1.0),
            start_index=index,
            end_index=index,
            description="Bullish Marubozu: No shadows, extreme buying pressure",
            psychology=(
                "Buyers controlled the entire session from open to close. "
                "No selling pressure observed. Extremely bullish signal."
            ),
            candlesticks=[candle],
            volume_confirmation=self._check_volume_confirmation(candles, index),
            trend_context=self._get_trend_context(candles, index)
        )
    
    def detect_bearish_marubozu(self, candles: List[Candlestick], 
                                 index: int) -> Optional[PatternResult]:
        """
        Bearish Marubozu Pattern:
        ━━━━━━━━━━━━━━━━━━━━━━━━━
        No shadows, full bearish body।
        Extreme selling pressure indicate करता है।
        """
        candle = candles[index]
        
        if not candle.is_bearish or not candle.is_marubozu:
            return None
        
        confidence = 0.7
        if self._check_volume_confirmation(candles, index):
            confidence += 0.2
        
        return PatternResult(
            pattern_type="Bearish Marubozu",
            pattern_category=PatternCategory.SINGLE_CANDLE,
            signal=SignalType.STRONG_SELL,
            confidence=min(confidence, 1.0),
            start_index=index,
            end_index=index,
            description="Bearish Marubozu: No shadows, extreme selling pressure",
            psychology=(
                "Sellers controlled the entire session from open to close. "
                "No buying pressure observed. Extremely bearish signal."
            ),
            candlesticks=[candle],
            volume_confirmation=self._check_volume_confirmation(candles, index),
            trend_context=self._get_trend_context(candles, index)
        )
    
    # =========================================================================
    # TWO CANDLE PATTERNS
    # =========================================================================
    
    def detect_bullish_engulfing(self, candles: List[Candlestick], 
                                  index: int) -> Optional[PatternResult]:
        """
        Bullish Engulfing Bar Pattern:
        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        First candle: Small body (bearish)
        Second candle: Engulfing candle (bullish)
        
        Market no longer under seller control indicate करता है।
        Downtrend के end पर: Powerful reversal (capitulation bottom)
        Uptrend के middle में: Continuation signal
        """
        if index < 1:
            return None
        
        prev_candle = candles[index - 1]
        curr_candle = candles[index]
        
        # Conditions check
        if not curr_candle.is_bullish or not prev_candle.is_bearish:
            return None
        
        # Current candle must fully engulf previous
        if not (curr_candle.open <= prev_candle.close and 
                curr_candle.close >= prev_candle.open):
            return None
        
        # Current candle should have significant body
        if curr_candle.body_ratio < self.engulfing_min_body_ratio:
            return None
        
        trend_ctx = self._get_trend_context(candles, index)
        
        # Confidence calculation with multiple factors
        confidence = 0.6
        confidence += 0.15 if curr_candle.body_ratio > 0.8 else 0
        confidence += 0.1 if prev_candle.body_ratio < 0.4 else 0
        confidence += 0.1 if self._check_volume_confirmation(candles, index, index-1) else 0
        confidence += 0.1 if trend_ctx == "downtrend" else 0
        
        return PatternResult(
            pattern_type="Bullish Engulfing",
            pattern_category=PatternCategory.TWO_CANDLE,
            signal=SignalType.BUY,
            confidence=min(confidence, 1.0),
            start_index=index - 1,
            end_index=index,
            description="Bullish Engulfing: Buyers take control, engulfing previous selling",
            psychology=(
                "The smaller body that represents the selling power was covered by the "
                "second body that represents the buying power. Market direction changes "
                "as buyers overwhelm sellers."
            ),
            candlesticks=[prev_candle, curr_candle],
            volume_confirmation=self._check_volume_confirmation(candles, index, index-1),
            trend_context=trend_ctx,
            additional_features={
                'engulfing_ratio': curr_candle.body / prev_candle.body if prev_candle.body > 0 else 0,
                'body_size_diff': curr_candle.body_ratio - prev_candle.body_ratio
            }
        )
    
    def detect_bearish_engulfing(self, candles: List[Candlestick], 
                                  index: int) -> Optional[PatternResult]:
        """
        Bearish Engulfing Bar Pattern:
        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        Candle fully engulfs previous candle।
        Second body first body को engulf करता है।
        
        Sellers are in control indicate करता है।
        Uptrend के end पर: Trend reversal signal
        Uptrend के middle में: Continuation signal
        """
        if index < 1:
            return None
        
        prev_candle = candles[index - 1]
        curr_candle = candles[index]
        
        # Conditions check
        if not curr_candle.is_bearish or not prev_candle.is_bullish:
            return None
        
        # Current candle must fully engulf previous
        if not (curr_candle.open >= prev_candle.close and 
                curr_candle.close <= prev_candle.open):
            return None
        
        # Current candle should have significant body
        if curr_candle.body_ratio < self.engulfing_min_body_ratio:
            return None
        
        trend_ctx = self._get_trend_context(candles, index)
        
        # Confidence calculation
        confidence = 0.6
        confidence += 0.15 if curr_candle.body_ratio > 0.8 else 0
        confidence += 0.1 if prev_candle.body_ratio < 0.4 else 0
        confidence += 0.1 if self._check_volume_confirmation(candles, index, index-1) else 0
        confidence += 0.1 if trend_ctx == "uptrend" else 0
        
        return PatternResult(
            pattern_type="Bearish Engulfing",
            pattern_category=PatternCategory.TWO_CANDLE,
            signal=SignalType.SELL,
            confidence=min(confidence, 1.0),
            start_index=index - 1,
            end_index=index,
            description="Bearish Engulfing: Second candle engulfs first, sellers in control",
            psychology=(
                "Buyers were engulfed by sellers. The smaller body represents selling power "
                "that was overwhelmed by the larger bearish body. This indicates a shift "
                "in market control from buyers to sellers."
            ),
            candlesticks=[prev_candle, curr_candle],
            volume_confirmation=self._check_volume_confirmation(candles, index, index-1),
            trend_context=trend_ctx,
            additional_features={
                'engulfing_ratio': curr_candle.body / prev_candle.body if prev_candle.body > 0 else 0,
                'body_size_diff': curr_candle.body_ratio - prev_candle.body_ratio
            }
        )
    
    def detect_bullish_harami(self, candles: List[Candlestick], 
                               index: int) -> Optional[PatternResult]:
        """
        Bullish Harami (Inside Bar) Pattern:
        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        "Harami" जापानी में "pregnant" का मतलब है।
        
        Mother candle: Large bearish
        Baby candle: Smaller, inside mother
        
        Baby candle mother candle के अंदर close होता है।
        Downtrend के bottom पर: Bullish reversal signal
        Trend के दौरान: Continuation signal
        Market consolidation/indecision indicate करता है।
        """
        if index < 1:
            return None
        
        mother = candles[index - 1]
        baby = candles[index]
        
        # Mother bearish होनी चाहिए
        if not mother.is_bearish:
            return None
        
        # Baby mother के अंदर होनी चाहिए
        if not (baby.high < mother.high and baby.low > mother.low):
            return None
        
        # Baby mother से smaller होनी चाहिए
        if baby.body >= mother.body:
            return None
        
        trend_ctx = self._get_trend_context(candles, index)
        
        confidence = 0.5
        confidence += 0.15 if baby.body_ratio < 0.3 else 0
        confidence += 0.1 if baby.is_bullish else 0
        confidence += 0.15 if baby.is_doji else 0
        confidence += 0.1 if trend_ctx == "downtrend" else 0
        
        return PatternResult(
            pattern_type="Bullish Harami",
            pattern_category=PatternCategory.TWO_CANDLE,
            signal=SignalType.BUY,
            confidence=min(confidence, 1.0),
            start_index=index - 1,
            end_index=index,
            description="Bullish Harami: Small candle inside large bearish candle",
            psychology=(
                "Smaller body is totally covered by previous mother candle. Market is in "
                "indecision period - consolidating. Buyers and sellers don't know what to do, "
                "no one in control. Selling power no longer in control of market."
            ),
            candlesticks=[mother, baby],
            volume_confirmation=self._check_volume_confirmation(candles, index),
            trend_context=trend_ctx
        )
    
    def detect_bearish_harami(self, candles: List[Candlestick], 
                               index: int) -> Optional[PatternResult]:
        """
        Bearish Harami (Inside Bar) Pattern:
        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        Bullish Harami का bearish version।
        
        Mother candle: Large bullish
        Baby candle: Smaller, inside mother
        
        Uptrend के top पर: Bearish reversal signal
        """
        if index < 1:
            return None
        
        mother = candles[index - 1]
        baby = candles[index]
        
        # Mother bullish होनी चाहिए
        if not mother.is_bullish:
            return None
        
        # Baby mother के अंदर होनी चाहिए
        if not (baby.high < mother.high and baby.low > mother.low):
            return None
        
        # Baby mother से smaller होनी चाहिए
        if baby.body >= mother.body:
            return None
        
        trend_ctx = self._get_trend_context(candles, index)
        
        confidence = 0.5
        confidence += 0.15 if baby.body_ratio < 0.3 else 0
        confidence += 0.1 if baby.is_bearish else 0
        confidence += 0.15 if baby.is_doji else 0
        confidence += 0.1 if trend_ctx == "uptrend" else 0
        
        return PatternResult(
            pattern_type="Bearish Harami",
            pattern_category=PatternCategory.TWO_CANDLE,
            signal=SignalType.SELL,
            confidence=min(confidence, 1.0),
            start_index=index - 1,
            end_index=index,
            description="Bearish Harami: Small candle inside large bullish candle",
            psychology=(
                "Market enters consolidation phase during this session. Buyers and sellers "
                "in indecision period. Buyer's domination may be over, beginning of "
                "downtrend is possible."
            ),
            candlesticks=[mother, baby],
            volume_confirmation=self._check_volume_confirmation(candles, index),
            trend_context=trend_ctx
        )
    
    def detect_tweezers_top(self, candles: List[Candlestick], 
                             index: int) -> Optional[PatternResult]:
        """
        Tweezers Top Pattern:
        ━━━━━━━━━━━━━━━━━━━━━
        Uptrend के top पर bearish reversal pattern।
        
        First: Bullish candlestick
        Second: Bearish candlestick
        Both candles के highs approximately equal होते हैं।
        
        Sellers surprised buyers by pushing market lower।
        """
        if index < 1:
            return None
        
        first = candles[index - 1]
        second = candles[index]
        
        # First bullish, second bearish
        if not (first.is_bullish and second.is_bearish):
            return None
        
        # Highs approximately equal
        if abs(first.high - second.high) > first.high * self.tweezers_tolerance:
            return None
        
        trend_ctx = self._get_trend_context(candles, index)
        
        confidence = 0.55
        if abs(first.high - second.high) < first.high * self.tweezers_tolerance * 0.5:
            confidence += 0.2
        if first.body_ratio > 0.5 and second.body_ratio > 0.5:
            confidence += 0.1
        if trend_ctx == "uptrend":
            confidence += 0.1
        
        return PatternResult(
            pattern_type="Tweezers Top",
            pattern_category=PatternCategory.TWO_CANDLE,
            signal=SignalType.SELL,
            confidence=min(confidence, 1.0),
            start_index=index - 1,
            end_index=index,
            description="Tweezers Top: Two candles with equal highs at uptrend top",
            psychology=(
                "Buyers pushed price higher, giving impression market still going up, but "
                "sellers surprised buyers by pushing market lower. Indicates bullish trend "
                "reversal when combined with other technical tools."
            ),
            candlesticks=[first, second],
            volume_confirmation=self._check_volume_confirmation(candles, index),
            trend_context=trend_ctx,
            additional_features={
                'high_difference_pct': abs(first.high - second.high) / first.high * 100
            }
        )
    
    def detect_tweezers_bottom(self, candles: List[Candlestick], 
                                index: int) -> Optional[PatternResult]:
        """
        Tweezers Bottom Pattern:
        ━━━━━━━━━━━━━━━━━━━━━━━━
        Downtrend के bottom पर bullish reversal pattern।
        
        First: Bearish candlestick
        Second: Bullish candlestick
        Both candles के lows approximately equal होते हैं।
        
        Buyers coming to reverse market direction।
        """
        if index < 1:
            return None
        
        first = candles[index - 1]
        second = candles[index]
        
        # First bearish, second bullish
        if not (first.is_bearish and second.is_bullish):
            return None
        
        # Lows approximately equal
        if abs(first.low - second.low) > first.low * self.tweezers_tolerance:
            return None
        
        trend_ctx = self._get_trend_context(candles, index)
        
        confidence = 0.55
        if abs(first.low - second.low) < first.low * self.tweezers_tolerance * 0.5:
            confidence += 0.2
        if first.body_ratio > 0.5 and second.body_ratio > 0.5:
            confidence += 0.1
        if trend_ctx == "downtrend":
            confidence += 0.1
        
        return PatternResult(
            pattern_type="Tweezers Bottom",
            pattern_category=PatternCategory.TWO_CANDLE,
            signal=SignalType.BUY,
            confidence=min(confidence, 1.0),
            start_index=index - 1,
            end_index=index,
            description="Tweezers Bottom: Two candles with equal lows at downtrend bottom",
            psychology=(
                "Bears pushed market downward on first session, but second session opened "
                "where prices closed on first session and went straight up. Indicates buyers "
                "coming to reverse market direction."
            ),
            candlesticks=[first, second],
            volume_confirmation=self._check_volume_confirmation(candles, index),
            trend_context=trend_ctx,
            additional_features={
                'low_difference_pct': abs(first.low - second.low) / first.low * 100
            }
        )
    
    def detect_inside_bar(self, candles: List[Candlestick], 
                           index: int) -> Optional[PatternResult]:
        """
        Inside Bar Pattern:
        ━━━━━━━━━━━━━━━━━━
        कोई भी candle जो पूरी तरह से previous candle के अंदर हो।
        Bullish और bearish दोनों setups के लिए use हो सकता है।
        False breakout strategies के लिए important है।
        """
        if index < 1:
            return None
        
        mother = candles[index - 1]
        baby = candles[index]
        
        # Baby completely inside mother होनी चाहिए
        if not (baby.high <= mother.high and baby.low >= mother.low):
            return None
        
        # Signal context के अनुसार determine करें
        signal = SignalType.SELL if mother.is_bullish else SignalType.BUY
        
        trend_ctx = self._get_trend_context(candles, index)
        
        confidence = 0.5
        confidence += 0.15 if baby.body_ratio < 0.2 else 0
        confidence += 0.15 if baby.is_doji else 0
        confidence += 0.1 if trend_ctx in ["uptrend", "downtrend"] else 0
        
        return PatternResult(
            pattern_type="Inside Bar",
            pattern_category=PatternCategory.TWO_CANDLE,
            signal=signal,
            confidence=min(confidence, 1.0),
            start_index=index - 1,
            end_index=index,
            description="Inside Bar: Candle completely contained within previous candle",
            psychology=(
                "Price consolidation within the range of the previous candle. Market is "
                "building energy for a potential breakout. Can be used for false breakout "
                "trading strategies."
            ),
            candlesticks=[mother, baby],
            volume_confirmation=self._check_volume_confirmation(candles, index),
            trend_context=trend_ctx
        )
    
    def detect_outside_bar(self, candles: List[Candlestick], 
                            index: int) -> Optional[PatternResult]:
        """
        Outside Bar Pattern:
        ━━━━━━━━━━━━━━━━━━━━
        Current candle previous candle को completely engulf करती है।
        Engulfing का generic version।
        """
        if index < 1:
            return None
        
        prev = candles[index - 1]
        curr = candles[index]
        
        # Current must completely contain previous
        if not (curr.high >= prev.high and curr.low <= prev.low):
            return None
        
        # Determine signal
        if curr.is_bullish:
            signal = SignalType.BUY
            desc = "Bullish Outside Bar: Current candle engulfs previous, buyers in control"
        else:
            signal = SignalType.SELL
            desc = "Bearish Outside Bar: Current candle engulfs previous, sellers in control"
        
        trend_ctx = self._get_trend_context(candles, index)
        
        confidence = 0.55
        confidence += 0.15 if curr.body_ratio > 0.6 else 0
        confidence += 0.1 if self._check_volume_confirmation(candles, index, index-1) else 0
        
        return PatternResult(
            pattern_type="Outside Bar",
            pattern_category=PatternCategory.TWO_CANDLE,
            signal=signal,
            confidence=min(confidence, 1.0),
            start_index=index - 1,
            end_index=index,
            description=desc,
            psychology="Current candle's range completely encompasses the previous candle, "
                      "indicating a potential shift in market control.",
            candlesticks=[prev, curr],
            volume_confirmation=self._check_volume_confirmation(candles, index, index-1),
            trend_context=trend_ctx
        )
    
    # =========================================================================
    # THREE CANDLE PATTERNS
    # =========================================================================
    
    def detect_morning_star(self, candles: List[Candlestick], 
                             index: int) -> Optional[PatternResult]:
        """
        Morning Star Pattern:
        ━━━━━━━━━━━━━━━━━━━━
        Downtrend के bottom पर bullish reversal pattern।
        
        Three candlesticks:
        1. First: Bearish - sellers still in charge
        2. Second: Small - sellers don't push much lower (can be bullish/bearish/Doji)
        3. Third: Bullish - gapped up, closed above midpoint of first candle
        
        Shows how buyers took control from sellers।
        """
        if index < 2:
            return None
        
        first = candles[index - 2]
        second = candles[index - 1]
        third = candles[index]
        
        # First bearish होना चाहिए
        if not first.is_bearish:
            return None
        
        # Second small होना चाहिए (indecision)
        if second.body_ratio > 0.4:
            return None
        
        # Third bullish होना चाहिए
        if not third.is_bullish:
            return None
        
        # Third first के midpoint से ऊपर close होना चाहिए
        first_midpoint = (first.open + first.close) / 2
        if third.close <= first_midpoint:
            return None
        
        trend_ctx = self._get_trend_context(candles, index)
        
        confidence = 0.6
        confidence += 0.1 if second.is_doji else 0
        confidence += 0.1 if third.body_ratio > 0.6 else 0
        confidence += 0.1 if third.close > first.open else 0
        confidence += 0.1 if trend_ctx == "downtrend" else 0
        if self._check_volume_confirmation(candles, index, index-1):
            confidence += 0.05
        
        return PatternResult(
            pattern_type="Morning Star",
            pattern_category=PatternCategory.THREE_CANDLE,
            signal=SignalType.BUY,
            confidence=min(confidence, 1.0),
            start_index=index - 2,
            end_index=index,
            description="Morning Star: Three candle bullish reversal at downtrend bottom",
            psychology=(
                "First candle confirmed seller's domination. Second candle (possibly Doji) "
                "indicated sellers struggling to push market lower. Third bullish candle "
                "shows buyers took control from sellers, market likely to reverse."
            ),
            candlesticks=[first, second, third],
            volume_confirmation=self._check_volume_confirmation(candles, index, index-1),
            trend_context=trend_ctx,
            additional_features={
                'second_is_doji': second.is_doji,
                'third_penetration': (third.close - first_midpoint) / first.body if first.body > 0 else 0
            }
        )
    
    def detect_evening_star(self, candles: List[Candlestick], 
                             index: int) -> Optional[PatternResult]:
        """
        Evening Star Pattern:
        ━━━━━━━━━━━━━━━━━━━━
        Uptrend के top पर bearish reversal pattern।
        
        Three candlesticks:
        1. First: Bullish - buyers still pushing higher
        2. Second: Small - buyers not as powerful (can be bullish/bearish/Doji)
        3. Third: Large bearish - confirmation of reversal
        
        Morning star का bearish version।
        """
        if index < 2:
            return None
        
        first = candles[index - 2]
        second = candles[index - 1]
        third = candles[index]
        
        # First bullish होना चाहिए
        if not first.is_bullish:
            return None
        
        # Second small होना चाहिए (indecision)
        if second.body_ratio > 0.4:
            return None
        
        # Third bearish होना चाहिए
        if not third.is_bearish:
            return None
        
        # Third first के midpoint से नीचे close होना चाहिए
        first_midpoint = (first.open + first.close) / 2
        if third.close >= first_midpoint:
            return None
        
        trend_ctx = self._get_trend_context(candles, index)
        
        confidence = 0.6
        confidence += 0.1 if second.is_doji else 0
        confidence += 0.1 if third.body_ratio > 0.6 else 0
        confidence += 0.1 if third.close < first.open else 0
        confidence += 0.1 if trend_ctx == "uptrend" else 0
        if self._check_volume_confirmation(candles, index, index-1):
            confidence += 0.05
        
        return PatternResult(
            pattern_type="Evening Star",
            pattern_category=PatternCategory.THREE_CANDLE,
            signal=SignalType.SELL,
            confidence=min(confidence, 1.0),
            start_index=index - 2,
            end_index=index,
            description="Evening Star: Three candle bearish reversal at uptrend top",
            psychology=(
                "First part is a bullish candle, bulls still pushing higher. Formation of "
                "smaller body shows buyers still in control but not as powerful. Final bearish "
                "candle indicates buyer's domination is over, possible bearish reversal."
            ),
            candlesticks=[first, second, third],
            volume_confirmation=self._check_volume_confirmation(candles, index, index-1),
            trend_context=trend_ctx,
            additional_features={
                'second_is_doji': second.is_doji,
                'third_penetration': (first_midpoint - third.close) / first.body if first.body > 0 else 0
            }
        )
    
    def detect_three_white_soldiers(self, candles: List[Candlestick], 
                                     index: int) -> Optional[PatternResult]:
        """
        Three White Soldiers Pattern:
        ━━━━━━━━━━━━━━━━━━━━━━━━━━
        Three consecutive long bullish candles।
        Strong bullish momentum indicate करता है।
        Downtrend के बाद powerful reversal signal।
        """
        if index < 2:
            return None
        
        first = candles[index - 2]
        second = candles[index - 1]
        third = candles[index]
        
        # All three must be bullish with long bodies
        if not (first.is_bullish and second.is_bullish and third.is_bullish):
            return None
        
        if not (first.body_ratio > 0.5 and second.body_ratio > 0.5 and third.body_ratio > 0.5):
            return None
        
        # Each should open within previous body
        if not (second.open >= first.body_bottom and second.open <= first.body_top):
            return None
        if not (third.open >= second.body_bottom and third.open <= second.body_top):
            return None
        
        # Each should close higher than previous
        if not (second.close > first.close and third.close > second.close):
            return None
        
        trend_ctx = self._get_trend_context(candles, index)
        
        confidence = 0.7
        confidence += 0.1 if trend_ctx == "downtrend" else 0
        if self._check_volume_confirmation(candles, index, index-1):
            confidence += 0.1
        
        return PatternResult(
            pattern_type="Three White Soldiers",
            pattern_category=PatternCategory.THREE_CANDLE,
            signal=SignalType.STRONG_BUY,
            confidence=min(confidence, 1.0),
            start_index=index - 2,
            end_index=index,
            description="Three White Soldiers: Three long bullish candles, strong momentum",
            psychology=(
                "Three consecutive bullish candles with increasing closes show strong "
                "buying momentum. Each candle opens within the previous body and closes "
                "higher, indicating sustained buying pressure."
            ),
            candlesticks=[first, second, third],
            volume_confirmation=self._check_volume_confirmation(candles, index, index-1),
            trend_context=trend_ctx
        )
    
    def detect_three_black_crows(self, candles: List[Candlestick], 
                                  index: int) -> Optional[PatternResult]:
        """
        Three Black Crows Pattern:
        ━━━━━━━━━━━━━━━━━━━━━━━━
        Three consecutive long bearish candles।
        Strong bearish momentum indicate करता है।
        Uptrend के बाद powerful reversal signal।
        """
        if index < 2:
            return None
        
        first = candles[index - 2]
        second = candles[index - 1]
        third = candles[index]
        
        # All three must be bearish with long bodies
        if not (first.is_bearish and second.is_bearish and third.is_bearish):
            return None
        
        if not (first.body_ratio > 0.5 and second.body_ratio > 0.5 and third.body_ratio > 0.5):
            return None
        
        # Each should open within previous body
        if not (second.open >= first.body_bottom and second.open <= first.body_top):
            return None
        if not (third.open >= second.body_bottom and third.open <= second.body_top):
            return None
        
        # Each should close lower than previous
        if not (second.close < first.close and third.close < second.close):
            return None
        
        trend_ctx = self._get_trend_context(candles, index)
        
        confidence = 0.7
        confidence += 0.1 if trend_ctx == "uptrend" else 0
        if self._check_volume_confirmation(candles, index, index-1):
            confidence += 0.1
        
        return PatternResult(
            pattern_type="Three Black Crows",
            pattern_category=PatternCategory.THREE_CANDLE,
            signal=SignalType.STRONG_SELL,
            confidence=min(confidence, 1.0),
            start_index=index - 2,
            end_index=index,
            description="Three Black Crows: Three long bearish candles, strong bearish momentum",
            psychology=(
                "Three consecutive bearish candles with decreasing closes show strong "
                "selling momentum. Each candle opens within the previous body and closes "
                "lower, indicating sustained selling pressure."
            ),
            candlesticks=[first, second, third],
            volume_confirmation=self._check_volume_confirmation(candles, index, index-1),
            trend_context=trend_ctx
        )
    
    def detect_bullish_abandoned_baby(self, candles: List[Candlestick], 
                                       index: int) -> Optional[PatternResult]:
        """
        Bullish Abandoned Baby Pattern:
        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        Rare और powerful bullish reversal pattern।
        
        1. Large bearish candle
        2. Doji that gaps down
        3. Large bullish candle that gaps up
        """
        if index < 2:
            return None
        
        first = candles[index - 2]
        second = candles[index - 1]
        third = candles[index]
        
        if not first.is_bearish or not third.is_bullish:
            return None
        
        if not second.is_doji:
            return None
        
        # Gaps
        if second.high >= first.low:  # Gap down
            return None
        if third.low <= second.high:  # Gap up
            return None
        
        trend_ctx = self._get_trend_context(candles, index)
        
        confidence = 0.8  # High confidence - rare pattern
        confidence += 0.1 if trend_ctx == "downtrend" else 0
        
        return PatternResult(
            pattern_type="Bullish Abandoned Baby",
            pattern_category=PatternCategory.THREE_CANDLE,
            signal=SignalType.STRONG_BUY,
            confidence=min(confidence, 1.0),
            start_index=index - 2,
            end_index=index,
            description="Bullish Abandoned Baby: Rare powerful reversal with gaps",
            psychology=(
                "A rare and extremely bullish pattern. The gap down followed by a Doji "
                "shows complete indecision at the bottom. The gap up with a bullish candle "
                "confirms buyers have taken control."
            ),
            candlesticks=[first, second, third],
            volume_confirmation=self._check_volume_confirmation(candles, index, index-1),
            trend_context=trend_ctx
        )
    
    def detect_bearish_abandoned_baby(self, candles: List[Candlestick], 
                                       index: int) -> Optional[PatternResult]:
        """
        Bearish Abandoned Baby Pattern:
        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        Rare और powerful bearish reversal pattern।
        
        1. Large bullish candle
        2. Doji that gaps up
        3. Large bearish candle that gaps down
        """
        if index < 2:
            return None
        
        first = candles[index - 2]
        second = candles[index - 1]
        third = candles[index]
        
        if not first.is_bullish or not third.is_bearish:
            return None
        
        if not second.is_doji:
            return None
        
        # Gaps
        if second.low <= first.high:  # Gap up
            return None
        if third.high >= second.low:  # Gap down
            return None
        
        trend_ctx = self._get_trend_context(candles, index)
        
        confidence = 0.8  # High confidence - rare pattern
        confidence += 0.1 if trend_ctx == "uptrend" else 0
        
        return PatternResult(
            pattern_type="Bearish Abandoned Baby",
            pattern_category=PatternCategory.THREE_CANDLE,
            signal=SignalType.STRONG_SELL,
            confidence=min(confidence, 1.0),
            start_index=index - 2,
            end_index=index,
            description="Bearish Abandoned Baby: Rare powerful reversal with gaps",
            psychology=(
                "A rare and extremely bearish pattern. The gap up followed by a Doji "
                "shows complete indecision at the top. The gap down with a bearish candle "
                "confirms sellers have taken control."
            ),
            candlesticks=[first, second, third],
            volume_confirmation=self._check_volume_confirmation(candles, index, index-1),
            trend_context=trend_ctx
        )


# ============================================================================
# CHAPTER 5: MARKET STRUCTURE ANALYZER (COMPLETE)
# ============================================================================

class MarketStructureAnalyzer:
    """
    एक trader के रूप में सबसे important skills में से एक है 
    market structure को पढ़ने की क्षमता।
    
    ये एक critical skill है जो आपको सही price action strategies 
    को सही market condition में use करने की अनुमति देती है।
    
    Three types of markets:
    ━━━━━━━━━━━━━━━━━━━━━━━
    1. Trending markets - Higher highs/higher lows या lower highs/lower lows
    2. Ranging markets - Support और resistance के बीच horizontally move करता है
    3. Choppy markets - No clear direction, lots of noise
    
    Professional Traders:
    ━━━━━━━━━━━━━━━━━━━
    - Impulsive moves की शुरुआत में Buy/Sell करते हैं
    - Impulsive moves के end पर Profits take करते हैं
    - Retracement moves के दौरान Entry से बचते हैं
    """
    
    def __init__(self,
                 swing_lookback: int = 3,
                 trend_min_swings: int = 2,
                 range_tolerance: float = 0.02,
                 min_swing_strength: float = 0.001):
        """
        Args:
            swing_lookback: Swing detection के लिए candles की संख्या
            trend_min_swings: Trend confirm करने के लिए minimum swings
            range_tolerance: Range detection के लिए tolerance
            min_swing_strength: Minimum swing strength threshold
        """
        self.swing_lookback = swing_lookback
        self.trend_min_swings = trend_min_swings
        self.range_tolerance = range_tolerance
        self.min_swing_strength = min_swing_strength
    
    def find_swing_points(self, candles: List[Candlestick]) -> List[SwingPoint]:
        """
        Price data में swing highs और swing lows find करें।
        
        Swing highs: Price high surrounding candles से higher है
        Swing lows: Price low surrounding candles से lower है
        """
        swing_points = []
        
        for i in range(self.swing_lookback, len(candles) - self.swing_lookback):
            current = candles[i]
            
            # ===== SWING HIGH CHECK =====
            is_swing_high = True
            for j in range(i - self.swing_lookback, i + self.swing_lookback + 1):
                if j != i and candles[j].high >= current.high:
                    is_swing_high = False
                    break
            
            if is_swing_high:
                # Calculate swing strength
                surrounding_highs = [candles[j].high for j in range(i - self.swing_lookback, 
                                                                      i + self.swing_lookback + 1) 
                                     if j != i]
                strength = (current.high - max(surrounding_highs)) / current.high if surrounding_highs else 1.0
                
                swing_points.append(SwingPoint(
                    index=i,
                    price=current.high,
                    point_type='high',
                    timestamp=current.timestamp,
                    strength=strength,
                    surrounding_candles=candles[i-self.swing_lookback:i+self.swing_lookback+1]
                ))
            
            # ===== SWING LOW CHECK =====
            is_swing_low = True
            for j in range(i - self.swing_lookback, i + self.swing_lookback + 1):
                if j != i and candles[j].low <= current.low:
                    is_swing_low = False
                    break
            
            if is_swing_low:
                # Calculate swing strength
                surrounding_lows = [candles[j].low for j in range(i - self.swing_lookback, 
                                                                    i + self.swing_lookback + 1) 
                                    if j != i]
                strength = (min(surrounding_lows) - current.low) / current.low if surrounding_lows else 1.0
                
                swing_points.append(SwingPoint(
                    index=i,
                    price=current.low,
                    point_type='low',
                    timestamp=current.timestamp,
                    strength=strength,
                    surrounding_candles=candles[i-self.swing_lookback:i+self.swing_lookback+1]
                ))
        
        return swing_points
    
    def identify_market_structure(self, candles: List[Candlestick]) -> Tuple[TrendDirection, Dict]:
        """
        Current market structure identify करें।
        
        Returns:
            Tuple of (TrendDirection, additional_info_dict)
        """
        if len(candles) < 20:
            return TrendDirection.UNKNOWN, {'reason': 'Insufficient data'}
        
        swing_points = self.find_swing_points(candles)
        
        # Separate swing highs और lows
        swing_highs = [sp for sp in swing_points if sp.is_high]
        swing_lows = [sp for sp in swing_points if sp.is_low]
        
        info = {
            'swing_highs': swing_highs,
            'swing_lows': swing_lows,
            'higher_highs': 0,
            'higher_lows': 0,
            'lower_highs': 0,
            'lower_lows': 0,
            'equal_highs': 0,
            'equal_lows': 0
        }
        
        # ===== COUNT TREND PATTERNS =====
        for i in range(1, len(swing_highs)):
            if swing_highs[i].price > swing_highs[i-1].price:
                info['higher_highs'] += 1
            elif swing_highs[i].price < swing_highs[i-1].price:
                info['lower_highs'] += 1
            else:
                info['equal_highs'] += 1
        
        for i in range(1, len(swing_lows)):
            if swing_lows[i].price > swing_lows[i-1].price:
                info['higher_lows'] += 1
            elif swing_lows[i].price < swing_lows[i-1].price:
                info['lower_lows'] += 1
            else:
                info['equal_lows'] += 1
        
        total_swings = len(swing_highs) + len(swing_lows)
        
        if total_swings < self.trend_min_swings * 2:
            return TrendDirection.UNKNOWN, {**info, 'reason': 'Insufficient swing points'}
        
        # ===== DETERMINE TREND =====
        uptrend_score = info['higher_highs'] + info['higher_lows']
        downtrend_score = info['lower_highs'] + info['lower_lows']
        total_score = uptrend_score + downtrend_score
        
        if total_score == 0:
            # Check for ranging
            return self._check_ranging(swing_highs, swing_lows, info)
        
        uptrend_ratio = uptrend_score / total_score
        downtrend_ratio = downtrend_score / total_score
        
        # Clear uptrend
        if uptrend_ratio > 0.7 and uptrend_score >= self.trend_min_swings:
            info['trend_strength'] = uptrend_ratio
            return TrendDirection.UP, info
        
        # Clear downtrend
        if downtrend_ratio > 0.7 and downtrend_score >= self.trend_min_swings:
            info['trend_strength'] = downtrend_ratio
            return TrendDirection.DOWN, info
        
        # Check for ranging
        return self._check_ranging(swing_highs, swing_lows, info)
    
    def _check_ranging(self, swing_highs: List[SwingPoint], 
                       swing_lows: List[SwingPoint],
                       info: Dict) -> Tuple[TrendDirection, Dict]:
        """Ranging market check करें"""
        if len(swing_highs) >= 2 and len(swing_lows) >= 2:
            high_prices = [sh.price for sh in swing_highs[-3:]]
            low_prices = [sl.price for sl in swing_lows[-3:]]
            
            high_range = max(high_prices) - min(high_prices)
            low_range = max(low_prices) - min(low_prices)
            avg_price = np.mean(high_prices + low_prices)
            
            if avg_price > 0:
                if (high_range / avg_price < self.range_tolerance and 
                    low_range / avg_price < self.range_tolerance):
                    info['support'] = min(low_prices)
                    info['resistance'] = max(high_prices)
                    info['range_size'] = info['resistance'] - info['support']
                    return TrendDirection.SIDEWAYS, info
        
        return TrendDirection.UNKNOWN, info
    
    def identify_trend_segments(self, candles: List[Candlestick]) -> List[TrendSegment]:
        """
        Trending market में impulsive और retracement moves identify करें।
        
        Trending markets characterize होते हैं:
        - Impulsive moves: Trend direction में strong moves
        - Retracement moves: Counter-trend pullbacks
        
        Professional traders:
        - Impulsive moves की शुरुआत में Buy/Sell करते हैं
        - Impulsive moves के end पर Profits take करते हैं
        - Retracement moves के दौरान Entry से बचते हैं
        """
        segments = []
        swing_points = self.find_swing_points(candles)
        
        if len(swing_points) < 3:
            return segments
        
        for i in range(1, len(swing_points) - 1):
            prev_point = swing_points[i - 1]
            curr_point = swing_points[i]
            next_point = swing_points[i + 1]
            
            # Direction determine करें
            if curr_point.price > prev_point.price:
                direction = TrendDirection.UP
            elif curr_point.price < prev_point.price:
                direction = TrendDirection.DOWN
            else:
                direction = TrendDirection.SIDEWAYS
            
            # Impulsive या retracement determine करें
            next_direction = TrendDirection.UP if next_point.price > curr_point.price else TrendDirection.DOWN
            
            if direction == next_direction:
                segment_type = SegmentType.IMPULSIVE
            elif direction == TrendDirection.SIDEWAYS:
                segment_type = SegmentType.CONSOLIDATION
            else:
                segment_type = SegmentType.RETRACEMENT
            
            # Calculate volatility
            segment_candles = candles[prev_point.index:curr_point.index+1]
            if segment_candles:
                prices = [c.close for c in segment_candles]
                volatility = np.std(prices) / np.mean(prices) if np.mean(prices) > 0 else 0
                volume_profile = np.mean([c.volume for c in segment_candles])
            else:
                volatility = 0
                volume_profile = 0
            
            segments.append(TrendSegment(
                start_index=prev_point.index,
                end_index=curr_point.index,
                start_price=prev_point.price,
                end_price=curr_point.price,
                segment_type=segment_type,
                direction=direction,
                volatility=volatility,
                volume_profile=volume_profile
            ))
        
        return segments
    
    def get_current_segment(self, candles: List[Candlestick]) -> Optional[TrendSegment]:
        """Current trend segment प्राप्त करें"""
        segments = self.identify_trend_segments(candles)
        return segments[-1] if segments else None
    
    def is_impulsive_move(self, candles: List[Candlestick]) -> bool:
        """क्या current move impulsive है?"""
        segment = self.get_current_segment(candles)
        return segment is not None and segment.segment_type == SegmentType.IMPULSIVE
    
    def is_retracement_move(self, candles: List[Candlestick]) -> bool:
        """क्या current move retracement है?"""
        segment = self.get_current_segment(candles)
        return segment is not None and segment.segment_type == SegmentType.RETRACEMENT
    
    def find_support_resistance(self, candles: List[Candlestick], 
                                 num_levels: int = 3) -> Tuple[List[float], List[float]]:
        """
        Support और Resistance levels find करें
        
        Returns:
            Tuple of (support_levels, resistance_levels)
        """
        swing_points = self.find_swing_points(candles)
        
        swing_highs = sorted([sp.price for sp in swing_points if sp.is_high], reverse=True)
        swing_lows = sorted([sp.price for sp in swing_points if sp.is_low])
        
        # Current price
        current_price = candles[-1].close if candles else 0
        
        # Support levels: Current price से नीचे
        supports = [low for low in swing_lows if low < current_price * 0.999][:num_levels]
        
        # Resistance levels: Current price से ऊपर
        resistances = [high for high in swing_highs if high > current_price * 1.001][:num_levels]
        
        return supports, resistances


# ============================================================================
# CHAPTER 6: TECHNICAL INDICATORS (COMPLETE)
# ============================================================================

class TechnicalIndicators:
    """
    Technical indicators price और volume का mathematical analysis provide करते हैं।
    Candlestick patterns के साथ combined, वे trading accuracy बढ़ाते हैं।
    """
    
    @staticmethod
    def calculate_ema(prices: np.ndarray, span: int) -> np.ndarray:
        """Exponential Moving Average"""
        return pd.Series(prices).ewm(span=span, adjust=False).mean().values
    
    @staticmethod
    def calculate_sma(prices: np.ndarray, window: int) -> np.ndarray:
        """Simple Moving Average"""
        return pd.Series(prices).rolling(window=window).mean().values
    
    @staticmethod
    def calculate_wma(prices: np.ndarray, window: int) -> np.ndarray:
        """Weighted Moving Average"""
        weights = np.arange(1, window + 1)
        return pd.Series(prices).rolling(window=window).apply(
            lambda x: np.dot(x, weights) / weights.sum(), raw=True
        ).values
    
    @staticmethod
    def calculate_macd(prices: np.ndarray,
                       fast: int = 12,
                       slow: int = 26,
                       signal: int = 9) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        MACD (Moving Average Convergence Divergence)
        
        Returns:
            Tuple of (MACD Line, Signal Line, Histogram)
        """
        ema_fast = TechnicalIndicators.calculate_ema(prices, fast)
        ema_slow = TechnicalIndicators.calculate_ema(prices, slow)
        macd_line = ema_fast - ema_slow
        signal_line = TechnicalIndicators.calculate_ema(macd_line, signal)
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram
    
    @staticmethod
    def calculate_adx(high: np.ndarray,
                      low: np.ndarray,
                      close: np.ndarray,
                      window: int = 14) -> np.ndarray:
        """
        ADX (Average Directional Index) - Trend strength measure करता है
        
        - ADX > 25: Strong trend
        - ADX < 20: Weak trend या ranging
        - ADX 20-25: Trend forming
        """
        df = pd.DataFrame({'high': high, 'low': low, 'close': close})
        
        plus_dm = df['high'].diff()
        minus_dm = -df['low'].diff()
        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm < 0] = 0
        
        tr1 = df['high'] - df['low']
        tr2 = abs(df['high'] - df['close'].shift(1))
        tr3 = abs(df['low'] - df['close'].shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        atr = tr.ewm(alpha=1/window, adjust=False).mean()
        plus_di = 100 * (plus_dm.ewm(alpha=1/window, adjust=False).mean() / atr)
        minus_di = 100 * (minus_dm.ewm(alpha=1/window, adjust=False).mean() / atr)
        
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
        adx = dx.ewm(alpha=1/window, adjust=False).mean()
        return adx.values
    
    @staticmethod
    def calculate_plus_minus_di(high: np.ndarray,
                                 low: np.ndarray,
                                 close: np.ndarray,
                                 window: int = 14) -> Tuple[np.ndarray, np.ndarray]:
        """+DI और -DI calculate करें"""
        df = pd.DataFrame({'high': high, 'low': low, 'close': close})
        
        plus_dm = df['high'].diff()
        minus_dm = -df['low'].diff()
        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm < 0] = 0
        
        tr1 = df['high'] - df['low']
        tr2 = abs(df['high'] - df['close'].shift(1))
        tr3 = abs(df['low'] - df['close'].shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        atr = tr.ewm(alpha=1/window, adjust=False).mean()
        plus_di = 100 * (plus_dm.ewm(alpha=1/window, adjust=False).mean() / atr)
        minus_di = 100 * (minus_dm.ewm(alpha=1/window, adjust=False).mean() / atr)
        
        return plus_di.values, minus_di.values
    
    @staticmethod
    def calculate_rsi(prices: np.ndarray, window: int = 14) -> np.ndarray:
        """
        RSI (Relative Strength Index)
        
        - RSI > 70: Overbought
        - RSI < 30: Oversold
        - RSI 30-70: Neutral zone
        """
        delta = pd.Series(prices).diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi.values
    
    @staticmethod
    def calculate_stochastic(high: np.ndarray,
                              low: np.ndarray,
                              close: np.ndarray,
                              k_window: int = 14,
                              d_window: int = 3) -> Tuple[np.ndarray, np.ndarray]:
        """
        Stochastic Oscillator
        
        - %K > 80: Overbought
        - %K < 20: Oversold
        """
        df = pd.DataFrame({'high': high, 'low': low, 'close': close})
        
        lowest_low = df['low'].rolling(window=k_window).min()
        highest_high = df['high'].rolling(window=k_window).max()
        
        stoch_k = 100 * (df['close'] - lowest_low) / (highest_high - lowest_low)
        stoch_d = stoch_k.rolling(window=d_window).mean()
        
        return stoch_k.values, stoch_d.values
    
    @staticmethod
    def calculate_bollinger_bands(prices: np.ndarray,
                                   window: int = 20,
                                   num_std: float = 2.0) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Bollinger Bands
        
        Returns:
            Tuple of (Upper Band, Middle Band, Lower Band)
        """
        series = pd.Series(prices)
        middle = series.rolling(window=window).mean()
        std = series.rolling(window=window).std()
        upper = middle + (std * num_std)
        lower = middle - (std * num_std)
        return upper.values, middle.values, lower.values
    
    @staticmethod
    def calculate_atr(high: np.ndarray,
                      low: np.ndarray,
                      close: np.ndarray,
                      window: int = 14) -> np.ndarray:
        """
        ATR (Average True Range) - Volatility measure करता है
        """
        df = pd.DataFrame({'high': high, 'low': low, 'close': close})
        
        tr1 = df['high'] - df['low']
        tr2 = abs(df['high'] - df['close'].shift(1))
        tr3 = abs(df['low'] - df['close'].shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        atr = tr.rolling(window=window).mean()
        return atr.values
    
    @staticmethod
    def calculate_vwap(high: np.ndarray,
                       low: np.ndarray,
                       close: np.ndarray,
                       volume: np.ndarray) -> np.ndarray:
        """VWAP (Volume Weighted Average Price)"""
        typical_price = (high + low + close) / 3
        cumulative_tp_vol = np.cumsum(typical_price * volume)
        cumulative_vol = np.cumsum(volume)
        vwap = cumulative_tp_vol / cumulative_vol
        return vwap
    
    @staticmethod
    def calculate_obv(close: np.ndarray, volume: np.ndarray) -> np.ndarray:
        """OBV (On Balance Volume)"""
        df = pd.DataFrame({'close': close, 'volume': volume})
        obv = (np.sign(df['close'].diff()) * df['volume']).fillna(0).cumsum()
        return obv.values


# ============================================================================
# CHAPTER 7: TECHNICAL STRATEGIES WITH GRID SEARCH
# ============================================================================

class TechnicalStrategies:
    """
    Technical indicators based strategies with grid search optimization।
    Paper specifications के अनुसार implemented।
    """
    
    def __init__(self, fee_per_trade: float = 0.001):
        """
        Args:
            fee_per_trade: Paper formula: α = 0.1% for each buy or sell
        """
        self.fee_per_trade = fee_per_trade
        self.indicators = TechnicalIndicators()
    
    def ema_strategy(self, df: pd.DataFrame, 
                     short_window: int, 
                     long_window: int) -> pd.Series:
        """
        EMA Cross Strategy:
        ━━━━━━━━━━━━━━━━━
        Buy signal: Short-term EMA crosses above Long-term EMA
        Sell signal: Short-term EMA crosses below Long-term EMA
        """
        df = df.copy()
        df['EMA_Short'] = df['Price'].ewm(span=short_window, adjust=False).mean()
        df['EMA_Long'] = df['Price'].ewm(span=long_window, adjust=False).mean()
        df['Signal'] = np.where(df['EMA_Short'] > df['EMA_Long'], 1, 0)
        # Shift by 1 to avoid look-ahead bias
        df['Position'] = df['Signal'].shift(1).fillna(0)
        return df['Position']
    
    def macd_adx_strategy(self, df: pd.DataFrame,
                          macd_short: int,
                          macd_long: int,
                          signal_window: int,
                          adx_window: int) -> pd.Series:
        """
        MACD + ADX Strategy:
        ━━━━━━━━━━━━━━━━━━
        MACD detects momentum
        ADX measures trend strength (>25 is strong)
        Trade signal only when both align
        """
        df = df.copy()
        
        # Calculate MACD
        ema_short = df['Price'].ewm(span=macd_short, adjust=False).mean()
        ema_long = df['Price'].ewm(span=macd_long, adjust=False).mean()
        macd_line = ema_short - ema_long
        signal_line = macd_line.ewm(span=signal_window, adjust=False).mean()
        
        # Calculate ADX (using close as proxy for high/low as per paper)
        high = df['Price']
        low = df['Price']
        close = df['Price']
        
        plus_dm = high.diff()
        minus_dm = -low.diff()
        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm < 0] = 0
        
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        atr = tr.ewm(alpha=1/adx_window, adjust=False).mean()
        plus_di = 100 * (plus_dm.ewm(alpha=1/adx_window, adjust=False).mean() / atr)
        minus_di = 100 * (minus_dm.ewm(alpha=1/adx_window, adjust=False).mean() / atr)
        
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
        adx = dx.ewm(alpha=1/adx_window, adjust=False).mean()
        
        # Generate Signals
        macd_bullish = macd_line > signal_line
        strong_trend = adx > 25
        
        df['Signal'] = np.where((macd_bullish) & (strong_trend), 1, 0)
        df['Position'] = df['Signal'].shift(1).fillna(0)
        return df['Position']
    
    def optimize_ema(self, train_df: pd.DataFrame,
                     short_range: Tuple[int, int] = (5, 20),
                     long_range: Tuple[int, int] = (20, 100)) -> Dict:
        """
        Grid search optimization for EMA parameters
        
        Returns:
            Dictionary with optimal parameters
        """
        best_return = -np.inf
        best_params = {'short': 6, 'long': 95}  # Paper fallback
        
        for short in range(short_range[0], short_range[1]):
            for long in range(long_range[0], long_range[1]):
                if short >= long:
                    continue
                positions = self.ema_strategy(train_df, short, long)
                returns = train_df['Price'].pct_change() * positions
                cum_return = (1 + returns).prod() - 1
                if cum_return > best_return:
                    best_return = cum_return
                    best_params = {'short': short, 'long': long}
        
        logger.info(f"Optimal EMA Params: Short={best_params['short']}, Long={best_params['long']}")
        return best_params
    
    def optimize_macd_adx(self, train_df: pd.DataFrame) -> Dict:
        """
        Grid search optimization for MACD+ADX parameters
        Paper optimal fallback: macd_s: 17, macd_l: 21, signal: 15, adx: 13
        """
        best_return = -np.inf
        best_params = {'macd_s': 17, 'macd_l': 21, 'signal': 15, 'adx': 13}
        
        for ms in range(5, 20):
            for ml in range(10, 30):
                if ms >= ml:
                    continue
                for sig in range(5, 15):
                    for adx_w in range(10, 20):
                        positions = self.macd_adx_strategy(train_df, ms, ml, sig, adx_w)
                        returns = train_df['Price'].pct_change() * positions
                        cum_return = (1 + returns).prod() - 1
                        if cum_return > best_return:
                            best_return = cum_return
                            best_params = {
                                'macd_s': ms, 'macd_l': ml,
                                'signal': sig, 'adx': adx_w
                            }
        
        logger.info(f"Optimal MACD+ADX Params: {best_params}")
        return best_params
    
    def calculate_strategy_returns(self, test_df: pd.DataFrame,
                                   positions: pd.Series,
                                   eval_start: str = None) -> Dict:
        """
        Paper Formula:
        ━━━━━━━━━━━━━
        Total commission cost = (Number of trades)(α) where α = 0.1%
        Cumulative Return with cost = Cumulative Return - Total commission cost
        """
        if eval_start:
            test_eval = test_df.loc[eval_start:]
            pos_eval = positions.loc[eval_start:]
        else:
            test_eval = test_df
            pos_eval = positions
        
        # Daily returns
        daily_returns = test_eval['Price'].pct_change()
        strategy_returns = daily_returns * pos_eval
        
        # Cumulative Return: (1 + returns).prod() - 1
        cumulative_return = (1 + strategy_returns).dropna().prod() - 1
        
        # Count trades (every position change = 1 trade)
        trades = pos_eval.diff().abs().dropna()
        num_trades = int(trades.sum())
        
        # Apply 0.1% fee per trade
        cost = num_trades * self.fee_per_trade
        return_with_cost = cumulative_return - cost
        
        return {
            'cumulative_return': cumulative_return,
            'return_with_cost': return_with_cost,
            'num_trades': num_trades,
            'cost': cost
        }


# ============================================================================

# ============================================================================
# LIVE FIRE ENGINE (polished confluence — pettern-4 Chapters 1–7)
# ============================================================================

@dataclass
class FireTradeSignal:
    """Live bot fire payload (bridge-compatible)."""
    symbol: str
    timestamp: datetime
    action: str  # LONG | SHORT
    entry_price: float
    stop_loss: float
    take_profit: float
    confidence: float
    risk_reward: float
    reasoning: str
    pattern_names: List[str] = field(default_factory=list)
    confluence: Dict[str, Any] = field(default_factory=dict)


# Back-compat alias used by fire_engine_bridge
TradeSignal = FireTradeSignal


_STRONG_REVERSAL = frozenset({
    "Morning Star",
    "Evening Star",
    "Bullish Engulfing",
    "Bearish Engulfing",
    "Bullish Abandoned Baby",
    "Bearish Abandoned Baby",
    "Hammer",
    "Shooting Star",
    "Inverted Hammer",
    "Hanging Man",
    "Piercing Line",
    "Dark Cloud Cover",
    "Three White Soldiers",
    "Three Black Crows",
})


class LiveTradeFireEngine:
    """Deep integrated scanner → fire with structure + indicators + patterns."""

    def __init__(
        self,
        *,
        min_confluence: float = 0.72,
        min_edge: float = 0.04,
        ema_short: int = 6,
        ema_long: int = 95,
        macd_fast: int = 17,
        macd_slow: int = 21,
        macd_signal: int = 15,
        adx_window: int = 13,
        adx_min: float = 20.0,
        rsi_window: int = 14,
        atr_sl_pad: float = 0.1,
        rr: float = 2.0,
        max_sl_atr: float = 2.5,
        require_impulsive_or_reversal: bool = True,
        skip_sideways: bool = True,
        require_tech_align: bool = False,
    ):
        self.detector = CandlestickPatternDetector()
        self.structure = MarketStructureAnalyzer()
        self.shadow = ShadowPsychologyAnalyzer()
        self.indicators = TechnicalIndicators()
        self.min_confluence = float(min_confluence)
        self.min_edge = float(min_edge)
        self.ema_short = int(ema_short)
        self.ema_long = int(ema_long)
        self.macd_fast = int(macd_fast)
        self.macd_slow = int(macd_slow)
        self.macd_signal = int(macd_signal)
        self.adx_window = int(adx_window)
        self.adx_min = float(adx_min)
        self.rsi_window = int(rsi_window)
        self.atr_sl_pad = float(atr_sl_pad)
        self.rr = float(rr)
        self.max_sl_atr = float(max_sl_atr)
        self.require_impulsive_or_reversal = require_impulsive_or_reversal
        self.skip_sideways = skip_sideways
        self.require_tech_align = require_tech_align

    @staticmethod
    def _safe_last(arr: np.ndarray, default: float = 0.0) -> float:
        if arr is None or len(arr) == 0:
            return default
        v = arr[-1]
        try:
            if v is None or (isinstance(v, float) and np.isnan(v)):
                return default
            return float(v)
        except (TypeError, ValueError):
            return default

    def calculate_atr(self, candles: List[Candlestick], period: int = 14) -> float:
        if len(candles) < 2:
            return candles[-1].total_range * 0.5 if candles else 0.0
        high = np.array([c.high for c in candles], dtype=float)
        low = np.array([c.low for c in candles], dtype=float)
        close = np.array([c.close for c in candles], dtype=float)
        atr = self.indicators.calculate_atr(high, low, close, window=period)
        val = self._safe_last(atr, 0.0)
        if val <= 0:
            return float(candles[-1].total_range * 0.5)
        return float(val)

    def _volume_trend(self, candles: List[Candlestick], window: int = 10) -> str:
        if len(candles) < window + 2:
            return "stable"
        vols = np.array([c.volume for c in candles[-window:]], dtype=float)
        if np.nanmean(vols) <= 0:
            return "stable"
        first = float(np.nanmean(vols[: max(2, window // 2)]))
        second = float(np.nanmean(vols[max(2, window // 2) :]))
        if first <= 0:
            return "stable"
        ratio = second / first
        if ratio >= 1.12:
            return "increasing"
        if ratio <= 0.88:
            return "decreasing"
        return "stable"

    def _df_to_candles(self, df: pd.DataFrame, lookback: int) -> List[Candlestick]:
        recent = df.iloc[-lookback:]
        # Prefer vectorized column resolve once
        cols = {str(c).lower(): c for c in recent.columns}

        def col(*names: str):
            for n in names:
                if n.lower() in cols:
                    return cols[n.lower()]
            return None

        o_c, h_c, l_c, c_c = col("Open", "open"), col("High", "high"), col("Low", "low"), col("Close", "close")
        v_c = col("Volume", "volume")
        if not all([o_c, h_c, l_c, c_c]):
            return []

        candles: List[Candlestick] = []
        for i, (idx, r) in enumerate(recent.iterrows()):
            ts = idx if isinstance(idx, datetime) else pd.to_datetime(idx).to_pydatetime()
            candles.append(
                Candlestick(
                    timestamp=ts,
                    open=float(r[o_c]),
                    high=float(r[h_c]),
                    low=float(r[l_c]),
                    close=float(r[c_c]),
                    volume=float(r[v_c] if v_c is not None else 0.0) or 0.0,
                    index=i,
                )
            )
        return candles

    def _sr_bias(self, price: float, supports: List[float], resistances: List[float], atr: float) -> Dict[str, float]:
        """Near support → long bias; near resistance → short bias."""
        band = max(atr * 0.35, price * 0.0008) if price else atr * 0.35
        bull = 0.0
        bear = 0.0
        near_sup = None
        near_res = None
        for s in supports or []:
            if s <= price and (price - s) <= band * 3:
                dist = price - s
                w = max(0.0, 1.0 - dist / (band * 3))
                if w > bull:
                    bull = w
                    near_sup = s
        for r in resistances or []:
            if r >= price and (r - price) <= band * 3:
                dist = r - price
                w = max(0.0, 1.0 - dist / (band * 3))
                if w > bear:
                    bear = w
                    near_res = r
        return {"bull": bull * 0.25, "bear": bear * 0.25, "near_support": near_sup or 0.0, "near_resistance": near_res or 0.0}

    def _tech_bias(self, candles: List[Candlestick]) -> Dict[str, Any]:
        closes = np.array([c.close for c in candles], dtype=float)
        highs = np.array([c.high for c in candles], dtype=float)
        lows = np.array([c.low for c in candles], dtype=float)
        ema_s = self.indicators.calculate_ema(closes, self.ema_short)
        ema_l = self.indicators.calculate_ema(closes, self.ema_long)
        macd, sig, hist = self.indicators.calculate_macd(
            closes, self.macd_fast, self.macd_slow, self.macd_signal
        )
        adx = self.indicators.calculate_adx(highs, lows, closes, self.adx_window)
        plus_di, minus_di = self.indicators.calculate_plus_minus_di(
            highs, lows, closes, self.adx_window
        )
        rsi = self.indicators.calculate_rsi(closes, self.rsi_window)

        ema_s1, ema_l1 = self._safe_last(ema_s), self._safe_last(ema_l)
        ema_s0 = self._safe_last(ema_s[:-1], ema_s1) if len(ema_s) > 1 else ema_s1
        ema_l0 = self._safe_last(ema_l[:-1], ema_l1) if len(ema_l) > 1 else ema_l1

        ema_bull = ema_s1 > ema_l1
        ema_bear = ema_s1 < ema_l1
        ema_cross_up = ema_s0 <= ema_l0 and ema_s1 > ema_l1
        ema_cross_dn = ema_s0 >= ema_l0 and ema_s1 < ema_l1

        macd1, sig1 = self._safe_last(macd), self._safe_last(sig)
        hist1 = self._safe_last(hist)
        macd_bull = macd1 > sig1
        macd_bear = macd1 < sig1
        adx_v = self._safe_last(adx)
        strong = adx_v >= self.adx_min
        rsi_v = self._safe_last(rsi, 50.0)
        pdi = self._safe_last(plus_di)
        mdi = self._safe_last(minus_di)

        bull_score = 0.0
        bear_score = 0.0
        if ema_bull:
            bull_score += 0.30
        if ema_bear:
            bear_score += 0.30
        if ema_cross_up:
            bull_score += 0.22
        if ema_cross_dn:
            bear_score += 0.22
        # MACD: full weight when ADX strong; half weight when weak (still informative)
        macd_w = 0.35 if strong else 0.18
        if macd_bull:
            bull_score += macd_w
        if macd_bear:
            bear_score += macd_w
        if hist1 > 0:
            bull_score += 0.05
        elif hist1 < 0:
            bear_score += 0.05
        if pdi > mdi:
            bull_score += 0.12
        elif mdi > pdi:
            bear_score += 0.12
        if rsi_v < 35:
            bull_score += 0.10
        elif rsi_v < 45:
            bull_score += 0.04
        if rsi_v > 65:
            bear_score += 0.10
        elif rsi_v > 55:
            bear_score += 0.04
        # Extreme RSI: fade continuation, keep reversals viable via pattern weight elsewhere
        if rsi_v > 78:
            bull_score *= 0.35
        if rsi_v < 22:
            bear_score *= 0.35
        if not strong:
            bull_score *= 0.85
            bear_score *= 0.85

        return {
            "ema_bull": bool(ema_bull),
            "macd_bull": bool(macd_bull),
            "adx": adx_v,
            "rsi": rsi_v,
            "bull_score": float(bull_score),
            "bear_score": float(bear_score),
            "strong_trend": strong,
            "plus_di": pdi,
            "minus_di": mdi,
        }

    def _score_patterns(
        self,
        latest: List[PatternResult],
        structure: TrendDirection,
        vol_trend: str,
    ) -> Tuple[float, float, Optional[PatternResult], Optional[PatternResult]]:
        bull_pat = 0.0
        bear_pat = 0.0
        best_bull: Optional[PatternResult] = None
        best_bear: Optional[PatternResult] = None
        best_bull_w = -1.0
        best_bear_w = -1.0

        for p in latest:
            w = float(p.confidence) * (0.55 + 0.45 * min(abs(p.signal.value) / 2.0, 1.0))
            if getattr(p, "volume_confirmation", False):
                w *= 1.18
            if vol_trend == "increasing":
                w *= 1.08
            elif vol_trend == "decreasing":
                w *= 0.92

            if structure == TrendDirection.UP and p.signal.value > 0:
                w *= 1.12
            if structure == TrendDirection.DOWN and p.signal.value < 0:
                w *= 1.12
            if structure == TrendDirection.UP and p.signal.value < 0 and p.signal.value > -2:
                w *= 0.50
            if structure == TrendDirection.DOWN and p.signal.value > 0 and p.signal.value < 2:
                w *= 0.50

            if p.signal.value > 0:
                bull_pat += w
                if w > best_bull_w:
                    best_bull_w = w
                    best_bull = p
            elif p.signal.value < 0:
                bear_pat += w
                if w > best_bear_w:
                    best_bear_w = w
                    best_bear = p

        return bull_pat, bear_pat, best_bull, best_bear

    def scan_and_fire(self, symbol: str, df: pd.DataFrame, lookback: int = 120) -> Optional[FireTradeSignal]:
        effective_lb = max(int(lookback), self.ema_long + 5, 100)
        if len(df) < effective_lb:
            return None

        candles = self._df_to_candles(df, effective_lb)
        if len(candles) < 30:
            return None

        last = candles[-1]
        atr = self.calculate_atr(candles)
        if atr <= 0 or last.close <= 0:
            return None

        structure, sinfo = self.structure.identify_market_structure(candles)
        supports, resistances = self.structure.find_support_resistance(candles)
        segment = self.structure.get_current_segment(candles)
        trend_strength = float(sinfo.get("trend_strength") or 0.5)
        vol_trend = self._volume_trend(candles)

        market = MarketState(
            structure=structure,
            trend_strength=trend_strength,
            volatility=atr / last.close,
            volume_trend=vol_trend,
            support_levels=supports,
            resistance_levels=resistances,
            current_segment=segment,
        )

        if self.skip_sideways and structure == TrendDirection.SIDEWAYS:
            return None

        psych = self.shadow.analyze(last, market)
        patterns = self.detector.detect_all_patterns(candles)
        latest = [p for p in patterns if p.end_index >= len(candles) - 3]
        if not latest:
            return None

        # Prefer freshest closing patterns
        max_end = max(p.end_index for p in latest)
        latest = [p for p in latest if p.end_index >= max_end - 1] or latest

        bull_pat, bear_pat, best_bull, best_bear = self._score_patterns(latest, structure, vol_trend)
        if best_bull is None and best_bear is None:
            return None

        # Retracement filter: only strong reversal patterns
        if self.require_impulsive_or_reversal and segment is not None:
            if segment.segment_type == SegmentType.RETRACEMENT:
                candidates = [p for p in (best_bull, best_bear) if p and p.pattern_type in _STRONG_REVERSAL]
                if not candidates:
                    return None

        tech = self._tech_bias(candles)
        sr = self._sr_bias(last.close, supports, resistances, atr)

        buy_p = float(psych.get("buying_pressure") or 0.0) + 0.05
        sell_p = float(psych.get("selling_pressure") or 0.0) + 0.05
        force = psych.get("dominant_force")
        if force in ("buyers", "buyers_strong"):
            buy_p += 0.35
        elif force == "buyers_weak":
            buy_p += 0.12
        if force in ("sellers", "sellers_strong"):
            sell_p += 0.35
        elif force == "sellers_weak":
            sell_p += 0.12

        struct_bull = trend_strength if structure == TrendDirection.UP else 0.22
        struct_bear = trend_strength if structure == TrendDirection.DOWN else 0.22
        if segment is not None:
            if segment.segment_type == SegmentType.IMPULSIVE and structure == TrendDirection.UP:
                struct_bull = min(1.0, struct_bull + 0.15)
            if segment.segment_type == SegmentType.IMPULSIVE and structure == TrendDirection.DOWN:
                struct_bear = min(1.0, struct_bear + 0.15)

        bull = (
            0.42 * min(bull_pat, 2.2) / 2.2
            + 0.18 * min(buy_p, 1.2) / 1.2
            + 0.14 * struct_bull
            + 0.18 * min(tech["bull_score"], 1.25) / 1.25
            + 0.08 * sr["bull"] / 0.25
        )
        bear = (
            0.42 * min(bear_pat, 2.2) / 2.2
            + 0.18 * min(sell_p, 1.2) / 1.2
            + 0.14 * struct_bear
            + 0.18 * min(tech["bear_score"], 1.25) / 1.25
            + 0.08 * sr["bear"] / 0.25
        )

        long_ok = bull >= self.min_confluence and (bull - bear) >= self.min_edge
        short_ok = bear >= self.min_confluence and (bear - bull) >= self.min_edge

        if long_ok and best_bull is not None:
            if force == "sellers" and best_bull.signal.value < 2:
                long_ok = False
            if self.require_tech_align and tech["bear_score"] > tech["bull_score"] + 0.25:
                long_ok = False
            # Don't long into hard resistance without strong reversal
            if sr["near_resistance"] and best_bull.pattern_type not in _STRONG_REVERSAL:
                if abs(last.close - sr["near_resistance"]) < atr * 0.25:
                    long_ok = False

        if short_ok and best_bear is not None:
            if force == "buyers" and best_bear.signal.value > -2:
                short_ok = False
            if self.require_tech_align and tech["bull_score"] > tech["bear_score"] + 0.25:
                short_ok = False
            if sr["near_support"] and best_bear.pattern_type not in _STRONG_REVERSAL:
                if abs(last.close - sr["near_support"]) < atr * 0.25:
                    short_ok = False

        if long_ok and short_ok:
            # Tie-break: higher score wins; else skip
            if abs(bull - bear) < self.min_edge:
                return None
            if bull > bear:
                short_ok = False
            else:
                long_ok = False

        if long_ok and best_bull is not None:
            return self._make_long(
                symbol, last, best_bull, atr, bull, latest, structure, tech, psych, supports, resistances, sr
            )
        if short_ok and best_bear is not None:
            return self._make_short(
                symbol, last, best_bear, atr, bear, latest, structure, tech, psych, supports, resistances, sr
            )
        return None

    def _clamp_sl(self, entry: float, sl: float, atr: float, side: str) -> float:
        max_dist = max(atr * self.max_sl_atr, entry * 0.0015)
        min_dist = max(atr * 0.25, entry * 0.0006)
        if side == "LONG":
            if sl >= entry:
                sl = entry - min_dist
            dist = entry - sl
            if dist > max_dist:
                sl = entry - max_dist
            if dist < min_dist:
                sl = entry - min_dist
        else:
            if sl <= entry:
                sl = entry + min_dist
            dist = sl - entry
            if dist > max_dist:
                sl = entry + max_dist
            if dist < min_dist:
                sl = entry + min_dist
        return sl

    def _make_long(
        self, symbol, candle, pattern, atr, score, latest, structure, tech, psych, supports, resistances, sr
    ) -> FireTradeSignal:
        entry = candle.close
        pl = getattr(pattern, "pattern_low", 0.0) or candle.low
        sl = min(pl, candle.low) - (atr * self.atr_sl_pad)
        # Prefer structural support just below if it tightens risk without cutting pattern
        near_sup = sr.get("near_support") or 0.0
        if near_sup and near_sup < entry:
            cand_sl = near_sup - (atr * self.atr_sl_pad)
            if cand_sl < entry and cand_sl >= sl:
                sl = cand_sl
        sl = self._clamp_sl(entry, sl, atr, "LONG")
        risk = entry - sl
        tp = entry + risk * self.rr
        # Cap TP below nearby resistance if very close (avoid firing into wall)
        near_res = sr.get("near_resistance") or 0.0
        if near_res and near_res > entry and (near_res - entry) < risk * self.rr:
            # Still keep at least 1.2R if resistance allows
            tp = max(entry + risk * 1.2, min(tp, near_res - atr * 0.05))

        names = [p.pattern_type for p in latest if p.signal.value > 0] or [pattern.pattern_type]
        return FireTradeSignal(
            symbol=symbol,
            timestamp=candle.timestamp,
            action="LONG",
            entry_price=round(entry, 8),
            stop_loss=round(sl, 8),
            take_profit=round(tp, 8),
            confidence=round(min(score, 1.0), 4),
            risk_reward=round((tp - entry) / risk, 3) if risk > 0 else self.rr,
            reasoning=(
                f"[{pattern.pattern_type}] {str(pattern.psychology)[:110]} | "
                f"structure={structure.value} ADX={tech.get('adx', 0):.1f} "
                f"RSI={tech.get('rsi', 0):.1f} | shadow={psych.get('dominant_force')}"
            ),
            pattern_names=names[:5],
            confluence={
                "score": score,
                "structure": structure.value,
                "tech": {k: tech[k] for k in ("adx", "rsi", "ema_bull", "macd_bull", "strong_trend")},
                "psych": psych.get("dominant_force"),
                "sr": {"support": sr.get("near_support"), "resistance": sr.get("near_resistance")},
            },
        )

    def _make_short(
        self, symbol, candle, pattern, atr, score, latest, structure, tech, psych, supports, resistances, sr
    ) -> FireTradeSignal:
        entry = candle.close
        ph = getattr(pattern, "pattern_high", 0.0) or candle.high
        sl = max(ph, candle.high) + (atr * self.atr_sl_pad)
        near_res = sr.get("near_resistance") or 0.0
        if near_res and near_res > entry:
            cand_sl = near_res + (atr * self.atr_sl_pad)
            if cand_sl > entry and cand_sl <= sl:
                sl = cand_sl
        sl = self._clamp_sl(entry, sl, atr, "SHORT")
        risk = sl - entry
        tp = entry - risk * self.rr
        near_sup = sr.get("near_support") or 0.0
        if near_sup and near_sup < entry and (entry - near_sup) < risk * self.rr:
            tp = min(entry - risk * 1.2, max(tp, near_sup + atr * 0.05))

        names = [p.pattern_type for p in latest if p.signal.value < 0] or [pattern.pattern_type]
        return FireTradeSignal(
            symbol=symbol,
            timestamp=candle.timestamp,
            action="SHORT",
            entry_price=round(entry, 8),
            stop_loss=round(sl, 8),
            take_profit=round(tp, 8),
            confidence=round(min(score, 1.0), 4),
            risk_reward=round((entry - tp) / risk, 3) if risk > 0 else self.rr,
            reasoning=(
                f"[{pattern.pattern_type}] {str(pattern.psychology)[:110]} | "
                f"structure={structure.value} ADX={tech.get('adx', 0):.1f} "
                f"RSI={tech.get('rsi', 0):.1f} | shadow={psych.get('dominant_force')}"
            ),
            pattern_names=names[:5],
            confluence={
                "score": score,
                "structure": structure.value,
                "tech": {k: tech[k] for k in ("adx", "rsi", "ema_bull", "macd_bull", "strong_trend")},
                "psych": psych.get("dominant_force"),
                "sr": {"support": sr.get("near_support"), "resistance": sr.get("near_resistance")},
            },
        )
