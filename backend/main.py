from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import asyncio
import hashlib
import hmac
import json
import math
import os
import random
import sys
import time
import httpx

# Windows' default console codepage (cp1252) can't encode emoji used in log
# messages below; force UTF-8 stdout/stderr so print() never crashes the app.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

app = FastAPI()

# Enable CORS for the frontend to connect smoothly
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://0.0.0.0:3000",
        "*"  # Fallback for any other origin
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

@app.get("/health")
async def health_check():
    """Render's health check target - just confirms the process is alive."""
    return {"status": "ok"}

# ==========================================
# LIVE NOTIFICATION CENTER (bell dropdown wiring)
# ==========================================
class NotificationCenter:
    """ Rolling feed of real backend events (trades, locks, emergencies) -
    broadcast live to the frontend bell dropdown via /ws/notifications. """
    def __init__(self):
        self.notifications = []
        self.seq = 0

    def push(self, message, ntype="info"):
        self.seq += 1
        self.notifications.append({
            "id": self.seq,
            "type": ntype,  # info | success | warning | error
            "message": message,
            "timestamp": time.time(),
        })
        # Keep only the most recent 30 events
        self.notifications = self.notifications[-30:]
        print(f"[NOTIFICATION:{ntype.upper()}] {message}")

notifications = NotificationCenter()

# ==========================================
# INTEGRATION SETTINGS: Bybit & AI API Store
# ==========================================
class SettingsPayload(BaseModel):
    bybit_api_key: str = ""
    bybit_api_secret: str = ""
    bybit_environment: str = "mainnet"
    ai_provider: str = "none"
    ai_api_key: str = ""
    ai_model: str = ""
    ai_base_url: str = ""

class SettingsStore:
    """ In-memory credential store for the local session.
    Secrets are NEVER logged in plaintext and NEVER echoed back to the frontend. """
    def __init__(self):
        self.bybit_api_key = ""
        self.bybit_api_secret = ""
        self.bybit_environment = "mainnet"
        self.ai_provider = "none"
        self.ai_api_key = ""
        self.ai_model = ""
        self.ai_base_url = ""

    def save(self, payload: SettingsPayload):
        # Only overwrite secret fields if the user actually typed a new value
        if payload.bybit_api_key:
            self.bybit_api_key = payload.bybit_api_key
        if payload.bybit_api_secret:
            self.bybit_api_secret = payload.bybit_api_secret
        if payload.ai_api_key:
            self.ai_api_key = payload.ai_api_key

        # Non-secret fields are always safe to overwrite
        self.bybit_environment = payload.bybit_environment or "mainnet"
        self.ai_provider = payload.ai_provider or "none"
        self.ai_model = payload.ai_model
        self.ai_base_url = payload.ai_base_url

    def reset(self):
        self.__init__()

    def is_bybit_configured(self):
        return bool(self.bybit_api_key and self.bybit_api_secret)

    def is_ai_configured(self):
        return self.ai_provider != "none" and bool(self.ai_api_key)

    def status_dict(self):
        # Deliberately excludes raw key/secret values
        return {
            "bybit_configured": self.is_bybit_configured(),
            "bybit_environment": self.bybit_environment,
            "ai_provider": self.ai_provider,
            "ai_model": self.ai_model,
            "ai_base_url": self.ai_base_url,
            "ai_configured": self.is_ai_configured(),
        }

settings_store = SettingsStore()

# ==========================================
# AI PROVIDER: settings connectivity test (OpenAI-compatible chat completions)
# ==========================================
# Per-provider defaults - only the API key is mandatory; base_url/model can
# be overridden from the Settings form. Azure OpenAI has no universal base
# URL (it's resource-specific), so it always requires ai_base_url to be set.
AI_PROVIDER_DEFAULTS = {
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini", "auth_header": "bearer"},
    "zhipu-glm": {"base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4.5-flash", "auth_header": "bearer"},
    "azure-openai": {"base_url": None, "model": "gpt-4o-mini", "auth_header": "api-key"},
    "custom": {"base_url": None, "model": "gpt-4o-mini", "auth_header": "bearer"},
}

async def consult_ai_provider(context):
    """ Asks the configured AI provider (OpenAI-compatible chat completions) whether to
    proceed with a RULE-3-triggered trade. Returns:
      True  - AI confirms, proceed with the trade
      False - AI rejects, skip this entry
      None  - no AI configured, or the provider is unreachable/errored - callers must
              fail OPEN (proceed as if no AI were configured) so a flaky third-party
              API can never be the reason the bot misses a real-time signal. """
    provider = settings_store.ai_provider
    if provider == "none" or not settings_store.ai_api_key:
        return None

    defaults = AI_PROVIDER_DEFAULTS.get(provider, AI_PROVIDER_DEFAULTS["custom"])
    base_url = (settings_store.ai_base_url or defaults["base_url"] or "").rstrip("/")
    if not base_url:
        print(f"[AI AGENT] No base URL configured for provider '{provider}' - skipping AI confirmation this tick.")
        return None
    model = settings_store.ai_model or defaults["model"]

    headers = {"Content-Type": "application/json"}
    if defaults["auth_header"] == "api-key":
        headers["api-key"] = settings_store.ai_api_key
    else:
        headers["Authorization"] = f"Bearer {settings_store.ai_api_key}"

    prompt = (
        "You are a risk-confirmation layer for an algorithmic crypto trading bot. "
        f"A rule-based volume-anomaly signal just fired on {context['pair']} "
        f"[Condition {context['condition']}]: current candle volume {context['candle_volume']:.1f} "
        f"vs previous candle {context['prev_candle_volume']:.1f} (2x+ trigger), candle height "
        f"{context['candle_height']:.2f} vs previous {context['prev_candle_height']:.2f}, "
        f"current price {context['current_price']:.2f}. "
        "Reply with ONLY the single word YES to confirm this BUY signal, or NO to reject it."
    )

    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 5,
                    "temperature": 0,
                },
            )
        if resp.status_code != 200:
            print(f"[AI AGENT] Provider '{provider}' returned HTTP {resp.status_code} - failing open (proceeding with trade).")
            return None
        data = resp.json()
        reply = data["choices"][0]["message"]["content"].strip().upper()
        decision = reply.startswith("YES")
        print(f"[AI AGENT] Provider '{provider}' confirmation reply: '{reply}' -> {'PROCEED' if decision else 'REJECTED'}")
        return decision
    except Exception as exc:
        print(f"[AI AGENT] Provider '{provider}' request failed ({exc}) - failing open (proceeding with trade).")
        return None

