from fastapi import FastAPI, Header, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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
from dotenv import load_dotenv

from auth import (
    create_session,
    extract_bearer_token,
    get_session_username,
    require_ws_token,
    revoke_token,
    verify_credentials,
    verify_token,
)
from api_secrets import (
    get_taapi_exchange,
    get_taapi_secret,
    get_bybit_testnet_api_key,
    get_bybit_testnet_api_secret,
    get_zai_api_key,
    is_bybit_testnet_configured,
    is_taapi_configured,
    is_zai_configured,
)
from chart_24h import chart_24h_refresh_loop, chart_24h_store
from system_log import system_log
from taapi_scanner import fetch_taapi_signals, evaluate_trade
from bybit_executor import BybitAgent

from pathlib import Path

# Load backend/.env before any credential reads (cwd-safe path).
load_dotenv(Path(__file__).resolve().parent / ".env")

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
# SECURE LOGIN (credentials only in env — never logged or sent to frontend)
# ==========================================
class LoginPayload(BaseModel):
    username: str = ""
    password: str = ""

PUBLIC_HTTP_PATHS = {"/health", "/auth/login", "/docs", "/openapi.json", "/redoc"}

@app.middleware("http")
async def require_auth_middleware(request: Request, call_next):
    path = request.url.path
    if request.method == "OPTIONS" or path in PUBLIC_HTTP_PATHS:
        return await call_next(request)
    token = extract_bearer_token(request.headers.get("Authorization"))
    if not verify_token(token):
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    return await call_next(request)

@app.post("/auth/login")
async def auth_login(payload: LoginPayload):
    if not verify_credentials(payload.username, payload.password):
        print("[AUTH] Failed sign-in attempt.")
        return JSONResponse(status_code=401, content={"message": "Invalid username or password."})
    token = create_session(payload.username.strip())
    print("[AUTH] Session created.")
    return {"token": token, "username": payload.username.strip()}

@app.get("/auth/session")
async def auth_session(authorization: str | None = Header(None)):
    token = extract_bearer_token(authorization)
    username = get_session_username(token)
    if not username:
        return {"authenticated": False}
    return {"authenticated": True, "username": username}

