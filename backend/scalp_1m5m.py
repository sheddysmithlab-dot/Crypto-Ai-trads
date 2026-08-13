"""1m / 5m scalp intelligence — liquidity traps, HTF location, panic/news/kill-zone gates.

Higher timeframes keep vanilla Fire Engine. This module only applies when
``timeframe_key`` is ``1m`` or ``5m``.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

UTC = timezone.utc
ET = ZoneInfo("America/New_York")

SCALP_TFS = frozenset({"1m", "5m", "1M", "5M"})
ENTRY_PATTERN_NAME = "FIRE_SCALP_1M5M"

# --- knobs ---
ATR_SPIKE_MULT = float(os.environ.get("SCALP_ATR_SPIKE_MULT", "3.0"))
ATR_LOOKBACK = int(os.environ.get("SCALP_ATR_LOOKBACK", "14"))
VOL_SPIKE_MULT = float(os.environ.get("SCALP_VOL_SPIKE_MULT", "5.0"))
VOL_LOOKBACK = int(os.environ.get("SCALP_VOL_LOOKBACK", "20"))
PANIC_PAUSE_SEC = float(os.environ.get("SCALP_PANIC_PAUSE_SEC", str(30 * 60)))
NEWS_PAD_MIN = float(os.environ.get("SCALP_NEWS_PAD_MIN", "15"))
HTF_KEY = (os.environ.get("SCALP_HTF_TF", "15m") or "15m").strip()
# Wider band so "near HTF" is usable on 1m/5m (was too tight → zero trades)
HTF_NEAR_ATR = float(os.environ.get("SCALP_HTF_NEAR_ATR", "2.5"))
SWEEP_LOOKBACK = int(os.environ.get("SCALP_SWEEP_LOOKBACK", "20"))  # Recent_High lookback
# Shadow must be > body * this (10% club rejection wick)
REJECTION_BODY_MULT = float(os.environ.get("SCALP_REJECTION_BODY_MULT", "1.5"))
SWEEP_SCORE_BOOST = float(os.environ.get("SCALP_SWEEP_SCORE_BOOST", "0.12"))
REQUIRE_SWEEP = os.environ.get("SCALP_REQUIRE_SWEEP", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
TRAP_CONFIDENCE = float(os.environ.get("SCALP_TRAP_CONFIDENCE", "0.80"))
# SL pad beyond sweep wick (curriculum: High + ATR*0.5)
ATR_SL_MULT = float(os.environ.get("SCALP_ATR_SL_MULT", "0.5"))
ADX_MIN = float(os.environ.get("SCALP_ADX_MIN", "18"))
ADX_PERIOD = int(os.environ.get("SCALP_ADX_PERIOD", "14"))
# Soft by default — hard ADX block was freezing 1m/5m in chop
HARD_ADX = os.environ.get("SCALP_HARD_ADX", "0").strip().lower() in {"1", "true", "yes", "on"}
# Soft by default — hard HTF + missing HTF data = zero trades
REQUIRE_HTF = os.environ.get("SCALP_REQUIRE_HTF", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
REQUIRE_KILLZONE = os.environ.get("SCALP_REQUIRE_KILLZONE", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
# Soft: outside kill-zone raise confluence need; hard if REQUIRE_KILLZONE
KILLZONE_EXTRA_CONF = float(os.environ.get("SCALP_KILLZONE_EXTRA_CONF", "0.05"))
MIN_CONFLUENCE = float(os.environ.get("SCALP_MIN_CONFLUENCE", os.environ.get("FIRE_ENGINE_MIN_CONFLUENCE", "0.72")))
# Clean trap (bait+reclaim+1.5x wick) auto-clears scorecard — curriculum STEP 5
TRAP_BYPASSES_SCORECARD = os.environ.get("SCALP_TRAP_BYPASS_SCORECARD", "1").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
PARTIAL_RR = float(os.environ.get("SCALP_PARTIAL_RR", "1.0"))
TRAIL_RR = float(os.environ.get("SCALP_TRAIL_RR", "2.0"))
BE_BUFFER_R = float(os.environ.get("SCALP_BE_BUFFER_R", "0.05"))  # fraction of risk beyond entry
PARTIAL_FRAC = float(os.environ.get("SCALP_PARTIAL_FRAC", "0.5"))

_DATA_DIR = Path(os.environ.get("SCALP_DATA_DIR", str(Path(__file__).resolve().parent / "data")))
_NEWS_PATH = Path(os.environ.get("SCALP_NEWS_EVENTS_PATH", str(_DATA_DIR / "scalp_news_events.json")))

# Panic pause until unix ts (global for 1m/5m)
_panic_until: float = 0.0
_panic_reason: str = ""


def is_scalp_timeframe(timeframe_key: str | None) -> bool:
    k = (timeframe_key or "").strip()
    return k.lower() in {"1m", "5m"}


def status_dict() -> dict[str, Any]:
    now = time.time()
    news = news_block_status(now)
    kz = killzone_status(datetime.now(UTC))
    return {
        "mode": ENTRY_PATTERN_NAME,
        "tfs": sorted(SCALP_TFS),
        "panic_active": now < _panic_until,
        "panic_until": _panic_until if now < _panic_until else None,
        "panic_reason": _panic_reason if now < _panic_until else None,
        "news": news,
        "killzone": kz,
        "htf": HTF_KEY,
        "require_htf": REQUIRE_HTF,
        "require_killzone": REQUIRE_KILLZONE,
        "require_sweep": REQUIRE_SWEEP,
        "hard_adx": HARD_ADX,
        "trap_bypasses_scorecard": TRAP_BYPASSES_SCORECARD,
        "adx_min": ADX_MIN,
        "atr_sl_mult": ATR_SL_MULT,
        "partial_rr": PARTIAL_RR,
        "trail_rr": TRAIL_RR,
        "doctrine": [
            "1_liquidity_sweep",
            "2_weighted_confluence",
            "3_asymmetric_atr_trail",
            "4_no_trade_zones",
        ],
    }


# ---------------------------------------------------------------------------
# Panic (ATR / volume)
# ---------------------------------------------------------------------------

def _true_ranges(candles: list[dict]) -> list[float]:
    out: list[float] = []
    prev_close = None
    for c in candles:
        h = float(c["high"])
        l = float(c["low"])
        cl = float(c["close"])
        if prev_close is None:
            out.append(max(h - l, 0.0))
        else:
            out.append(max(h - l, abs(h - prev_close), abs(l - prev_close)))
        prev_close = cl
    return out


def check_volatility_panic(candles: list[dict]) -> tuple[bool, str | None]:
    """Return (blocked, reason). Arms global panic pause on spike."""
    global _panic_until, _panic_reason
    now = time.time()
    if now < _panic_until:
        left = int(_panic_until - now)
        return True, f"volatility panic pause ({left}s left: {_panic_reason})"

    if len(candles) < max(ATR_LOOKBACK, VOL_LOOKBACK) + 1:
        return False, None

    closed = candles  # caller should pass closed-only
    trs = _true_ranges(closed)
    atr = sum(trs[-(ATR_LOOKBACK + 1) : -1]) / ATR_LOOKBACK
    last_range = float(closed[-1]["high"]) - float(closed[-1]["low"])
    if atr > 0 and last_range >= atr * ATR_SPIKE_MULT:
        _panic_until = now + PANIC_PAUSE_SEC
        _panic_reason = f"ATR spike {last_range:.6g} >= {ATR_SPIKE_MULT}xATR{ATR_LOOKBACK}={atr:.6g}"
        return True, _panic_reason

    vols = [float(c.get("volume") or 0) for c in closed]
    avg_vol = sum(vols[-(VOL_LOOKBACK + 1) : -1]) / VOL_LOOKBACK
    last_vol = vols[-1]
    if avg_vol > 0 and last_vol >= avg_vol * VOL_SPIKE_MULT:
        _panic_until = now + PANIC_PAUSE_SEC
        _panic_reason = f"volume spike {last_vol:.4g} >= {VOL_SPIKE_MULT}x avg={avg_vol:.4g}"
        return True, _panic_reason

    return False, None


def arm_panic(reason: str, seconds: float | None = None) -> None:
    global _panic_until, _panic_reason
    _panic_until = time.time() + (seconds if seconds is not None else PANIC_PAUSE_SEC)
    _panic_reason = reason


# ---------------------------------------------------------------------------
# News ±15m (file-based calendar + built-in US red-folder heuristics)
# ---------------------------------------------------------------------------

def _load_news_events() -> list[dict]:
    events: list[dict] = []
    try:
        if _NEWS_PATH.is_file():
            raw = json.loads(_NEWS_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                events.extend(raw)
            elif isinstance(raw, dict) and isinstance(raw.get("events"), list):
                events.extend(raw["events"])
    except Exception as exc:
        print(f"[SCALP NEWS] load note: {exc}")
    events.extend(_builtin_high_impact_windows())
    return events


def _builtin_high_impact_windows() -> list[dict]:
    """Approximate recurring US red-folder windows (ET) for ±pad checks.

    Not a full economic calendar — covers common CPI / FOMC / NFP windows.
    Override / extend via ``scalp_news_events.json``.
    """
    now_et = datetime.now(ET)
    out: list[dict] = []
    # Scan ±2 days for known patterns
    for day_offset in range(-1, 3):
        d = (now_et + timedelta(days=day_offset)).date()
        # NFP: first Friday 08:30 ET
        if d.weekday() == 4 and 1 <= d.day <= 7:
            out.append(
                {
                    "title": "Non-Farm Payrolls (approx)",
                    "impact": "high",
                    "at": datetime(d.year, d.month, d.day, 8, 30, tzinfo=ET).astimezone(UTC).isoformat(),
                }
            )
        # CPI often mid-month ~08:30 ET — mark 10–15th Tue–Fri as soft windows only if env says
        # Keep builtin light: FOMC statement days are irregular — rely on JSON file for those.
    # Always include today's optional SCALP_NEWS_FORCE_ISO if set (testing)
    force = (os.environ.get("SCALP_NEWS_FORCE_ISO") or "").strip()
    if force:
        out.append({"title": "forced_news", "impact": "high", "at": force})
    return out


def _parse_event_ts(ev: dict) -> float | None:
    raw = ev.get("at") or ev.get("time") or ev.get("datetime") or ev.get("ts")
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        v = float(raw)
        return v / 1000.0 if v > 1e12 else v
    s = str(raw).strip()
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.timestamp()
    except Exception:
        return None


def news_block_status(now_ts: float | None = None) -> dict[str, Any]:
    now = now_ts if now_ts is not None else time.time()
    pad = NEWS_PAD_MIN * 60.0
    nearest = None
    blocked = False
    title = None
    for ev in _load_news_events():
        impact = str(ev.get("impact") or ev.get("importance") or "high").lower()
        if impact not in ("high", "red", "3", "critical"):
            continue
        ts = _parse_event_ts(ev)
        if ts is None:
            continue
        dist = abs(now - ts)
        if nearest is None or dist < nearest[0]:
            nearest = (dist, ev.get("title") or ev.get("name") or "high-impact", ts)
        if dist <= pad:
            blocked = True
            title = ev.get("title") or ev.get("name") or "high-impact"
    return {
        "blocked": blocked,
        "pad_min": NEWS_PAD_MIN,
        "event": title,
        "nearest_sec": nearest[0] if nearest else None,
        "nearest_title": nearest[1] if nearest else None,
        "nearest_at": nearest[2] if nearest else None,
    }


def check_news_block() -> tuple[bool, str | None]:
    st = news_block_status()
    if st["blocked"]:
        return True, f"news kill-switch ±{NEWS_PAD_MIN:.0f}m ({st.get('event')})"
    return False, None


# ---------------------------------------------------------------------------
# Kill zones (UTC hours — London / NY liquidity)
# ---------------------------------------------------------------------------

# Default: London open–NY afternoon in UTC (rough crypto liquid hours)
# 07:00–11:00 UTC London, 12:00–20:00 UTC NY / overlap
_DEFAULT_KZ = "7-11,12-20"


def _parse_killzone_hours() -> list[tuple[int, int]]:
    raw = (os.environ.get("SCALP_KILLZONE_UTC") or _DEFAULT_KZ).strip()
    ranges: list[tuple[int, int]] = []
    for part in raw.split(","):
        part = part.strip()
        if not part or "-" not in part:
            continue
        a, b = part.split("-", 1)
        try:
            ranges.append((int(a), int(b)))
        except ValueError:
            continue
    return ranges or [(7, 11), (12, 20)]


def in_killzone(dt: datetime | None = None) -> bool:
    now = dt.astimezone(UTC) if dt else datetime.now(UTC)
    h = now.hour
    for start, end in _parse_killzone_hours():
        if start <= end:
            if start <= h < end:
                return True
        else:
            # crosses midnight
            if h >= start or h < end:
                return True
    return False


def killzone_status(dt: datetime | None = None) -> dict[str, Any]:
    now = dt.astimezone(UTC) if dt else datetime.now(UTC)
    active = in_killzone(now)
    return {
        "active": active,
        "now_utc": now.strftime("%Y-%m-%d %H:%M UTC"),
        "windows_utc": _parse_killzone_hours(),
        "require": REQUIRE_KILLZONE,
    }


# ---------------------------------------------------------------------------
# HTF location + sweep (deviate & reclaim)
# ---------------------------------------------------------------------------

def _atr_from_candles(candles: list[dict], n: int = 14) -> float:
    trs = _true_ranges(candles)
    if len(trs) < n:
        return max(trs[-1], 0.0) if trs else 0.0
    return sum(trs[-n:]) / n


def htf_levels(htf_candles: list[dict], lookback: int = 40) -> tuple[list[float], list[float]]:
    """Simple swing highs/lows as S/R proxies from HTF closed bars."""
    if len(htf_candles) < 5:
        return [], []
    bars = htf_candles[-lookback:]
    supports: list[float] = []
    resistances: list[float] = []
    for i in range(2, len(bars) - 2):
        lo = float(bars[i]["low"])
        hi = float(bars[i]["high"])
        if lo <= float(bars[i - 1]["low"]) and lo <= float(bars[i - 2]["low"]) and lo <= float(bars[i + 1]["low"]) and lo <= float(bars[i + 2]["low"]):
            supports.append(lo)
        if hi >= float(bars[i - 1]["high"]) and hi >= float(bars[i - 2]["high"]) and hi >= float(bars[i + 1]["high"]) and hi >= float(bars[i + 2]["high"]):
            resistances.append(hi)
    # also recent extremes
    supports.append(min(float(c["low"]) for c in bars[-10:]))
    resistances.append(max(float(c["high"]) for c in bars[-10:]))
    return supports, resistances


def detect_liquidity_sweep(
    candles: list[dict],
    side_hint: str | None = None,
) -> dict[str, Any]:
    """Bull/Bear trap → REVERSE trade (never skip a confirmed trap).

    Resistance = Highest(High, 20 prior) · Support = Lowest(Low, 20 prior)

    BULL TRAP (retail buys breakout) → bot SHORTS:
      High > Resistance AND Close < Resistance AND UpperWick > Body*1.5
      Trap_Confidence ≈ 80%

    BEAR TRAP (retail sells breakdown) → bot LONGS:
      Low < Support AND Close > Support AND LowerWick > Body*1.5
      Trap_Confidence ≈ 80%
    """
    empty: dict[str, Any] = {
        "sweep": False,
        "trap": False,
        "trap_type": None,
        "direction": None,
        "execute_side": None,
        "trap_confidence": 0.0,
        "boost": 0.0,
        "resistance": None,
        "support": None,
        "breakout_happen": False,
        "reclaim_happen": False,
        "strong_rejection": False,
        "shadow_size": 0.0,
        "body_size": 0.0,
        "curriculum": "bull_bear_trap_reverse_v1",
    }
    n = max(5, SWEEP_LOOKBACK)
    if len(candles) < n + 1:
        return empty

    prior = candles[-(n + 1) : -1]
    last = candles[-1]
    h = float(last["high"])
    l = float(last["low"])
    c = float(last["close"])
    o = float(last["open"])

    resistance = max(float(x["high"]) for x in prior)
    support = min(float(x["low"]) for x in prior)
    empty["resistance"] = resistance
    empty["support"] = support

    body = abs(c - o)
    body_eff = max(body, abs(h - l) * 0.01, 1e-12)
    upper_shadow = h - max(o, c)
    lower_shadow = min(o, c) - l

    # BULL TRAP → reverse SHORT (retail buying the fake breakout)
    bull_trap = (
        h > resistance
        and c < resistance
        and upper_shadow > (body_eff * REJECTION_BODY_MULT)
    )
    # BEAR TRAP → reverse LONG (retail selling the fake breakdown)
    bear_trap = (
        l < support
        and c > support
        and lower_shadow > (body_eff * REJECTION_BODY_MULT)
    )

    if bull_trap and bear_trap:
        if upper_shadow >= lower_shadow:
            bear_trap = False
        else:
            bull_trap = False

    # Optional side_hint only filters when both aren't set; traps ignore Fire buy/sell labels
    if side_hint in ("BUY", "LONG") and bull_trap and not bear_trap:
        pass  # still SHORT — reverse of retail buy
    if side_hint in ("SELL", "SHORT") and bear_trap and not bull_trap:
        pass  # still LONG — reverse of retail sell

    if bull_trap:
        print(
            f"[TRAP] Bull Trap Detected! Retail is Buying, Bot is SHORTING. "
            f"R={resistance:.6g} High={h:.6g} Close={c:.6g} conf={TRAP_CONFIDENCE:.0%}"
        )
        return {
            "sweep": True,
            "trap": True,
            "trap_type": "bull_trap",
            "direction": "bearish",  # price reject up → we short
            "execute_side": "SHORT",
            "trap_confidence": TRAP_CONFIDENCE,
            "boost": SWEEP_SCORE_BOOST,
            "resistance": resistance,
            "support": support,
            "recent_high": resistance,
            "recent_low": support,
            "breakout_happen": True,
            "reclaim_happen": True,
            "strong_rejection": True,
            "shadow_size": upper_shadow,
            "body_size": body,
            "rejection_mult": REJECTION_BODY_MULT,
            "detail": (
                f"BULL TRAP → SHORT: High>{resistance:.6g} Close<{resistance:.6g} "
                f"upperWick>{REJECTION_BODY_MULT}xBody (retail buy, bot shorts)"
            ),
            "sweep_extreme": h,
            "curriculum": "bull_bear_trap_reverse_v1",
            "steps": {
                "1_resistance": resistance,
                "2_breakout_above": True,
                "3_reclaim_below": True,
                "4_upper_wick_1_5x": True,
                "5_reverse": "SHORT",
                "confidence": TRAP_CONFIDENCE,
            },
        }

    if bear_trap:
        print(
            f"[TRAP] Bear Trap Detected! Retail is Selling, Bot is BUYING. "
            f"S={support:.6g} Low={l:.6g} Close={c:.6g} conf={TRAP_CONFIDENCE:.0%}"
        )
        return {
            "sweep": True,
            "trap": True,
            "trap_type": "bear_trap",
            "direction": "bullish",  # price reject down → we long
            "execute_side": "LONG",
            "trap_confidence": TRAP_CONFIDENCE,
            "boost": SWEEP_SCORE_BOOST,
            "resistance": resistance,
            "support": support,
            "recent_high": resistance,
            "recent_low": support,
            "breakout_happen": True,
            "reclaim_happen": True,
            "strong_rejection": True,
            "shadow_size": lower_shadow,
            "body_size": body,
            "rejection_mult": REJECTION_BODY_MULT,
            "detail": (
                f"BEAR TRAP → LONG: Low<{support:.6g} Close>{support:.6g} "
                f"lowerWick>{REJECTION_BODY_MULT}xBody (retail sell, bot buys)"
            ),
            "sweep_extreme": l,
            "curriculum": "bull_bear_trap_reverse_v1",
            "steps": {
                "1_support": support,
                "2_breakdown_below": True,
                "3_reclaim_above": True,
                "4_lower_wick_1_5x": True,
                "5_reverse": "LONG",
                "confidence": TRAP_CONFIDENCE,
            },
        }

    empty["breakout_happen"] = (h > resistance) or (l < support)
    empty["reclaim_happen"] = (h > resistance and c < resistance) or (l < support and c > support)
    empty["strong_rejection"] = (
        upper_shadow > body_eff * REJECTION_BODY_MULT
        or lower_shadow > body_eff * REJECTION_BODY_MULT
    )
    empty["shadow_size"] = max(upper_shadow, lower_shadow)
    empty["body_size"] = body
    if h > resistance and c < resistance and not (upper_shadow > body_eff * REJECTION_BODY_MULT):
        empty["detail"] = "bull bait+reclaim but wick < 1.5x body — no reverse yet"
    elif l < support and c > support and not (lower_shadow > body_eff * REJECTION_BODY_MULT):
        empty["detail"] = "bear bait+reclaim but wick < 1.5x body — no reverse yet"
    return empty


def apply_asymmetric_sl_tp(
    action: str,
    entry: float,
    candles: list[dict],
    sweep: dict[str, Any],
) -> tuple[float, float, float]:
    """STEP 5 — SL beyond wick by ATR*0.5; TP at 1:2 R:R (then partial/BE/trail manager)."""
    atr = _atr_from_candles(candles)
    last = candles[-1]
    pad = atr * ATR_SL_MULT
    if action in ("BUY", "LONG"):
        extreme = float(sweep.get("sweep_extreme") or last["low"])
        sl = extreme - pad  # Low - ATR*0.5
        if sl >= entry:
            sl = entry - max(pad, entry * 0.0008)
        risk = entry - sl
        tp = entry + risk * TRAIL_RR
    else:
        extreme = float(sweep.get("sweep_extreme") or last["high"])
        sl = extreme + pad  # High + ATR*0.5
        if sl <= entry:
            sl = entry + max(pad, entry * 0.0008)
        risk = sl - entry
        tp = entry - risk * TRAIL_RR
    return sl, tp, risk


def compute_adx(candles: list[dict], period: int | None = None) -> float:
    """Wilder-style ADX for sideways filter (doctrine #4)."""
    p = period or ADX_PERIOD
    if len(candles) < p + 2:
        return 0.0
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    trs: list[float] = []
    for i in range(1, len(candles)):
        h = float(candles[i]["high"])
        l = float(candles[i]["low"])
        ph = float(candles[i - 1]["high"])
        pl = float(candles[i - 1]["low"])
        pc = float(candles[i - 1]["close"])
        up = h - ph
        down = pl - l
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))

    if len(trs) < p:
        return 0.0

    def _wilder_smooth(vals: list[float], n: int) -> list[float]:
        out: list[float] = []
        s = sum(vals[:n])
        out.append(s)
        for v in vals[n:]:
            s = s - (s / n) + v
            out.append(s)
        return out

    atr_s = _wilder_smooth(trs, p)
    p_s = _wilder_smooth(plus_dm, p)
    m_s = _wilder_smooth(minus_dm, p)
    dx_vals: list[float] = []
    for i in range(len(atr_s)):
        atr_v = atr_s[i] / p if i == 0 else atr_s[i] / p
        # Wilder stores smoothed sums; DI uses smoothed DM / smoothed TR
        tr_sm = atr_s[i]
        if tr_sm <= 0:
            continue
        pdi = 100.0 * (p_s[i] / tr_sm)
        mdi = 100.0 * (m_s[i] / tr_sm)
        denom = pdi + mdi
        if denom <= 0:
            continue
        dx_vals.append(100.0 * abs(pdi - mdi) / denom)
    if len(dx_vals) < p:
        return float(dx_vals[-1]) if dx_vals else 0.0
    adx = sum(dx_vals[:p]) / p
    for d in dx_vals[p:]:
        adx = ((adx * (p - 1)) + d) / p
    return float(adx)