# ==========================================
# PILLAR 4 & 5: API & BYBIT EXECUTION GROUND
# ==========================================
class BybitAPIWrapper:
    """ API Data Cable & Execution Ground (Pillar 4 & 5) """
    def __init__(self):
        # DEFAULT: PAPER TRADING (As per Automation.txt)
        self.mode = "PAPER_TRADING"
        self.connected = False
        # RULE 7: Taker fee tier, continuously "fetched" from Bybit (simulated here at
        # Bybit's standard ~0.055% taker rate; real integration would poll the fee-rate endpoint).
        self.taker_fee_pct = 0.05

        # Real Bybit account equity (USD), refreshed in the background while LIVE_TRADING.
        # None until the first successful fetch - callers fall back to paper capital until then.
        self.last_known_balance = None
        self.last_error = None
        self._was_failing = False

    def connect_real_api(self):
        self.mode = "LIVE_TRADING"
        self.connected = True
        self.last_known_balance = None
        print("[PILLAR 5: BYBIT] LIVE ACCOUNT CONNECTED. REAL TRADING ENABLED.")
        notifications.push("Bybit API Connected - Real Money Trading is now ACTIVE.", "warning")
        # Kick off an immediate balance read instead of waiting for the next background poll.
        asyncio.create_task(self.fetch_real_balance())

    def disconnect_real_api(self, reason="Credentials reset"):
        if self.mode == "LIVE_TRADING":
            print(f"[PILLAR 5: BYBIT] Reverting to Paper Trading ({reason}).")
            notifications.push(f"Bybit disconnected ({reason}) - reverted to Paper Trading.", "info")
        self.mode = "PAPER_TRADING"
        self.connected = False
        self.last_known_balance = None

    def _sign(self, timestamp, recv_window, query_string):
        payload = f"{timestamp}{settings_store.bybit_api_key}{recv_window}{query_string}"
        return hmac.new(settings_store.bybit_api_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

    async def fetch_real_balance(self):
        """ RULE 5 wiring: pull the REAL unified-account total equity from Bybit's v5 API.
        Used both by 'Test Bybit' (to actually verify credentials) and by the background
        refresher that keeps total_capital showing the live account balance once connected.
        Returns the equity as a float, or None on any failure (network/auth/parsing). """
        if not settings_store.is_bybit_configured():
            self.last_error = "No Bybit API Key/Secret configured."
            return None

        base_url = (
            "https://api-testnet.bybit.com"
            if settings_store.bybit_environment == "testnet"
            else "https://api.bybit.com"
        )
        query_string = "accountType=UNIFIED"
        timestamp = str(int(time.time() * 1000))
        recv_window = "5000"
        headers = {
            "X-BAPI-API-KEY": settings_store.bybit_api_key,
            "X-BAPI-SIGN": self._sign(timestamp, recv_window, query_string),
            "X-BAPI-SIGN-TYPE": "2",
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": recv_window,
        }

        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(f"{base_url}/v5/account/wallet-balance?{query_string}", headers=headers)

            # Bybit returns a bare 401 with an empty body for invalid/expired API keys -
            # there's no JSON to parse in that case, so check the status first.
            if resp.status_code == 401:
                self.last_error = "Invalid API key/secret (Bybit returned 401 Unauthorized)."
                self._note_failure()
                return None
            if resp.status_code != 200:
                self.last_error = f"Bybit API returned HTTP {resp.status_code}."
                self._note_failure()
                return None

            data = resp.json()

            if data.get("retCode") != 0:
                self.last_error = data.get("retMsg", "Unknown Bybit API error.")
                self._note_failure()
                return None

            account_list = data.get("result", {}).get("list", [])
            if not account_list:
                self.last_error = "Bybit returned no account data for this key."
                self._note_failure()
                return None

            equity = float(account_list[0]["totalEquity"])
            self.last_known_balance = equity
            self.last_error = None
            if self._was_failing:
                notifications.push("Bybit connection restored - live balance is syncing again.", "success")
            self._was_failing = False
            return equity
        except Exception as exc:
            self.last_error = f"Bybit request failed: {exc}"
            self._note_failure()
            return None

    def _note_failure(self):
        if not self._was_failing:
            print(f"[BYBIT] Balance fetch failing: {self.last_error}")
            notifications.push(f"Bybit balance unreachable ({self.last_error}). Showing last known value.", "error")
        self._was_failing = True

    def get_taker_fee_pct(self):
        """ RULE 6/7: Live taker fee tier used for all True Net Profit calculations. """
        return self.taker_fee_pct

    def execute_market_buy(self, pair, reason):
        # RULE 7: Entry orders are ALWAYS Market Orders for guaranteed instant fill
        if self.mode == "PAPER_TRADING":
            print(f"👉 [PAPER TRADING - VIRTUAL] Bybit API -> Market BUY {pair} -> {reason}")
        else:
            print(f"🔥 [REAL LIVE TRADING - ACTUAL] Bybit REST API -> MARKET BUY {pair} -> {reason}")

    def execute_market_sell(self, pair, reason):
        # REST API ACTION CABLE - RULE 7: Exit orders are ALWAYS Market Orders
        if self.mode == "PAPER_TRADING":
            print(f"👉 [PAPER TRADING - VIRTUAL] Bybit API -> Market Sell {pair} -> {reason}")
        else:
            print(f"🔥 [REAL LIVE TRADING - ACTUAL] Bybit REST API -> MARKET SELL {pair} -> {reason}")

bybit_api = BybitAPIWrapper()

# ==========================================
# PILLAR 3: CORE AI AGENT LOGIC (State & Rules)
# ==========================================
class AITradingAgent:
    # PROFIT BOOKING POLICY (updated): 0.40% strict floor + 30% dynamic trailing
    # from the highest peak, with a floor override. See process_tick for the math.
    PROFIT_FLOOR_PCT = 0.40      # never sell below this net profit %
    TRAILING_DROP_PCT = 0.30     # sell when net drops 30% from its peak

    def __init__(self):
        self.is_active = False
        self.emergency_triggered = False
        self.emergency_trigger_time = None  # RULE 8: Backend timer (Source of Truth)
        self.emergency_auto_kill_executed = False  # RULE 8: Flag to prevent double-execution
        # RULE 8: True ONLY while a genuine 2.5%+ auto-kill popup is actively awaiting the
        # user's choice. This is what the frontend popup is wired to - NOT emergency_triggered,
        # which stays true (blocking new trades) long after the decision is already resolved.
        # Without this split, a manual STOP TRADING click or a page reload after a resolved
        # emergency would both incorrectly re-show the "choose your action" popup.
        self.emergency_awaiting_decision = False

        # POLICY 2 / RULE 5 Config: Portfolio Kill Switch
        self.starting_capital = 142560.88
        self.current_capital = self.starting_capital
        # Default 3.0% per the AI Agent Instructions setup modal; the modal's
        # /agent/config call updates this to whatever the user picks before start.
        self.max_loss_pct = 3.0
        # AI Agent Instructions modal: max stacked trades the bot may hold at once,
        # derived as half-up round(stop_loss_pct * 1.5). Default 3% -> round(4.5) = 5.
        self.max_concurrent_trades = 5
        # AI Agent Instructions modal: optional "Capital profit of the day" target.
        # 0.0 means disabled. Once the day's profit % crosses this, new entries are
        # halted (existing positions keep being managed by the trailing lock).
        self.daily_profit_target_pct = 0.0
        self.daily_target_reached = False

        # RULE 1: Position Sizing & Leverage (The "100/1" Rule)
        self.leverage = 100
        self.margin_pct = 0.01  # exactly 1% of total capital per trade, never increased

        # POLICY 1 / RULE 4 & 6 Config: Dynamic Trailing Lock (now driven by TRUE NET PROFIT)
        self.current_price = 68415.70
        self.peak_net_pct = 0.0
        self.is_lock_active = False

        # SINGLE-COIN FOCUS: The agent only ever trades ONE active pair at a time,
        # but that pair can carry MULTIPLE stacked trades (scaled-in positions).
        self.active_pair = "BTC/USDT"
        self.trade_seq = 0
        self.trades = []  # list of {id, pair, side, entry, margin, position_size, entry_fee_usd}

        # Chart-only timeframe (kept so the frontend's /set-timeframe call still
        # works). Entries no longer read candles - they fire on a 10s cadence.
        self.timeframe_seconds = 30

    def _trade_metrics(self, t):
        """ RULE 6: True Net Profit = Gross Profit - (Entry Fee + Estimated Exit Fee).
        RULE 7: Exit fee is recalculated dynamically off the LIVE current price. """
        if t["side"] == "LONG":
            gross_pct = ((self.current_price - t["entry"]) / t["entry"]) * 100
        else:
            gross_pct = ((t["entry"] - self.current_price) / t["entry"]) * 100

        entry_fee_pct = t["entry_fee_pct"]  # fixed at the moment of entry
        # Exit fee scales with the live price ratio - it is an ESTIMATE until actually filled
        exit_fee_pct = bybit_api.get_taker_fee_pct() * (self.current_price / t["entry"])
        net_pct = gross_pct - entry_fee_pct - exit_fee_pct

        gross_usd = t["position_size"] * (gross_pct / 100)
        exit_fee_usd = t["position_size"] * (exit_fee_pct / 100)
        net_usd = gross_usd - t["entry_fee_usd"] - exit_fee_usd

        return {
            "gross_pct": gross_pct, "net_pct": net_pct,
            "gross_usd": gross_usd, "exit_fee_usd": exit_fee_usd, "net_usd": net_usd,
            "entry_fee_pct": entry_fee_pct, "exit_fee_pct": exit_fee_pct,
        }

    def get_unrealized_net_usd(self):
        return sum(self._trade_metrics(t)["net_usd"] for t in self.trades)

    def get_total_portfolio_value(self):
        """ RULE 5: Total Portfolio Value.
        PAPER_TRADING (default) -> simulated capital + unrealized P&L of open (simulated) positions.
        LIVE_TRADING -> the REAL Bybit account equity, refreshed in the background by
        bybit_api.fetch_real_balance(). Falls back to the simulated value until the first
        successful read comes back, so the UI never shows a blank/zero balance mid-switch. """
        if bybit_api.mode == "LIVE_TRADING" and bybit_api.last_known_balance is not None:
            return bybit_api.last_known_balance
        return self.current_capital + self.get_unrealized_net_usd()

    def set_paper_capital(self, amount):
        """ Resets the simulated PAPER_TRADING balance to a new starting amount.
        Only allowed while the agent is still in PAPER_TRADING mode (never touches real funds). """
        self.starting_capital = amount
        self.current_capital = amount
        self.trades = []
        self.is_lock_active = False
        self.peak_net_pct = 0.0
        print(f"[PILLAR 3: AI AGENT] Paper trading capital reset to ${amount:,.2f}.")

    def set_timeframe(self, seconds):
        """ Chart-only timeframe sync (frontend -> backend). The UI's selected
        timeframe is stored for display purposes; entries no longer depend on
        candles - they fire on a 10s unconditional cadence (auto_buy_loop). """
        self.timeframe_seconds = seconds
        print(f"[TIMEFRAME SYNC] Backend timeframe set to {seconds}s (chart-only; entries are 10s unconditional).")

    def open_trade(self, side="LONG", reason="Manual", source="auto"):
        """ RULE 1: Opens a position sized at EXACTLY 1% margin of current total capital,
        with 100x leverage, filled as a Market Order (RULE 7) with simulated minor slippage.
        `source` tags who opened it - "auto" (the 10s unconditional auto-buy loop) or
        "manual" (the dashboard's manual BUY button) - so the manual SELL button can
        tell them apart and only ever close manually-opened positions. """
        if not self.is_active or self.emergency_triggered:
            return None
        # AI Agent Instructions modal: cap stacked positions at max_concurrent_trades.
        if len(self.trades) >= self.max_concurrent_trades:
            notifications.push(
                f"Max concurrent trades ({self.max_concurrent_trades}) reached on {self.active_pair} - new entry skipped.",
                "info",
            )
            return None
        # Daily profit target reached -> no new entries (existing ones keep trailing).
        if self.daily_target_reached:
            return None

        # RULE 1: 1% margin, 100x leverage -> position_size = margin * leverage
        margin = round(self.current_capital * self.margin_pct, 2)
        position_size = round(margin * self.leverage, 2)

        # RULE 7: Market orders fill with minor slippage vs the requested price
        slippage = random.uniform(-0.0002, 0.0002)
        filled_price = round(self.current_price * (1 + slippage), 4)

        # RULE 6: Live Entry Fee, based on Bybit's current Taker fee tier
        entry_fee_pct = bybit_api.get_taker_fee_pct()
        entry_fee_usd = round(position_size * (entry_fee_pct / 100), 4)

        self.trade_seq += 1
        trade = {
            "id": self.trade_seq,
            "pair": self.active_pair,
            "side": side,
            "entry": filled_price,
            "margin": margin,
            "position_size": position_size,
            "entry_fee_pct": entry_fee_pct,
            "entry_fee_usd": entry_fee_usd,
            "source": source,
        }
        self.trades.append(trade)
        bybit_api.execute_market_buy(self.active_pair, f"{reason} | Margin=${margin} (1% of capital) x{self.leverage} leverage -> Position=${position_size}")
        print(f"[PILLAR 3: AI AGENT] Opened new {side} position #{trade['id']} on {self.active_pair} @ {filled_price} "
              f"(margin=${margin}, position=${position_size}, entry_fee=${entry_fee_usd}, source={source})")
        notifications.push(f"Order Filled: {self.active_pair} {side} @ {filled_price:,.4f} (Margin ${margin:,.2f} x{self.leverage})", "success")
        return trade

    def manual_close_best(self, reason="Manual SELL button"):
        """ Manual SELL button: closes exactly ONE position among the manually-opened
        trades (never touches the auto/RULE-3 pyramided ones) - specifically whichever
        manual trade currently has the highest True Net Profit (or, if all manual trades
        are underwater, whichever has the smallest loss - the same "pick the max net_pct"
        comparison covers both cases). """
        manual_trades = [t for t in self.trades if t.get("source") == "manual"]
        if not manual_trades:
            return None

        best = max(manual_trades, key=lambda t: self._trade_metrics(t)["net_pct"])
        m = self._trade_metrics(best)
        self.current_capital += m["net_usd"]
        self.trades = [t for t in self.trades if t["id"] != best["id"]]
        bybit_api.execute_market_sell(best["pair"], f"{reason} | Realized Net P&L: ${m['net_usd']:.2f} ({m['net_pct']:.3f}%)")
        print(f"[PILLAR 3: AI AGENT] Manual SELL closed position #{best['id']} on {best['pair']} "
              f"(net_pct={m['net_pct']:.3f}%, net_usd=${m['net_usd']:.2f})")
        notifications.push(
            f"Manual SELL: Position #{best['id']} closed on {best['pair']} | Net P&L: ${m['net_usd']:.2f} ({m['net_pct']:.3f}%)",
            "success" if m["net_usd"] >= 0 else "error",
        )
        return best

    def set_active_pair(self, pair, price):
        """ Switching the focused coin closes any prior positions - one coin at a time rule. """
        self.active_pair = pair
        self.current_price = price
        self.trades = []
        self.peak_net_pct = 0.0
        self.is_lock_active = False
        print(f"[PILLAR 3: AI AGENT] Active pair switched to {pair}. Previous positions cleared (single-coin focus).")

    def get_trades_snapshot(self):
        """ Live GROSS vs TRUE NET PnL% per trade (RULE 6), computed off the real-time current_price. """
        snapshot = []
        for t in self.trades:
            m = self._trade_metrics(t)
            snapshot.append({
                "id": t["id"],
                "pair": t["pair"],
                "side": t["side"],
                "entry": t["entry"],
                "current": round(self.current_price, 4),
                "margin": t["margin"],
                "position_size": t["position_size"],
                "gross_pnl_pct": round(m["gross_pct"], 4),
                "pnl": round(m["net_pct"], 4),  # TRUE NET profit % (post-fee) - what the UI displays
                "net_pnl_usd": round(m["net_usd"], 2),
                "entry_fee_usd": t["entry_fee_usd"],
                "exit_fee_usd": round(m["exit_fee_usd"], 4),
                "status": "locked" if self.is_lock_active else "active",
            })
        return snapshot

    async def process_tick(self, new_price, volume_increment):
        """ Runs on EVERY market tick (real Bybit feed or synthetic fallback).
        NEW POLICY: entries are NO LONGER driven by candles/volume/indicators -
        they fire on a strict 10-second cadence from auto_buy_loop(). This tick
        handler now only: updates the live price, runs the RULE 8 30s auto-kill
        timer, the RULE 5 portfolio kill switch, the optional daily-profit
        target, and the RULE 4/6 trailing-lock exit (net-of-fees profit booking).
        `volume_increment` is accepted for feed compatibility but unused now. """
        self.current_price = new_price

        # RULE 8: Backend Timer (Source of Truth) - Auto-execute emergency exit if 30 seconds pass with no response
        now = time.time()
        if (self.emergency_triggered and self.emergency_trigger_time is not None
            and not self.emergency_auto_kill_executed):
            seconds_elapsed = now - self.emergency_trigger_time
            if seconds_elapsed >= 30:
                # User didn't respond within 30 seconds -> backend auto-executes emergency exit,
                # actually selling positions now (they were left running during the 30s window).
                print(f"[RULE 8: AUTO-KILL] 30-second countdown expired. Backend auto-executing EMERGENCY EXIT.")
                self.emergency_auto_kill_executed = True
                self.emergency_awaiting_decision = False  # decision defaulted to Exit - stop showing the popup
                self._close_all_positions("EMERGENCY SELL ALL TRIGGERED | RULE 8 30s auto-kill (no response)")
                self.is_active = False  # Stop all processing
                notifications.push("⚠️ RULE 8: 30-second timeout reached. System auto-halted.", "error")
                return

        # (Entry logic removed - see auto_buy_loop() for the 10s unconditional fire & hold.)

        if not self.trades:
            return

        # RULE 5: Master Kill Switch - checked against TOTAL portfolio value (realized + unrealized)
        total_value = self.get_total_portfolio_value()
        portfolio_drop = ((self.starting_capital - total_value) / self.starting_capital) * 100

        # RULE 8: Only trigger emergency if:
        # 1. Loss is >= threshold (not already triggered in this session)
        # 2. We're not already in emergency state
        if (portfolio_drop >= self.max_loss_pct
            and not self.emergency_triggered):
            self.trigger_emergency_exit(reason=f"RULE 5: {self.max_loss_pct:.1f}% Portfolio Stop-Loss Hit! (Total Value ${total_value:,.2f}). Selling All unconditionally.")
            return

        # AI Agent Instructions modal: optional "Capital profit of the day" target.
        # Once the day's profit % crosses it, halt NEW entries (existing positions
        # keep being managed by the trailing lock and can still close on their own).
        if (self.is_active and self.daily_profit_target_pct > 0
                and not self.daily_target_reached and not self.emergency_triggered):
            daily_profit_pct = ((total_value - self.starting_capital) / self.starting_capital) * 100
            if daily_profit_pct >= self.daily_profit_target_pct:
                self.daily_target_reached = True
                print(f"[PILLAR 3: AI AGENT] Daily profit target {self.daily_profit_target_pct}% reached "
                      f"(current {daily_profit_pct:.2f}%). New entries halted; existing positions remain open.")
                notifications.push(
                    f"🎯 Daily profit target of {self.daily_profit_target_pct}% reached! New entries halted.",
                    "success",
                )

        # RULE 4 & 6: Dynamic Trailing Lock, driven by TRUE NET PROFIT (post-fee), not gross
        net_pcts = [self._trade_metrics(t)["net_pct"] for t in self.trades]
        avg_net_pct = sum(net_pcts) / len(net_pcts)

        # NEW PROFIT BOOKING POLICY: 0.40% strict floor + 30% dynamic trailing.
        # Floor: never sell below PROFIT_FLOOR_PCT net profit (after Bybit's
        # ~0.20% fee, real profit still remains). Trailing: sell when avg net
        # drops TRAILING_DROP_PCT (30%) from its highest peak. Example: peak
        # +1.00% -> 30% of 1.00 = 0.30 -> sell at +0.70%. Floor override: if the
        # peak is small enough that 30% off it would land below 0.40%, ignore the
        # 30% rule and sell strictly at 0.40%.
        floor = self.PROFIT_FLOOR_PCT
        trailing_target = self.peak_net_pct * (1 - self.TRAILING_DROP_PCT)
        floor_override = trailing_target < floor
        effective_target = floor if floor_override else trailing_target

        if avg_net_pct >= floor:
            if not self.is_lock_active:
                notifications.push(f"Trailing Lock ACTIVATED on {self.active_pair} at +{avg_net_pct:.3f}% net profit (floor {floor}%).", "success")
                self.is_lock_active = True
            if avg_net_pct > self.peak_net_pct:
                self.peak_net_pct = avg_net_pct  # Trail Up
                notifications.push(f"Trailing Lock peak shifted up to +{avg_net_pct:.3f}%.", "info")

        # Fire only once we've actually retreated from the peak (avg < peak), so
        # the lock activating at exactly the floor doesn't instantly sell.
        if self.is_lock_active and self.peak_net_pct >= floor:
            if avg_net_pct < self.peak_net_pct and avg_net_pct <= effective_target:
                if floor_override:
                    reason = (f"Profit Book: Floor Override @ {floor:.2f}% net "
                              f"(peak {self.peak_net_pct:.3f}%, 30% drop -> {trailing_target:.3f}% would breach floor). Market SELL.")
                else:
                    reason = (f"Profit Book: 30% Trailing Drop (peak {self.peak_net_pct:.3f}% -> target {effective_target:.3f}%). Market SELL.")
                self.execute_sell(reason)

    def execute_sell(self, reason):
        # RULE 4/6 & 7: Profit-protection sell. Exit orders are Market Orders.
        # ONLY trades currently in TRUE NET profit (green / net_pct > 0) are closed -
        # their gains are realized into current_capital. Trades still in loss (red /
        # net_pct <= 0) are LEFT OPEN so they have room to recover back into profit;
        # selling them here would lock in a loss, which is the opposite of what a
        # profit-protection trailing lock exists to do. The RULE 5/8 emergency kill
        # switch uses _close_all_positions() instead, which still sells everything.
        print(f"[PILLAR 3: AI AGENT] Output -> SELL REQUIRED: {reason}")

        scored = [(t, self._trade_metrics(t)) for t in self.trades]
        winners = [(t, m) for (t, m) in scored if m["net_pct"] > 0]
        held = [t for (t, m) in scored if m["net_pct"] <= 0]

        if not winners:
            print("[PILLAR 3: AI AGENT] No trades currently in net profit - holding all red positions until they turn green.")
            return

        for trade, m in winners:
            self.current_capital += m["net_usd"]
            bybit_api.execute_market_sell(trade["pair"], f"{reason} | Realized Net P&L: ${m['net_usd']:.2f} ({m['net_pct']:.3f}%)")
            notifications.push(f"Position #{trade['id']} CLOSED on {trade['pair']} | Net P&L: ${m['net_usd']:.2f} ({m['net_pct']:.3f}%)",
                                "success" if m["net_usd"] >= 0 else "error")

        # Keep only the still-red (losing) positions open for recovery.
        self.trades = held

        # Recompute trailing-lock state off whatever remains.
        if not self.trades:
            self.is_lock_active = False
            self.peak_net_pct = 0.0
        else:
            remaining = [self._trade_metrics(t)["net_pct"] for t in self.trades]
            new_avg = sum(remaining) / len(remaining)
            if new_avg < self.PROFIT_FLOOR_PCT:
                # Everything left is under the floor - drop the lock until the held
                # positions climb back above 0.40% and start a fresh trail.
                self.is_lock_active = False
                self.peak_net_pct = 0.0
            else:
                self.peak_net_pct = min(self.peak_net_pct, new_avg)

    def _close_all_positions(self, reason):
        """ RULE 5 & 7: Unconditional 'SELL ALL' via Market Order, realizing whatever net P&L remains. """
        for trade in self.trades:
            m = self._trade_metrics(trade)
            self.current_capital += m["net_usd"]
            bybit_api.execute_market_sell(trade["pair"], f"{reason} | Realized Net P&L: ${m['net_usd']:.2f}")
        self.trades = []
        self.peak_net_pct = 0.0
        self.is_lock_active = False

    def trigger_emergency_exit(self, reason="Manual Master Switch Action"):
        """ RULE 8: Called ONLY by the automatic 2.5%+ portfolio-loss detector in process_tick.
        Per policy this PAUSES new entries and arms the 30-second decision window - it does
        NOT sell existing positions. Trades keep running normally (trailing lock etc. still
        active) while the popup is up, so a CONTINUE choice truly means "keep going exactly
        as before" with nothing force-closed. Positions are only actually sold if the user
        confirms EMERGENCY EXIT (button click or the 30s timeout) - see confirm_emergency_exit(). """
        print(f"[RULE 8: EMERGENCY POPUP TRIGGERED]: {reason}")
        # RULE 8: Backend Timer - Source of Truth starts counting (30-second auto-exit countdown)
        self.emergency_trigger_time = time.time()
        self.emergency_triggered = True  # blocks NEW entries (open_trade checks this) - existing ones are untouched
        self.emergency_awaiting_decision = True
        print("[RULE 8: NEW ENTRIES PAUSED] Waiting for user choice (EMERGENCY EXIT or CONTINUE) within 30 seconds...")
        notifications.push(f"⏰ RULE 8: 30-second Emergency Exit countdown started. {reason}", "error")

    def confirm_emergency_exit(self):
        """ User clicked 'EMERGENCY EXIT' on an ACTIVE RULE 8 popup (or the frontend's 30s
        fallback timer fired) - THIS is where positions actually get sold, not at the
        initial 2.5% detection. """
        print("[RULE 8: EMERGENCY EXIT CONFIRMED] Selling all positions and halting.")
        self._close_all_positions("EMERGENCY SELL ALL TRIGGERED | RULE 8 confirmed by user")
        self.emergency_awaiting_decision = False
        self.is_active = False

    def manual_stop(self, reason="Manual Kill Switch Activated from Frontend"):
        """ Plain STOP TRADING button click - a deliberate user action with NO portfolio-loss
        event. Must NOT set emergency_triggered/emergency_awaiting_decision - that popup is
        reserved for the automatic RULE 5 detector, not a voluntary stop. """
        print(f"[PILLAR 2: BACKEND] {reason}")
        self._close_all_positions(reason)
        self.is_active = False

    def resume_trading_with_higher_buffer(self):
        """ User chose 'CONTINUE' on the risk popup: raise the cumulative stop-loss
        ceiling by another 2.5% (2.5% -> 5% -> 7.5% ...) and resume trading. """
        self.max_loss_pct += 2.5
        self.emergency_triggered = False
        self.emergency_awaiting_decision = False
        self.emergency_trigger_time = None  # RULE 8: Reset the 30-second timer
        self.emergency_auto_kill_executed = False  # RULE 8: Reset auto-kill flag
        self.is_active = True
        print(f"[RULE 8: CONTINUE CHOSEN] Trading resumed by user choice. New cumulative stop-loss threshold: {self.max_loss_pct}%.")
        notifications.push(f"✅ RULE 8: User chose CONTINUE. Trading resumed - stop-loss threshold raised to {self.max_loss_pct:.1f}%.", "warning")

agent = AITradingAgent()

# ==========================================
# BACKGROUND MARKET SIMULATOR (price feed; entries now run in auto_buy_loop)
# Runs regardless of whether any browser tab is connected, keeping
# current_price live for the trailing-lock exit math in process_tick.
# ==========================================
# Bybit's own public market data (no API key needed) - this is a Bybit bot,
# so its real-price source should be Bybit itself. This also sidesteps a
# real finding: Binance's endpoints (both WebSocket, on ports 9443 AND 443,
# and REST) never delivered a single real tick when called FROM this Render
# backend, despite working perfectly from a local machine and from the
# user's own browser (the frontend chart, which fetches Binance directly
# client-side, always showed correct real data) - strongly suggesting
# Binance blocks/throttles this hosting provider's server IP range
# specifically, not a protocol/port issue. Bybit's public API has no such
# problem, verified working from this exact backend.
BYBIT_SYMBOL_MAP = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "BNB": "BNBUSDT"}

