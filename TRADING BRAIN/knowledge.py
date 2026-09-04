"""
The knowledge base — the "memory" of the brain.

Every candlestick pattern is recorded with its direction, type (reversal vs
continuation), candle count, a plain-language definition, the psychology
behind its formation, and what it signals. This is distilled from the three
source documents and is what powers the human-readable report.

A pattern entry is a dict with keys:
    direction : "bullish" | "bearish" | "neutral"
    kind      : "reversal" | "continuation" | "both" | "indecision"
    candles   : int (number of candles in the pattern)
    family    : grouping label
    definition: plain-language anatomy
    psychology: why the pattern forms (buyer/seller battle)
    signal    : what it tells the trader
    confirm   : what strengthens / confirms the signal
    source    : provenance
"""

PATTERNS: dict = {
    # ================================================================== #
    #  BULLISH REVERSAL / CONTINUATION
    # ================================================================== #
    "bullish_engulfing": {
        "direction": "bullish", "kind": "reversal", "candles": 2,
        "family": "engulfing",
        "definition": "A small bearish candle fully engulfed (body and wicks) by a larger bullish candle.",
        "psychology": "Sellers were in control, but buyers overwhelmed them and closed above the prior open.",
        "signal": "Strong buying pressure; reversal up (more powerful at the end of a downtrend — a capitulation bottom).",
        "confirm": "Occurs at support / demand zone, in an oversold area, or with trend confluence.",
        "source": "38 Patterns + Candlestick Trading Bible",
    },
    "hammer": {
        "direction": "bullish", "kind": "reversal", "candles": 1,
        "family": "pin bar",
        "definition": "Small body near the top with a long lower shadow (>=2x body) and little upper shadow.",
        "psychology": "Sellers pushed price down but buyers rejected the move and closed near the open/high.",
        "signal": "Bullish reversal at the bottom of a downtrend — buyers becoming dominant.",
        "confirm": "Form in a downtrend, near support, with the trend, or at a Fibonacci level.",
        "source": "38 Patterns + Candlestick Trading Bible (pin bar)",
    },
    "inverted_hammer": {
        "direction": "bullish", "kind": "reversal", "candles": 1,
        "family": "pin bar",
        "definition": "Small body near the bottom with a long upper shadow and little/no lower shadow.",
        "psychology": "Buyers tried to push price higher during the session (rejection test of sellers).",
        "signal": "Potential bullish reversal; needs bullish confirmation on the next candle.",
        "confirm": "Followed by a strong bullish candle; occurs at a downtrend bottom.",
        "source": "38 Patterns",
    },
    "morning_star": {
        "direction": "bullish", "kind": "reversal", "candles": 3,
        "family": "star",
        "definition": "Long bearish candle, then a small-body candle (gap down), then a long bullish candle closing into the first body.",
        "psychology": "Sellers lose momentum, indecision, then buyers take over decisively.",
        "signal": "Strong bullish reversal out of a downtrend.",
        "confirm": "Third candle closes above the midpoint of the first candle's body.",
        "source": "38 Patterns + Candlestick Trading Bible",
    },
    "piercing_line": {
        "direction": "bullish", "kind": "reversal", "candles": 2,
        "family": "piercing",
        "definition": "Bearish candle then a bullish candle that opens below the prior close and closes above the midpoint (50%) of the prior bearish body.",
        "psychology": "Buyers step in and reclaim more than half of the sellers' prior push.",
        "signal": "Bullish reversal signal — buyers stepping in to reverse the downtrend.",
        "confirm": "Close above 50% (ideally above 60%) of the prior bearish body.",
        "source": "38 Patterns",
    },
    "bullish_harami": {
        "direction": "bullish", "kind": "reversal", "candles": 2,
        "family": "inside bar",
        "definition": "A small bullish candle completely contained within the body of the previous large bearish candle.",
        "psychology": "Selling pressure fades; market enters indecision/consolidation.",
        "signal": "Possible bullish reversal; also a continuation pause in a strong trend.",
        "confirm": "At a downtrend bottom; or in an uptrend it is a continuation entry.",
        "source": "38 Patterns + Candlestick Trading Bible (inside bar)",
    },
    "three_white_soldiers": {
        "direction": "bullish", "kind": "reversal", "candles": 3,
        "family": "soldiers",
        "definition": "Three consecutive long bullish candles with small wicks, each opening within the prior body and closing higher.",
        "psychology": "Sustained, unrelenting buying pressure.",
        "signal": "Downtrend-to-uptrend shift; strong bullish momentum.",
        "confirm": "Occurs after a downtrend or consolidation.",
        "source": "38 Patterns",
    },
    "dragonfly_doji": {
        "direction": "bullish", "kind": "reversal", "candles": 1,
        "family": "doji",
        "definition": "Open/high/close at nearly the same price with a long lower shadow and almost no body.",
        "psychology": "Sellers pushed price down but buyers drove it right back — strong rejection.",
        "signal": "Bullish reversal at a downtrend bottom / support.",
        "confirm": "Near support/demand; often mistaken for a hammer (doji has ~no body).",
        "source": "38 Patterns + Candlestick Trading Bible",
    },
    "bullish_abandoned_baby": {
        "direction": "bullish", "kind": "reversal", "candles": 3,
        "family": "star",
        "definition": "Long bearish candle, a doji that gaps down, then a long bullish candle that gaps up (leaving the doji isolated).",
        "psychology": "Complete sentiment shift from bearish to bullish across the gap.",
        "signal": "Strong bullish reversal — a significant shift in sentiment.",
        "confirm": "The doji's wicks do not overlap the bodies on either side (true gap).",
        "source": "38 Patterns",
    },
    "three_inside_up": {
        "direction": "bullish", "kind": "reversal", "candles": 3,
        "family": "harami-combo",
        "definition": "Large bearish candle, a small bullish candle closing above its 50% level, then a bullish candle closing above the first candle's open.",
        "psychology": "Sellers exhaust, buyers stage a two-step reclaim.",
        "signal": "Potential bullish reversal (confirmed on the third candle).",
        "confirm": "Third candle closes above first candle's open.",
        "source": "38 Patterns",
    },
    "three_outside_up": {
        "direction": "bullish", "kind": "reversal", "candles": 3,
        "family": "engulfing-combo",
        "definition": "Bearish candle, a bullish candle that engulfs it, then another bullish candle that closes higher.",
        "psychology": "Engulfing reversal followed by confirmation buying.",
        "signal": "Confirms the strength of a bullish reversal.",
        "confirm": "Third candle closes higher than the second.",
        "source": "38 Patterns",
    },
    "bullish_kicker": {
        "direction": "bullish", "kind": "reversal", "candles": 2,
        "family": "kicker",
        "definition": "Long bearish candle then an even longer bullish candle that opens higher than the prior close and rises more.",
        "psychology": "Sudden, violent takeover by buyers after a bearish day.",
        "signal": "Strong reversal in market sentiment — buyers suddenly in control.",
        "confirm": "Gap up open above the prior candle's close.",
        "source": "38 Patterns",
    },
    "tweezer_bottom": {
        "direction": "bullish", "kind": "reversal", "candles": 2,
        "family": "tweezer",
        "definition": "Two candles (bearish then bullish) with matching/equal lows.",
        "psychology": "Sellers fail to push lower on the second attempt — support holds.",
        "signal": "The market has found a support level; bullish reversal.",
        "confirm": "Equal lows near a known support level.",
        "source": "38 Patterns + Candlestick Trading Bible",
    },
    "rising_three_methods": {
        "direction": "bullish", "kind": "continuation", "candles": 5,
        "family": "three-methods",
        "definition": "Long bullish candle, three small bearish candles within its range, then a long bullish candle closing above the first high.",
        "psychology": "A brief pause/consolidation inside an uptrend before the next leg up.",
        "signal": "Uptrend likely to continue — buyers still in control.",
        "confirm": "Fifth candle closes above the first candle's high.",
        "source": "38 Patterns",
    },
    "concealing_baby_swallow": {
        "direction": "bullish", "kind": "reversal", "candles": 4,
        "family": "swallow",
        "definition": "Two long bearish candles, a gap-down small candle, then a long bearish candle that fully engulfs the small candle (rare).",
        "psychology": "Selling pressure is decreasing in a downtrend — exhaustion.",
        "signal": "Potential bullish reversal as the downtrend loses steam.",
        "confirm": "Rare pattern; combine with support and momentum divergence.",
        "source": "38 Patterns",
    },
    "bullish_mat_hold": {
        "direction": "bullish", "kind": "continuation", "candles": 5,
        "family": "three-methods",
        "definition": "Long bullish candle, small bearish candles that drift lower but stay in range, then a long bullish candle closing above the first high.",
        "psychology": "Brief pause/consolidation in an uptrend before continuation.",
        "signal": "Uptrend continuation after a shallow pullback.",
        "confirm": "Final candle closes above the first candle's high.",
        "source": "38 Patterns",
    },
    "bullish_separating_lines": {
        "direction": "bullish", "kind": "continuation", "candles": 2,
        "family": "separating-lines",
        "definition": "A bearish candle followed by a bullish candle that opens at the same level as the bearish open.",
        "psychology": "Bulls resume control from the same opening level after a pause.",
        "signal": "Uptrend continues after a brief pause.",
        "confirm": "Second candle opens at/above the prior open.",
        "source": "38 Patterns",
    },
    "bullish_belt_hold": {
        "direction": "bullish", "kind": "reversal", "candles": 1,
        "family": "belt-hold",
        "definition": "A single long bullish candle that opens at (or near) its low and closes near its high with little lower shadow.",
        "psychology": "Strong buying from the open — bulls dominate the whole session.",
        "signal": "Strong buying pressure; reversal from a downtrend to an uptrend.",
        "confirm": "Appears at the bottom of a downtrend.",
        "source": "38 Patterns",
    },
    "bullish_three_line_strike": {
        "direction": "bullish", "kind": "continuation", "candles": 4,
        "family": "three-line-strike",
        "definition": "Three consecutive bullish candles, then a long bearish candle that opens higher and closes below the first candle's open.",
        "psychology": "A sharp bearish flush (stop-hunt) that does not change the uptrend.",
        "signal": "Price resumes upward after a brief bearish flush.",
        "confirm": "Next candle resumes upward.",
        "source": "38 Patterns",
    },
    "ladder_bottom": {
        "direction": "bullish", "kind": "reversal", "candles": 5,
        "family": "ladder",
        "definition": "Three consecutive long bearish candles, a small bearish/bullish candle, then a long bullish candle.",
        "psychology": "Bearish trend ends as buying pressure starts to take control.",
        "signal": "Bullish reversal; downtrend ending.",
        "confirm": "Final bullish candle closes strongly.",
        "source": "38 Patterns",
    },
    "meeting_lines": {
        "direction": "bullish", "kind": "reversal", "candles": 2,
        "family": "meeting-lines",
        "definition": "A long bearish candle followed by a long bullish candle that opens lower but closes at the same level as the bearish close.",
        "psychology": "Buyers meet sellers at the same price — shift from selling to buying pressure.",
        "signal": "Bullish reversal at a downtrend.",
        "confirm": "Second close matches the prior close (shared level).",
        "source": "38 Patterns",
    },

    # ================================================================== #
    #  BEARISH REVERSAL / CONTINUATION
    # ================================================================== #
    "bearish_engulfing": {
        "direction": "bearish", "kind": "reversal", "candles": 2,
        "family": "engulfing",
        "definition": "A small bullish candle fully engulfed by a larger bearish candle.",
        "psychology": "Buyers were in control, but sellers overwhelmed them and closed below the prior open.",
        "signal": "Sellers take control; bearish reversal at the end of an uptrend.",
        "confirm": "Occurs at resistance / supply, or after an extended uptrend.",
        "source": "38 Patterns + Candlestick Trading Bible",
    },
    "hanging_man": {
        "direction": "bearish", "kind": "reversal", "candles": 1,
        "family": "pin bar",
        "definition": "A single candle with a small body and a long lower shadow at the top of an uptrend.",
        "psychology": "Sellers pushed price down intraday — first sign of selling pressure.",
        "signal": "Selling pressure increasing; the uptrend may be ending.",
        "confirm": "Appears at the top of an uptrend; confirm with a bearish follow-through.",
        "source": "38 Patterns",
    },
    "shooting_star": {
        "direction": "bearish", "kind": "reversal", "candles": 1,
        "family": "pin bar",
        "definition": "Small body near the low with a long upper shadow (>=2x body) and little/no lower shadow.",
        "psychology": "Buyers pushed price up but sellers rejected it back down.",
        "signal": "Bearish reversal at the top of an uptrend — sellers taking over.",
        "confirm": "Near resistance/supply; upper shadow >= 2x body (per the Bible).",
        "source": "38 Patterns + Candlestick Trading Bible (bearish pin bar)",
    },
    "evening_star": {
        "direction": "bearish", "kind": "reversal", "candles": 3,
        "family": "star",
        "definition": "Long bullish candle, a small-body candle (gap up), then a long bearish candle closing well into the first body.",
        "psychology": "Buyers lose momentum, indecision, then sellers take over.",
        "signal": "Uptrend losing momentum; a downtrend may be starting.",
        "confirm": "Third candle closes into the first candle's body.",
        "source": "38 Patterns + Candlestick Trading Bible",
    },
    "bearish_harami": {
        "direction": "bearish", "kind": "reversal", "candles": 2,
        "family": "inside bar",
        "definition": "A small bearish candle fully contained within the body of the previous large bullish candle.",
        "psychology": "Buying pressure weakens; market consolidates.",
        "signal": "Buying pressure weakening — reversal to the downside may be coming.",
        "confirm": "At the top of an uptrend; or a continuation pause in a downtrend.",
        "source": "38 Patterns + Candlestick Trading Bible (inside bar)",
    },
    "three_black_crows": {
        "direction": "bearish", "kind": "reversal", "candles": 3,
        "family": "crows",
        "definition": "Three consecutive long red candles with small wicks, each closing lower.",
        "psychology": "Strong, steady, sustained selling pressure.",
        "signal": "Continuation/reversal to the downside — sellers firmly in control.",
        "confirm": "Occurs after an uptrend or at resistance.",
        "source": "38 Patterns",
    },
    "gravestone_doji": {
        "direction": "bearish", "kind": "reversal", "candles": 1,
        "family": "doji",
        "definition": "Open/close/low at nearly the same price with a long upper shadow and almost no body.",
        "psychology": "Buyers pushed price up but sellers drove it right back — rejection at supply.",
        "signal": "Bulls losing momentum; bearish reversal at a resistance level.",
        "confirm": "Must occur near resistance for reliability (per the Bible).",
        "source": "Candlestick Trading Bible",
    },
    "bearish_abandoned_baby": {
        "direction": "bearish", "kind": "reversal", "candles": 3,
        "family": "star",
        "definition": "Long bullish candle, a doji that gaps up, then a long bearish candle that gaps down.",
        "psychology": "Sharp reversal from bullish to bearish sentiment.",
        "signal": "Sharp bearish reversal — the beginning of a downtrend.",
        "confirm": "The doji's wicks do not overlap neighboring bodies (true gap).",
        "source": "38 Patterns",
    },
    "three_inside_down": {
        "direction": "bearish", "kind": "reversal", "candles": 3,
        "family": "harami-combo",
        "definition": "Large bullish candle, a small bearish candle within it, then a bearish candle closing lower.",
        "psychology": "Sellers gain dominance over buyers across two steps.",
        "signal": "Confirms a bearish reversal; potential downtrend.",
        "confirm": "Third candle closes below the first candle's low.",
        "source": "38 Patterns",
    },
    "three_outside_down": {
        "direction": "bearish", "kind": "reversal", "candles": 3,
        "family": "engulfing-combo",
        "definition": "Bullish candle, a bearish candle that engulfs it, then another bearish candle closing lower.",
        "psychology": "Engulfing reversal followed by confirmation selling.",
        "signal": "Confirms the strength of a bearish reversal.",
        "confirm": "Third candle closes lower than the second.",
        "source": "38 Patterns",
    },
    "bearish_kicker": {
        "direction": "bearish", "kind": "reversal", "candles": 2,
        "family": "kicker",
        "definition": "Long bullish candle then a long bearish candle that opens below the prior open and closes lower.",
        "psychology": "Dramatic shift in market sentiment — sudden seller takeover.",
        "signal": "Strong reversal to the downside.",
        "confirm": "Gap down open below the prior candle's open.",
        "source": "38 Patterns",
    },
    "tweezer_top": {
        "direction": "bearish", "kind": "reversal", "candles": 2,
        "family": "tweezer",
        "definition": "Two candles (bullish then bearish) with matching/equal highs.",
        "psychology": "Buyers fail to push higher on the second attempt — resistance holds.",
        "signal": "Upward momentum weakening; bearish reversal likely.",
        "confirm": "Matching highs at a known resistance level.",
        "source": "38 Patterns + Candlestick Trading Bible",
    },
    "bearish_mat_hold": {
        "direction": "bearish", "kind": "continuation", "candles": 5,
        "family": "three-methods",
        "definition": "Long bearish candle, smaller bullish candles that drift up but stay in range, then a long bearish candle closing below the first low.",
        "psychology": "Brief pause before the downtrend continues.",
        "signal": "Downtrend continuation after a shallow pullback.",
        "confirm": "Final candle closes below the first candle's low.",
        "source": "38 Patterns",
    },
    "bearish_separating_lines": {
        "direction": "bearish", "kind": "continuation", "candles": 2,
        "family": "separating-lines",
        "definition": "A bullish candle followed by a bearish candle that opens at the same level as the bullish open.",
        "psychology": "Bears resume control from the same opening level after a pause.",
        "signal": "Downtrend continues after a brief pause.",
        "confirm": "Second candle opens at/below the prior open.",
        "source": "38 Patterns",
    },
    "bearish_belt_hold": {
        "direction": "bearish", "kind": "reversal", "candles": 1,
        "family": "belt-hold",
        "definition": "A single long bearish candle that opens at (or near) its high and closes near its low with little upper shadow.",
        "psychology": "Strong selling from the open — bears dominate the whole session.",
        "signal": "Strong selling pressure; reversal from an uptrend to a downtrend.",
        "confirm": "Appears at the top of an uptrend.",
        "source": "38 Patterns",
    },
    "bearish_three_line_strike": {
        "direction": "bearish", "kind": "continuation", "candles": 4,
        "family": "three-line-strike",
        "definition": "Three consecutive bearish candles, then a long bullish candle that opens lower and closes above the first candle's open.",
        "psychology": "A sharp bullish flush (stop-hunt) that does not change the downtrend.",
        "signal": "Short pullback; the downtrend continues.",
        "confirm": "Next candle resumes downward.",
        "source": "38 Patterns",
    },
    "upside_gap_two_crows": {
        "direction": "bearish", "kind": "reversal", "candles": 3,
        "family": "crows",
        "definition": "A long green candle, then two small red candles that gap up; the second red candle closes below the first red candle's close.",
        "psychology": "Buyers stall after a gap; sellers begin to press.",
        "signal": "Potential reversal or brief consolidation before the downtrend continues.",
        "confirm": "Second red candle closes below the first red close.",
        "source": "38 Patterns",
    },
    "dark_cloud_cover": {
        "direction": "bearish", "kind": "reversal", "candles": 2,
        "family": "piercing",
        "definition": "A long green candle then a red candle that opens above the prior high but closes below the midpoint of the green body.",
        "psychology": "Buyers push to new highs but sellers overwhelm and reclaim more than half.",
        "signal": "The uptrend might be over; a downtrend could begin.",
        "confirm": "Second close below the 50% midpoint of the first body.",
        "source": "38 Patterns",
    },
    "bearish_doji_star": {
        "direction": "bearish", "kind": "reversal", "candles": 2,
        "family": "star",
        "definition": "A long bullish candle followed by a doji (very small body) near its high.",
        "psychology": "Indecision after a strong up move — buyers hesitate.",
        "signal": "Potential downtrend if followed by a bearish candle.",
        "confirm": "A bearish candle follows the doji to confirm reversal.",
        "source": "38 Patterns",
    },
    "doji": {
        "direction": "neutral", "kind": "indecision", "candles": 1,
        "family": "doji",
        "definition": "Open and close at (nearly) the same price — equality between buyers and sellers.",
        "psychology": "Market indecision; no one is in control.",
        "signal": "Potential reversal when it appears at the top/bottom of a trend.",
        "confirm": "Combine with key levels or a confirmation candle.",
        "source": "Candlestick Trading Bible",
    },
}