def check_adx_sideways(candles: list[dict]) -> tuple[bool, str | None, float]:
    """ADX filter. Soft by default (HARD_ADX=0) so chop doesn't freeze the bot.

    Returns (hard_blocked, reason, adx_value). Soft weakness is reported via reason
    starting with 'soft:' and hard_blocked=False.
    """
    if len(candles) < ADX_PERIOD + 5:
        # Not enough history — do not hard-block (was returning 0 and killing all entries)
        return False, "soft:ADX warming up", 0.0
    adx = compute_adx(candles)
    if adx <= 0.0:
        return False, "soft:ADX unavailable", adx
    if adx < ADX_MIN:
        msg = f"ADX {adx:.1f} < {ADX_MIN}"
        if HARD_ADX:
            return True, f"sideways filter {msg}", adx
        return False, f"soft:{msg}", adx
    return False, None, adx


def weighted_confluence_scorecard(
    fire_result: dict[str, Any] | None,
    sweep: dict[str, Any],
    *,
    adx_value: float | None = None,
    htf_near: bool = False,
) -> dict[str, Any]:
    """Scorecard for non-trap / Fire-assist path. Clean trap can bypass (STEP 5)."""
    conf = float((fire_result or {}).get("confidence") or (fire_result or {}).get("strength") or 0.0)
    cflow = (fire_result or {}).get("confluence") or {}
    base = float(cflow.get("score") or conf) if isinstance(cflow, dict) else conf

    # Pattern: map fire confidence into 0..0.45
    pattern_score = 0.45 * min(1.0, max(0.0, base / max(MIN_CONFLUENCE, 0.01)))
    if (fire_result or {}).get("pattern") == "liquidity_sweep" or sweep.get("sweep"):
        pattern_score = max(pattern_score, 0.40)

    shadow_score = 0.20 if sweep.get("sweep") else 0.05

    trend_score = 0.10
    tech = cflow.get("tech") if isinstance(cflow, dict) else None
    adx_v = float(adx_value) if adx_value is not None else float((tech or {}).get("adx") or 0)
    if adx_v >= ADX_MIN or (isinstance(tech, dict) and tech.get("strong_trend")):
        trend_score = 0.20
    elif adx_v >= ADX_MIN * 0.75:
        trend_score = 0.14

    location_score = 0.15 if htf_near else 0.10

    total = pattern_score + shadow_score + trend_score + location_score
    # Confirmed trap: floor at threshold (curriculum says fire when steps 2–4 true)
    if sweep.get("sweep") and TRAP_BYPASSES_SCORECARD:
        total = max(total, MIN_CONFLUENCE)

    return {
        "pattern_score": round(pattern_score, 4),
        "shadow_score": round(shadow_score, 4),
        "trend_score": round(trend_score, 4),
        "location_score": round(location_score, 4),
        "total": round(total, 4),
        "threshold": MIN_CONFLUENCE,
        "pass": total >= MIN_CONFLUENCE,
        "trap_floor": bool(sweep.get("sweep") and TRAP_BYPASSES_SCORECARD),
    }