def get_bybit_symbol(pair_label):
    symbol = (pair_label or "").split("/")[0]
    return BYBIT_SYMBOL_MAP.get(symbol)

# Tracks the last time binance_price_feed successfully processed a REAL tick,
# so market_simulator can tell "actively receiving real data" apart from
# "silently stuck" (network hiccup, DNS issue, host blocking outbound, etc.)
# and self-heal by taking over with synthetic movement instead of leaving
# current_price frozen forever.
_last_real_feed_update = 0.0
REAL_FEED_STALE_AFTER_SECONDS = 10

# ==========================================
# NEW ENTRY POLICY: 10-SEC UNCONDITIONAL FIRE & HOLD
# Replaces the old candle/volume (RULE 3) entry logic. Once the bot is active,
# this loop fires ONE new BUY trade every 10 seconds - unconditionally, with no
# technical-indicator gating. The max_concurrent_trades cap (Risk-based SL * 1.5,
# set from the AI Agent Instructions modal) acts as the HOLD: when active trades
# reach the cap, the loop skips firing; the moment a trade sells and frees a
# slot, the next 10s tick resumes firing so the book refills to the cap.
# Exit/profit-booking still runs in process_tick (trailing lock, kill switch).
# ==========================================
AUTO_BUY_INTERVAL_SECONDS = 10

