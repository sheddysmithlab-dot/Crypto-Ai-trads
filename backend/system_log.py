"""Rolling transparency log for the System Log modal (TAAPI, AI, Bybit, trades, chart)."""
import time


class SystemLogStore:
    def __init__(self, max_entries: int = 120):
        self.max_entries = max_entries
        self.entries: list[dict] = []
        self.seq = 0
        self.last_taapi_scan: dict | None = None
        self.last_volume_analysis: dict | None = None
        self.last_trade_fire: dict | None = None

    def push(self, category: str, message: str, details: dict | None = None):
        self.seq += 1
        entry = {
            "id": self.seq,
            "category": category,
            "message": message,
            "details": details or {},
            "timestamp": time.time(),
        }
        self.entries.append(entry)
        if len(self.entries) > self.max_entries:
            self.entries = self.entries[-self.max_entries :]
        print(f"[SYSTEM-LOG:{category.upper()}] {message}")

    def set_last_taapi_scan(self, pair, timeframe, signals, decision, candle):
        active = [s for s in signals if s.get("value", 0) != 0]
        bullish = [s["pattern"] for s in active if s["value"] == 1]
        bearish = [s["pattern"] for s in active if s["value"] == -1]
        self.last_taapi_scan = {
            "pair": pair,
            "timeframe": timeframe,
            "timestamp": time.time(),
            "bullish": bullish,
            "bearish": bearish,
            "active_count": len(active),
            "total_patterns": len(signals),
            "decision": decision,
            "candle": {
                "high": candle.get("high"),
                "low": candle.get("low"),
                "close_time": candle.get("close_time"),
            },
        }
        action = decision.get("action", "UNKNOWN")
        reason = decision.get("reason") or decision.get("pattern") or ""
        self.push(
            "taapi",
            f"{pair} @ {timeframe}: {action} — {reason}",
            {"bullish": bullish, "bearish": bearish, "decision": decision},
        )

    def set_last_volume_analysis(self, pair, timeframe, action, analysis: dict):
        self.last_volume_analysis = {
            "pair": pair,
            "timeframe": timeframe,
            "action": action,
            "timestamp": time.time(),
            **analysis,
        }
        passed = analysis.get("passed")
        status = "PASSED" if passed else "BLOCKED"
        reason = analysis.get("reason") or ("Volume gate OK" if passed else "Volume gate failed")
        self.push(
            "volume",
            f"{pair} @ {timeframe}: {status} — {reason}",
            {"action": action, **analysis},
        )

    def set_last_trade_fire(self, payload: dict):
        self.last_trade_fire = {**payload, "timestamp": time.time()}
        status = "FIRED" if payload.get("success") else "FAILED"
        self.push(
            "trade",
            f"{status}: {payload.get('action')} {payload.get('symbol')} | {payload.get('pattern', 'n/a')}",
            payload,
        )


system_log = SystemLogStore()
