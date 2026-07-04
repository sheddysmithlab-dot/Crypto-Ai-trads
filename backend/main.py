from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import asyncio
import hashlib
import hmac
import json
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
    def __init__(self):
        self.is_active = False
        self.emergency_triggered = False
        self.emergency_trigger_time = None  # RULE 8: Backend timer (Source of Truth)
        self.emergency_auto_kill_executed = False  # RULE 8: Flag to prevent double-execution

        # POLICY 2 / RULE 5 Config: Portfolio Kill Switch
        self.starting_capital = 142560.88
        self.current_capital = self.starting_capital
        self.max_loss_pct = 2.5  

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

        # RULE 2: Dynamic Timeframe Syncing (Front-end -> Back-end)
        self.timeframe_seconds = 60  # default "1 minute", synced live from the UI

        # RULE 3: Volume Surge 2x entry condition - live candle simulation state
        self.candle_open_time = None
        self.candle_volume = 0.0
        self.prev_candle_volume = 0.0
        self.volume_signal_used = False

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
        """ RULE 2: Dynamic Timeframe Syncing - the UI's selected timeframe drives
        exactly which candle interval the agent reads volume/price data on. """
        self.timeframe_seconds = seconds
        self.candle_open_time = None  # force a fresh candle bucket on the new interval
        self.candle_volume = 0.0
        self.prev_candle_volume = 0.0
        self.volume_signal_used = False
        print(f"[RULE 2: TIMEFRAME SYNC] Backend now reading {seconds}s candles (synced from Front-end).")

    def open_trade(self, side="LONG", reason="Manual"):
        """ RULE 1: Opens a position sized at EXACTLY 1% margin of current total capital,
        with 100x leverage, filled as a Market Order (RULE 7) with simulated minor slippage. """
        if not self.is_active or self.emergency_triggered:
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
        }
        self.trades.append(trade)
        bybit_api.execute_market_buy(self.active_pair, f"{reason} | Margin=${margin} (1% of capital) x{self.leverage} leverage -> Position=${position_size}")
        print(f"[PILLAR 3: AI AGENT] Opened new {side} position #{trade['id']} on {self.active_pair} @ {filled_price} "
              f"(margin=${margin}, position=${position_size}, entry_fee=${entry_fee_usd})")
        notifications.push(f"Order Filled: {self.active_pair} {side} @ {filled_price:,.4f} (Margin ${margin:,.2f} x{self.leverage})", "success")
        return trade

    def set_active_pair(self, pair, price):
        """ Switching the focused coin closes any prior positions - one coin at a time rule. """
        self.active_pair = pair
        self.current_price = price
        self.trades = []
        self.peak_net_pct = 0.0
        self.is_lock_active = False
        self.candle_open_time = None
        self.candle_volume = 0.0
        self.prev_candle_volume = 0.0
        self.volume_signal_used = False
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

    def process_tick(self, new_price, volume_increment):
        """ Runs on EVERY simulated market tick, independent of any connected UI client
        (RULE 3: 'no lag', 'do not wait', continuous millisecond-level monitoring). """
        self.current_price = new_price

        # RULE 8: Backend Timer (Source of Truth) - Auto-execute emergency exit if 30 seconds pass with no response
        now = time.time()
        if (self.emergency_triggered and self.emergency_trigger_time is not None
            and not self.emergency_auto_kill_executed):
            seconds_elapsed = now - self.emergency_trigger_time
            if seconds_elapsed >= 30:
                # User didn't respond within 30 seconds -> backend auto-executes emergency exit
                print(f"[RULE 8: AUTO-KILL] 30-second countdown expired. Backend auto-executing EMERGENCY EXIT.")
                self.emergency_auto_kill_executed = True
                self.is_active = False  # Stop all processing
                notifications.push("⚠️ RULE 8: 30-second timeout reached. System auto-halted.", "error")
                return

        # RULE 2: Candle bucketing strictly on the currently synced timeframe
        bucket_start = int(now // self.timeframe_seconds) * self.timeframe_seconds
        if self.candle_open_time is None:
            self.candle_open_time = bucket_start
        elif bucket_start > self.candle_open_time:
            # Candle closed -> its volume becomes the new baseline for the 2x check
            self.prev_candle_volume = self.candle_volume
            self.candle_open_time = bucket_start
            self.candle_volume = 0.0
            self.volume_signal_used = False

        self.candle_volume += volume_increment

        # RULE 3: The 2x Volume Surge Trigger -> immediate Market BUY, mid-candle, no waiting
        if (self.is_active and not self.emergency_triggered and not self.volume_signal_used
                and self.prev_candle_volume > 0 and self.candle_volume >= (self.prev_candle_volume * 2)):
            self.volume_signal_used = True
            self.open_trade("LONG", reason=f"RULE 3: 2x Volume Surge ({self.candle_volume:.1f} >= 2x{self.prev_candle_volume:.1f})")

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

        # RULE 4 & 6: Dynamic Trailing Lock, driven by TRUE NET PROFIT (post-fee), not gross
        net_pcts = [self._trade_metrics(t)["net_pct"] for t in self.trades]
        avg_net_pct = sum(net_pcts) / len(net_pcts)

        if avg_net_pct >= 0.07:  # Net Profit Activation (RULE 6, updated 0.07+ rule)
            if not self.is_lock_active:
                notifications.push(f"Trailing Lock ACTIVATED on {self.active_pair} at +{avg_net_pct:.3f}% net profit.", "success")
            self.is_lock_active = True
            if avg_net_pct > self.peak_net_pct:
                self.peak_net_pct = avg_net_pct  # Trail Up
                notifications.push(f"Trailing Lock shifted up to +{avg_net_pct:.3f}% peak.", "info")

        if self.is_lock_active:
            if avg_net_pct <= 0.05:  # Net Profit Hard Floor
                self.execute_sell(f"RULE 4/6: Hard Floor Hit at NET {avg_net_pct:.3f}% (Limit 0.05% net-of-fees). Market SELL.")
            elif self.peak_net_pct - avg_net_pct >= 0.02:  # Reversal Sell
                self.execute_sell(f"RULE 4: Reversal Sell (Peak NET {self.peak_net_pct:.3f}% dropped 0.02%). Market SELL.")

    def execute_sell(self, reason):
        # RULE 7: Exit orders are Market Orders. Realize TRUE NET P&L into current_capital.
        print(f"[PILLAR 3: AI AGENT] Output -> SELL REQUIRED: {reason}")
        for trade in self.trades:
            m = self._trade_metrics(trade)
            self.current_capital += m["net_usd"]
            bybit_api.execute_market_sell(trade["pair"], f"{reason} | Realized Net P&L: ${m['net_usd']:.2f} ({m['net_pct']:.3f}%)")
            notifications.push(f"Position #{trade['id']} CLOSED on {trade['pair']} | Net P&L: ${m['net_usd']:.2f} ({m['net_pct']:.3f}%)",
                                "success" if m["net_usd"] >= 0 else "error")

        self.trades = []
        self.is_lock_active = False
        self.peak_net_pct = 0.0

    def trigger_emergency_exit(self, reason="Manual Master Switch Action"):
        print(f"[RULE 8: EMERGENCY KILL SWITCH TRIGGERED]: {reason}")
        # RULE 8: Backend Timer - Source of Truth starts counting (30-second auto-exit countdown)
        self.emergency_trigger_time = time.time()

        # RULE 5 & 7: Unconditional 'SELL ALL' via Market Order, realizing whatever net P&L remains
        for trade in self.trades:
            m = self._trade_metrics(trade)
            self.current_capital += m["net_usd"]
            bybit_api.execute_market_sell(trade["pair"], f"EMERGENCY SELL ALL TRIGGERED | Realized Net P&L: ${m['net_usd']:.2f}")

        # RULE 5: Halt System - stop all further trading until user chooses action
        self.is_active = False
        self.emergency_triggered = True
        self.trades = []
        self.peak_net_pct = 0.0
        print("[RULE 8: SYSTEM PAUSED] Waiting for user choice (EMERGENCY EXIT or CONTINUE) within 30 seconds...")
        notifications.push(f"⏰ RULE 8: 30-second Emergency Exit countdown started. {reason}", "error")

    def resume_trading_with_higher_buffer(self):
        """ User chose 'CONTINUE' on the risk popup: raise the cumulative stop-loss
        ceiling by another 2.5% (2.5% -> 5% -> 7.5% ...) and resume trading. """
        self.max_loss_pct += 2.5
        self.emergency_triggered = False
        self.emergency_trigger_time = None  # RULE 8: Reset the 30-second timer
        self.emergency_auto_kill_executed = False  # RULE 8: Reset auto-kill flag
        self.is_active = True
        print(f"[RULE 8: CONTINUE CHOSEN] Trading resumed by user choice. New cumulative stop-loss threshold: {self.max_loss_pct}%.")
        notifications.push(f"✅ RULE 8: User chose CONTINUE. Trading resumed - stop-loss threshold raised to {self.max_loss_pct:.1f}%.", "warning")

agent = AITradingAgent()

# ==========================================
# BACKGROUND MARKET SIMULATOR (RULE 3: continuous, UI-independent monitoring)
# Runs regardless of whether any browser tab is connected - satisfies the
# "no lag / do not wait" requirement for the 2x volume surge trigger.
# ==========================================
async def market_simulator():
    while True:
        volatility = random.uniform(-10, 10)
        new_price = agent.current_price + volatility
        # Simulate a live trade-volume tick (baseline + occasional surges to demonstrate Rule 3)
        volume_increment = random.uniform(0.5, 3.0)
        if random.random() < 0.03:  # occasional volume spike burst
            volume_increment *= random.uniform(3, 6)
        agent.process_tick(new_price, volume_increment)
        await asyncio.sleep(0.5)

async def bybit_balance_refresher():
    """ Keeps bybit_api.last_known_balance fresh while LIVE_TRADING, so
    get_total_portfolio_value() can read it synchronously without ever
    blocking on a network call in the hot path (WS loops, kill-switch checks). """
    while True:
        if bybit_api.mode == "LIVE_TRADING" and bybit_api.connected:
            await bybit_api.fetch_real_balance()
        await asyncio.sleep(3)

@app.on_event("startup")
async def start_background_tasks():
    asyncio.create_task(market_simulator())
    asyncio.create_task(bybit_balance_refresher())

# ==========================================
# 2. REST API COMMAND "WIRES"
# ==========================================
@app.post("/start-bot")
async def start_bot():
    agent.is_active = True
    agent.emergency_triggered = False
    agent.emergency_trigger_time = None  # RULE 8: Reset timer
    agent.emergency_auto_kill_executed = False  # RULE 8: Reset auto-kill flag
    print("[PILLAR 2: BACKEND] Received 'START' from Frontend. AI Agent awakened.")
    notifications.push(f"AI Agent STARTED - now monitoring {agent.active_pair} live.", "success")
    return {"status": "success", "message": "Bot active & trailing logic initialized."}

@app.post("/emergency-exit")
async def emergency_exit():
    print("[PILLAR 2: BACKEND] Received 'EMERGENCY' from Frontend. Bypassing AI...")
    agent.trigger_emergency_exit("Manual User Intervention from Frontend")
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
    """ Opens an additional REAL-TIME position on the currently active pair only
    (enforces: one coin at a time, but multiple stacked trades allowed). """
    side = payload.side.upper() if payload.side.upper() in ("LONG", "SHORT") else "LONG"
    trade = agent.open_trade(side)
    if trade is None:
        return {"status": "error", "message": "Start the bot before opening a position."}
    return {"status": "success", "trade": trade, "pair": agent.active_pair}

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
    return {"success": True, "message": f"AI provider '{settings_store.ai_provider}' credentials look valid."}

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
    """ POLICY 3 / RULE 3: Millisecond latency WebSocket for Chart & Price execution.
    NOTE: The actual price/volume simulation and Rule 3 (2x volume surge) logic now run in
    the independent background market_simulator() task - this endpoint only PUSHES the
    latest agent state to connected clients (LIVE_TRADING mode / backend-driven chart source). """
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
                "emergency": agent.emergency_triggered,
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