async def auto_buy_loop():
    print(f"[AUTO BUY LOOP] Background task starting (every {AUTO_BUY_INTERVAL_SECONDS}s, unconditional fire & hold).")
    while True:
        await asyncio.sleep(AUTO_BUY_INTERVAL_SECONDS)
        # No-op unless the bot is actively running and not in a protective halt.
        if not agent.is_active or agent.emergency_triggered or agent.daily_target_reached:
            continue
        # HOLD: max capacity reached - wait for a slot to free (a sell in process_tick).
        if len(agent.trades) >= agent.max_concurrent_trades:
            continue
        # FIRE: open one 1%-margin / 100x LONG. open_trade() re-checks the cap too,
        # so a race between this loop and a manual BUY can never exceed the limit.
        agent.open_trade("LONG", reason="10s Unconditional Auto-Buy (Time-Based Fire & Hold)")

async def market_simulator():
    """ Synthetic random-walk price - runs whenever the active pair has no
    real market-data mapping, AND as a self-healing fallback if the real
    feed hasn't delivered a tick recently (so current_price can never stay
    permanently frozen even if the real feed silently breaks). """
    while True:
        no_real_feed = get_bybit_symbol(agent.active_pair) is None
        real_feed_stale = (time.time() - _last_real_feed_update) > REAL_FEED_STALE_AFTER_SECONDS
        if no_real_feed or real_feed_stale:
            volatility = random.uniform(-10, 10)
            new_price = agent.current_price + volatility
            # Simulate a live trade-volume tick (baseline + occasional surges to demonstrate Rule 3)
            volume_increment = random.uniform(0.5, 3.0)
            if random.random() < 0.03:  # occasional volume spike burst
                volume_increment *= random.uniform(3, 6)
            await agent.process_tick(new_price, volume_increment)
        await asyncio.sleep(0.5)