@app.post("/auth/logout")
async def auth_logout(authorization: str | None = Header(None)):
    token = extract_bearer_token(authorization)
    revoke_token(token)
    return {"status": "success", "message": "Signed out."}

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
    Secrets are NEVER logged in plaintext and NEVER echoed back to the frontend.
    Z.ai (GLM-4.5-Flash) is the permanent default AI provider — loaded from
    ZAI_API_KEY in backend/.env or the host environment on every start/reset. """
    def __init__(self):
        self.bybit_api_key = ""
        self.bybit_api_secret = ""
        self.bybit_environment = "mainnet"
        self.ai_provider = "z-ai"
        self.ai_api_key = ""
        self.ai_model = "glm-4.5-flash"
        self.ai_base_url = "https://api.z.ai/api/paas/v4"
        self._load_from_env()

    def _load_from_env(self):
        """ Apply permanent Z.ai defaults + any secrets from .env / Render env vars. """
        zai_key = get_zai_api_key()
        if zai_key:
            self.ai_api_key = zai_key
        self.ai_provider = (os.environ.get("AI_PROVIDER") or "z-ai").strip() or "z-ai"
        self.ai_model = (os.environ.get("ZAI_MODEL") or os.environ.get("AI_MODEL") or "glm-4.5-flash").strip()
        self.ai_base_url = (
            os.environ.get("ZAI_BASE_URL") or os.environ.get("AI_BASE_URL") or "https://api.z.ai/api/paas/v4"
        ).strip().rstrip("/")

        bybit_key = (os.environ.get("BYBIT_API_KEY") or "").strip()
        bybit_secret = (os.environ.get("BYBIT_API_SECRET") or "").strip()
        if bybit_key:
            self.bybit_api_key = bybit_key
        if bybit_secret:
            self.bybit_api_secret = bybit_secret
        env_mode = (os.environ.get("BYBIT_ENVIRONMENT") or "").strip()
        if env_mode in ("mainnet", "testnet"):
            self.bybit_environment = env_mode

        if is_zai_configured():
            print(f"[SETTINGS] Z.ai AI loaded (model={self.ai_model}, provider={self.ai_provider}).")
        else:
            print("[SETTINGS] Z.ai is the default AI provider — set ZAI_API_KEY to enable.")
        if is_taapi_configured():
            print(f"[SETTINGS] TAAPI.io loaded (exchange={get_taapi_exchange()}).")
        else:
            print("[SETTINGS] TAAPI_SECRET not set — pattern scans disabled.")

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
        self.ai_provider = payload.ai_provider or "z-ai"
        self.ai_model = payload.ai_model or "glm-4.5-flash"
        if payload.ai_base_url:
            self.ai_base_url = payload.ai_base_url.rstrip("/")
        elif not self.ai_base_url:
            self.ai_base_url = "https://api.z.ai/api/paas/v4"

    def reset(self):
        self.__init__()
        # __init__ already re-applies Z.ai defaults + env secrets.

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
            "taapi_configured": is_taapi_configured(),
            "taapi_exchange": get_taapi_exchange(),
            "bybit_testnet_configured": is_bybit_testnet_configured(),
        }

settings_store = SettingsStore()

# ==========================================
# AI PROVIDER: settings connectivity test (OpenAI-compatible chat completions)
# ==========================================
# Per-provider defaults - only the API key is mandatory; base_url/model can
# be overridden from the Settings form. Azure OpenAI has no universal base
# URL (it's resource-specific), so it always requires ai_base_url to be set.
AI_PROVIDER_DEFAULTS = {
    "z-ai": {"base_url": "https://api.z.ai/api/paas/v4", "model": "glm-4.5-flash", "auth_header": "bearer"},
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini", "auth_header": "bearer"},
    "zhipu-glm": {"base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4.5-flash", "auth_header": "bearer"},
    "azure-openai": {"base_url": None, "model": "gpt-4o-mini", "auth_header": "api-key"},
    "custom": {"base_url": None, "model": "glm-4.5-flash", "auth_header": "bearer"},
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
        # Bybit USDT perpetual standard taker fee (0.055% per market fill).
        self.taker_fee_pct = 0.055

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

    def _base_url(self):
        return (
            "https://api-testnet.bybit.com"
            if settings_store.bybit_environment == "testnet"
            else "https://api.bybit.com"
        )

    def _auth_headers(self, query_string):
        timestamp = str(int(time.time() * 1000))
        recv_window = "5000"
        return {
            "X-BAPI-API-KEY": settings_store.bybit_api_key,
            # _sign() computes HMAC-SHA256 (hex), not RSA - sign-type must say "2" ONLY
            # when actually RSA-signing (base64 output). Sending "2" here while
            # signing with HMAC tells Bybit to verify against the wrong algorithm
            # entirely, which fails auth regardless of whether the key/secret are
            # correct - was hardcoded wrong, always claiming RSA.
            "X-BAPI-SIGN-TYPE": "1",
            "X-BAPI-SIGN": self._sign(timestamp, recv_window, query_string),
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": recv_window,
        }

    async def _get_outbound_ip(self):
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                resp = await client.get("https://api.ipify.org?format=json")
            if resp.status_code == 200:
                return resp.json().get("ip")
        except Exception:
            pass
        return None

    async def _format_http_error(self, status_code, resp):
        body_text = (resp.text or "").strip()
        ret_msg = None
        if body_text:
            try:
                payload = resp.json()
                ret_msg = payload.get("retMsg") or payload.get("message")
            except Exception:
                ret_msg = body_text[:200]

        if status_code == 401:
            return "Invalid API key or secret (Bybit returned 401 Unauthorized)."

        if status_code == 403:
            outbound_ip = await self._get_outbound_ip()
            parts = ["Bybit returned HTTP 403 (Forbidden)."]
            if ret_msg:
                parts.append(str(ret_msg))
            parts.append(
                "The API call is made from your backend server, not your browser. "
                "In Bybit → API Management → Edit key → add that server's public IP or choose 'No IP restriction'."
            )
            if outbound_ip:
                parts.append(f"Backend outbound IP: {outbound_ip} (whitelist this in Bybit).")
            if settings_store.bybit_environment == "testnet":
                parts.append("Testnet keys must be created at testnet.bybit.com and Environment must be Testnet.")
            else:
                parts.append("Mainnet keys must be created at bybit.com and Environment must be Mainnet.")
            return " ".join(parts)

        if ret_msg:
            return f"Bybit API HTTP {status_code}: {ret_msg}"
        return f"Bybit API returned HTTP {status_code}."

    def _format_ret_error(self, data, outbound_ip=None):
        ret_code = data.get("retCode")
        ret_msg = data.get("retMsg", "Unknown Bybit API error.")
        if ret_code in (10007, 10010, 10024):
            hint = (
                " IP whitelist mismatch — add your backend server's public IP in Bybit API Management "
                "or disable IP restriction."
            )
            if outbound_ip:
                hint += f" Backend outbound IP: {outbound_ip}."
            return f"{ret_msg} (code {ret_code}).{hint}"
        return ret_msg

    async def fetch_real_balance(self):
        """ RULE 5 wiring: pull the REAL unified-account total equity from Bybit's v5 API.
        Used both by 'Test Bybit' (to actually verify credentials) and by the background
        refresher that keeps total_capital showing the live account balance once connected.
        Returns the equity as a float, or None on any failure (network/auth/parsing). """
        if not settings_store.is_bybit_configured():
            self.last_error = "No Bybit API Key/Secret configured."
            return None

        try:
            for account_type in ("UNIFIED", "SPOT"):
                query_string = f"accountType={account_type}"
                headers = self._auth_headers(query_string)
                async with httpx.AsyncClient(timeout=8.0) as client:
                    resp = await client.get(
                        f"{self._base_url()}/v5/account/wallet-balance?{query_string}",
                        headers=headers,
                    )

                if resp.status_code == 401:
                    self.last_error = "Invalid API key/secret (Bybit returned 401 Unauthorized)."
                    self._note_failure()
                    return None
                if resp.status_code == 403:
                    self.last_error = await self._format_http_error(403, resp)
                    self._note_failure()
                    return None
                if resp.status_code != 200:
                    self.last_error = await self._format_http_error(resp.status_code, resp)
                    self._note_failure()
                    return None

                data = resp.json()
                if data.get("retCode") != 0:
                    if account_type == "SPOT":
                        outbound_ip = await self._get_outbound_ip()
                        self.last_error = self._format_ret_error(data, outbound_ip)
                        self._note_failure()
                        return None
                    continue

                account_list = data.get("result", {}).get("list", [])
                if not account_list:
                    if account_type == "SPOT":
                        self.last_error = "Bybit returned no account data for this key."
                        self._note_failure()
                        return None
                    continue

                equity = float(account_list[0]["totalEquity"])
                self.last_known_balance = equity
                self.last_error = None
                if self.mode == "LIVE_TRADING":
                    agent.current_capital = equity
                if self._was_failing:
                    notifications.push("Bybit connection restored - live balance is syncing again.", "success")
                self._was_failing = False
                return equity

            self.last_error = "Bybit returned no account data for this key."
            self._note_failure()
            return None
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
            print(f"👉 [PAPER TRADING - VIRTUAL] Bybit API -> Market SELL {pair} -> {reason}")
        else:
            print(f"🔥 [REAL LIVE TRADING - ACTUAL] Bybit REST API -> MARKET SELL {pair} -> {reason}")

    def execute_market_open(self, pair, side, reason):
        """ Open a position: LONG = market buy, SHORT/inverse = market sell (Bybit linear). """
        if side == "SHORT":
            self.execute_market_sell(pair, f"OPEN SHORT | {reason}")
        else:
            self.execute_market_buy(pair, f"OPEN LONG | {reason}")

    def execute_market_close(self, pair, side, reason):
        """ Close a position: LONG exit = sell, SHORT exit = buy to cover. """
        if side == "SHORT":
            self.execute_market_buy(pair, f"CLOSE SHORT | {reason}")
        else:
            self.execute_market_sell(pair, f"CLOSE LONG | {reason}")

bybit_api = BybitAPIWrapper()

# ==========================================
# PILLAR 3: CORE AI AGENT LOGIC (State & Rules)
# ==========================================
class AITradingAgent:
    # Net-profit floor per chart TF (after 0.055% taker fee each leg). 1M starts at 0.20%+.
    TRAILING_DROP_PCT = 0.30
    PROFIT_FLOOR_BY_TIMEFRAME = {
        "1m": 0.20,
        "5m": 0.40,
        "15m": 0.60,
        "1h": 0.80,
        "1D": 1.0,
    }
    PROFIT_FLOOR_PCT = 0.20      # default fallback (1m chart)

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

        # Pre-start strategy config (AI Agent Instructions modal before START).
        self.starting_capital = 1000.0
        self.current_capital = self.starting_capital
        # Risk % from modal -> max_concurrent_trades via round(risk_pct * 1.5). Not a stop-loss.
        self.risk_level_pct = 3.0
        self.max_concurrent_trades = 5
        # AI Agent Instructions modal: optional "Capital profit of the day" target.
        # 0.0 means disabled. Once the day's profit % crosses this, new entries are
        # halted (existing positions keep being managed by the trailing lock).
        self.daily_profit_target_pct = 0.0
        self.daily_target_reached = False
        # AI Season: profit tracked from START until STOP.
        self.ai_season_start_capital = None

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
        self.trade_history = []  # session list (active + sold), cleared only on START/STOP

        # Chart-only timeframe (kept so the frontend's /set-timeframe call still
        # works). No auto-entry policy currently reads this - see auto_buy_loop().
        self.timeframe_seconds = 60

    def get_profit_floor_pct(self):
        """ Net-profit floor for trailing lock / profit booking on the active chart TF. """
        key = SECONDS_TO_TIMEFRAME_KEY.get(self.timeframe_seconds, "1m")
        return self.PROFIT_FLOOR_BY_TIMEFRAME.get(key, self.PROFIT_FLOOR_PCT)

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

    def _append_trade_history(self, trade):
        self.trade_history.append({
            "id": trade["id"],
            "pair": trade["pair"],
            "side": trade["side"],
            "entry": trade["entry"],
            "current": trade["entry"],
            "margin": trade["margin"],
            "position_size": trade["position_size"],
            "pnl": 0.0,
            "gross_pnl_pct": 0.0,
            "net_pnl_usd": 0.0,
            "entry_fee_usd": trade["entry_fee_usd"],
            "exit_fee_usd": 0.0,
            "status": "active",
            "closed_reason": None,
            "source": trade.get("source", "auto"),
            "protected": trade.get("source") == "manual",
        })

    def _finalize_trade_history(self, trade, metrics, reason):
        for row in self.trade_history:
            if row["id"] == trade["id"]:
                row["current"] = round(self.current_price, 4)
                row["pnl"] = round(metrics["net_pct"], 4)
                row["gross_pnl_pct"] = round(metrics["gross_pct"], 4)
                row["net_pnl_usd"] = round(metrics["net_usd"], 2)
                row["exit_fee_usd"] = round(metrics["exit_fee_usd"], 4)
                row["status"] = "sold"
                row["closed_reason"] = reason
                return

    def get_unrealized_net_usd(self):
        return sum(self._trade_metrics(t)["net_usd"] for t in self.trades)

    def get_trading_capital_base(self):
        """ Capital used for position sizing. LIVE -> Bybit equity; paper -> simulated ledger. """
        if bybit_api.mode == "LIVE_TRADING":
            if bybit_api.last_known_balance is None:
                return None
            return max(0.0, float(bybit_api.last_known_balance))
        return self.current_capital

    def on_live_connected(self, equity: float):
        """ Paper credentials sleep: clear simulated state and bind ledger to Bybit equity. """
        self.trades = []
        self.trade_history = []
        self.current_capital = equity
        self.starting_capital = equity
        self.ai_season_start_capital = None
        self.is_active = False
        self.daily_target_reached = False
        self.is_lock_active = False
        self.peak_net_pct = 0.0
        self.clear_emergency_state()
        print(f"[LIVE SYNC] Paper state cleared. Bybit equity ${equity:,.2f} is now the account baseline.")
        notifications.push(f"Live account synced from Bybit: ${equity:,.2f} equity. Paper simulation paused.", "success")

    def get_total_portfolio_value(self):
        """ RULE 5: Total Portfolio Value.
        PAPER_TRADING (default) -> simulated capital + unrealized P&L of open (simulated) positions.
        LIVE_TRADING -> the REAL Bybit account equity, refreshed in the background by
        bybit_api.fetch_real_balance(). Falls back to the simulated value until the first
        successful read comes back, so the UI never shows a blank/zero balance mid-switch. """
        if bybit_api.mode == "LIVE_TRADING" and bybit_api.last_known_balance is not None:
            return bybit_api.last_known_balance
        return self.current_capital + self.get_unrealized_net_usd()

    def _live_insufficient_balance(self) -> bool:
        if bybit_api.mode != "LIVE_TRADING":
            return False
        base = self.get_trading_capital_base()
        return base is None or base <= 0

    def get_session_baseline(self):
        """ Baseline for RULE 5/8 loss % while AI is running: the portfolio value at
        AI season start. Falls back to paper starting_capital when no season is active. """
        if self.ai_season_start_capital is not None:
            return self.ai_season_start_capital
        return self.starting_capital

    def begin_ai_season(self):
        """ Called when START AI AUTOMATION fires — resets season P&L and kill-switch baseline. """
        self.ai_season_start_capital = self.get_total_portfolio_value()
        print(f"[AI SEASON] Started. Baseline ${self.ai_season_start_capital:,.2f} (season profit + stop-loss tracking).")

    def end_ai_season(self):
        """ Called on STOP / Emergency Exit — clears season profit and kill-switch baseline. """
        if self.ai_season_start_capital is not None:
            print("[AI SEASON] Ended. Season profit tracking reset.")
        self.ai_season_start_capital = None

    def clear_emergency_state(self):
        """ Fully clears RULE 8 emergency flags so the popup cannot re-fire on the next start. """
        self.emergency_triggered = False
        self.emergency_awaiting_decision = False
        self.emergency_trigger_time = None
        self.emergency_auto_kill_executed = False

    def set_paper_capital(self, amount):
        """ Resets the simulated PAPER_TRADING balance to a new starting amount.
        Only allowed while the agent is still in PAPER_TRADING mode (never touches real funds). """
        self.starting_capital = amount
        self.current_capital = amount
        self.trades = []
        self.trade_history = []
        self.is_lock_active = False
        self.peak_net_pct = 0.0
        print(f"[PILLAR 3: AI AGENT] Paper trading capital reset to ${amount:,.2f}.")

    def set_timeframe(self, seconds):
        """ Frontend -> backend timeframe sync. Drives the TAAPI candle-pattern
        auto-buy loop's polling interval. A candle's close_time isn't
        comparable across different candle granularities, so a timeframe
        change clears every pair's last-processed timestamp - otherwise the
        loop could wrongly treat the new timeframe's current candle as
        "already seen" and skip it until the next boundary. """
        self.timeframe_seconds = seconds
        LAST_CANDLE_TIMESTAMPS.clear()
        print(f"[TIMEFRAME SYNC] Backend timeframe set to {seconds}s.")

    def open_trade(self, side="LONG", reason="Manual", source="auto", position_size_usd=None, qty=None):
        """ RULE 1: Opens a position as a Market Order (RULE 7) with simulated minor slippage.
        Manual entries default to 1% margin x 100x leverage. Auto entries pass
        `position_size_usd` + `qty` from compute_auto_trade_plan() (2% of capital). """
        if self.emergency_triggered:
            return None

        if source == "manual":
            # Manual trading is the opposite of automation - only allowed while the bot
            # is NOT running (START AI AUTOMATION has not been clicked / was stopped).
            if self.is_active:
                return None
        else:
            if not self.is_active:
                return None
            if self.daily_target_reached:
                return None

        # AI Agent Instructions modal: cap stacked positions at max_concurrent_trades.
        if len(self.trades) >= self.max_concurrent_trades:
            notifications.push(
                f"Max concurrent trades ({self.max_concurrent_trades}) reached on {self.active_pair} - new entry skipped.",
                "info",
            )
            return None

        if self.current_price <= 0:
            print(f"[PILLAR 3: AI AGENT] Skipping entry — invalid current_price ({self.current_price}) on {self.active_pair}.")
            return None

        capital_base = self.get_trading_capital_base()
        if bybit_api.mode == "LIVE_TRADING":
            if capital_base is None:
                notifications.push("Waiting for Bybit balance sync — please try again shortly.", "warning")
                return None
            if capital_base <= 0:
                notifications.push("Insufficient balance: Bybit account equity is $0.00.", "error")
                return None

        # RULE 1: 1% margin, 100x leverage for manual; auto paper may pass a fixed notional.
        if position_size_usd is not None:
            position_size = round(float(position_size_usd), 2)
            margin = round(position_size / self.leverage, 2)
        else:
            margin = round((capital_base if capital_base is not None else self.current_capital) * self.margin_pct, 2)
            position_size = round(margin * self.leverage, 2)
        if margin <= 0 or position_size <= 0:
            notifications.push("Insufficient balance to open a position.", "error")
            return None

        # RULE 7: Market orders fill with minor slippage vs the requested price
        slippage = random.uniform(-0.0002, 0.0002)
        filled_price = round(self.current_price * (1 + slippage), 4)

        if qty is None and position_size_usd is not None:
            qty = compute_order_qty(position_size_usd, filled_price)

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
            "qty": qty,
            "entry_fee_pct": entry_fee_pct,
            "entry_fee_usd": entry_fee_usd,
            "source": source,
            "peak_net_pct": 0.0,
            "is_lock_active": False,
        }
        self.trades.append(trade)
        self._append_trade_history(trade)
        qty_label = f" | qty={qty}" if qty is not None else ""
        bybit_api.execute_market_open(
            self.active_pair,
            side,
            f"{reason} | ${position_size} notional ({margin} margin x{self.leverage}){qty_label}",
        )
        print(f"[PILLAR 3: AI AGENT] Opened new {side} position #{trade['id']} on {self.active_pair} @ {filled_price} "
              f"(margin=${margin}, position=${position_size}, qty={qty}, entry_fee=${entry_fee_usd}, source={source})")
        qty_note = f" | {qty} coins" if qty is not None else ""
        if source == "auto" and position_size_usd is not None:
            inverse_note = " inverse SHORT" if side == "SHORT" else ""
            fill_msg = (
                f"Order Filled:{inverse_note} {self.active_pair} {side} @ {filled_price:,.4f} "
                f"(${position_size:,.2f} = {AUTO_TRADE_CAPITAL_PCT * 100:.0f}% of capital{qty_note})"
            )
        else:
            fill_msg = (
                f"Order Filled: {self.active_pair} {side} @ {filled_price:,.4f} "
                f"(Margin ${margin:,.2f} x{self.leverage}{qty_note})"
            )
        notifications.push(fill_msg, "success")
        return trade

    def manual_close_best(self, reason="Manual SELL button"):
        """ Manual SELL button: closes exactly ONE position among the manually-opened
        trades (never touches auto trades) - specifically whichever manual trade
        currently has the highest True Net Profit (or smallest loss if all underwater).
        Only allowed while automation is OFF, matching the ControlBar UI. """
        if self.is_active:
            return None
        manual_trades = [t for t in self.trades if t.get("source") == "manual"]
        if not manual_trades:
            return None

        best = max(manual_trades, key=lambda t: self._trade_metrics(t)["net_pct"])
        m = self._trade_metrics(best)
        self._close_single_trade(best, m, reason)
        self.trades = [t for t in self.trades if t["id"] != best["id"]]
        print(f"[PILLAR 3: AI AGENT] Manual SELL closed position #{best['id']} on {best['pair']} "
              f"(net_pct={m['net_pct']:.3f}%, net_usd=${m['net_usd']:.2f})")
        return best

    def set_active_pair(self, pair, price):
        """ Switch pair for chart/trading while keeping open positions intact. """
        pair_changed = pair != self.active_pair
        self.active_pair = pair
        self.current_price = price
        if pair_changed:
            self.peak_net_pct = 0.0
            self.is_lock_active = False
            print(f"[PILLAR 3: AI AGENT] Active pair switched to {pair}. Open positions preserved.")
        else:
            print(f"[PILLAR 3: AI AGENT] Active pair refreshed to {pair} @ {price}.")

    def get_trades_snapshot(self):
        """Live trade list for the active panel. Completed trades are hidden here.

        This ensures that once the bot closes all active positions on STOP,
        the frontend trade window clears immediately instead of still showing
        sold positions from the session history.
        """
        snapshot = []
        for trade in self.trades:
            m = self._trade_metrics(trade)
            out = dict(trade)
            out.update({
                "current": round(self.current_price, 4),
                "gross_pnl_pct": round(m["gross_pct"], 4),
                "pnl": round(m["net_pct"], 4),
                "net_pnl_usd": round(m["net_usd"], 2),
                "exit_fee_usd": round(m["exit_fee_usd"], 4),
                "status": "locked" if trade.get("is_lock_active") else "active",
            })
            snapshot.append(out)
        return snapshot

    async def process_tick(self, new_price, volume_increment):
        """ Updates live price, clears corrupt entries, and books profit per open
        auto trade (LONG + inverse SHORT) via the chart-timeframe floor + 30% trail. """
        clean_price = _sanitize_market_price(new_price)
        if clean_price is None:
            print(f"[PILLAR 3: AI AGENT] Ignoring invalid market tick: {new_price!r}")
            return
        self.current_price = clean_price

        corrupt = [t for t in self.trades if t.get("entry", 0) <= 0]
        if corrupt:
            self.trades = [t for t in self.trades if t.get("entry", 0) > 0]
            notifications.push(
                f"Removed {len(corrupt)} corrupt position(s) with invalid entry prices on {self.active_pair}.",
                "warning",
            )

        if not self.is_active or not self.trades:
            return

        floor = self.get_profit_floor_pct()
        still_open = []
        for trade in self.trades:
            if trade.get("source") == "manual":
                still_open.append(trade)
                continue

            m = self._trade_metrics(trade)
            net = m["net_pct"]
            peak = float(trade.get("peak_net_pct", 0.0))
            locked = bool(trade.get("is_lock_active", False))

            if net >= floor:
                if not locked:
                    trade["is_lock_active"] = True
                    notifications.push(
                        f"Trailing lock ON #{trade['id']} {trade['side']} {trade['pair']} @ +{net:.3f}% net",
                        "success",
                    )
                    locked = True
                if net > peak:
                    trade["peak_net_pct"] = net
                    peak = net

                trailing_target = peak * (1 - self.TRAILING_DROP_PCT)
                floor_override = trailing_target < floor
                effective_target = floor if floor_override else trailing_target

                if locked and peak >= floor and net < peak and net <= effective_target:
                    if floor_override:
                        reason = (
                            f"Profit book #{trade['id']}: floor {floor:.2f}% net "
                            f"(peak {peak:.3f}%, trail would breach floor)"
                        )
                    else:
                        reason = (
                            f"Profit book #{trade['id']}: 30% trail "
                            f"(peak {peak:.3f}% -> target {effective_target:.3f}%)"
                        )
                    self._close_single_trade(trade, m, reason)
                    continue

            still_open.append(trade)

        self.trades = still_open
        locked_trades = [t for t in self.trades if t.get("is_lock_active")]
        self.is_lock_active = bool(locked_trades)
        self.peak_net_pct = max((float(t.get("peak_net_pct", 0)) for t in locked_trades), default=0.0)

    def _close_single_trade(self, trade, metrics, reason):
        """ Realize P&L and close one position (LONG sell / SHORT buy-to-cover). """
        self.current_capital += metrics["net_usd"]
        self._finalize_trade_history(trade, metrics, reason)
        bybit_api.execute_market_close(
            trade["pair"],
            trade["side"],
            f"{reason} | Realized Net P&L: ${metrics['net_usd']:.2f} ({metrics['net_pct']:.3f}%)",
        )
        print(
            f"[PILLAR 3: AI AGENT] Closed {trade['side']} #{trade['id']} on {trade['pair']} "
            f"| net=${metrics['net_usd']:.2f} ({metrics['net_pct']:.3f}%)"
        )
        notifications.push(
            f"Position #{trade['id']} CLOSED ({trade['side']}) {trade['pair']} | "
            f"Net P&L: ${metrics['net_usd']:.2f} ({metrics['net_pct']:.3f}%)",
            "success" if metrics["net_usd"] >= 0 else "error",
        )

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
        # Manual positions stay protected — only AI-opened winners may auto-close.
        auto_winners = [(t, m) for (t, m) in scored if m["net_pct"] > 0 and t.get("source") != "manual"]
        held = [t for (t, m) in scored if m["net_pct"] <= 0 or t.get("source") == "manual"]

        if not auto_winners:
            print("[PILLAR 3: AI AGENT] No auto trades in net profit to close — manual positions protected, losers held.")
            return

        for trade, m in auto_winners:
            self._close_single_trade(trade, m, reason)

        self.trades = held

        # Recompute trailing-lock state off whatever remains.
        if not self.trades:
            self.is_lock_active = False
            self.peak_net_pct = 0.0
        else:
            remaining = [self._trade_metrics(t)["net_pct"] for t in self.trades]
            new_avg = sum(remaining) / len(remaining)
            if new_avg < self.get_profit_floor_pct():
                # Everything left is under the floor - drop the lock until the held
                # positions climb back above the chart floor and start a fresh trail.
                self.is_lock_active = False
                self.peak_net_pct = 0.0
            else:
                self.peak_net_pct = min(self.peak_net_pct, new_avg)

    def _close_all_positions(self, reason):
        """ Unconditional close all — LONG (sell) and SHORT (buy to cover). """
        for trade in list(self.trades):
            m = self._trade_metrics(trade)
            self._close_single_trade(trade, m, reason)
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
        initial stop-loss detection. Fully resets emergency + AI season so the popup
        cannot immediately re-fire on the next page load or restart. """
        print("[RULE 8: EMERGENCY EXIT CONFIRMED] Selling all positions and halting.")
        self._close_all_positions("EMERGENCY SELL ALL TRIGGERED | RULE 8 confirmed by user")
        self.is_active = False
        self.clear_emergency_state()
        self.end_ai_season()
        self.peak_net_pct = 0.0
        self.is_lock_active = False

    def manual_stop(self, reason="Manual Kill Switch Activated from Frontend"):
        """ Plain STOP TRADING button — emergency exit: sell all open positions and halt AI. """
        print(f"[PILLAR 2: BACKEND] {reason}")
        self._close_all_positions(reason)
        self.is_active = False
        self.end_ai_season()
        self.peak_net_pct = 0.0
        self.is_lock_active = False
        system_log.push("ai", "AI automation STOPPED — emergency exit, all positions closed.", {"reason": reason})
        notifications.push("EMERGENCY EXIT: All positions closed and AI automation stopped.", "error")

    def resume_trading_after_emergency(self):
        """ Legacy continue endpoint — portfolio stop-loss removed; clears halt flags only. """
        self.clear_emergency_state()
        self.is_active = True
        print("[AI AGENT] Trading resumed (portfolio stop-loss disabled).")
        notifications.push("Trading resumed.", "warning")

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
BYBIT_SYMBOL_MAP = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "XRP": "XRPUSDT",
    "LTC": "LTCUSDT",
    # XMR intentionally has NO mapping: delisted from both Bybit and Binance
    # spot (verified live 2026-07-08 - "Not supported symbols" / last Binance
    # candle Feb 2024). The pair stays selectable in the UI, but with no
    # mapping the synthetic market_simulator drives its price and the TAAPI
    # auto-buy loop skips it entirely (no real market -> no real trades).
}