def pattern_info(name: str):
    """Return the knowledge entry for a pattern name (or None)."""
    return PATTERNS.get(name)


def pattern_names() -> list:
    return list(PATTERNS.keys())


def bullish_patterns() -> list:
    return [n for n, d in PATTERNS.items() if d["direction"] == "bullish"]


def bearish_patterns() -> list:
    return [n for n, d in PATTERNS.items() if d["direction"] == "bearish"]


def reversal_patterns() -> list:
    return [n for n, d in PATTERNS.items() if d["kind"] in ("reversal", "both")]


def continuation_patterns() -> list:
    return [n for n, d in PATTERNS.items() if d["kind"] in ("continuation", "both")]


# --------------------------------------------------------------------------
#  STRATEGIES (from the Candlestick Trading Bible)
# --------------------------------------------------------------------------
STRATEGIES = {
    "pin_bar": {
        "patterns": ["hammer", "shooting_star", "inverted_hammer", "hanging_man"],
        "definition": "A candle with a small body and a long tail (>=2x body) showing rejection.",
        "entry": "Aggressive: enter on close of the pin bar. Conservative: enter on a 50% retracement of its range.",
        "stop": "Beyond the long tail (above the tail for shorts, below for longs).",
        "target": "Next support (for shorts) / resistance (for longs) level.",
        "rules": [
            "Trade on 4H / daily time frames (not 5-min).",
            "With the trend is more powerful than counter-trend.",
            "Longer tail = stronger rejection = more powerful.",
            "Rejection at a key level (S/R, MA, Fib, supply/demand) is essential.",
        ],
    },
    "engulfing_bar": {
        "patterns": ["bullish_engulfing", "bearish_engulfing"],
        "definition": "Second body fully engulfs the first body (Nison's criteria: clear trend, opposite bodies, full engulf).",
        "entry": "On the close of the engulfing candle.",
        "stop": "Below/above the engulfing pattern.",
        "target": "Next support/resistance level.",
        "rules": [
            "Requires a clearly definable trend (per Steve Nison).",
            "The two real bodies must be opposite colors.",
            "Trade with the trend; use with MA (8/21), Fibonacci 50%/61%, trendlines.",
            "Sideways-market variants: from S/R, breakouts, or false breakouts.",
        ],
    },
    "inside_bar": {
        "patterns": ["bullish_harami", "bearish_harami"],
        "definition": "A small bar completely inside the previous (mother) bar — consolidation/indecision.",
        "entry": "On the breakout of the mother bar (safest), in the direction of the trend.",
        "stop": "Beyond the mother candle.",
        "target": "Next support/resistance level.",
        "rules": [
            "Bulkowski: bearish inside bar in a bull market reverses ~65% of the time; bullish continuation ~52%.",
            "Trade the dominant trend on bigger time frames.",
            "Trade only from key levels; find confluence.",
        ],
    },
    "inside_bar_false_breakout": {
        "patterns": ["bullish_harami", "bearish_harami"],
        "definition": "Price breaks out of the inside/mother bar then quickly reverses back inside — a stop-hunt / bull or bear trap.",
        "entry": "After the close of the reversal bar (the trap is sprung).",
        "stop": "Beyond the reversal bar.",
        "target": "Next support/resistance level; can offer very high R:R.",
        "rules": [
            "Exploits institutional stop-loss hunting (liquidity grabs).",
            "Bullish FB at a downtrend bottom = bullish reversal; bearish FB at an uptrend top = bearish reversal.",
            "Best at 50%/61% Fibonacci, 21 MA, S/R, trendlines, or horizontal range levels.",
        ],
    },
}