async def binance_price_feed():
    """ Keeps agent.current_price tracking REAL market prices, polling Bybit's
    public recent-trades endpoint every ~1.5s for whichever mapped pair is
    active. (Volume is folded through to process_tick for feed compatibility
    but is no longer used for entries - those fire on a 10s cadence now.)
    Verified this delivers real price from this exact backend. """
    global _last_real_feed_update
    print("[MARKET FEED] Background task starting (Bybit REST polling mode).")
    last_seen_trade_id = None
    current_symbol = None

    async with httpx.AsyncClient(timeout=6.0) as client:
        while True:
            target_symbol = get_bybit_symbol(agent.active_pair)
            if target_symbol is None:
                await asyncio.sleep(2)
                continue

            if target_symbol != current_symbol:
                current_symbol = target_symbol
                last_seen_trade_id = None  # fresh pair - don't diff against the old symbol's trade IDs

            try:
                url = f"https://api.bybit.com/v5/market/recent-trade?category=spot&symbol={target_symbol}&limit=20"
                resp = await client.get(url)
                if resp.status_code == 200:
                    body = resp.json()
                    trades = body.get("result", {}).get("list", [])
                    if trades:
                        # Bybit returns newest-first; only fold in trades newer than the
                        # last poll, so volume reflects genuine recent activity instead
                        # of re-counting the same trades.
                        new_trades = [t for t in trades if last_seen_trade_id is None or t["seq"] > last_seen_trade_id]
                        if new_trades:
                            total_qty = sum(float(t["size"]) for t in new_trades)
                            last_price = float(new_trades[0]["price"])  # newest-first -> index 0 is latest
                            await agent.process_tick(last_price, total_qty)
                            last_seen_trade_id = trades[0]["seq"]
                            _last_real_feed_update = time.time()
                else:
                    print(f"[MARKET FEED] Bybit REST returned HTTP {resp.status_code} for {target_symbol}")
            except Exception as exc:
                print(f"[MARKET FEED] REST poll error for {target_symbol}: {exc}")

            await asyncio.sleep(1.5)