def get_bybit_symbol(pair_label):
    symbol = (pair_label or "").split("/")[0]
    return BYBIT_SYMBOL_MAP.get(symbol)

# ==========================================
# TAAPI CANDLE-PATTERN POLICY: interval translation tables.
# Three different "interval" vocabularies have to agree here:
#  - agent.timeframe_seconds: raw seconds, set by the frontend's chart timeframe
#    selector (1M/5M/15M/1H/1D).
#  - TIMEFRAME_RULES keys (taapi_scanner.py / SYSTEM_RULES.md): "30s".."1D" -
#    includes 3m/10m/30m, which nothing on the frontend can select yet. TAAPI's
#    own query interval is translated FROM these keys inside taapi_scanner
#    (TAAPI_INTERVAL_MAP) - callers only ever deal in TIMEFRAME_RULES keys.
#  - Bybit kline's `interval` param: plain minute numbers ("1","5","60") or "D" -
#    no native 30s or 10m candle, so those fall back to the nearest one available.
# ==========================================
SECONDS_TO_TIMEFRAME_KEY = {
    30: "30s",
    60: "1m",
    300: "5m",
    900: "15m",
    3600: "1h",
    86400: "1D",
}

TIMEFRAME_KEY_TO_BYBIT_KLINE = {
    "30s": "1", "1m": "1", "3m": "3", "5m": "5", "10m": "5",
    "15m": "15", "30m": "30", "1h": "60", "1D": "D",
}

