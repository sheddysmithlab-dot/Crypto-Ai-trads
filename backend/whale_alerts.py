"""WhaleBot Alerts (Telegram public preview) → LONG/SHORT signals.

Source: https://t.me/s/WhaleBotAlerts

Rules (BTC only, ≥ MIN_BTC_AMOUNT):
  SHORT — Unknown wallet → Exchange (deposit / sell pressure)
  LONG  — Exchange → Unknown wallet (withdrawal / accumulation)
"""
from __future__ import annotations

import hashlib
import re
import time
from typing import Any

import httpx

WHALE_SOURCE_URL = "https://t.me/s/WhaleBotAlerts"
# Scan Telegram preview once per minute (1m timeframe).
WHALE_POLL_SECONDS = float(__import__("os").environ.get("WHALE_POLL_SECONDS", "60"))
MIN_BTC_AMOUNT = float(__import__("os").environ.get("WHALE_MIN_BTC", "100"))
SL_PCT = 0.005  # 0.5% reference SL for sizing
RR_RATIO = 2.0


# Venue names as they appear (or substring) on WhaleBotAlerts.
_EXCHANGES = (
    "binance",
    "bybit",
    "okex",
    "okx",
    "okx.com",
    "kraken",
    "coinbase",
    "bitfinex",
    "huobi",
    "htx",
    "gate.io",
    "gateio",
    "gate",
    "kucoin",
    "bitstamp",
    "gemini",
    "bitmex",
    "deribit",
    "bitget",
    "mexc",
    "crypto.com",
    "crypto com",
    "poloniex",
    "bithumb",
    "upbit",
    "bitflyer",
)

_TRANSFER_RE = re.compile(
    r"(?P<amount>[\d,]+\.?\d*)\s*BTC\s*(?:\([^)]*\))?\s*"
    r"transfer+ed\s+from\s+(?P<frm>.+?)\s+to\s+(?P<to>.+?)(?:\n|$)",
    re.IGNORECASE,
)

_seen_ids: set[str] = set()
_seeded_once = False
_last_fetch: dict[str, Any] = {
    "ok": False,
    "fetched_at": 0.0,
    "raw_count": 0,
    "signals": [],
    "error": None,
}


def is_btc_pair(pair_label: str | None) -> bool:
    """Whale flow merges into BTC/USDT automation (no separate UI pair)."""
    p = (pair_label or "").strip().upper().replace("-", "/")
    return p in ("BTC/USDT", "BTC")


def is_whale_pair(pair_label: str | None) -> bool:
    """Deprecated alias — whale is merged into BTC."""
    return is_btc_pair(pair_label)


def _norm_party(name: str) -> str:
    s = re.sub(r"\s+", " ", (name or "").strip())
    # Drop trailing noise from HTML scrape
    s = re.split(r"(?i)\d+\s*(?:minutes?|minute|hours?|hour|seconds?|second|views?)\b", s)[0]
    s = s.strip(" .,\u200b")
    return s


def _is_unknown(name: str) -> bool:
    n = _norm_party(name).lower()
    if not n:
        return False
    if n.startswith("unknown"):
        return True
    if "unknown wallet" in n or n == "unknown":
        return True
    return False


def _is_exchange(name: str) -> bool:
    n = _norm_party(name).lower()
    if _is_unknown(n):
        return False
    for ex in _EXCHANGES:
        if ex in n:
            return True
    return False


def _party_class(name: str) -> str:
    if _is_exchange(name):
        return "exchange"
    if _is_unknown(name):
        return "unknown"
    return "other"


def parse_whale_text(text: str) -> list[dict[str, Any]]:
    """Parse one or many alert bodies into structured transfer rows."""
    out: list[dict[str, Any]] = []
    if not text:
        return out
    # Normalize typos / HTML entities
    cleaned = (
        text.replace("\xa0", " ")
        .replace("&nbsp;", " ")
        .replace("transfered", "transferred")
        .replace("Transfered", "Transferred")
    )
    for m in _TRANSFER_RE.finditer(cleaned):
        try:
            amount = float(m.group("amount").replace(",", ""))
        except ValueError:
            continue
        frm = _norm_party(m.group("frm"))
        to = _norm_party(m.group("to"))
        # Trim "to" if it captured extra lines
        to = to.split("Sender")[0].split("Receiver")[0].split("Sent from")[0].strip()
        frm_c = _party_class(frm)
        to_c = _party_class(to)
        row = {
            "amount_btc": amount,
            "from": frm,
            "to": to,
            "from_class": frm_c,
            "to_class": to_c,
            "raw": m.group(0).strip()[:240],
        }
        out.append(row)
    return out


def classify_transfer(row: dict[str, Any]) -> dict[str, Any] | None:
    """Apply ≥150 BTC Unknown↔Exchange rules. Returns signal or None."""
    amount = float(row.get("amount_btc") or 0)
    if amount < MIN_BTC_AMOUNT:
        return None
    frm_c = row.get("from_class")
    to_c = row.get("to_class")
    if frm_c == "unknown" and to_c == "exchange":
        action = "SELL"  # SHORT
        side = "SHORT"
        pattern = "WHALE-SHORT"
        reason = (
            f"Whale deposit ≥{MIN_BTC_AMOUNT:.0f} BTC: {amount:,.2f} BTC "
            f"Unknown → {row.get('to')} (bearish)"
        )
    elif frm_c == "exchange" and to_c == "unknown":
        action = "BUY"  # LONG
        side = "LONG"
        pattern = "WHALE-LONG"
        reason = (
            f"Whale withdrawal ≥{MIN_BTC_AMOUNT:.0f} BTC: {amount:,.2f} BTC "
            f"{row.get('from')} → Unknown (bullish)"
        )
    else:
        return None

    sig_id = hashlib.sha1(
        f"{pattern}|{amount:.4f}|{row.get('from')}|{row.get('to')}|{row.get('raw')}".encode()
    ).hexdigest()[:16]
    return {
        "id": sig_id,
        "action": action,
        "side": side,
        "pattern": pattern,
        "reason": reason,
        "amount_btc": amount,
        "from": row.get("from"),
        "to": row.get("to"),
        "setup": "whale_flow",
        "source": "WhaleBotAlerts",
        "source_url": WHALE_SOURCE_URL,
    }