async def bybit_balance_refresher():
    """ Keeps bybit_api.last_known_balance fresh while LIVE_TRADING, so
    get_total_portfolio_value() can read it synchronously without ever
    blocking on a network call in the hot path (WS loops, kill-switch checks). """
    while True:
        if bybit_api.mode == "LIVE_TRADING" and bybit_api.connected:
            await bybit_api.fetch_real_balance()
        await asyncio.sleep(3)

# Render's free tier spins a web service down after ~15 minutes with no
# inbound HTTP traffic, so the next real request pays a cold-start delay
# (up to ~50s). RENDER_EXTERNAL_URL is set automatically by Render on every
# web service - self-pinging it periodically (safely under 15 minutes)
# generates real inbound traffic so the service never silently goes to
# sleep. No-op for local dev, where that env var isn't set.
KEEPALIVE_INTERVAL_SECONDS = 11 * 60  # 11 minutes - safely inside the 10-14 minute window asked for

async def self_ping_keepalive():
    self_url = os.environ.get("RENDER_EXTERNAL_URL")
    if not self_url:
        print("[KEEPALIVE] RENDER_EXTERNAL_URL not set (local dev) - keepalive disabled.")
        return

    print(f"[KEEPALIVE] Will self-ping {self_url}/health every {KEEPALIVE_INTERVAL_SECONDS // 60} minutes to prevent Render's free-tier sleep.")
    async with httpx.AsyncClient(timeout=15.0) as client:
        while True:
            await asyncio.sleep(KEEPALIVE_INTERVAL_SECONDS)
            try:
                resp = await client.get(f"{self_url}/health")
                print(f"[KEEPALIVE] Self-ping OK (HTTP {resp.status_code}) - service stays awake.")
            except Exception as exc:
                print(f"[KEEPALIVE] Self-ping failed ({exc}) - will retry next interval.")

@app.on_event("startup")
async def start_background_tasks():
    asyncio.create_task(market_simulator())
    asyncio.create_task(binance_price_feed())
    asyncio.create_task(bybit_balance_refresher())
    asyncio.create_task(self_ping_keepalive())
    asyncio.create_task(auto_buy_loop())

# ==========================================
# 2. REST API COMMAND "WIRES"
# ==========================================
@app.post("/start-bot")
async def start_bot():
    agent.is_active = True
    agent.emergency_triggered = False
    agent.emergency_awaiting_decision = False
    agent.emergency_trigger_time = None  # RULE 8: Reset timer
    agent.emergency_auto_kill_executed = False  # RULE 8: Reset auto-kill flag
    agent.daily_target_reached = False  # fresh session -> clear any prior daily-target halt
    print("[PILLAR 2: BACKEND] Received 'START' from Frontend. AI Agent awakened.")
    notifications.push(f"AI Agent STARTED - now monitoring {agent.active_pair} live.", "success")
    return {"status": "success", "message": "Bot active & trailing logic initialized."}

