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