def near_htf_level(
    price: float,
    htf_candles: list[dict] | None,
    ltf_atr: float,
) -> tuple[bool, str | None, dict[str, Any]]:
    # Fail-OPEN when HTF missing — hard REQUIRE_HTF + no data used to freeze the bot
    if not htf_candles or len(htf_candles) < 10:
        if REQUIRE_HTF:
            return True, "htf history thin — fail-open", {"near": True, "fail_open": True}
        return True, "htf history thin — soft pass", {"near": False, "fail_open": True}
    supports, resistances = htf_levels(htf_candles)
    htf_atr = _atr_from_candles(htf_candles)
    band = max(ltf_atr, htf_atr * 0.35, price * 0.0004) * HTF_NEAR_ATR
    best = None
    for lvl in supports:
        d = abs(price - lvl)
        if best is None or d < best[0]:
            best = (d, "support", lvl)
    for lvl in resistances:
        d = abs(price - lvl)
        if best is None or d < best[0]:
            best = (d, "resistance", lvl)
    if best is None:
        return (not REQUIRE_HTF), "no HTF levels", {"near": False}
    near = best[0] <= band
    meta = {"near": near, "kind": best[1], "level": best[2], "dist": best[0], "band": band}
    if near:
        return True, f"near HTF {best[1]} @{best[2]:.6g}", meta
    if REQUIRE_HTF:
        return False, f"mid-range vs HTF (dist={best[0]:.6g} > band={band:.6g})", meta
    return True, f"soft: mid-range vs HTF (dist={best[0]:.6g})", meta