TIMEFRAME_KEY_TO_SECONDS = {
    "30s": 30, "1m": 60, "3m": 180, "5m": 300, "10m": 600,
    "15m": 900, "30m": 1800, "1h": 3600, "1D": 86400,
}

# Per-pair last-processed CLOSED candle timestamp, keyed by pair label - keeping
# this per-pair (not one shared scalar) means switching pairs never needs a
# manual reset: a pair's own last-seen timestamp is either genuinely stale
# (correctly triggers a re-scan) or doesn't exist yet (defaults to 0). A
# TIMEFRAME change is the only case that needs an explicit reset (see
# set_timeframe below) since a candle's close_time isn't comparable across
# different candle granularities.
LAST_CANDLE_TIMESTAMPS = {}

# Pairs already warned about a failing candle fetch (e.g. no Bybit LINEAR/USDT
# Perpetual market for that symbol - several of BYBIT_SYMBOL_MAP's smaller-cap
# tokens may only have a SPOT listing). Warns once per pair instead of every
# failed poll cycle, so a genuinely-unsupported pair doesn't spam the bell.
_CANDLE_FETCH_WARNED_PAIRS = set()

async def fetch_closed_candle_ohlc(bybit_symbol, timeframe_key):
    """ Reads the last 2 klines from Bybit's LINEAR (USDT Perpetual) market -
    matching where bybit_executor.py actually places orders, not the spot
    feed the dashboard's price simulation uses - and returns the previous,
    fully closed candle (index 0 is still forming). Native httpx/async so it
    never blocks the event loop (unlike pybit's sync client). """
    bybit_interval = TIMEFRAME_KEY_TO_BYBIT_KLINE.get(timeframe_key, "1")
    url = (
        f"https://api.bybit.com/v5/market/kline?category=linear"
        f"&symbol={bybit_symbol}&interval={bybit_interval}&limit=2"
    )
    async with httpx.AsyncClient(timeout=6.0) as client:
        resp = await client.get(url)
    resp.raise_for_status()
    candles = resp.json()["result"]["list"]  # newest-first
    closed = candles[1]
    # Bybit's kline row is [startTime, open, high, low, close, volume, turnover] -
    # startTime doubles as a unique, strictly-increasing per-candle id.
    return {"high": float(closed[2]), "low": float(closed[3]), "close_time": int(closed[0])}