# --------------------------------------------------------------------------
#  MARKET STRUCTURE + CONFLUENCE + MONEY MANAGEMENT (from the Bible)
# --------------------------------------------------------------------------
MARKET_STRUCTURE = {
    "uptrend": "A repeating pattern of higher highs (HH) and higher lows (HL).",
    "downtrend": "A repeating pattern of lower highs (LH) and lower lows (LL).",
    "ranging": "Price moves horizontally between definable support and resistance (>=2 touches each).",
    "choppy": "No clear direction, tight noisy range, no identifiable boundaries — stay away.",
    "trend_share": "Trends occur ~30% of the time; markets range ~70% of the time.",
}

CONFLUENCE_FACTORS = {
    "trend": "Is the signal in line with the dominant trend? (most important factor)",
    "support_resistance": "Is price at a horizontal S/R level?",
    "supply_demand": "Is price at a supply/demand zone (institutional levels)?",
    "moving_average_8": "Is price at the 8 EMA/SMA dynamic level?",
    "moving_average_21": "Is price at the 21 SMA dynamic level (author's favorite)?",
    "fibonacci": "Is price at the 50% or 61% Fibonacci retracement?",
    "trendline": "Is price at a drawn trendline?",
    "bollinger": "Is price at the upper/lower Bollinger band (range markets)?",
    "rsi": "Is RSI(14) oversold/overbought or showing momentum on the trade side?",
    "macd": "Is MACD(12/26/9) aligned (line vs signal) with the trade side?",
    "vwap": "Is price at VWAP (session mean) as dynamic support/resistance?",
    "vwap_bias": "Is price on the correct side of VWAP for directional bias?",
    "timeframe_alignment": "Do the higher time frames agree (top-down analysis)?",
}