# ---------------------------------------------------------------------------
# Entry gate + enrich Fire Engine result
# ---------------------------------------------------------------------------

@dataclass
class GateResult:
    ok: bool
    reason: str
    meta: dict[str, Any]


def entry_gates(
    candles: list[dict],
    *,
    htf_candles: list[dict] | None = None,
    price: float | None = None,
) -> GateResult:
    meta: dict[str, Any] = {}
    blocked, reason = check_news_block()
    meta["news"] = news_block_status()
    if blocked:
        return GateResult(False, reason or "news", meta)

    blocked, reason = check_volatility_panic(candles)
    meta["panic"] = {"blocked": blocked, "reason": reason}
    if blocked:
        return GateResult(False, reason or "panic", meta)

    adx_block, adx_reason, adx_v = check_adx_sideways(candles)
    meta["adx"] = {
        "value": round(adx_v, 2),
        "min": ADX_MIN,
        "blocked": adx_block,
        "hard": HARD_ADX,
        "note": adx_reason,
    }
    if adx_block:
        return GateResult(False, adx_reason or "ADX sideways", meta)
    if adx_reason and str(adx_reason).startswith("soft:"):
        meta["adx_soft"] = adx_reason

    kz = killzone_status()
    meta["killzone"] = kz
    if REQUIRE_KILLZONE and not kz["active"]:
        return GateResult(False, "outside kill-zone (SCALP_REQUIRE_KILLZONE)", meta)

    px = float(price if price is not None else candles[-1]["close"])
    atr = _atr_from_candles(candles)
    near, near_reason, near_meta = near_htf_level(px, htf_candles, atr)
    meta["htf"] = near_meta
    meta["htf_reason"] = near_reason
    if REQUIRE_HTF and not near:
        return GateResult(False, near_reason or "not near HTF level", meta)

    return GateResult(True, "gates clear", meta)