async def fetch_recent_closed_kline_volumes(bybit_symbol: str, timeframe_key: str, closed_count: int = 3):
    """Last N fully closed LINEAR klines (newest first) with total volume."""
    bybit_interval = TIMEFRAME_KEY_TO_BYBIT_KLINE.get(timeframe_key, "1")
    limit = closed_count + 1  # index 0 is still forming
    url = (
        f"https://api.bybit.com/v5/market/kline?category=linear"
        f"&symbol={bybit_symbol}&interval={bybit_interval}&limit={limit}"
    )
    async with httpx.AsyncClient(timeout=6.0) as client:
        resp = await client.get(url)
    resp.raise_for_status()
    candles = resp.json()["result"]["list"]
    out = []
    for row in candles[1 : closed_count + 1]:
        out.append({
            "start_time": int(row[0]),
            "volume": float(row[5]),
            "turnover": float(row[6]) if len(row) > 6 else 0.0,
        })
    return out


async def fetch_candle_taker_volumes(bybit_symbol: str, candle_start_ms: int, duration_sec: int):
    """Sum taker buy/sell base-asset size inside one closed candle window."""
    end_ms = candle_start_ms + duration_sec * 1000
    url = (
        f"https://api.bybit.com/v5/market/recent-trade?category=linear"
        f"&symbol={bybit_symbol}&limit=1000"
    )
    async with httpx.AsyncClient(timeout=6.0) as client:
        resp = await client.get(url)
    resp.raise_for_status()
    trades = resp.json().get("result", {}).get("list", [])
    buy_vol = 0.0
    sell_vol = 0.0
    for trade in trades:
        ts = int(trade.get("time", 0))
        if ts < candle_start_ms:
            break
        if candle_start_ms <= ts < end_ms:
            size = float(trade.get("size", 0))
            if trade.get("side") == "Buy":
                buy_vol += size
            else:
                sell_vol += size
    return {
        "buy_volume": buy_vol,
        "sell_volume": sell_vol,
        "total_taker_volume": buy_vol + sell_vol,
    }


async def analyze_signal_candle_volume(bybit_symbol: str, timeframe_key: str) -> dict:
    """After TAAPI BUY/SELL: signal candle volume must exceed both prior closed candles."""
    duration_sec = TIMEFRAME_KEY_TO_SECONDS.get(timeframe_key, 60)
    candles = await fetch_recent_closed_kline_volumes(bybit_symbol, timeframe_key, closed_count=3)
    if len(candles) < 3:
        return {
            "passed": False,
            "reason": f"Need 3 closed candles, got {len(candles)}.",
            "candles": candles,
        }

    signal, prev1, prev2 = candles[0], candles[1], candles[2]
    taker = await fetch_candle_taker_volumes(bybit_symbol, signal["start_time"], duration_sec)
    signal_vol = signal["volume"]
    prev1_vol = prev1["volume"]
    prev2_vol = prev2["volume"]
    passed = signal_vol > prev1_vol and signal_vol > prev2_vol

    if passed:
        reason = (
            f"Signal vol {signal_vol:.4f} > prev-1 ({prev1_vol:.4f}) "
            f"and prev-2 ({prev2_vol:.4f})"
        )
    else:
        reason = (
            f"Signal vol {signal_vol:.4f} not above both prior candles "
            f"({prev1_vol:.4f}, {prev2_vol:.4f}) — trade blocked"
        )

    return {
        "passed": passed,
        "reason": reason,
        "signal_candle": {
            "start_time": signal["start_time"],
            "kline_volume": signal_vol,
            "buy_volume": taker["buy_volume"],
            "sell_volume": taker["sell_volume"],
            "turnover": signal.get("turnover", 0),
        },
        "prev_candle_1": {"start_time": prev1["start_time"], "kline_volume": prev1_vol},
        "prev_candle_2": {"start_time": prev2["start_time"], "kline_volume": prev2_vol},
    }


# TAAPI auto-order sizing: 2% of total portfolio value per fired trade.
# Example: $1,000 capital -> $20 position -> ~0.00021 BTC @ $97,000.
AUTO_TRADE_CAPITAL_PCT = 0.02


def qty_decimals_for_price(price: float) -> int:
    """ Precision for base-asset qty — BTC-sized prices need extra decimals. """
    if price >= 10000:
        return 6
    if price >= 1000:
        return 5
    if price >= 1:
        return 4
    return 2


def compute_auto_trade_plan(agent, price: float | None = None) -> dict | None:
    """ Single source of truth for TAAPI auto entries.
    Returns USD notional (2% of total capital), base-asset qty, and margin. """
    entry_price = _sanitize_market_price(price if price is not None else agent.current_price)
    if entry_price is None:
        return None
    total = agent.get_total_portfolio_value()
    if total is None or total <= 0:
        return None
    position_usd = round(total * AUTO_TRADE_CAPITAL_PCT, 2)
    if position_usd <= 0:
        return None
    decimals = qty_decimals_for_price(entry_price)
    qty = round(position_usd / entry_price, decimals)
    if qty <= 0:
        return None
    margin = round(position_usd / agent.leverage, 4)
    return {
        "total_capital": round(total, 2),
        "position_usd": position_usd,
        "capital_pct": AUTO_TRADE_CAPITAL_PCT * 100,
        "qty": qty,
        "qty_decimals": decimals,
        "margin": margin,
        "price": entry_price,
    }


def compute_auto_trade_notional_usd(agent) -> float | None:
    """ Back-compat wrapper — prefer compute_auto_trade_plan(). """
    plan = compute_auto_trade_plan(agent)
    return plan["position_usd"] if plan else None


def compute_order_qty(position_size_usd, current_price, qty_decimals=None):
    """ Converts a USD notional into base-asset qty for pybit place_order(). """
    if not current_price or current_price <= 0:
        return None
    if qty_decimals is None:
        qty_decimals = qty_decimals_for_price(current_price)
    qty = round(position_size_usd / current_price, qty_decimals)
    return qty if qty > 0 else None


def _log_trade_skip(action: str, symbol: str, pattern: str | None, reason: str, **extra):
    """ Surface silent auto-entry failures in the System Log modal. """
    system_log.set_last_trade_fire({
        "success": False,
        "action": action,
        "symbol": symbol,
        "pattern": pattern,
        "error": reason,
        **extra,
    })
    system_log.push("trade", f"SKIPPED: {action} {symbol} — {reason}", {"pattern": pattern, **extra})
    print(f"[AUTO BUY LOOP] Trade skipped: {reason}")

_bybit_executor_agent = None
_bybit_testnet_keys_warned = False

def get_bybit_executor_agent():
    """ Lazily builds the real-order-placing BybitAgent from TESTNET-only
    credentials, deliberately kept SEPARATE from settings_store's Bybit
    credentials (those are the dashboard's mainnet balance-reading keys).
    Testnet requires its own key pair from testnet.bybit.com - mixing
    testnet/mainnet keys fails outright (Bybit retCode 10003), so this must
    not silently reuse settings_store's mainnet keys. """
    global _bybit_executor_agent
    if _bybit_executor_agent is None:
        key = get_bybit_testnet_api_key()
        secret = get_bybit_testnet_api_secret()
        _bybit_executor_agent = BybitAgent(key, secret, testnet=True)
    return _bybit_executor_agent

async def fetch_bybit_spot_price(pair_label):
    """ Latest spot last price for pair switching / seeding current_price. """
    symbol = get_bybit_symbol(pair_label)
    if not symbol:
        return None
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get(
                f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={symbol}"
            )
        if resp.status_code != 200:
            return None
        body = resp.json()
        item = body.get("result", {}).get("list", [{}])[0]
        price = float(item.get("lastPrice", 0))
        return price if price > 0 else None
    except Exception as exc:
        print(f"[MARKET FEED] Could not fetch spot price for {pair_label}: {exc}")
        return None

def _sanitize_market_price(price):
    """ Reject corrupt ticks so entries/PnL never use zero or negative prices. """
    try:
        value = float(price)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return value

# Tracks the last time binance_price_feed successfully processed a REAL tick,
# so market_simulator can tell "actively receiving real data" apart from
# "silently stuck" (network hiccup, DNS issue, host blocking outbound, etc.)
# and self-heal by taking over with synthetic movement instead of leaving
# current_price frozen forever.
_last_real_feed_update = 0.0
REAL_FEED_STALE_AFTER_SECONDS = 10