@app.post("/emergency-exit")
async def emergency_exit():
    """ Shared by two very different situations, disambiguated by server-side state:
    1. The main STOP TRADING button (voluntary, no loss event) -> manual_stop().
    2. The RULE 8 popup's 'Emergency Exit' button / 30s frontend fallback, responding to
       an ALREADY-ACTIVE automatic emergency -> confirm_emergency_exit() (positions are
       already closed at trigger time; this just resolves the pending decision). """
    if agent.emergency_awaiting_decision:
        print("[PILLAR 2: BACKEND] User confirmed EMERGENCY EXIT choice from the RULE 8 popup.")
        agent.confirm_emergency_exit()
    else:
        print("[PILLAR 2: BACKEND] Received manual STOP TRADING from Frontend control button.")
        agent.manual_stop()
    return {"status": "success", "message": "All trades closed."}

@app.post("/continue-trading")
async def continue_trading():
    """ RULE 5 (Popup 'CONTINUE' choice): resumes trading with the cumulative
    stop-loss ceiling raised by another 2.5% (e.g. 2.5% -> 5% -> 7.5% ...). """
    agent.resume_trading_with_higher_buffer()
    return {
        "status": "success",
        "message": f"Trading resumed. New stop-loss threshold: {agent.max_loss_pct:.1f}% cumulative drop.",
        "max_loss_pct": agent.max_loss_pct,
    }

@app.post("/connect-bybit")
async def connect_bybit():
    print("[PILLAR 2: BACKEND] Switching from Paper Trading to Live Real Trading...")
    bybit_api.connect_real_api()
    return {"status": "success", "message": "SUCCESS: Bybit API Connected. Real Money Trading is ACTIVE."}

@app.get("/trading-mode")
async def get_trading_mode():
    """ Tells the frontend which chart data source to use:
    PAPER_TRADING -> free public crypto feed (e.g. Binance), LIVE_TRADING -> backend/Bybit feed. """
    return {"mode": bybit_api.mode}

# ==========================================
# SINGLE-COIN, MULTI-TRADE WIRING
# ==========================================
class OpenTradePayload(BaseModel):
    side: str = "LONG"

class SetPairPayload(BaseModel):
    pair: str
    price: float

class CloseTradePayload(BaseModel):
    id: int

@app.post("/open-trade")
async def open_trade(payload: OpenTradePayload):
    """ Manual BUY button: opens an additional 1%-margin/100x position on the currently
    active pair (single-coin focus, but multiple stacked trades allowed). Every click
    books one more trade - tagged "manual" so the SELL button only ever closes these,
    never the RULE-3 auto-pyramided trades. """
    side = payload.side.upper() if payload.side.upper() in ("LONG", "SHORT") else "LONG"
    trade = agent.open_trade(side, reason="Manual BUY button", source="manual")
    if trade is None:
        return {"status": "error", "message": "Start the bot before opening a position."}
    return {"status": "success", "trade": trade, "pair": agent.active_pair}

@app.post("/manual-sell")
async def manual_sell():
    """ Manual SELL button: closes exactly the ONE manually-opened trade with the
    highest True Net Profit (or smallest loss, if none are in profit) - never the
    auto/RULE-3 trades, and never more than one position per click. """
    closed = agent.manual_close_best()
    if closed is None:
        return {"status": "error", "message": "No manually-opened positions to sell."}
    return {"status": "success", "message": f"Manual SELL executed - position #{closed['id']} closed.", "trade": closed}

@app.post("/close-trade")
async def close_trade(payload: CloseTradePayload):
    """ Force-closes a single stacked position on the active pair (trash icon action). """
    trade = next((t for t in agent.trades if t["id"] == payload.id), None)
    if not trade:
        return {"status": "error", "message": "Trade not found or already closed."}

    agent.trades = [t for t in agent.trades if t["id"] != payload.id]
    bybit_api.execute_market_sell(trade["pair"], f"Manual force-close of position #{trade['id']}")
    notifications.push(f"Position #{trade['id']} manually force-closed on {trade['pair']}.", "warning")
    return {"status": "success", "message": f"Position #{trade['id']} closed at market price."}

@app.post("/set-pair")
async def set_pair(payload: SetPairPayload):
    """ Switches the agent's single focused trading pair. Clears any prior positions. """
    agent.set_active_pair(payload.pair, payload.price)
    return {"status": "success", "message": f"Active trading pair set to {payload.pair}.", "pair": agent.active_pair}

class SetTimeframePayload(BaseModel):
    seconds: int

@app.post("/set-timeframe")
async def set_timeframe(payload: SetTimeframePayload):
    """ RULE 2: Dynamic Timeframe Syncing - the frontend tells the backend exactly
    which candle interval to read volume/price data on. """
    agent.set_timeframe(payload.seconds)
    return {"status": "success", "message": f"Backend now synced to {payload.seconds}s candles.", "seconds": agent.timeframe_seconds}

# ==========================================
# PAPER TRADING CAPITAL WIRING
# ==========================================
class PaperCapitalPayload(BaseModel):
    amount: float

@app.post("/paper-trading/set-capital")
async def set_paper_capital(payload: PaperCapitalPayload):
    if bybit_api.mode != "PAPER_TRADING":
        return {"status": "error", "message": "Cannot change simulated capital while LIVE trading is active."}
    if payload.amount < 100:
        return {"status": "error", "message": "Minimum paper trading capital is $100."}

    agent.set_paper_capital(payload.amount)
    return {"status": "success", "message": f"Paper trading capital set to ${payload.amount:,.2f}.", "capital": agent.current_capital}

# ==========================================
# AI AGENT INSTRUCTIONS MODAL WIRING
# ==========================================
class AgentConfigPayload(BaseModel):
    stop_loss_pct: float = 3.0
    daily_profit_pct: float = 0.0


def _half_up_round(value: float) -> int:
    """ Round half UP (0.5 -> next integer), matching the modal's strict-integer
    rule. Python's built-in round() uses banker's rounding (round(2.5) == 2),
    which would break the UI's "0.5 or more rounds up" contract. """
    return math.floor(value + 0.5)


@app.post("/agent/config")
async def set_agent_config(payload: AgentConfigPayload):
    """ Applied from the "AI Agent Instructions" pre-start modal.
    - stop_loss_pct  -> sets the RULE 5/8 portfolio kill-switch threshold
      (agent.max_loss_pct) and drives the concurrent-trades cap via
      round(stop_loss_pct * 1.5) (half-up).
    - daily_profit_pct -> optional "Capital profit of the day" target; 0 disables.
    Both are validated and stored before /start-bot is called by the frontend. """
    if payload.stop_loss_pct < 0.5 or payload.stop_loss_pct > 50:
        return {"status": "error", "message": "Stop loss must be between 0.5% and 50%."}
    if payload.daily_profit_pct < 0 or payload.daily_profit_pct > 1000:
        return {"status": "error", "message": "Daily profit target must be between 0% and 1000%."}

    agent.max_loss_pct = payload.stop_loss_pct
    agent.max_concurrent_trades = max(1, _half_up_round(payload.stop_loss_pct * 1.5))
    agent.daily_profit_target_pct = payload.daily_profit_pct
    agent.daily_target_reached = False
    print(f"[AGENT CONFIG] stop_loss={agent.max_loss_pct}% | max_concurrent_trades="
          f"{agent.max_concurrent_trades} | daily_profit_target={agent.daily_profit_target_pct}%")
    return {
        "status": "success",
        "message": "Agent config applied.",
        "max_loss_pct": agent.max_loss_pct,
        "max_concurrent_trades": agent.max_concurrent_trades,
        "daily_profit_target_pct": agent.daily_profit_target_pct,
    }