def apply_scalp_to_fire_result(
    result: dict[str, Any],
    candles: list[dict],
    *,
    timeframe_key: str,
    htf_candles: list[dict] | None = None,
) -> dict[str, Any]:
    """1m/5m: confirmed trap → ALWAYS reverse-fire; else normal Fire ≥0.72."""
    out = dict(result)
    out["scalp"] = True
    out["entry_pattern"] = ENTRY_PATTERN_NAME
    out["exit_mode"] = "scalp_partial_be_trail"
    out["timeframe_key"] = timeframe_key

    gate = entry_gates(
        candles,
        htf_candles=htf_candles,
        price=float(result.get("entry") or candles[-1]["close"]),
    )
    out["scalp_gates"] = {"ok": gate.ok, "reason": gate.reason, **gate.meta}
    if not gate.ok:
        out["action"] = "NO_TRADE"
        out["reason"] = f"Scalp gate: {gate.reason}"
        return out

    sweep = detect_liquidity_sweep(candles, side_hint=None)
    out["liquidity_sweep"] = sweep
    fire_action = result.get("action")

    # --- PATH A: confirmed bull/bear trap → REVERSE, never skip ---
    if sweep.get("trap") or sweep.get("sweep"):
        exec_side = sweep.get("execute_side")
        if exec_side == "SHORT" or sweep.get("trap_type") == "bull_trap":
            action = "SELL"  # bot shorts while retail buys
        elif exec_side == "LONG" or sweep.get("trap_type") == "bear_trap":
            action = "BUY"  # bot longs while retail sells
        else:
            action = "BUY" if sweep.get("direction") == "bullish" else "SELL"

        conf = float(sweep.get("trap_confidence") or TRAP_CONFIDENCE)
        out["action"] = action
        out["confidence"] = conf
        out["strength"] = conf
        out["trap_confidence"] = conf
        out["trap_type"] = sweep.get("trap_type")
        out["reason"] = sweep.get("detail") or f"Reverse trap → {action}"
        if fire_action in ("BUY", "SELL") and fire_action != action:
            out["reason"] += f" (Fire said {fire_action}, reverse wins)"

        entry = float(result.get("entry") or candles[-1]["close"])
        sl, tp, risk = apply_asymmetric_sl_tp(action, entry, candles, sweep)
        if risk <= 0:
            out["action"] = "NO_TRADE"
            out["reason"] = "Trap seen but invalid SL/risk"
            return out
        out["entry"] = entry
        out["sl"] = sl
        out["tp"] = tp
        out["risk_reward"] = TRAIL_RR
        out["side"] = "LONG" if action == "BUY" else "SHORT"
        out["scorecard"] = {
            "total": conf,
            "threshold": TRAP_CONFIDENCE,
            "pass": True,
            "trap_floor": True,
            "mode": "reverse_trap",
        }
        out["scalp_plan"] = {
            "partial_rr": PARTIAL_RR,
            "trail_rr": TRAIL_RR,
            "partial_frac": PARTIAL_FRAC,
            "risk": risk,
            "be_buffer_r": BE_BUFFER_R,
            "atr_sl_mult": ATR_SL_MULT,
            "mode": "reverse_trap",
        }
        out["pattern"] = sweep.get("trap_type") or f"trap_{'short' if action == 'SELL' else 'long'}"
        return out

    # --- PATH B: no trap → normal Fire Engine 0.72 score logic ---
    if fire_action not in ("BUY", "SELL"):
        out["action"] = "NO_TRADE"
        out["reason"] = (
            "No bull/bear trap — "
            + (sweep.get("detail") or "waiting")
            + f" | Fire: {result.get('reason', 'no 0.72 setup')}"
        )
        return out

    if REQUIRE_SWEEP:
        # Legacy hard mode: only trade traps (off by default now)
        out["action"] = "NO_TRADE"
        out["reason"] = "SCALP_REQUIRE_SWEEP=1 and no trap — skipping Fire fallback"
        return out

    action = fire_action
    out["action"] = action
    out["reason"] = f"No trap → Fire 0.72 path: {result.get('reason', action)}"
    htf_near = bool((gate.meta.get("htf") or {}).get("near"))
    adx_v = float((gate.meta.get("adx") or {}).get("value") or 0)
    scorecard = weighted_confluence_scorecard(
        out, sweep, adx_value=adx_v, htf_near=htf_near
    )
    need = MIN_CONFLUENCE
    if not gate.meta.get("killzone", {}).get("active"):
        need += KILLZONE_EXTRA_CONF
    if 0 < adx_v < ADX_MIN:
        need += 0.04
    scorecard = dict(scorecard)
    scorecard["threshold"] = round(need, 4)
    scorecard["pass"] = float(scorecard["total"]) >= need
    scorecard["mode"] = "fire_0_72"
    out["scorecard"] = scorecard
    out["confidence"] = float(scorecard["total"])
    out["strength"] = float(scorecard["total"])
    if not scorecard["pass"]:
        # Fall back to raw Fire confidence if scorecard under-counts
        fire_conf = float(result.get("confidence") or result.get("strength") or 0)
        if fire_conf >= need:
            out["confidence"] = fire_conf
            out["strength"] = fire_conf
            scorecard["pass"] = True
            scorecard["total"] = fire_conf
            scorecard["fire_conf_fallback"] = True
        else:
            out["action"] = "NO_TRADE"
            out["reason"] = (
                f"Fire fallback score {max(scorecard['total'], fire_conf):.3f} < {need:.3f}"
            )
            return out

    entry = float(result.get("entry") or candles[-1]["close"])
    sl = result.get("sl")
    tp = result.get("tp")
    if sl is None or tp is None:
        sl, tp, risk = apply_asymmetric_sl_tp(action, entry, candles, {})
    else:
        sl, tp = float(sl), float(tp)
        risk = abs(entry - sl)
    out["entry"] = entry
    out["sl"] = sl
    out["tp"] = tp
    out["risk_reward"] = float(result.get("risk_reward") or TRAIL_RR)
    out["side"] = "LONG" if action == "BUY" else "SHORT"
    out["scalp_plan"] = {
        "partial_rr": PARTIAL_RR,
        "trail_rr": TRAIL_RR,
        "partial_frac": PARTIAL_FRAC,
        "risk": risk,
        "be_buffer_r": BE_BUFFER_R,
        "atr_sl_mult": ATR_SL_MULT,
        "mode": "fire_0_72",
    }
    out["pattern"] = result.get("pattern") or "fire_0_72"
    out["exit_mode"] = "scalp_partial_be_trail"
    return out