# ==========================================
# ENTRY POLICY: TAAPI CANDLE-PATTERN SCAN
# PAPER_TRADING (default): simulated positions via agent.open_trade() — no Bybit
# API keys required. LIVE_TRADING + TESTNET keys: real orders via bybit_executor.
# ==========================================

async def auto_buy_loop():
    print("[AUTO BUY LOOP] TAAPI candle-pattern policy active (paper simulation by default).")
    while True:
        timeframe_key = SECONDS_TO_TIMEFRAME_KEY.get(agent.timeframe_seconds, "1m")
        # Smart sleep - avoids spamming Bybit/TAAPI on fast timeframes.
        if timeframe_key in ("30s", "1m"):
            sleep_seconds = 5
        elif timeframe_key in ("5m", "15m"):
            sleep_seconds = 15
        else:
            sleep_seconds = 30
        await asyncio.sleep(sleep_seconds)

        if not agent.is_active or agent.emergency_triggered:
            continue

        bybit_symbol = get_bybit_symbol(agent.active_pair)
        if bybit_symbol is None:
            continue  # no real market mapping for this pair - nothing to scan

        try:
            candle = await fetch_closed_candle_ohlc(bybit_symbol, timeframe_key)
        except Exception as exc:
            print(f"[AUTO BUY LOOP] Candle fetch failed for {bybit_symbol}: {exc}")
            if agent.active_pair not in _CANDLE_FETCH_WARNED_PAIRS:
                _CANDLE_FETCH_WARNED_PAIRS.add(agent.active_pair)
                notifications.push(
                    f"TAAPI policy can't read {bybit_symbol} candles (no Bybit LINEAR market for "
                    f"this pair?) - auto-entries paused for {agent.active_pair} until this resolves.",
                    "warning",
                )
            continue
        _CANDLE_FETCH_WARNED_PAIRS.discard(agent.active_pair)  # recovered - clear so a future failure re-warns

        close_time = candle["close_time"]
        if close_time <= LAST_CANDLE_TIMESTAMPS.get(agent.active_pair, 0):
            continue  # already scanned this candle
        LAST_CANDLE_TIMESTAMPS[agent.active_pair] = close_time
        print(f"🔄 New {timeframe_key} candle detected for {agent.active_pair}. Scanning patterns...")

        taapi_key = get_taapi_secret()
        if not taapi_key:
            system_log.push("taapi", "TAAPI_SECRET not configured — pattern scan skipped.", {"pair": agent.active_pair})
            continue

        taapi_exchange = get_taapi_exchange()
        try:
            signals = await asyncio.to_thread(
                fetch_taapi_signals, agent.active_pair, timeframe_key, taapi_exchange, taapi_key
            )
        except Exception as exc:
            print(f"[AUTO BUY LOOP] TAAPI fetch failed: {exc}")
            system_log.push("taapi", f"TAAPI fetch failed: {exc}", {"pair": agent.active_pair, "timeframe": timeframe_key})
            continue

        result = evaluate_trade(signals, timeframe_key, candle["high"], candle["low"])
        system_log.set_last_taapi_scan(agent.active_pair, timeframe_key, signals, result, candle)
        if result["action"] not in ("BUY", "SELL"):
            print(f"[AUTO BUY LOOP] {result['action']}: {result['reason']}")
            continue

        try:
            volume_analysis = await analyze_signal_candle_volume(bybit_symbol, timeframe_key)
        except Exception as exc:
            print(f"[AUTO BUY LOOP] Volume analysis failed: {exc}")
            volume_analysis = {
                "passed": False,
                "reason": f"Volume check error: {exc}",
            }
        system_log.set_last_volume_analysis(
            agent.active_pair, timeframe_key, result["action"], volume_analysis
        )
        if not volume_analysis.get("passed"):
            _log_trade_skip(
                result["action"],
                bybit_symbol,
                result.get("pattern"),
                volume_analysis.get("reason", "Volume gate failed"),
                volume_analysis=volume_analysis,
            )
            continue

        capital_base = agent.get_trading_capital_base()
        if capital_base is None or capital_base <= 0:
            _log_trade_skip(
                result["action"], bybit_symbol, result.get("pattern"),
                f"Insufficient capital (base={capital_base}).",
            )
            continue

        plan = compute_auto_trade_plan(agent, agent.current_price)
        if plan is None:
            _log_trade_skip(
                result["action"], bybit_symbol, result.get("pattern"),
                f"Could not size trade (capital=${agent.get_total_portfolio_value()}, "
                f"price=${agent.current_price}).",
            )
            continue

        position_size_usd = plan["position_usd"]
        qty = plan["qty"]
        result["symbol"] = bybit_symbol
        is_paper = bybit_api.mode == "PAPER_TRADING"

        if is_paper:
            side = "LONG" if result["action"] == "BUY" else "SHORT"
            trade = agent.open_trade(
                side,
                reason=f"PAPER TAAPI {result.get('pattern', 'signal')}",
                source="auto",
                position_size_usd=position_size_usd,
                qty=qty,
            )
            fired = trade is not None
            skip_reason = None
            if not fired:
                if not agent.is_active:
                    skip_reason = "AI automation is not running — click START AI AUTOMATION."
                elif agent.emergency_triggered:
                    skip_reason = "Emergency halt is active."
                elif len(agent.trades) >= agent.max_concurrent_trades:
                    skip_reason = f"Max concurrent trades ({agent.max_concurrent_trades}) reached."
                else:
                    skip_reason = "Paper entry blocked (invalid price or zero balance)."
            system_log.set_last_trade_fire({
                "success": fired,
                "mode": "PAPER_TRADING",
                "action": result["action"],
                "symbol": bybit_symbol,
                "pattern": result.get("pattern"),
                "qty": qty,
                "position_usd": position_size_usd,
                "capital_total": plan["total_capital"],
                "capital_pct": plan["capital_pct"],
                "sl": result.get("sl"),
                "tp": result.get("tp"),
                "entry": result.get("entry"),
                "reason": result.get("reason"),
                "error": skip_reason,
                "trade_id": trade["id"] if trade else None,
            })
            if fired:
                side_label = "inverse SHORT" if side == "SHORT" else "LONG"
                notifications.push(
                    f"PAPER {side_label} opened: {agent.active_pair} | {qty} @ ~${plan['price']:,.0f} | "
                    f"${position_size_usd} ({plan['capital_pct']:.0f}% of ${plan['total_capital']:,.0f}) | "
                    f"pattern={result.get('pattern')} #{trade['id']}",
                    "success",
                )
            elif skip_reason:
                notifications.push(f"Paper trade skipped: {skip_reason}", "warning")
            continue

        if not is_bybit_testnet_configured():
            global _bybit_testnet_keys_warned
            err = "BYBIT_TESTNET_API_KEY / BYBIT_TESTNET_API_SECRET not set — add keys from https://testnet.bybit.com to backend/.env"
            system_log.set_last_trade_fire({
                "success": False,
                "action": result["action"],
                "symbol": bybit_symbol,
                "pattern": result.get("pattern"),
                "qty": qty,
                "position_usd": position_size_usd,
                "capital_total": plan["total_capital"],
                "capital_pct": plan["capital_pct"],
                "sl": result.get("sl"),
                "tp": result.get("tp"),
                "entry": result.get("entry"),
                "reason": result.get("reason"),
                "error": err,
            })
            if not _bybit_testnet_keys_warned:
                _bybit_testnet_keys_warned = True
                system_log.push("bybit", err, {"symbol": bybit_symbol})
                notifications.push("Bybit TESTNET keys missing — TAAPI signals fire but orders cannot execute.", "error")
            continue

        executor = get_bybit_executor_agent()
        fired, order_error = await asyncio.to_thread(executor.execute_trade, result, qty)
        system_log.set_last_trade_fire({
            "success": bool(fired),
            "action": result["action"],
            "symbol": bybit_symbol,
            "pattern": result.get("pattern"),
            "qty": qty,
            "position_usd": position_size_usd,
            "capital_pct": plan["capital_pct"],
            "sl": result.get("sl"),
            "tp": result.get("tp"),
            "entry": result.get("entry"),
            "reason": result.get("reason"),
            "error": order_error,
        })
        if fired:
            notifications.push(
                f"TESTNET order fired: {result['action']} {bybit_symbol} | {qty} | "
                f"${position_size_usd} ({plan['capital_pct']:.0f}% of ${plan['total_capital']:,.0f}) | "
                f"pattern={result['pattern']}",
                "success",
            )
        else:
            msg = order_error or "Unknown Bybit error — see server logs."
            notifications.push(f"TESTNET order FAILED: {result['action']} {bybit_symbol} — {msg}", "error")
            system_log.push("trade", f"Order rejected: {msg}", {"symbol": bybit_symbol, "action": result["action"]})