def extract_message_texts(html: str) -> list[str]:
    """Pull message bodies from t.me/s/ public HTML."""
    texts: list[str] = []
    # Primary: <div class="tgme_widget_message_text ...">...</div>
    for m in re.finditer(
        r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
        html,
        flags=re.I | re.S,
    ):
        chunk = m.group(1)
        chunk = re.sub(r"<br\s*/?>", "\n", chunk, flags=re.I)
        chunk = re.sub(r"<[^>]+>", " ", chunk)
        chunk = re.sub(r"\s+", " ", chunk).strip()
        if chunk and "BTC" in chunk.upper():
            texts.append(chunk)
    # Fallback: any "N BTC (... ) transfer" snippets
    if not texts:
        for m in re.finditer(
            r"([\d,]+\.?\d*\s*BTC\s*\([^)]*\)\s*transfer\w*\s+from\s+.+?\s+to\s+\S+)",
            html,
            flags=re.I,
        ):
            texts.append(m.group(1))
    return texts


async def fetch_whale_alerts(client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Fetch Telegram preview and return classified signals (newest first)."""
    global _last_fetch
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=20.0, follow_redirects=True)
    try:
        assert client is not None
        resp = await client.get(
            WHALE_SOURCE_URL,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; AitradsWhaleBot/1.0)",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        if resp.status_code != 200:
            _last_fetch = {
                "ok": False,
                "fetched_at": time.time(),
                "raw_count": 0,
                "signals": [],
                "error": f"HTTP {resp.status_code}",
            }
            return _last_fetch

        texts = extract_message_texts(resp.text)
        signals: list[dict[str, Any]] = []
        seen_local: set[str] = set()
        for text in texts:
            for row in parse_whale_text(text):
                sig = classify_transfer(row)
                if not sig:
                    continue
                if sig["id"] in seen_local:
                    continue
                seen_local.add(sig["id"])
                signals.append(sig)

        _last_fetch = {
            "ok": True,
            "fetched_at": time.time(),
            "raw_count": len(texts),
            "signals": signals,
            "error": None,
            "source": WHALE_SOURCE_URL,
            "min_btc": MIN_BTC_AMOUNT,
        }
        return _last_fetch
    except Exception as exc:
        _last_fetch = {
            "ok": False,
            "fetched_at": time.time(),
            "raw_count": 0,
            "signals": [],
            "error": str(exc),
        }
        return _last_fetch
    finally:
        if own_client and client is not None:
            await client.aclose()


def seed_seen_from_snapshot(signals: list[dict[str, Any]]) -> int:
    """Mark current page alerts as already-seen (avoid firing history on first poll)."""
    global _seeded_once
    n = 0
    for sig in signals:
        sid = sig.get("id")
        if sid and sid not in _seen_ids:
            _seen_ids.add(sid)
            n += 1
    _seeded_once = True
    return n


def is_seeded() -> bool:
    return _seeded_once


def mark_signal_seen(sig_id: str) -> None:
    _seen_ids.add(sig_id)
    if len(_seen_ids) > 500:
        for i, k in enumerate(list(_seen_ids)):
            if i >= 250:
                break
            _seen_ids.discard(k)


def is_signal_seen(sig_id: str) -> bool:
    return sig_id in _seen_ids


def reset_whale_seen() -> None:
    global _seeded_once
    _seen_ids.clear()
    _seeded_once = False


def last_fetch_snapshot() -> dict[str, Any]:
    return dict(_last_fetch)


def build_trade_plan_from_signal(sig: dict[str, Any], entry: float) -> dict[str, Any] | None:
    """Map whale signal → UVSS-shaped result for fire_taapi_auto_trade."""
    if entry is None or entry <= 0:
        return None
    action = sig["action"]
    if action == "BUY":
        sl = entry * (1.0 - SL_PCT)
        tp = entry + (entry - sl) * RR_RATIO
    else:
        sl = entry * (1.0 + SL_PCT)
        tp = entry - (sl - entry) * RR_RATIO
    return {
        "action": action,
        "pattern": sig["pattern"],
        "reason": sig["reason"],
        "setup": "whale_flow",
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "size_mult": RR_RATIO,
        "target_mult": RR_RATIO,
        "rules_fired": [sig["pattern"]],
        "long_rules": [sig["pattern"]] if action == "BUY" else [],
        "short_rules": [sig["pattern"]] if action == "SELL" else [],
        "whale": {
            "id": sig["id"],
            "amount_btc": sig["amount_btc"],
            "from": sig["from"],
            "to": sig["to"],
            "source_url": WHALE_SOURCE_URL,
        },
        "engine": "whale_alerts",
        "bible_key": None,
        "ml_gate": "off",
        "strength": min(sig["amount_btc"] / MIN_BTC_AMOUNT, 10.0),
    }