def try_sweep_only_entry(
    candles: list[dict],
    *,
    timeframe_key: str,
    htf_candles: list[dict] | None = None,
    pair: str = "default",
) -> dict[str, Any]:
    """Fire reverse trade when bull/bear trap is confirmed (no Fire needed)."""
    sweep = detect_liquidity_sweep(candles, side_hint=None)
    base: dict[str, Any] = {
        "action": "NO_TRADE",
        "engine": "scalp_1m5m",
        "entry_pattern": ENTRY_PATTERN_NAME,
        "scalp": True,
        "timeframe_key": timeframe_key,
        "liquidity_sweep": sweep,
    }
    if not (sweep.get("trap") or sweep.get("sweep")):
        base["reason"] = sweep.get("detail") or "No bull/bear trap"
        return base

    action = "SELL" if (
        sweep.get("execute_side") == "SHORT" or sweep.get("trap_type") == "bull_trap"
    ) else "BUY"
    entry = float(candles[-1]["close"])
    seed = {
        "action": action,
        "confidence": float(sweep.get("trap_confidence") or TRAP_CONFIDENCE),
        "strength": float(sweep.get("trap_confidence") or TRAP_CONFIDENCE),
        "pattern": sweep.get("trap_type") or "liquidity_trap",
        "confluence": {"score": float(sweep.get("trap_confidence") or TRAP_CONFIDENCE)},
        "reason": sweep.get("detail"),
        "entry": entry,
    }
    return apply_scalp_to_fire_result(
        seed, candles, timeframe_key=timeframe_key, htf_candles=htf_candles
    )