async def market_simulator():
    """ Synthetic random-walk price - runs whenever the active pair has no
    real market-data mapping, AND as a self-healing fallback if the real
    feed hasn't delivered a tick recently (so current_price can never stay
    permanently frozen even if the real feed silently breaks).

    Uses percentage volatility (not fixed ±$10) so low-priced coins like SOL
    cannot drift into zero/negative prices and corrupt entry levels. """
    while True:
        no_real_feed = get_bybit_symbol(agent.active_pair) is None
        real_feed_stale = (time.time() - _last_real_feed_update) > REAL_FEED_STALE_AFTER_SECONDS
        if no_real_feed or real_feed_stale:
            base = _sanitize_market_price(agent.current_price)
            if base is None:
                base = 1.0
                agent.current_price = base
            volatility_pct = random.uniform(-0.002, 0.002)
            new_price = max(base * (1 + volatility_pct), base * 0.0001)
            volume_increment = random.uniform(0.5, 3.0)
            if random.random() < 0.03:
                volume_increment *= random.uniform(3, 6)
            await agent.process_tick(new_price, volume_increment)
        await asyncio.sleep(0.5)

async def binance_price_feed():
    """ Keeps agent.current_price tracking REAL market prices, polling Bybit's
    public recent-trades endpoint every ~1.5s for whichever mapped pair is
    active. (Volume is folded through to process_tick for feed compatibility
    but is no longer used for entries; TAAPI candle-pattern scans now drive
    entry decisions.)
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
# inbound HTTP traffic. Self-ping ONLY hits GET /health — it does not stop
# the bot, sell trades, or restart the process; it just keeps the service warm.
# RENDER_EXTERNAL_URL is set automatically on Render web services.
KEEPALIVE_INTERVAL_SECONDS = 13 * 60  # 13 minutes — under Render's ~15m idle sleep window


async def _ping_health(client: httpx.AsyncClient, self_url: str) -> bool:
    try:
        resp = await client.get(f"{self_url}/health")
        print(f"[KEEPALIVE] Self-ping OK (HTTP {resp.status_code}) — /health only, no trades touched.")
        return True
    except Exception as exc:
        print(f"[KEEPALIVE] Self-ping failed ({exc}) — will retry next interval.")
        return False


async def self_ping_keepalive():
    self_url = os.environ.get("RENDER_EXTERNAL_URL")
    if not self_url:
        print("[KEEPALIVE] RENDER_EXTERNAL_URL not set (local dev) — keepalive disabled.")
        return

    interval = int(os.environ.get("KEEPALIVE_INTERVAL_SECONDS", str(KEEPALIVE_INTERVAL_SECONDS)))
    print(
        f"[KEEPALIVE] Pinging {self_url}/health every {interval // 60} minutes "
        f"(read-only wake ping — bot/trades unchanged)."
    )
    async with httpx.AsyncClient(timeout=15.0) as client:
        await _ping_health(client, self_url)
        while True:
            await asyncio.sleep(interval)
            await _ping_health(client, self_url)

@app.on_event("startup")
async def start_background_tasks():
    asyncio.create_task(market_simulator())
    asyncio.create_task(binance_price_feed())
    asyncio.create_task(bybit_balance_refresher())
    asyncio.create_task(self_ping_keepalive())
    asyncio.create_task(auto_buy_loop())
    asyncio.create_task(chart_24h_refresh_loop(BYBIT_SYMBOL_MAP))

# ==========================================
# 2. REST API COMMAND "WIRES"
# ==========================================
@app.post("/start-bot")
async def start_bot():
    open_count = len(agent.trades)
    agent.clear_emergency_state()
    agent.daily_target_reached = False  # fresh session -> clear any prior daily-target halt
    agent.begin_ai_season()  # season P&L + kill-switch baseline = portfolio value right now
    agent.is_active = True
    print("[PILLAR 2: BACKEND] Received 'START' from Frontend. AI Agent awakened.")
    system_log.push(
        "ai",
        f"AI automation STARTED on {agent.active_pair} ({open_count} open position(s) preserved).",
        {"open_positions": open_count, "timeframe_seconds": agent.timeframe_seconds},
    )
    if open_count:
        notifications.push(
            f"AI Agent STARTED on {agent.active_pair}. {open_count} open position(s) preserved (manual trades protected).",
            "success",
        )
    else:
        notifications.push(f"AI Agent STARTED - now monitoring {agent.active_pair} live.", "success")
    return {
        "status": "success",
        "message": "Bot active & trailing logic initialized.",
        "open_positions": open_count,
        "trade_history_count": len(agent.trade_history),
    }

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
        agent.manual_stop("STOP AI AUTOMATION | Emergency Exit from Frontend")
    return {"status": "success", "message": "All trades closed."}

@app.post("/continue-trading")
async def continue_trading():
    """ Clears emergency halt flags. Portfolio stop-loss is disabled. """
    agent.resume_trading_after_emergency()
    return {
        "status": "success",
        "message": "Trading resumed.",
        "risk_level_pct": agent.risk_level_pct,
        "max_concurrent_trades": agent.max_concurrent_trades,
    }

@app.post("/connect-bybit")
async def connect_bybit():
    print("[PILLAR 2: BACKEND] Switching from Paper Trading to Live Real Trading...")
    bybit_api.connect_real_api()
    equity = await bybit_api.fetch_real_balance()
    if equity is not None:
        agent.on_live_connected(equity)
    else:
        notifications.push(
            f"Bybit connected but balance sync failed: {bybit_api.last_error or 'unknown error'}.",
            "error",
        )
    return {
        "status": "success",
        "message": "SUCCESS: Bybit API Connected. Real Money Trading is ACTIVE.",
        "equity": equity,
        "trading_mode": bybit_api.mode,
    }

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
    confirmed: bool = False

class ManualSellPayload(BaseModel):
    confirmed: bool = False

@app.post("/open-trade")
async def open_trade(payload: OpenTradePayload):
    """ Manual BUY button: opens an additional 1%-margin/100x position on the currently
    active pair while AI automation is OFF. Every click books one more trade tagged
    "manual" so the SELL button only ever closes these, never auto trades. """
    side = payload.side.upper() if payload.side.upper() in ("LONG", "SHORT") else "LONG"
    if agent.emergency_triggered:
        return {"status": "error", "message": "Cannot open a position - emergency halt is active."}
    if agent.is_active:
        return {"status": "error", "message": "Stop AI automation before using manual BUY."}
    if len(agent.trades) >= agent.max_concurrent_trades:
        return {"status": "error", "message": f"Max concurrent trades ({agent.max_concurrent_trades}) reached."}

    trade = agent.open_trade(side, reason="Manual BUY button", source="manual")
    if trade is None:
        if agent._live_insufficient_balance():
            return {"status": "error", "message": "Insufficient balance on your Bybit account."}
        return {"status": "error", "message": "Could not open a manual position."}
    return {"status": "success", "message": f"Manual BUY filled on {agent.active_pair}.", "trade": trade, "pair": agent.active_pair}

@app.post("/manual-sell")
async def manual_sell(payload: ManualSellPayload = ManualSellPayload()):
    """ Manual SELL button: closes exactly the ONE manually-opened trade with the
    highest True Net Profit while AI automation is OFF. """
    if not payload.confirmed:
        return {"status": "error", "message": "Manual SELL requires explicit confirmation."}
    if agent.is_active:
        return {"status": "error", "message": "Stop AI automation before using manual SELL."}
    closed = agent.manual_close_best()
    if closed is None:
        return {"status": "error", "message": "No manually-opened positions to sell."}
    return {"status": "success", "message": f"Manual SELL executed - position #{closed['id']} closed.", "trade": closed}

@app.post("/close-trade")
async def close_trade(payload: CloseTradePayload):
    """ Force-closes a single stacked position on the active pair (trash icon action). """
    if not payload.confirmed:
        return {"status": "error", "message": "Force close requires explicit confirmation."}
    trade = next((t for t in agent.trades if t["id"] == payload.id), None)
    if not trade:
        return {"status": "error", "message": "Trade not found or already closed."}

    m = agent._trade_metrics(trade)
    agent._finalize_trade_history(trade, m, "Manual force-close")
    agent.trades = [t for t in agent.trades if t["id"] != payload.id]
    bybit_api.execute_market_sell(trade["pair"], f"Manual force-close of position #{trade['id']}")
    notifications.push(f"Position #{trade['id']} manually force-closed on {trade['pair']}.", "warning")
    return {"status": "success", "message": f"Position #{trade['id']} closed at market price."}

@app.post("/set-pair")
async def set_pair(payload: SetPairPayload):
    """ Switches focused pair only when there are no open positions. """
    global _last_real_feed_update
    if payload.pair != agent.active_pair and agent.trades:
        return {
            "status": "locked",
            "message": f"Pair switch blocked while {len(agent.trades)} trade(s) are active on {agent.active_pair}.",
            "pair": agent.active_pair,
            "price": agent.current_price,
        }
    live_price = await fetch_bybit_spot_price(payload.pair)
    seed_price = live_price if live_price is not None else _sanitize_market_price(payload.price)
    if seed_price is None:
        return {"status": "error", "message": f"Could not resolve a valid market price for {payload.pair}."}
    agent.set_active_pair(payload.pair, seed_price)
    _last_real_feed_update = time.time()
    source = "Bybit live" if live_price is not None else "fallback"
    return {
        "status": "success",
        "message": f"Active trading pair set to {payload.pair} @ ${seed_price:,.4f} ({source}).",
        "pair": agent.active_pair,
        "price": seed_price,
    }

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
    - stop_loss_pct (risk level from the popup) -> max_concurrent_trades via
      round(stop_loss_pct * 1.5) (half-up). Pre-start strategy only — not a stop-loss.
    - daily_profit_pct -> optional "Capital profit of the day" target; 0 disables.
    Both are validated and stored before /start-bot is called by the frontend. """
    if payload.stop_loss_pct < 0.5 or payload.stop_loss_pct > 50:
        return {"status": "error", "message": "Risk level must be between 0.5% and 50%."}
    if payload.daily_profit_pct < 0 or payload.daily_profit_pct > 1000:
        return {"status": "error", "message": "Daily profit target must be between 0% and 1000%."}

    agent.risk_level_pct = payload.stop_loss_pct
    agent.max_concurrent_trades = max(1, _half_up_round(payload.stop_loss_pct * 1.5))
    agent.daily_profit_target_pct = payload.daily_profit_pct
    agent.daily_target_reached = False
    print(f"[AGENT CONFIG] risk_level={payload.stop_loss_pct}% | max_concurrent_trades="
          f"{agent.max_concurrent_trades} | daily_profit_target={agent.daily_profit_target_pct}%")
    return {
        "status": "success",
        "message": "Agent config applied.",
        "risk_pct": payload.stop_loss_pct,
        "max_concurrent_trades": agent.max_concurrent_trades,
        "daily_profit_target_pct": agent.daily_profit_target_pct,
    }