MONEY_MANAGEMENT = {
    "max_risk_per_trade": "Risk no more than 2% of equity per trade (1% for beginners).",
    "min_risk_reward": "Minimum 1:2 risk:reward — only take trades that can win >= 2x the risk.",
    "position_sizing": "Size in dollars-at-risk, not pips: units = (equity * risk%) / stop_distance.",
    "stop_loss": "Always place a protective stop; never use mental stops.",
    "afford": "Never risk money you cannot afford to lose; start small.",
    "edge_math": "At 1:3 R:R you can lose 70% of trades and still be profitable.",
    "example_1_2": "10 trades, 1:2 R:R, risk $100 each: 5 wins + 5 losses => +$500 net.",
    "example_1_3": "10 trades, 1:3 R:R: 7 losses (-$1400) + 3 wins (+$1800) => +$400 net.",
}

TOP_DOWN = {
    "primary_timeframes": ["1H", "4H", "Daily"],
    "analysis_order": "Weekly -> Daily -> 4H (or Daily -> 1H). Start big, then drill down.",
    "weekly_big_picture": ["Key S/R levels", "Market structure (trend/range/choppy)", "Previous candle"],
    "entry_timeframe": ["Market condition", "Key levels", "Price-action signal (pin bar / engulfing / inside bar)"],
    "rule": "Never trade a signal that fights a higher-timeframe level.",
}