def evaluate_scalp_entry(
    candles: list[dict],
    timeframe_key: str,
    *,
    fire_result: dict[str, Any] | None = None,
    htf_candles: list[dict] | None = None,
    pair: str = "default",
) -> dict[str, Any]:
    """1m/5m law: trap → reverse NOW; else Run_Normal_0.72_Score_Logic()."""
    # 1) Trap reverse (bull→SHORT / bear→LONG) — never skip when confirmed
    trap = try_sweep_only_entry(
        candles, timeframe_key=timeframe_key, htf_candles=htf_candles, pair=pair
    )
    if trap.get("action") in ("BUY", "SELL"):
        return trap

    # 2) No trap → normal Fire Engine ≥0.72
    if fire_result and fire_result.get("action") in ("BUY", "SELL"):
        return apply_scalp_to_fire_result(
            fire_result, candles, timeframe_key=timeframe_key, htf_candles=htf_candles
        )

    out = dict(fire_result or {})
    out["action"] = "NO_TRADE"
    out["scalp"] = True
    out["entry_pattern"] = ENTRY_PATTERN_NAME
    out["liquidity_sweep"] = trap.get("liquidity_sweep")
    out["scalp_gates"] = trap.get("scalp_gates")
    out["reason"] = (
        (trap.get("reason") or "No trap")
        + " | "
        + str((fire_result or {}).get("reason") or "no Fire 0.72 setup")
    )
    return out