@app.get("/agent/config")
async def get_agent_config():
    """ Lets the modal show the currently-applied config when reopened. """
    # Derive the popup risk % from the stored concurrent-trades cap (inverse of * 1.5).
    risk_pct = agent.risk_level_pct or (
        round(agent.max_concurrent_trades / 1.5, 1) if agent.max_concurrent_trades else 3.0
    )
    return {
        "stop_loss_pct": risk_pct,
        "risk_pct": risk_pct,
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

@app.get("/system/logs")
async def get_system_logs():
    """ Transparency snapshot for the System Log modal — connections, TAAPI scan,
    trade pipeline, and rolling backend event log. No secrets are returned. """
    taapi_key = is_taapi_configured()
    timeframe_key = SECONDS_TO_TIMEFRAME_KEY.get(agent.timeframe_seconds, "1m")
    return {
        "connections": {
            "bybit_configured": settings_store.is_bybit_configured(),
            "bybit_environment": settings_store.bybit_environment,
            "bybit_mode": bybit_api.mode,
            "bybit_connected": bybit_api.connected,
            "bybit_last_error": bybit_api.last_error,
            "bybit_balance": bybit_api.last_known_balance,
            "ai_configured": settings_store.is_ai_configured(),
            "ai_provider": settings_store.ai_provider,
            "ai_model": settings_store.ai_model,
            "ai_base_url": settings_store.ai_base_url,
            "taapi_configured": taapi_key,
            "taapi_exchange": get_taapi_exchange(),
            "bybit_testnet_configured": is_bybit_testnet_configured(),
        },
        "agent": {
            "is_active": agent.is_active,
            "active_pair": agent.active_pair,
            "timeframe_key": timeframe_key,
            "timeframe_seconds": agent.timeframe_seconds,
            "current_price": round(agent.current_price, 4),
            "open_trades": len(agent.trades),
            "emergency_triggered": agent.emergency_triggered,
            "policy": (
                f"TAAPI BUY->LONG / SELL->inverse SHORT | volume gate (signal > prev 2 candles) | "
                f"{AUTO_TRADE_CAPITAL_PCT * 100:.0f}% capital per trade | "
                f"per-trade profit book (floor {agent.get_profit_floor_pct()}% + 30% trail)"
                if bybit_api.mode == "PAPER_TRADING"
                else (
                    f"TAAPI BUY/SELL -> Bybit TESTNET linear | volume gate (signal > prev 2) | "
                    f"{AUTO_TRADE_CAPITAL_PCT * 100:.0f}% capital | per-trade profit book"
                )
            ),
        },
        "chart": {
            "history_hint": "5M: backend /chart/24h snapshot, else Bybit spot klines, fallback Binance, then mock",
            "live_hint_paper": "Paper mode: public Bybit/Binance WebSocket trade stream in browser",
            "live_hint_live": "Live mode: backend /ws/market feed (Bybit price engine)",
        },
        "last_taapi_scan": system_log.last_taapi_scan,
        "last_volume_analysis": system_log.last_volume_analysis,
        "last_trade_fire": system_log.last_trade_fire,
        "entries": system_log.entries[-60:],
        "notifications": notifications.notifications[-20:],
    }

@app.get("/chart/24h")
async def get_chart_24h(pair: str | None = Query(None, description="e.g. BTC/USDT")):
    """ Latest 24h high/low + 5m candles. Uses persisted snapshot when available;
    fetches live from Bybit on demand when a mapped pair is missing from cache. """
    if pair:
        bybit_symbol = get_bybit_symbol(pair)
        try:
            if bybit_symbol:
                entry = await chart_24h_store.ensure_pair(pair, bybit_symbol)
            else:
                entry = chart_24h_store.get_pair(pair)
        except Exception as exc:
            print(f"[CHART 24H] Live fetch failed for {pair}: {exc}")
            entry = None
        if not entry:
            return {
                "pair": pair,
                "high": None,
                "low": None,
                "last_price": None,
                "candles": [],
                "updated_at": chart_24h_store.updated_at,
            }
        return {"updated_at": chart_24h_store.updated_at, **entry}
    return chart_24h_store.get_snapshot()

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
    if not await require_ws_token(websocket):
        return
    await websocket.accept()
    try:
        while True:
            payload = {
                "price": round(agent.current_price, 4),
                "active_pair": agent.active_pair,
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
    if not await require_ws_token(websocket):
        return
    await websocket.accept()
    try:
        # POLICY 4: Check live portfolio balance and lock positions immediately on connect
        print("POLICY 4: Reconnected. Synchronizing current positions and portfolio lock state.")
        while True:
            # RULE 6: current_capital now only changes via REALIZED trade P&L (execute_sell /
            # trigger_emergency_exit), never a random walk - this is the true capital ledger.
            total_value = agent.get_total_portfolio_value()
            # Daily profit = vs paper-account starting capital (lifetime account P&L).
            daily_profit = total_value - agent.starting_capital
            daily_profit_pct = (daily_profit / agent.starting_capital) * 100 if agent.starting_capital else 0

            # AI Season profit = vs capital at the moment START AI AUTOMATION was clicked.
            if agent.ai_season_start_capital is not None and agent.is_active:
                ai_season_profit = total_value - agent.ai_season_start_capital
                ai_season_profit_pct = (ai_season_profit / agent.ai_season_start_capital) * 100
            else:
                ai_season_profit = 0.0
                ai_season_profit_pct = 0.0

            baseline = agent.get_session_baseline()
            portfolio_drop = ((baseline - total_value) / baseline) * 100 if baseline else 0

            payload = {
                "capital": round(agent.current_capital, 2),
                "total_portfolio_value": round(total_value, 2),
                "trading_mode": bybit_api.mode,
                "daily_profit": round(daily_profit, 2),
                "daily_profit_pct": round(daily_profit_pct, 2),
                "ai_season_profit": round(ai_season_profit, 2),
                "ai_season_profit_pct": round(ai_season_profit_pct, 2),
                "ai_season_active": agent.ai_season_start_capital is not None and agent.is_active,
                "portfolio_drop_pct": round(portfolio_drop, 2),
                "is_active": agent.is_active,
                "emergency": False,
                "risk_level_pct": agent.risk_level_pct,
                "max_concurrent_trades": agent.max_concurrent_trades,
                "profit_floor_pct": agent.get_profit_floor_pct(),
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
    if not await require_ws_token(websocket):
        return
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
    if not await require_ws_token(websocket):
        return
    await websocket.accept()
    try:
        while True:
            payload = {
                "pair": agent.active_pair,
                "trades": agent.get_trades_snapshot(),
                "active_count": len(agent.trades),
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