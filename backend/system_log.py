"""Rolling transparency log for the System Log modal (UVSS, AI, Bybit, trades, chart)."""
import time


class SystemLogStore:
    def __init__(self, max_entries: int = 120, max_agent_chat: int = 40):
        self.max_entries = max_entries
        self.max_agent_chat = max_agent_chat
        self.entries: list[dict] = []
        self.agent_chat: list[dict] = []
        self.seq = 0
        # last_taapi_scan kept as API field name for older frontends; engine is UVSS.
        self.last_taapi_scan: dict | None = None
        self.last_trade_fire: dict | None = None

    def push_agent_chat(self, message: str, *, status: str = "scanning", details: dict | None = None):
        """Live chat line for the main dashboard — pattern scan activity."""
        self.seq += 1
        entry = {
            "id": self.seq,
            "message": message,
            "status": status,
            "details": details or {},
            "timestamp": time.time(),
        }
        self.agent_chat.append(entry)
        if len(self.agent_chat) > self.max_agent_chat:
            self.agent_chat = self.agent_chat[-self.max_agent_chat :]
        print(f"[AGENT-CHAT] {message}")
        return entry

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

    def set_last_uvss_scan(self, pair, timeframe, decision, candle, cost_aware=None):
        long_rules = decision.get("long_rules") or decision.get("rules_fired") if decision.get("action") == "BUY" else []
        short_rules = decision.get("short_rules") or decision.get("rules_fired") if decision.get("action") == "SELL" else []
        if decision.get("action") == "NO_TRADE":
            long_rules = decision.get("long_rules") or []
            short_rules = decision.get("short_rules") or []
        self.last_taapi_scan = {
            "pair": pair,
            "timeframe": timeframe,
            "timestamp": time.time(),
            "engine": decision.get("engine") or "candle_pattern",
            "bullish": long_rules,
            "bearish": short_rules,
            "active_count": len(long_rules) + len(short_rules),
            "total_patterns": 24,
            "decision": decision,
            "candle": {
                "high": candle.get("high"),
                "low": candle.get("low"),
                "close": candle.get("close"),
                "close_time": candle.get("close_time"),
            },
            "cost_aware": cost_aware,
        }
        action = decision.get("action", "UNKNOWN")
        reason = decision.get("reason") or decision.get("pattern") or ""
        self.push(
            "pattern",
            f"{pair} @ {timeframe}: {action} — {reason}",
            {"long_rules": long_rules, "short_rules": short_rules, "decision": decision},
        )

    def set_last_trade_fire(self, payload: dict, *, emit_log: bool = True):
        if payload.get("success"):
            status = "FIRED"
        elif payload.get("skipped"):
            status = "BLOCKED" if payload.get("cost_aware") else "SKIPPED"
        else:
            status = "FAILED"
        self.last_trade_fire = {**payload, "status": status, "timestamp": time.time()}
        if not emit_log:
            return
        msg = f"{status}: {payload.get('action')} {payload.get('symbol')} | {payload.get('pattern', 'n/a')}"
        if not payload.get("success") and payload.get("error"):
            msg += f" — {payload.get('error')}"
        self.push("trade", msg, self.last_trade_fire)


system_log = SystemLogStore()