# ---------------------------------------------------------------------------
# Dynamic exit: partial @ 1R → BE → trail to trail_rr
# ---------------------------------------------------------------------------

def is_scalp_trade(trade: dict | None) -> bool:
    if not trade:
        return False
    if trade.get("exit_mode") == "scalp_partial_be_trail":
        return True
    if trade.get("entry_pattern") == ENTRY_PATTERN_NAME:
        return True
    tf = str(trade.get("timeframe_key") or "").lower()
    return tf in ("1m", "5m") and trade.get("source") == "auto"


def unrealized_r(trade: dict, mark: float) -> float | None:
    entry = float(trade.get("entry") or 0)
    sl = trade.get("sl_price")
    if entry <= 0 or sl is None:
        return None
    risk = abs(entry - float(sl))
    # After BE move, original risk stored
    risk = float(trade.get("scalp_initial_risk") or risk)
    if risk <= 0:
        return None
    side = trade.get("side")
    if side == "LONG":
        return (mark - entry) / risk
    if side == "SHORT":
        return (entry - mark) / risk
    return None


def evaluate_scalp_exit(trade: dict, mark: float) -> dict[str, Any] | None:
    """Return action dict: full_close | partial | update_sl — or None."""
    if not is_scalp_trade(trade):
        return None
    side = trade.get("side")
    entry = float(trade.get("entry") or 0)
    sl = trade.get("sl_price")
    tp = trade.get("tp_price")
    try:
        sl_f = float(sl) if sl is not None else None
    except (TypeError, ValueError):
        sl_f = None
    try:
        tp_f = float(tp) if tp is not None else None
    except (TypeError, ValueError):
        tp_f = None

    # Hard SL always
    if side == "LONG" and sl_f is not None and mark <= sl_f:
        return {"action": "full_close", "reason": f"Scalp SL: LONG {mark:.6f} ≤ {sl_f:.6f}"}
    if side == "SHORT" and sl_f is not None and mark >= sl_f:
        return {"action": "full_close", "reason": f"Scalp SL: SHORT {mark:.6f} ≥ {sl_f:.6f}"}

    # Final TP
    if side == "LONG" and tp_f is not None and mark >= tp_f:
        return {"action": "full_close", "reason": f"Scalp TP: LONG {mark:.6f} ≥ {tp_f:.6f}"}
    if side == "SHORT" and tp_f is not None and mark <= tp_f:
        return {"action": "full_close", "reason": f"Scalp TP: SHORT {mark:.6f} ≤ {tp_f:.6f}"}

    r = unrealized_r(trade, mark)
    if r is None:
        return None

    partial_done = bool(trade.get("scalp_partial_done"))
    be_done = bool(trade.get("scalp_be_done"))
    initial_risk = float(trade.get("scalp_initial_risk") or 0)
    if initial_risk <= 0 and sl_f is not None:
        initial_risk = abs(entry - sl_f)

    # Phase 1: partial at PARTIAL_RR
    if not partial_done and r >= PARTIAL_RR:
        return {
            "action": "partial",
            "frac": PARTIAL_FRAC,
            "reason": f"Scalp partial {PARTIAL_FRAC:.0%} @ {r:.2f}R (target {PARTIAL_RR}R)",
            "then_be": True,
            "initial_risk": initial_risk,
        }

    # Phase 2: move SL to BE if partial done but BE not set
    if partial_done and not be_done and initial_risk > 0:
        buf = initial_risk * BE_BUFFER_R
        if side == "LONG":
            be_sl = entry + buf
        else:
            be_sl = entry - buf
        return {
            "action": "update_sl",
            "sl": be_sl,
            "reason": f"Scalp BE SL → {be_sl:.6f}",
            "mark_be": True,
        }

    # Phase 3: trail remaining — ratchet SL by R progress toward TRAIL_RR
    if partial_done and be_done and initial_risk > 0 and r > PARTIAL_RR:
        # Trail stop locks ~ (r - 0.5)R profit floor, capped below TP path
        lock_r = max(0.0, r - 0.6)
        if side == "LONG":
            trail_sl = entry + initial_risk * lock_r
            if sl_f is None or trail_sl > sl_f:
                return {"action": "update_sl", "sl": trail_sl, "reason": f"Scalp trail SL → {trail_sl:.6f} ({lock_r:.2f}R lock)"}
        else:
            trail_sl = entry - initial_risk * lock_r
            if sl_f is None or trail_sl < sl_f:
                return {"action": "update_sl", "sl": trail_sl, "reason": f"Scalp trail SL → {trail_sl:.6f} ({lock_r:.2f}R lock)"}

    return None


def ensure_news_file() -> None:
    """Create empty events file scaffold if missing."""
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not _NEWS_PATH.is_file():
            _NEWS_PATH.write_text(
                json.dumps(
                    {
                        "events": [
                            {
                                "title": "Example CPI (edit/remove)",
                                "impact": "high",
                                "at": "2099-01-01T13:30:00+00:00",
                                "note": "Add real high-impact US events as ISO8601 UTC",
                            }
                        ]
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
    except Exception as exc:
        print(f"[SCALP NEWS] scaffold note: {exc}")


ensure_news_file()
