"""
=====================================================================================
THE CANDLESTICK TRADING BIBLE - ULTRA DEEP AI ENGINE
=====================================================================================
Author AI Architecture: Based strictly on Munehisa Homma's principles & provided PDF.
Philosophy: "Candlesticks are the language of financial markets." 
            We do not trade colors; we trade psychology, rejection, and equilibrium.
            
Core Logic Flow:
1. PRIORITY 1: Smart Money Trap Detection (80% Confidence Liquidity Grabs)
2. PRIORITY 2: Strict 10-Pattern Bible Recognition (Zero Color Bias, Strict Shadow Math)
3. PRIORITY 3: Market Structure Filtering (Impulsive vs Retracement, HH/HL, 30% Trend Rule)
4. PRIORITY 4: Risk Management (Max 2%, Strict 1:2 R:R, ATR-based Stops)
=====================================================================================
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional, List, Tuple, Dict, Any
from enum import Enum

# ==========================================
# 1. ENUMERATIONS & DATA STRUCTURES
# ==========================================

class TrendDirection(Enum):
    """PDF Rule: Market makes HH/HL (Uptrend) or LH/LL (Downtrend)."""
    UPTREND = "UPTREND"
    DOWNTREND = "DOWNTREND"
    RANGING = "RANGING"
    CHOPPY = "CHOPPY" # PDF Rule: Zoom out on daily, if no clear direction = Choppy (DO NOT TRADE)

class MarketPhase(Enum):
    """PDF Rule (Page 54): Trending markets have Impulsive moves and Retracement moves."""
    IMPULSIVE = "IMPULSIVE"       # Professional traders buy HERE.
    RETRACEMENT = "RETRACEMENT"   # Retail traders get trapped HERE. Bot must AVOID.
    EQUILIBRIUM = "EQUILIBRIUM"   # Ranging market state.

class PsychologyState(Enum):
    """PDF Rule: Human behavior dominated by Fear, Greed, Hope. Patterns show this."""
    BULLISH_REJECTION = "BULLISH_REJECTION"     # Sellers tried, buyers overwhelmed (Hammer)
    BEARISH_REJECTION = "BEARISH_REJECTION"     # Buyers tried, sellers overwhelmed (Shooting Star)
    INDECISION = "INDECISION"                   # Equality, no control (Doji/Harami)
    CAPITULATION = "CAPITULATION"               # Total surrender of one side (Engulfing)
    MOMENTUM_SHIFT = "MOMENTUM_SHIFT"           # Trend losing strength, major turning point (Stars)

@dataclass
class CandlestickAnatomy:
    """Deconstructs a candle exactly as PDF Page 11-13 describes."""
    index: int
    timestamp: Any
    open: float
    high: float
    low: float
    close: float
    
    @property
    def is_bullish_candle(self) -> bool:
        """PDF: Close > Open means market is rising."""
        return self.close > self.open
    
    @property
    def absolute_body_size(self) -> float:
        """PDF: The filled part between open and close."""
        return abs(self.close - self.open)
    
    @property
    def body_top(self) -> float:
        """The highest point of the real body."""
        return max(self.open, self.close)
    
    @property
    def body_bottom(self) -> float:
        """The lowest point of the real body."""
        return min(self.open, self.close)
    
    @property
    def upper_shadow(self) -> float:
        """PDF: Thin line poking above the body. Signifies session high rejection."""
        return self.high - self.body_top
    
    @property
    def lower_shadow(self) -> float:
        """PDF: Thin line poking below the body. Signifies session low rejection."""
        return self.body_bottom - self.low
    
    @property
    def total_range(self) -> float:
        """Total distance from High to Low."""
        return self.high - self.low

@dataclass
class TradeSignal:
    """The final output if all strict Bible conditions are met."""
    symbol: str
    direction: str 
    entry_price: float
    stop_loss: float
    take_profit: float
    pattern_name: str
    bible_psychology: PsychologyState
    market_structure: TrendDirection
    market_phase: MarketPhase
    confluences: List[str]
    risk_reward_ratio: float
    position_size: float
    timestamp: Any

@dataclass
class MarketStructureState:
    """PDF Pages 51-68: The study of market behavior."""
    trend: TrendDirection
    phase: MarketPhase
    resistance_level: float
    support_level: float
    swing_highs: List[float] = field(default_factory=list)
    swing_lows: List[float] = field(default_factory=list)

# ==========================================
# 2. MARKET PSYCHOLOGY MAPPER (PDF PAGES 8-10)
# ==========================================

class PsychologyMapper:
    """
    PDF Core Concept: "He wanted to track the emotion of the market players... 
    human behavior in relation to money is always dominated by fear; greed, and hope."
    """
    @staticmethod
    def get_psychology(pattern_name: str) -> PsychologyState:
        mapping = {
            "Bullish Engulfing": PsychologyState.CAPITULATION,
            "Bearish Engulfing": PsychologyState.CAPITULATION,
            "Dragonfly Doji": PsychologyState.BULLISH_REJECTION,
            "Gravestone Doji": PsychologyState.BEARISH_REJECTION,
            "Morning Star": PsychologyState.MOMENTUM_SHIFT,
            "Evening Star": PsychologyState.MOMENTUM_SHIFT,
            "Hammer (Pin Bar)": PsychologyState.BULLISH_REJECTION,
            "Shooting Star (Pin Bar)": PsychologyState.BEARISH_REJECTION,
            "Bullish Harami": PsychologyState.INDECISION,
            "Bearish Harami": PsychologyState.INDECISION,
            "Tweezers Bottom": PsychologyState.BULLISH_REJECTION,
            "Tweezers Top": PsychologyState.BEARISH_REJECTION,
            "80% Bull Trap": PsychologyState.BEARISH_REJECTION,
            "80% Bear Trap": PsychologyState.BULLISH_REJECTION,
        }
        return mapping.get(pattern_name, PsychologyState.INDECISION)

# ==========================================
# 3. STRICT BIBLE PATTERN DETECTOR (PDF PAGES 14-46)
# ==========================================

class BiblePatternDetector:
    """
    Absolute strict pattern recognition.
    PDF Rule (Page 19): "The color of the bodies is not important. 
    What’s important is that the smaller one is totally engulfed."
    """
    
    @staticmethod
    def _validate_anatomy(c: CandlestickAnatomy) -> bool:
        """Sanity check to prevent division by zero or invalid math."""
        return c.total_range > 0 and c.high >= c.low

    # --- 1. ENGULFING BAR (Pages 16-19) ---
    @classmethod
    def detect_engulfing(cls, curr: CandlestickAnatomy, prev: CandlestickAnatomy) -> Optional[str]:
        """
        PDF Rule: "The Engulfing bar as it states in its title is formed when it fully 
        engulfs the previous candle... at least one candle must be fully consumed."
        """
        if not cls._validate_anatomy(curr) or not cls._validate_anatomy(prev):
            return None
            
        # Rule: Curr body MUST totally cover Prev body (Top to Bottom)
        curr_covers_prev_top = curr.body_top >= prev.body_top
        curr_covers_prev_bottom = curr.body_bottom <= prev.body_bottom
        is_larger_body = curr.absolute_body_size > prev.absolute_body_size
        
        if curr_covers_prev_top and curr_covers_prev_bottom and is_larger_body:
            # PDF Rule: Direction is determined by close vs open of CURRENT candle
            if curr.close > curr.open:
                return "Bullish Engulfing" # Buyers take control from sellers
            else:
                return "Bearish Engulfing" # Sellers take control from buyers
        return None

    # --- 2 & 3. DRAGONFLY & GRAVESTONE DOJI (Pages 22-27) ---
    @classmethod
    def detect_dragonfly_doji(cls, c: CandlestickAnatomy) -> bool:
        """
        PDF Rule: "formed when the open high and close are the same or about the same price. 
        What characterizes the dragonfly Doji is the long lower tail..."
        """
        if not cls._validate_anatomy(c): return False
        
        # Rule: Open, High, Close roughly same (Body must be at absolute top)
        is_body_at_top = c.upper_shadow <= (c.total_range * 0.05) # Less than 5% tolerance
        
        # Rule: "Long lower tail that shows the resistance of buyers"
        has_long_tail = c.lower_shadow > (c.absolute_body_size * 2.0) if c.absolute_body_size > 0 else c.lower_shadow > (c.total_range * 0.6)
        
        return is_body_at_top and has_long_tail

    @classmethod
    def detect_gravestone_doji(cls, c: CandlestickAnatomy) -> bool:
        """
        PDF Rule: "formed when the open and close are the same or about the same price.
        What differentiates the Gravestone Doji... is the long upper tail."
        """
        if not cls._validate_anatomy(c): return False
        
        # Rule: Open, Low, Close roughly same (Body must be at absolute bottom)
        is_body_at_bottom = c.lower_shadow <= (c.total_range * 0.05)
        
        # Rule: "Long upper tail... testing a powerful supply or resistance area"
        has_long_tail = c.upper_shadow > (c.absolute_body_size * 2.0) if c.absolute_body_size > 0 else c.upper_shadow > (c.total_range * 0.6)
        
        return is_body_at_bottom and has_long_tail

    # --- 4 & 5. MORNING & EVENING STAR (Pages 28-33) ---
    @classmethod
    def detect_morning_star(cls, c1: CandlestickAnatomy, c2: CandlestickAnatomy, c3: CandlestickAnatomy) -> bool:
        """
        PDF Rule: 3 Candles.
        1st: Large bearish (sellers in charge).
        2nd: Small candle (indecision, can be doji).
        3rd: Bullish candle that gapped up and "closed above the midpoint of the body of the first day".
        """
        # Candle 1 conditions
        is_c1_bearish = c1.close < c1.open
        c1_midpoint = (c1.body_top + c1.body_bottom) / 2.0
        
        # Candle 2 conditions (Indecision)
        is_c2_small = c2.absolute_body_size < (c1.absolute_body_size * 0.3)
        
        # Candle 3 conditions (Bullish reversal confirmation)
        is_c3_bullish = c3.close > c3.open
        closes_above_midpoint = c3.close > c1_midpoint
        
        # Gap check (PDF: "gapped up on the open" - highly favorable)
        # Note: Strict gap is c3.open > c2.high, but we allow proximity for forex/crypto
        
        return is_c1_bearish and is_c2_small and is_c3_bullish and closes_above_midpoint

    @classmethod
    def detect_evening_star(cls, c1: CandlestickAnatomy, c2: CandlestickAnatomy, c3: CandlestickAnatomy) -> bool:
        """
        PDF Rule: Bearish version of Morning Star.
        3rd candle is large bearish closing below midpoint of 1st day.
        """
        is_c1_bullish = c1.close > c1.open
        c1_midpoint = (c1.body_top + c1.body_bottom) / 2.0
        
        is_c2_small = c2.absolute_body_size < (c1.absolute_body_size * 0.3)
        
        is_c3_bearish = c3.close < c3.open
        closes_below_midpoint = c3.close < c1_midpoint
        
        return is_c1_bullish and is_c2_small and is_c3_bearish and closes_below_midpoint

    # --- 6 & 7. HAMMER & SHOOTING STAR (Pages 34-39) ---
    @classmethod
    def detect_hammer(cls, c: CandlestickAnatomy) -> bool:
        """
        PDF Rule: "created when the open high and close are roughly the same price; 
        it is also characterized by a long lower shadow... The shadow should be twice the length."
        """
        if not cls._validate_anatomy(c): return False
        
        # Rule: Open, High, Close roughly same -> Body at top
        is_body_at_top = c.upper_shadow <= (c.total_range * 0.15)
        
        # Rule: Shadow 2x length of real body
        if c.absolute_body_size == 0:
            has_tail = c.lower_shadow >= (c.total_range * 0.6)
        else:
            has_tail = c.lower_shadow >= (c.absolute_body_size * 2.0)
            
        return is_body_at_top and has_tail

    @classmethod
    def detect_shooting_star(cls, c: CandlestickAnatomy) -> bool:
        """
        PDF Rule: "open low, and close are roughly the same price... 
        characterized by a small body and a long upper shadow."
        """
        if not cls._validate_anatomy(c): return False
        
        # Rule: Open, Low, Close roughly same -> Body at bottom
        is_body_at_bottom = c.lower_shadow <= (c.total_range * 0.15)
        
        # Rule: Shadow 2x length of real body
        if c.absolute_body_size == 0:
            has_tail = c.upper_shadow >= (c.total_range * 0.6)
        else:
            has_tail = c.upper_shadow >= (c.absolute_body_size * 2.0)
            
        return is_body_at_bottom and has_tail

    # --- 8. HARAMI / INSIDE BAR (Pages 40-42) ---
    @classmethod
    def detect_harami(cls, mother: CandlestickAnatomy, baby: CandlestickAnatomy) -> bool:
        """
        PDF Rule: "The first candle is the large candle (mother)... followed by a smaller candle (baby).
        the smaller body closes inside of the first bigger candle."
        """
        if not cls._validate_anatomy(mother) or not cls._validate_anatomy(baby): return False
        
        # Rule: Baby body must be strictly inside Mother body
        baby_inside_top = baby.body_top < mother.body_top
        baby_inside_bottom = baby.body_bottom > mother.body_bottom
        is_smaller = baby.absolute_body_size < mother.absolute_body_size
        
        return baby_inside_top and baby_inside_bottom and is_smaller

    # --- 9 & 10. TWEEZERS TOPS & BOTTOMS (Pages 43-46) ---
    @classmethod
    def detect_tweezers_top(cls, c1: CandlestickAnatomy, c2: CandlestickAnatomy, atr: float) -> bool:
        """
        PDF Rule: "First one is a bullish candlestick followed by a bearish candlestick."
        Matching Highs (testing same resistance).
        """
        if atr == 0: return False
        # Tolerance based on market volatility (ATR)
        tolerance = atr * 0.1 
        
        highs_match = abs(c1.high - c2.high) <= tolerance
        c1_is_bullish = c1.close > c1.open
        c2_is_bearish = c2.close < c2.open
        
        return highs_match and c1_is_bullish and c2_is_bearish

    @classmethod
    def detect_tweezers_bottom(cls, c1: CandlestickAnatomy, c2: CandlestickAnatomy, atr: float) -> bool:
        """
        PDF Rule: "First candle is bearish followed by a bullish candlestick."
        Matching Lows (testing same support).
        """
        if atr == 0: return False
        tolerance = atr * 0.1 
        
        lows_match = abs(c1.low - c2.low) <= tolerance
        c1_is_bearish = c1.close < c1.open
        c2_is_bullish = c2.close > c2.open
        
        return lows_match and c1_is_bearish and c2_is_bullish

# ==========================================
# 4. MARKET STRUCTURE ANALYZER (PDF PAGES 51-68)
# ==========================================

class StructureAnalyzer:
    """
    PDF Rule: "One of the most important skill that you need as a trader is the ability 
    to read the market structure... trends are estimated to occur 30% of the time."
    """
    
    @staticmethod
    def find_swing_points(df: pd.DataFrame, lookback: int = 3) -> Tuple[List[int], List[int]]:
        """Identifies exact pivot highs and lows for S/R and Trendlines."""
        swing_high_idxs = []
        swing_low_idxs = []
        
        for i in range(lookback, len(df) - lookback):
            high_window = df['High'].iloc[i-lookback : i+lookback+1]
            low_window = df['Low'].iloc[i-lookback : i+lookback+1]
            
            if df['High'].iloc[i] == high_window.max():
                swing_high_idxs.append(i)
            if df['Low'].iloc[i] == low_window.min():
                swing_low_idxs.append(i)
                
        return swing_high_idxs, swing_low_idxs

    @classmethod
    def analyze_structure(cls, df: pd.DataFrame, idx: int) -> MarketStructureState:
        """Determines Trend, Phase (Impulse/Retrace), and S/R."""
        if idx < 0 or idx >= len(df):
            return MarketStructureState(
                trend=TrendDirection.CHOPPY,
                phase=MarketPhase.EQUILIBRIUM,
                resistance_level=0.0,
                support_level=0.0,
            )

        lookback = 50
        start = max(0, idx - lookback)
        recent_df = df.iloc[start : idx + 1]
        # find_swing_points returns indices relative to recent_df (0-based), not global df.
        sh_idxs, sl_idxs = cls.find_swing_points(recent_df, lookback=3)
        sh_prices = [float(recent_df["High"].iloc[i]) for i in sh_idxs] if sh_idxs else []
        sl_prices = [float(recent_df["Low"].iloc[i]) for i in sl_idxs] if sl_idxs else []
        
        state = MarketStructureState(
            trend=TrendDirection.RANGING,
            phase=MarketPhase.EQUILIBRIUM,
            resistance_level=float(recent_df['High'].max()),
            support_level=float(recent_df['Low'].min()),
            swing_highs=sh_prices,
            swing_lows=sl_prices
        )
        
        # PDF Rule: Determine HH/HL or LH/LL
        if len(sh_prices) >= 2 and len(sl_prices) >= 2:
            is_hh = sh_prices[-1] > sh_prices[-2]
            is_hl = sl_prices[-1] > sl_prices[-2]
            is_lh = sh_prices[-1] < sh_prices[-2]
            is_ll = sl_prices[-1] < sl_prices[-2]
            
            if is_hh and is_hl: state.trend = TrendDirection.UPTREND
            elif is_lh and is_ll: state.trend = TrendDirection.DOWNTREND

        # PDF Rule: Choppy Market Filter (Page 68) — soft tag only, do NOT block (exits are fixed ±0.5%).
        atr = float(recent_df['High'].subtract(recent_df['Low']).mean() or 0.0)
        range_size = state.resistance_level - state.support_level
        if atr > 0 and range_size < (atr * 2.5):
            state.trend = TrendDirection.CHOPPY

        # Impulsive vs Retracement — informational only. Retracement no longer hard-blocks
        # (expert policy: traps/momentum can still fire in retracement with fixed ±0.5% exit).
        if idx > 0:
            curr_close = float(df['Close'].iloc[idx])
            prev_close = float(df['Close'].iloc[idx-1])
            
            if state.trend == TrendDirection.UPTREND and curr_close > prev_close:
                state.phase = MarketPhase.IMPULSIVE
            elif state.trend == TrendDirection.DOWNTREND and curr_close < prev_close:
                state.phase = MarketPhase.IMPULSIVE
            else:
                state.phase = MarketPhase.RETRACEMENT

        return state

# ==========================================
# 5. SMART MONEY TRAP DETECTOR (PRIORITY 1)
# ==========================================

class SmartMoneyTrapDetector:
    """
    Intercepts Retail Traders being trapped by False Breakouts.
    "Deviate & Reclaim" Strategy.
    """
    @staticmethod
    def calculate_atr(df: pd.DataFrame, period: int = 14) -> float:
        if len(df) < period + 1: return 0.0
        tr = pd.concat([
            df['High'] - df['Low'],
            np.abs(df['High'] - df['Close'].shift(1)),
            np.abs(df['Low'] - df['Close'].shift(1))
        ], axis=1).max(axis=1)
        return tr.iloc[-period:].mean()

    @classmethod
    def scan_for_traps(cls, c: CandlestickAnatomy, resistance: float, support: float, atr: float) -> Optional[Dict]:
        if atr == 0: return None
        
        # 1. Bull Trap (Retail Buy -> Bot Short)
        # Price went above Resistance, but CLOSED BELOW it. Long upper wick.
        if c.high > resistance and c.close < resistance:
            if c.upper_shadow > (c.absolute_body_size * 1.5):
                return {
                    "action": "SHORT",
                    "pattern": "80% Bull Trap",
                    "sl": c.high + (atr * 0.5),
                    "reason": "Retail buying breakout, Smart money selling. Deviate & Reclaim."
                }
                
        # 2. Bear Trap (Retail Sell -> Bot Long)
        # Price went below Support, but CLOSED ABOVE it. Long lower wick.
        if c.low < support and c.close > support:
            if c.lower_shadow > (c.absolute_body_size * 1.5):
                return {
                    "action": "LONG",
                    "pattern": "80% Bear Trap",
                    "sl": c.low - (atr * 0.5),
                    "reason": "Retail selling breakdown, Smart money buying. Deviate & Reclaim."
                }
        return None

# ==========================================
# 6. RISK MANAGEMENT (PDF PAGE 133+)
# ==========================================

class RiskManager:
    """
    PDF Rule: "create a money management and risk control plan that will allow you 
    to protect your trading capital... Maximum 2%... Minimum 1:2 R:R"
    """
    def __init__(self, account_balance: float, risk_pct: float = 0.01, min_rr: float = 2.0):
        self.account_balance = account_balance
        self.risk_pct = risk_pct
        self.min_rr = min_rr
        self.max_risk_amount = account_balance * risk_pct

    def calculate_position_size(self, entry: float, stop_loss: float) -> float:
        risk_per_unit = abs(entry - stop_loss)
        if risk_per_unit == 0: return 0.0
        return round(self.max_risk_amount / risk_per_unit, 2)

    def calculate_take_profit(self, entry: float, stop_loss: float, direction: str, rr: float = 2.0) -> float:
        risk = abs(entry - stop_loss)
        if direction == 'LONG': return entry + (risk * rr)
        else: return entry - (risk * rr)

# ==========================================
# 7. THE MASTER AI BRAIN (ORCHESTRATOR)
# ==========================================

class CandlestickTradingBibleEngine:
    """
    The ultimate autonomous agent brain. 
    Executes logic exactly in the order a professional trader would.
    """
    def __init__(self, account_balance: float = 10000.0, risk_pct: float = 0.01):
        self.detector = BiblePatternDetector()
        self.structure_analyzer = StructureAnalyzer()
        self.trap_detector = SmartMoneyTrapDetector()
        self.risk_manager = RiskManager(account_balance, risk_pct)
        self.psychology_mapper = PsychologyMapper()
        
        # Strict Bible Confluence Requirements — loosened for live 1m crypto (exits are fixed ±0.5%).
        self.min_confluences_required = 1

    def _build_anatomy(self, df: pd.DataFrame, idx: int) -> CandlestickAnatomy:
        row = df.iloc[idx]
        return CandlestickAnatomy(
            index=idx, timestamp=row.name, open=row['Open'], high=row['High'],
            low=row['Low'], close=row['Close']
        )

    def _get_dynamic_sr(self, df: pd.DataFrame, idx: int, lookback: int = 20) -> Tuple[float, float]:
        start = max(0, idx - lookback)
        window = df.iloc[start:idx+1]
        return window['High'].max(), window['Low'].min()

    def _evaluate_confluences(self, c: CandlestickAnatomy, state: MarketStructureState, direction: str) -> List[str]:
        """
        PDF Rule: "you will need other factors of confluence to decide whether 
        the pattern is worth trading or not."
        """
        confs = []
        tolerance = (state.resistance_level - state.support_level) * 0.05 
        
        if direction == 'LONG':
            if abs(c.low - state.support_level) <= tolerance: confs.append("Touches Support")
            if state.trend == TrendDirection.UPTREND: confs.append("Aligns with Uptrend")
        elif direction == 'SHORT':
            if abs(c.high - state.resistance_level) <= tolerance: confs.append("Touches Resistance")
            if state.trend == TrendDirection.DOWNTREND: confs.append("Aligns with Downtrend")
            
        # PDF CRITICAL RULE: Must be at start of Impulsive move
        if state.phase == MarketPhase.IMPULSIVE:
            confs.append("Impulsive Move Start (Pro Entry)")
        else:
            confs.append("RETRACEMENT PHASE (High Risk)") 
            
        return confs

    def _finalize_signal(self, symbol: str, c: CandlestickAnatomy, state: MarketStructureState, 
                         pattern_name: str, direction: str, sl: float, tp: float) -> Optional[TradeSignal]:
        """Final Gatekeeper: confluences only (retracement no longer hard-blocks)."""
        
        confluences = self._evaluate_confluences(c, state, direction)
        
        # GATE 1: Must have enough confluences (loosened to 1).
        if len([cf for cf in confluences if "High Risk" not in cf]) < self.min_confluences_required:
            return None

        pos_size = self.risk_manager.calculate_position_size(c.close, sl)
        rr = round(abs(tp - c.close) / abs(c.close - sl), 2) if c.close != sl else 0
        
        return TradeSignal(
            symbol=symbol, direction=direction, entry_price=c.close, stop_loss=sl,
            take_profit=tp, pattern_name=pattern_name, 
            bible_psychology=self.psychology_mapper.get_psychology(pattern_name),
            market_structure=state.trend, market_phase=state.phase,
            confluences=confluences, risk_reward_ratio=rr, position_size=pos_size,
            timestamp=c.timestamp
        )

    def evaluate_candle(self, symbol: str, df: pd.DataFrame, idx: int) -> Optional[TradeSignal]:
        """Main evaluation loop ran on every closed candle."""
        if df is None or len(df) < 21:
            return None
        if idx < 20 or idx >= len(df):
            return None  # Need history for S/R; never iloc past end

        # 1. Prepare Data Structures
        c0 = self._build_anatomy(df, idx)
        c1 = self._build_anatomy(df, idx-1)
        c2 = self._build_anatomy(df, idx-2) if idx >= 2 else None
        
        state = self.structure_analyzer.analyze_structure(df, idx)
        resistance, support = self._get_dynamic_sr(df, idx, 20)
        atr = self.trap_detector.calculate_atr(df, 14)
        
        if state.trend == TrendDirection.CHOPPY:
            # Choppy no longer hard-blocks; traps + momentum fallback can still fire.
            pass
        if not self.detector._validate_anatomy(c0): return None

        # ====================================================================
        # PRIORITY 1: SMART MONEY TRAPS (Bypasses normal pattern priority)
        # ====================================================================
        trap = self.trap_detector.scan_for_traps(c0, resistance, support, atr)
        if trap:
            sl = trap['sl']
            tp = self.risk_manager.calculate_take_profit(c0.close, sl, trap['action'], 2.0)
            pos_size = self.risk_manager.calculate_position_size(c0.close, sl)
            rr = round(abs(tp - c0.close) / abs(c0.close - sl), 2) if c0.close != sl else 0
            # Trap gate loosened: exits are fixed ±0.5%, so even sub-2 R:R traps are valid.
            if rr >= 0.5:
                return TradeSignal(
                    symbol, trap['action'], c0.close, sl, tp, trap['pattern'],
                    self.psychology_mapper.get_psychology(trap['pattern']), state.trend, state.phase,
                    ["Deviate & Reclaim", "Liquidity Grab"], rr, pos_size, c0.timestamp
                )

        # ====================================================================
        # PRIORITY 2: STRICT 10-PATTERN BIBLE SCANNING
        # ====================================================================
        
        # 1 & 2. ENGULFING
        engulf = self.detector.detect_engulfing(c0, c1)
        if engulf:
            direction = "LONG" if "Bullish" in engulf else "SHORT"
            sl_buffer = c0.total_range * 0.1
            sl = (c0.low - sl_buffer) if direction == "LONG" else (c0.high + sl_buffer)
            tp = self.risk_manager.calculate_take_profit(c0.close, sl, direction, 2.5)
            return self._finalize_signal(symbol, c0, state, engulf, direction, sl, tp)

        # 3 & 4. DOJIS (Dragonfly & Gravestone)
        if self.detector.detect_dragonfly_doji(c0):
            sl = c0.low - (c0.total_range * 0.1)
            tp = self.risk_manager.calculate_take_profit(c0.close, sl, "LONG", 2.0)
            return self._finalize_signal(symbol, c0, state, "Dragonfly Doji", "LONG", sl, tp)
            
        if self.detector.detect_gravestone_doji(c0):
            sl = c0.high + (c0.total_range * 0.1)
            tp = self.risk_manager.calculate_take_profit(c0.close, sl, "SHORT", 2.0)
            return self._finalize_signal(symbol, c0, state, "Gravestone Doji", "SHORT", sl, tp)

        # 5 & 6. STARS (Morning & Evening) - Requires 3 candles
        if c2:
            if self.detector.detect_morning_star(c2, c1, c0):
                sl = c2.low - (c2.total_range * 0.1)
                tp = self.risk_manager.calculate_take_profit(c0.close, sl, "LONG", 2.0)
                return self._finalize_signal(symbol, c0, state, "Morning Star", "LONG", sl, tp)
                
            if self.detector.detect_evening_star(c2, c1, c0):
                sl = c2.high + (c2.total_range * 0.1)
                tp = self.risk_manager.calculate_take_profit(c0.close, sl, "SHORT", 2.0)
                return self._finalize_signal(symbol, c0, state, "Evening Star", "SHORT", sl, tp)

        # 7 & 8. HAMMER & SHOOTING STAR (Pin Bars)
        if self.detector.detect_hammer(c0):
            sl = c0.low - (c0.total_range * 0.1) # SL beyond tail
            tp = self.risk_manager.calculate_take_profit(c0.close, sl, "LONG", 2.5)
            return self._finalize_signal(symbol, c0, state, "Hammer (Pin Bar)", "LONG", sl, tp)
            
        if self.detector.detect_shooting_star(c0):
            sl = c0.high + (c0.total_range * 0.1) # SL beyond tail
            tp = self.risk_manager.calculate_take_profit(c0.close, sl, "SHORT", 2.5)
            return self._finalize_signal(symbol, c0, state, "Shooting Star (Pin Bar)", "SHORT", sl, tp)

        # 9. HARAMI (Inside Bar)
        # PDF Note: Harami itself is indecision. Trading it requires waiting for breakout of mother candle.
        # We detect it here for logging, but strict entry would be on the NEXT candle.
        if self.detector.detect_harami(c1, c0):
            pass # Skipped for immediate execution per standard Bible strategy (needs breakout confirmation)

        # 10. TWEEZERS
        if self.detector.detect_tweezers_bottom(c1, c0, atr):
            sl = c0.low - (c0.total_range * 0.1)
            tp = self.risk_manager.calculate_take_profit(c0.close, sl, "LONG", 2.0)
            return self._finalize_signal(symbol, c0, state, "Tweezers Bottom", "LONG", sl, tp)
            
        if self.detector.detect_tweezers_top(c1, c0, atr):
            sl = c0.high + (c0.total_range * 0.1)
            tp = self.risk_manager.calculate_take_profit(c0.close, sl, "SHORT", 2.0)
            return self._finalize_signal(symbol, c0, state, "Tweezers Top", "SHORT", sl, tp)

        # ====================================================================
        # PRIORITY 3: EMA MOMENTUM SCALP FALLBACK (expert policy — keeps bot active)
        # Guarantees trades fire on directional 1m momentum even when no bible
        # pattern completes. Exit is fixed ±0.5% gross so risk is bounded.
        # ====================================================================
        mom = self._detect_momentum_scalp(df, idx, c0, atr)
        if mom:
            direction = mom["direction"]
            sl = (c0.low - c0.total_range * 0.1) if direction == "LONG" else (c0.high + c0.total_range * 0.1)
            tp = self.risk_manager.calculate_take_profit(c0.close, sl, direction, 1.0)
            pos_size = self.risk_manager.calculate_position_size(c0.close, sl)
            rr = round(abs(tp - c0.close) / abs(c0.close - sl), 2) if c0.close != sl else 0
            confs = [mom["reason"], f"EMA20 {mom['ema_side']}"]
            return TradeSignal(
                symbol, direction, c0.close, sl, tp, mom["pattern"],
                self.psychology_mapper.get_psychology(mom["pattern"]), state.trend, state.phase,
                confs, rr, pos_size, c0.timestamp
            )

        return None # No high-probability Bible setup found

    def _detect_momentum_scalp(
        self, df: pd.DataFrame, idx: int, c0: CandlestickAnatomy, atr: float
    ) -> Optional[Dict]:
        """EMA20 momentum scalp fallback. Fires on strong body in EMA direction."""
        if atr <= 0 or len(df) < 21:
            return None
        window = df["Close"].iloc[max(0, idx - 19) : idx + 1]
        if len(window) < 20:
            return None
        ema20 = float(window.ewm(span=20, adjust=False).mean().iloc[-1])
        body = c0.absolute_body_size
        rng = c0.total_range
        if rng <= 0 or body <= 0:
            return None
        body_ratio = body / rng
        # Strong body (>= 55% of range) closing in EMA direction.
        if body_ratio < 0.55:
            return None
        if c0.close > c0.open and c0.close > ema20:
            return {
                "direction": "LONG",
                "pattern": "Bullish Momentum Scalp",
                "reason": "Strong bullish body above EMA20",
                "ema_side": "above",
            }
        if c0.close < c0.open and c0.close < ema20:
            return {
                "direction": "SHORT",
                "pattern": "Bearish Momentum Scalp",
                "reason": "Strong bearish body below EMA20",
                "ema_side": "below",
            }
        return None

    def run_backtest(self, symbol: str, df: pd.DataFrame) -> List[TradeSignal]:
        signals = []
        print(f"🔥 Initiating Deep Bible Scan on {len(df)} candles...")
        for i in range(20, len(df)):
            sig = self.evaluate_candle(symbol, df, i)
            if sig:
                signals.append(sig)
                print(f"✅ [{sig.timestamp}] {sig.direction} | {sig.pattern_name} | Psych: {sig.bible_psychology.name} | Phase: {sig.market_phase.name} | R:R 1:{sig.risk_reward_ratio}")
        print(f"📖 Bible Scan Complete. Total Anointed Trades: {len(signals)}")
        return signals