@app.get("/agent/config")
async def get_agent_config():
    """ Lets the modal show the currently-applied config when reopened. """
    return {
        "stop_loss_pct": agent.max_loss_pct,
        "daily_profit_pct": agent.daily_profit_target_pct,
        "max_concurrent_trades": agent.max_concurrent_trades,
    }

# ==========================================
# INTEGRATION SETTINGS: Bybit & AI API Wiring
# ==========================================
@app.get("/settings/status")
async def get_settings_status():
    """ Returns only non-secret configuration state. Keys/secrets are never returned. """
    return settings_store.status_dict()

@app.post("/settings/save")
async def save_settings(payload: SettingsPayload):
    settings_store.save(payload)
    # Log only that credentials were updated - never the raw values
    print(f"[SETTINGS] Bybit credentials {'updated' if payload.bybit_api_key else 'unchanged'} "
          f"(env={settings_store.bybit_environment}). AI provider set to '{settings_store.ai_provider}'.")
    return {"status": "success", "message": "Settings saved securely. Keys are stored locally and never displayed again."}

@app.post("/settings/test-bybit")
async def test_bybit_connection():
    if not settings_store.is_bybit_configured():
        return {"success": False, "message": "Test failed: No Bybit API Key/Secret configured yet."}

    print(f"[SETTINGS] Testing Bybit connectivity on {settings_store.bybit_environment}...")
    equity = await bybit_api.fetch_real_balance()
    if equity is None:
        return {"success": False, "message": f"Bybit test failed: {bybit_api.last_error}"}
    return {
        "success": True,
        "message": f"Bybit credentials verified on {settings_store.bybit_environment}. Account equity: ${equity:,.2f}.",
    }

@app.post("/settings/test-ai")
async def test_ai_connection():
    if settings_store.ai_provider == "none":
        return {"success": True, "message": "Using built-in rule engine — no external AI provider configured."}
    if not settings_store.ai_api_key:
        return {"success": False, "message": f"Test failed: No API key configured for provider '{settings_store.ai_provider}'."}

    print(f"[SETTINGS] Testing AI provider '{settings_store.ai_provider}' (model={settings_store.ai_model or 'default'})...")
    decision = await consult_ai_provider({
        "pair": "TEST/USDT", "condition": "Test Ping", "candle_volume": 100, "prev_candle_volume": 40,
        "candle_height": 5, "prev_candle_height": 3, "current_price": 100,
    })
    if decision is None:
        return {
            "success": False,
            "message": f"Test failed: could not reach '{settings_store.ai_provider}' - check the API key/base URL and try again.",
        }
    return {
        "success": True,
        "message": f"AI provider '{settings_store.ai_provider}' responded successfully (test decision: {'YES' if decision else 'NO'}). Provider is reachable and ready.",
    }

@app.post("/settings/reset")
async def reset_settings():
    settings_store.reset()
    bybit_api.disconnect_real_api(reason="Settings reset")
    print("[SETTINGS] All stored Bybit & AI settings have been reset.")
    return {"status": "success", "message": "All settings have been reset to defaults."}

# ==========================================
# PILLAR 4: REAL-TIME DATA PIPELINES (WebSockets)
# ==========================================
@app.websocket("/ws/market")
async def market_feed(websocket: WebSocket):
    """ Pushes the latest agent state (price, lock, peak, mode) to connected
    clients every 500ms. Entries fire in auto_buy_loop(); this endpoint is
    read-only and only broadcasts state for the chart / price display. """
    await websocket.accept()
    try:
        while True:
            payload = {
                "price": round(agent.current_price, 4),
                "lock_active": agent.is_lock_active,
                "peak_pct": round(agent.peak_net_pct, 4),
                "trading_mode": bybit_api.mode,
            }
            await websocket.send_json(payload)
            await asyncio.sleep(0.5) # Send updates every 500ms
    except WebSocketDisconnect:
        # POLICY 4: Error Handling & System Safety
        print("POLICY 4: Market WS Client Disconnected. Bot is NOT crashed. Waiting for Reconnection...")

@app.websocket("/ws/portfolio")
async def portfolio_feed(websocket: WebSocket):
    """ Real-time Portfolio monitor & PnL pipeline """
    await websocket.accept()
    try:
        # POLICY 4: Check live portfolio balance and lock positions immediately on connect
        print("POLICY 4: Reconnected. Synchronizing current positions and portfolio lock state.")
        while True:
            # RULE 6: current_capital now only changes via REALIZED trade P&L (execute_sell /
            # trigger_emergency_exit), never a random walk - this is the true capital ledger.
            total_value = agent.get_total_portfolio_value()
            daily_profit = total_value - agent.starting_capital
            daily_profit_pct = (daily_profit / agent.starting_capital) * 100

            # RULE 5: Calculate actual portfolio loss percentage for emergency threshold display
            portfolio_drop = ((agent.starting_capital - total_value) / agent.starting_capital) * 100

            payload = {
                "capital": round(agent.current_capital, 2),
                "total_portfolio_value": round(total_value, 2),
                "trading_mode": bybit_api.mode,
                "daily_profit": round(daily_profit, 2),
                "daily_profit_pct": round(daily_profit_pct, 2),
                "portfolio_drop_pct": round(portfolio_drop, 2),  # RULE 8: Actual loss for emergency modal
                "is_active": agent.is_active,
                # RULE 8: Only true while a decision is genuinely pending - NOT emergency_triggered,
                # which stays true long after resolution and would otherwise re-pop the modal on
                # every reconnect/reload (and even on a plain manual STOP TRADING click).
                "emergency": agent.emergency_awaiting_decision,
                "max_loss_pct": agent.max_loss_pct,
                "trades": len(agent.trades)
            }
            # POLICY 4: Alerting Frontend on Emergency Exit
            await websocket.send_json(payload)
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        print("POLICY 4: Portfolio WS Client Disconnected. System tracking preserved.")

@app.websocket("/ws/notifications")
async def notifications_feed(websocket: WebSocket):
    """ Live feed for the bell dropdown - pushes the rolling notification list
    generated by REAL backend events (trades, locks, emergencies, connections). """
    await websocket.accept()
    try:
        while True:
            await websocket.send_json({"notifications": notifications.notifications})
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        print("POLICY 4: Notifications WS Client Disconnected. System tracking preserved.")

@app.websocket("/ws/trades")
async def trades_feed(websocket: WebSocket):
    """ Real-time feed of ALL live trades for the single active trading pair. """
    await websocket.accept()
    try:
        while True:
            payload = {
                "pair": agent.active_pair,
                "trades": agent.get_trades_snapshot(),
                "lock_active": agent.is_lock_active,
            }
            await websocket.send_json(payload)
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        print("POLICY 4: Trades WS Client Disconnected. System tracking preserved.")

if __name__ == "__main__":
    import os
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload="PORT" not in os.environ)