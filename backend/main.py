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
from bybit_public import (
    fetch_kline_rows,
    fetch_ticker_last_price,
    sanitize_price as _sanitize_market_price,
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
    TAAPI_PAUSED,
)
from chart_24h import chart_24h_refresh_loop, chart_24h_store
from system_log import system_log
from volume_spread_system import (
    UVSS_POLICIES_ENABLED,
    UVSS_COST_AWARE_ENTRY,
    evaluate_uvss,
    MIN_CANDLES,
    compute_risk_trade_plan,
    log_trade_execution,
    reset_blue_box_state,
    build_blue_box_chart_overlay,
    RISK_PCT_PER_TRADE,
    RR_RATIO,
)
from bybit_executor import BybitAgent
from trading_policy import evaluate_cost_aware_entry
from candlestick_bible_memory import (
    bible_system_prompt_blurb,
    fetch_bible,
    list_bible_toc,
    memory_stats as bible_memory_stats,
    search_bible,
)
from ml_trading_memory import (
    fetch_ml,
    list_ml_toc,
    memory_stats as ml_memory_stats,
    ml_system_prompt_blurb,
    search_ml,
)
from agent_brain import brain_chat_summary, enrich_signal, strategy_system_blurb
from whale_alerts import (
    WHALE_POLL_SECONDS,
    WHALE_SOURCE_URL,
    MIN_BTC_AMOUNT,
    build_trade_plan_from_signal,
    fetch_whale_alerts,
    is_signal_seen,
    is_seeded,
    is_btc_pair,
    last_fetch_snapshot,
    mark_signal_seen,
    seed_seen_from_snapshot,
)

from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent / "DATA"

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
        print("[SETTINGS] Entry engine: Candle patterns + Bible + ML cost-aware (Bybit linear).")

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
            "taapi_paused": TAAPI_PAUSED,
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

    messages = []
    system_role = load_system_role_text()
    if system_role:
        messages.append({"role": "system", "content": system_role[:20000]})
    # Inject matching Candlestick Bible + ML context from RAM.
    bible_q = context.get("bible_key") or context.get("pattern") or context.get("condition")
    bible_ctx = fetch_bible_context_for_signal(bible_q)
    if bible_ctx:
        messages.append({"role": "system", "content": bible_ctx[:4000]})
    try:
        ml_hit = fetch_ml("cost aware", max_chars=1200)
        if ml_hit.get("ok"):
            messages.append(
                {
                    "role": "system",
                    "content": (
                        f"[ML cost-aware · {ml_hit.get('title')}]\n{ml_hit.get('text')}"
                    )[:2500],
                }
            )
    except Exception:
        pass
    messages.append({"role": "user", "content": prompt})

    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "messages": messages,
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
    # Strict Exit Logic (replaces all prior profit-lock / trailing / target exit rules).
    STRICT_EXIT_HARD_TARGET_PCT = float(os.environ.get("STRICT_EXIT_HARD_TARGET", "1.2"))
    STRICT_EXIT_MIN_LOCK_PCT = float(os.environ.get("STRICT_EXIT_MIN_LOCK", "0.20"))
    STRICT_EXIT_FLUCTUATION_X_PCT = float(os.environ.get("STRICT_EXIT_FLUCTUATION_X", "0.10"))
    STRICT_EXIT_TRAIL_MULTIPLIER = float(os.environ.get("STRICT_EXIT_TRAIL_MULT", "1.5"))

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
        # halted (existing positions keep being managed by strict exit logic).
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
        """Minimum profit lock floor (Rule 2) — 0.20% gross."""
        return self.STRICT_EXIT_MIN_LOCK_PCT

    def _sync_agent_trailing_lock_state(self):
        """Mirror per-trade lock/peak to agent-level fields for WS + UI."""
        auto = [t for t in self.trades if t.get("source") != "manual"]
        if not auto:
            self.is_lock_active = False
            self.peak_net_pct = 0.0
            return
        self.is_lock_active = any(t.get("is_lock_active") for t in auto)
        locked_peaks = [
            float(t.get("peak_gross_pct") or 0)
            for t in auto
            if t.get("is_lock_active")
        ]
        if locked_peaks:
            self.peak_net_pct = max(locked_peaks)
        else:
            self.peak_net_pct = max(float(t.get("peak_gross_pct") or 0) for t in auto)

    def _evaluate_strict_exit(self, trade: dict, gross: float, net: float) -> str | None:
        """Strict Exit Logic — 3 rules only (gross % P&L).

        Rule 1: Hard target at +1.2% — immediate auto-exit.
        Rule 2: Once +0.20% is reached, profit floor locks at +0.20% permanently.
        Rule 3: Trailing exit = peak − (1.5 × x), never below +0.20% floor.
        """
        hard_target = self.STRICT_EXIT_HARD_TARGET_PCT
        min_lock = self.STRICT_EXIT_MIN_LOCK_PCT
        x = self.STRICT_EXIT_FLUCTUATION_X_PCT
        trail_mult = self.STRICT_EXIT_TRAIL_MULTIPLIER

        # Rule 1 — hard target (no trailing wait).
        if gross >= hard_target:
            return (
                f"Strict Exit Rule 1 (Hard Target): +{gross:.3f}% ≥ +{hard_target:.2f}% — "
                "immediate auto-exit."
            )

        prev_peak = float(trade.get("peak_gross_pct") or 0.0)
        if gross > prev_peak:
            trade["peak_gross_pct"] = gross
            trade["peak_net_pct"] = max(float(trade.get("peak_net_pct") or 0.0), net)
        peak = float(trade.get("peak_gross_pct") or 0.0)

        # Rule 2 — minimum profit lock at +0.20%.
        if peak >= min_lock and not trade.get("is_lock_active"):
            trade["is_lock_active"] = True
            trade["lock_level_pct"] = min_lock
            notifications.push(
                f"Min profit lock ON #{trade['id']} @ +{gross:.3f}% "
                f"(floor locked at +{min_lock:.2f}% gross).",
                "success",
            )
            system_log.push(
                "agent",
                f"Strict Exit Rule 2 — min lock #{trade['id']} +{gross:.3f}%",
                {"trade_id": trade["id"], "peak_gross_pct": gross, "min_lock_pct": min_lock},
            )

        if peak >= min_lock:
            trade["lock_level_pct"] = min_lock

        if peak < min_lock:
            return None

        # Rule 3 — 1.5× trailing stop from peak, floored at Rule 2 minimum.
        sell_trigger = max(min_lock, peak - (trail_mult * x))
        trade["sell_trigger_pct"] = sell_trigger

        if gross < peak and gross <= sell_trigger + 1e-9:
            return (
                f"Strict Exit Rule 3 (1.5× trail): peak +{peak:.3f}% → "
                f"exit trigger +{sell_trigger:.3f}% (peak − {trail_mult}×{x:.2f}%, "
                f"floor +{min_lock:.2f}%) → +{gross:.3f}%. Market close."
            )
        return None

    def _trade_metrics(self, t, *, for_close: bool = False):
        """PnL metrics for a position.

        Open (UI): unrealized = gross − entry fee only (exit fee not paid yet).
        Close: true net = gross − entry − exit (RULE 6/7).
        """
        if t["side"] == "LONG":
            gross_pct = ((self.current_price - t["entry"]) / t["entry"]) * 100
        else:
            gross_pct = ((t["entry"] - self.current_price) / t["entry"]) * 100

        entry_fee_pct = float(t["entry_fee_pct"])
        exit_fee_pct = bybit_api.get_taker_fee_pct() * (self.current_price / t["entry"])
        if for_close:
            net_pct = gross_pct - entry_fee_pct - exit_fee_pct
        else:
            # Unrealized: do not mark exit fee — that was painting winners red.
            net_pct = gross_pct - entry_fee_pct

        gross_usd = t["position_size"] * (gross_pct / 100)
        exit_fee_usd = t["position_size"] * (exit_fee_pct / 100) if for_close else 0.0
        if for_close:
            net_usd = gross_usd - t["entry_fee_usd"] - exit_fee_usd
        else:
            net_usd = gross_usd - t["entry_fee_usd"]

        return {
            "gross_pct": gross_pct,
            "net_pct": net_pct,
            "gross_usd": gross_usd,
            "exit_fee_usd": exit_fee_usd,
            "net_usd": net_usd,
            "entry_fee_pct": entry_fee_pct,
            "exit_fee_pct": exit_fee_pct if for_close else 0.0,
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
            "signal_candle_time": trade.get("signal_candle_time"),
            "pattern": trade.get("pattern"),
            "opened_at": trade.get("opened_at"),
            "exchange": trade.get("exchange"),
        })

    def get_entry_candle_highlights(self) -> list[dict]:
        """Candles where auto trades fired — for frontend neon chart markers."""
        seen: set[int] = set()
        out: list[dict] = []
        for row in self.trade_history:
            if row.get("source") == "manual":
                continue
            raw = row.get("signal_candle_time")
            if raw is None:
                continue
            chart_time = int(raw // 1000) if raw > 1_000_000_000_000 else int(raw)
            if chart_time in seen:
                continue
            seen.add(chart_time)
            out.append({
                "time": chart_time,
                "side": row.get("side", "LONG"),
                "pattern": row.get("pattern"),
                "opened_at": row.get("opened_at"),
            })
        return out

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

    def get_available_capital(self):
        """Free cash for the next 10% auto slot (paper ledger after open reserves)."""
        if bybit_api.mode == "LIVE_TRADING":
            base = self.get_trading_capital_base()
            return max(0.0, float(base)) if base is not None else 0.0
        return max(0.0, float(self.current_capital))

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
        """Equity = available cash + reserved in open trades + unrealized net P&L."""
        reserved = sum(
            float(t.get("capital_reserved") or t.get("margin") or 0) for t in self.trades
        )
        unrealized = self.get_unrealized_net_usd()
        if bybit_api.mode == "LIVE_TRADING" and bybit_api.last_known_balance is not None:
            return bybit_api.last_known_balance
        return self.current_capital + reserved + unrealized

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
        """Trading-engine candle interval (not the frontend chart view).

        Drives auto_buy_loop polling and pattern scans. The chart UI may use a
        different display timeframe without calling this — open trades must not
        be reset when the user only changes how candles are drawn.
        """
        if self.timeframe_seconds == seconds:
            return
        self.timeframe_seconds = seconds
        LAST_CANDLE_TIMESTAMPS.clear()
        _recent_signal_fire_keys.clear()
        reset_blue_box_state()
        _invalidate_kline_cache()
        print(f"[TIMEFRAME SYNC] Backend trading timeframe set to {seconds}s. Pattern state cleared.")

    def open_trade(
        self,
        side="LONG",
        reason="Manual",
        source="auto",
        position_size_usd=None,
        qty=None,
        skip_exchange_open=False,
        entry_price=None,
        exchange=None,
        bybit_symbol=None,
        pattern=None,
        signal_candle_time=None,
        taapi_action=None,
        sl_price=None,
        tp_price=None,
        target_mult=None,
    ):
        """ RULE 1: Opens a position as a Market Order (RULE 7) with simulated minor slippage.
        Manual entries default to 1% margin x 100x leverage. Auto entries pass
        `position_size_usd` + `qty` from compute_auto_trade_plan() (10% of available capital).
        `skip_exchange_open=True` when Bybit TESTNET already filled the order (FIX 4). """
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

            if self.has_duplicate_auto_entry(
                side, self.active_pair, pattern, signal_candle_time, float(entry_price or self.current_price)
            ):
                print(
                    f"[PILLAR 3: AI AGENT] Duplicate auto-entry blocked on {self.active_pair} "
                    f"({side}, pattern={pattern}, candle={signal_candle_time})."
                )
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

        # Paper ledger: reserve capital on open (auto = 10% notional slot; manual = margin).
        capital_reserved = round(position_size, 2) if source == "auto" and position_size_usd is not None else round(margin, 2)
        if bybit_api.mode != "LIVE_TRADING":
            if self.current_capital < capital_reserved:
                notifications.push(
                    f"Insufficient balance — need ${capital_reserved:,.2f}, have ${self.current_capital:,.2f}.",
                    "error",
                )
                return None

        # RULE 7: Market orders fill with minor slippage vs the requested price
        if entry_price is not None:
            filled_price = round(float(entry_price), 4)
        else:
            slippage = random.uniform(-0.0002, 0.0002)
            filled_price = round(self.current_price * (1 + slippage), 4)

        if qty is None and position_size_usd is not None:
            qty = compute_order_qty(position_size_usd, filled_price)

        if exchange is None and bybit_api.mode == "PAPER_TRADING":
            exchange = "paper"
        if bybit_symbol is None:
            bybit_symbol = get_bybit_symbol(self.active_pair)

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
            "opened_at": time.time(),
            "peak_gross_pct": 0.0,
            "peak_net_pct": 0.0,
            "lock_level_pct": None,
            "is_lock_active": False,
            "exchange": exchange,
            "bybit_symbol": bybit_symbol,
            "pattern": pattern,
            "signal_candle_time": signal_candle_time,
            "taapi_action": taapi_action,
            "sl_price": round(float(sl_price), 4) if sl_price is not None else None,
            "tp_price": round(float(tp_price), 4) if tp_price is not None else None,
            "target_mult": target_mult,
            "capital_reserved": capital_reserved,
        }
        self.trades.append(trade)
        if bybit_api.mode != "LIVE_TRADING":
            self.current_capital = round(self.current_capital - capital_reserved, 2)
            print(
                f"[CAPITAL] Reserved ${capital_reserved:,.2f} for #{trade['id']} "
                f"(available ${self.current_capital:,.2f}, notional ${position_size:,.2f})."
            )
        self._append_trade_history(trade)
        qty_label = f" | qty={qty}" if qty is not None else ""
        if not skip_exchange_open:
            bybit_api.execute_market_open(
                self.active_pair,
                side,
                f"{reason} | ${position_size} notional ({margin} margin x{self.leverage}){qty_label}",
            )
        print(f"[PILLAR 3: AI AGENT] Opened new {side} position #{trade['id']} on {self.active_pair} @ {filled_price} "
              f"(margin=${margin}, position=${position_size}, qty={qty}, entry_fee=${entry_fee_usd}, source={source})")
        qty_note = f" | {qty} coins" if qty is not None else ""
        if source == "auto" and position_size_usd is not None:
            risk_note = f"{AUTO_TRADE_CAPITAL_PCT * 100:.0f}% of available capital{qty_note}"
            fill_msg = (
                f"Order Filled: {self.active_pair} {side} @ {filled_price:,.4f} "
                f"(${position_size:,.2f} notional, {risk_note})"
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
        if self._close_single_trade(best, m, reason):
            self.trades = [t for t in self.trades if t["id"] != best["id"]]
        print(f"[PILLAR 3: AI AGENT] Manual SELL closed position #{best['id']} on {best['pair']} "
              f"(net_pct={m['net_pct']:.3f}%, net_usd=${m['net_usd']:.2f})")
        return best

    def set_active_pair(self, pair, price):
        """ Switch pair for chart/trading while keeping open positions intact. """
        # Legacy UI pair removed — whale flow is merged into BTC automation.
        p = (pair or "").strip().upper().replace("-", "/")
        if p in ("WHALE/BTC", "WHALE"):
            pair = "BTC/USDT"
        pair_changed = pair != self.active_pair
        self.active_pair = pair
        self.current_price = price
        if pair_changed:
            self.peak_net_pct = 0.0
            self.is_lock_active = False
            reset_blue_box_state()
            _invalidate_kline_cache()
            print(f"[PILLAR 3: AI AGENT] Active pair switched to {pair}. Open positions preserved.")
        else:
            print(f"[PILLAR 3: AI AGENT] Active pair refreshed to {pair} @ {price}.")

    def get_trades_snapshot(self):
        """Active trades on top, then session exits (sold) listed below for the UI."""
        open_ids = {t["id"] for t in self.trades}
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
                "protected": trade.get("source") == "manual",
                "peak_gross_pct": round(float(trade.get("peak_gross_pct") or 0), 4),
                "sell_trigger_pct": (
                    round(float(trade["sell_trigger_pct"]), 4)
                    if trade.get("sell_trigger_pct") is not None
                    else None
                ),
            })
            snapshot.append(out)

        # Newest exits first under the open list (profit-lock / force-close / STOP).
        sold_rows = [
            row for row in self.trade_history
            if row.get("status") == "sold" and row.get("id") not in open_ids
        ]
        sold_rows.sort(key=lambda r: r.get("id") or 0, reverse=True)
        for row in sold_rows:
            out = dict(row)
            out.setdefault("protected", out.get("source") == "manual")
            snapshot.append(out)
        return snapshot

    async def process_tick(self, new_price, volume_increment):
        """Updates live price; evaluates Strict Exit Logic on each auto trade."""
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

        if not UVSS_POLICIES_ENABLED:
            return

        still_open = []
        for trade in self.trades:
            if trade.get("source") == "manual":
                still_open.append(trade)
                continue

            m = self._trade_metrics(trade)
            if AUTO_TRADE_AUTO_EXIT_ENABLED:
                reason = self._evaluate_strict_exit(trade, m["gross_pct"], m["net_pct"])
                if reason and self._close_single_trade(trade, m, reason):
                    continue

            still_open.append(trade)

        self.trades = still_open
        self._sync_agent_trailing_lock_state()

    def _close_single_trade(self, trade, metrics, reason) -> bool:
        """Close one position. Returns True if closed locally (and on Bybit when applicable)."""
        # Always settle with full round-trip fees at close.
        metrics = self._trade_metrics(trade, for_close=True)
        if trade_uses_bybit_executor(trade):
            ok, err = bybit_close_trade(trade)
            if not ok:
                msg = err or "Unknown Bybit close error"
                notifications.push(
                    f"Bybit TESTNET close FAILED #{trade['id']} {trade['pair']}: {msg}",
                    "error",
                )
                system_log.push(
                    "bybit",
                    f"Close failed #{trade['id']} {trade.get('bybit_symbol')}: {msg}",
                    {"trade_id": trade["id"], "reason": reason},
                )
                return False
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(bybit_api.fetch_real_balance())
            except RuntimeError:
                pass
        else:
            bybit_api.execute_market_close(
                trade["pair"],
                trade["side"],
                f"{reason} | Realized Net P&L: ${metrics['net_usd']:.2f} ({metrics['net_pct']:.3f}%)",
            )

        if bybit_api.mode != "LIVE_TRADING":
            reserved = float(trade.get("capital_reserved") or trade.get("margin") or 0)
            self.current_capital = round(self.current_capital + reserved + metrics["net_usd"], 2)
            print(
                f"[CAPITAL] Released ${reserved:,.2f} + net ${metrics['net_usd']:,.2f} "
                f"from #{trade['id']} → available ${self.current_capital:,.2f}."
            )

        self._finalize_trade_history(trade, metrics, reason)
        print(
            f"[PILLAR 3: AI AGENT] Closed {trade['side']} #{trade['id']} on {trade['pair']} "
            f"| net=${metrics['net_usd']:.2f} ({metrics['net_pct']:.3f}%)"
        )
        exchange_note = " (Paper)" if trade.get("exchange") == "paper" else (
            " (Bybit TESTNET)" if trade_uses_bybit_executor(trade) else ""
        )
        notifications.push(
            f"Position #{trade['id']} CLOSED{exchange_note} ({trade['side']}) {trade['pair']} | "
            f"Net P&L: ${metrics['net_usd']:.2f} ({metrics['net_pct']:.3f}%)",
            "success" if metrics["net_usd"] >= 0 else "error",
        )
        return True

    def has_opposite_position(self, side: str, pair: str) -> bool:
        opposite = "SHORT" if side == "LONG" else "LONG"
        return any(t["pair"] == pair and t["side"] == opposite for t in self.trades)

    def has_duplicate_auto_entry(
        self,
        side: str,
        pair: str,
        pattern: str | None,
        signal_candle_time: int | None,
        entry_price: float,
    ) -> bool:
        """Block stacking: same candle (any pattern), or same side+pattern near same price."""
        for t in self.trades:
            if t.get("source") != "auto" or t["pair"] != pair:
                continue
            # One auto entry per signal candle — stops every-pattern spam on same bar.
            if (
                signal_candle_time
                and t.get("signal_candle_time") is not None
                and int(t["signal_candle_time"]) == int(signal_candle_time)
            ):
                return True
            if t["side"] != side:
                continue
            if (
                pattern
                and signal_candle_time
                and t.get("pattern") == pattern
                and t.get("signal_candle_time") == signal_candle_time
            ):
                return True
            if pattern and t.get("pattern") == pattern and entry_price > 0:
                if abs(t["entry"] - entry_price) / entry_price < 0.0002:
                    return True
        return False

    def last_auto_entry_candle_time(self, pair: str) -> int | None:
        times = [
            int(t["signal_candle_time"])
            for t in self.trades
            if t.get("source") == "auto"
            and t.get("pair") == pair
            and t.get("signal_candle_time") is not None
        ]
        return max(times) if times else None

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
        auto_winners = [
            (t, m) for t, m in scored if m["net_pct"] > 0 and t.get("source") != "manual"
        ]
        if not auto_winners:
            print("[PILLAR 3: AI AGENT] No auto trades in net profit to close — manual positions protected, losers held.")
            return

        still_open = []
        for trade, m in scored:
            if m["net_pct"] > 0 and trade.get("source") != "manual":
                if not self._close_single_trade(trade, m, reason):
                    still_open.append(trade)
            else:
                still_open.append(trade)

        self.trades = still_open

        # Recompute trailing-lock state off whatever remains.
        if not self.trades:
            self.is_lock_active = False
            self.peak_net_pct = 0.0
        else:
            remaining = [self._trade_metrics(t)["gross_pct"] for t in self.trades]
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
        still_open = []
        for trade in list(self.trades):
            m = self._trade_metrics(trade)
            if not self._close_single_trade(trade, m, reason):
                still_open.append(trade)
        self.trades = still_open
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
# Bybit public market data (no API key) — linear USDT perpetual symbols.
BYBIT_SYMBOL_MAP = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "XRP": "XRPUSDT",
    "LTC": "LTCUSDT",
    # XMR: no Bybit linear market — UI selectable; synthetic feed only; auto loop skips.
}

def get_bybit_symbol(pair_label):
    symbol = (pair_label or "").split("/")[0]
    return BYBIT_SYMBOL_MAP.get(symbol)

# Chart timeframe (seconds) → UVSS key → Bybit kline interval.
# Frontend: 1M/5M/15M/1H/1D. Bybit has no native 30s/10m (fallbacks below).
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

# Per-pair last-processed CLOSED candle timestamp, keyed by pair label - keeping
# this per-pair (not one shared scalar) means switching pairs never needs a
# manual reset: a pair's own last-seen timestamp is either genuinely stale
# (correctly triggers a re-scan) or doesn't exist yet (defaults to 0). A
# TIMEFRAME change is the only case that needs an explicit reset (see
# set_timeframe below) since a candle's close_time isn't comparable across
# different candle granularities.
LAST_CANDLE_TIMESTAMPS = {}

# Cached OHLCV windows for UVSS — avoids refetching 200+ klines on every poll.
_KLINE_HISTORY_CACHE: dict[str, dict] = {}

# Ultra-fast entry loop tuning (seconds). Override via env if needed.
_AUTO_BUY_BURST_POLL = float(os.environ.get("AUTO_BUY_BURST_POLL", "0.12"))
_AUTO_BUY_FAST_POLL = float(os.environ.get("AUTO_BUY_FAST_POLL", "0.35"))
_AUTO_BUY_TICKER_POLL = float(os.environ.get("MARKET_TICKER_POLL", "0.35"))

# Prevent concurrent / duplicate auto fires for the same candle+pattern.
_trade_fire_lock = asyncio.Lock()
_recent_signal_fire_keys: set[str] = set()

# Pairs already warned about a failing candle fetch (e.g. no Bybit LINEAR/USDT
# Perpetual market for that symbol - several of BYBIT_SYMBOL_MAP's smaller-cap
# tokens may only have a SPOT listing). Warns once per pair instead of every
# failed poll cycle, so a genuinely-unsupported pair doesn't spam the bell.
_CANDLE_FETCH_WARNED_PAIRS = set()

async def fetch_kline_history(bybit_symbol, timeframe_key, limit=None, client=None):
    """Fetch chronological OHLCV history (oldest first) for UVSS."""
    from volume_spread_system import parse_bybit_kline, MIN_CANDLES as _min

    need = limit or max(_min + 5, 230)
    bybit_interval = TIMEFRAME_KEY_TO_BYBIT_KLINE.get(timeframe_key, "1")
    if client is not None:
        rows = await fetch_kline_rows(client, bybit_symbol, bybit_interval, need)
    else:
        async with httpx.AsyncClient(timeout=5.0) as _client:
            rows = await fetch_kline_rows(_client, bybit_symbol, bybit_interval, need)
    candles = [parse_bybit_kline(row) for row in reversed(rows)]
    if len(candles) >= 2:
        candles = candles[:-1]
    return candles


def _invalidate_kline_cache(pair: str | None = None) -> None:
    """Drop cached kline windows after pair/timeframe switch."""
    if pair is None:
        _KLINE_HISTORY_CACHE.clear()
        return
    prefix = f"{pair}|"
    for key in list(_KLINE_HISTORY_CACHE):
        if key.startswith(prefix):
            del _KLINE_HISTORY_CACHE[key]


def _auto_buy_poll_seconds(timeframe_key: str, timeframe_seconds: int) -> float:
    """Adaptive poll — burst right after each candle close so entries fire in <1s."""
    into_bucket = time.time() % max(timeframe_seconds, 1)
    # First ~4s of a new bar: Bybit publishes the closed candle here — poll aggressively.
    if into_bucket <= 4.0:
        return _AUTO_BUY_BURST_POLL
    # Last 2s before close: watch for early pattern completion on forming bar (next loop picks closed).
    if into_bucket >= max(timeframe_seconds - 2.0, 0):
        return 0.2
    if timeframe_key in ("30s", "1m"):
        return _AUTO_BUY_FAST_POLL
    if timeframe_key in ("5m", "15m"):
        return 0.8
    if timeframe_key == "1h":
        return 2.0
    return 3.0


async def probe_latest_closed_kline(client, bybit_symbol, timeframe_key) -> dict | None:
    """Tiny 3-kline probe — detects new closed bar without downloading full history."""
    from volume_spread_system import parse_bybit_kline

    bybit_interval = TIMEFRAME_KEY_TO_BYBIT_KLINE.get(timeframe_key, "1")
    rows = await fetch_kline_rows(client, bybit_symbol, bybit_interval, 3)
    if len(rows) < 2:
        return None
    candles = [parse_bybit_kline(row) for row in reversed(rows)]
    if len(candles) >= 2:
        candles = candles[:-1]
    return candles[-1] if candles else None


async def resolve_uvss_history(
    client,
    pair_label: str,
    bybit_symbol: str,
    timeframe_key: str,
    latest_closed: dict,
) -> list[dict]:
    """Return full UVSS window — incremental slide when possible, else one REST pull."""
    cache_key = f"{pair_label}|{timeframe_key}"
    close_time = latest_closed["close_time"]
    cached = _KLINE_HISTORY_CACHE.get(cache_key)

    if cached and cached.get("candles"):
        history = cached["candles"]
        last_ct = history[-1]["close_time"] if history else 0
        if last_ct == close_time:
            return history
        if last_ct < close_time and len(history) >= MIN_CANDLES:
            history = history[1:] + [latest_closed]
            if len(history) >= MIN_CANDLES:
                _KLINE_HISTORY_CACHE[cache_key] = {"close_time": close_time, "candles": history}
                return history

    history = await fetch_kline_history(bybit_symbol, timeframe_key, client=client)
    _KLINE_HISTORY_CACHE[cache_key] = {"close_time": close_time, "candles": history}
    return history


async def fetch_closed_candle_ohlc(bybit_symbol, timeframe_key):
    """ Reads the last 2 klines from Bybit's LINEAR (USDT Perpetual) market -
    matching where bybit_executor.py actually places orders, not the spot
    feed the dashboard's price simulation uses - and returns the previous,
    fully closed candle (index 0 is still forming). Native httpx/async so it
    never blocks the event loop (unlike pybit's sync client). """
    bybit_interval = TIMEFRAME_KEY_TO_BYBIT_KLINE.get(timeframe_key, "1")
    async with httpx.AsyncClient(timeout=6.0) as client:
        candles = await fetch_kline_rows(client, bybit_symbol, bybit_interval, 2)
    closed = candles[1]
    # Bybit's kline row is [startTime, open, high, low, close, volume, turnover] -
    # startTime doubles as a unique, strictly-increasing per-candle id.
    return {"high": float(closed[2]), "low": float(closed[3]), "close_time": int(closed[0])}

# Auto-order sizing: 10% of available capital per fired trade.
AUTO_TRADE_CAPITAL_PCT = 0.10

# Fire discipline — stop "every candle / every pattern" spam.
MIN_PATTERN_STRENGTH = float(os.environ.get("MIN_PATTERN_STRENGTH", "0.7"))
MIN_BARS_BETWEEN_AUTO_ENTRIES = int(os.environ.get("MIN_BARS_BETWEEN_AUTO_ENTRIES", "3"))
BLOCK_OPPOSITE_AUTO_SIDE = os.environ.get("BLOCK_OPPOSITE_AUTO_SIDE", "true").strip().lower() in (
    "1", "true", "yes", "on",
)

# Strict Exit Logic — auto trades only (see AITradingAgent._evaluate_strict_exit).
AUTO_TRADE_AUTO_EXIT_ENABLED = True

# False = normal fire: BUY pattern → LONG, SELL pattern → SHORT.
INVERT_AUTO_TRADE_FIRE = False


def qty_decimals_for_price(price: float) -> int:
    """ Precision for base-asset qty — BTC-sized prices need extra decimals. """
    if price >= 10000:
        return 6
    if price >= 1000:
        return 5
    if price >= 1:
        return 4
    return 2


def compute_auto_trade_plan(agent, price: float | None = None, size_mult: float = 1.0) -> dict | None:
    """Auto sizing: 10% of available capital per fire; next fire uses what remains."""
    entry_price = _sanitize_market_price(price if price is not None else agent.current_price)
    if entry_price is None:
        return None
    available = agent.get_available_capital()
    if available is None or available <= 0:
        return None
    mult = max(1.0, float(size_mult))
    position_usd = round(available * AUTO_TRADE_CAPITAL_PCT * mult, 2)
    if position_usd <= 0:
        return None
    decimals = qty_decimals_for_price(entry_price)
    qty = round(position_usd / entry_price, decimals)
    if qty <= 0:
        return None
    margin = round(position_usd / agent.leverage, 4)
    return {
        "total_capital": round(available, 2),
        "available_capital": round(available, 2),
        "position_usd": position_usd,
        "capital_pct": AUTO_TRADE_CAPITAL_PCT * 100 * mult,
        "size_mult": mult,
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
    """Surface auto-entry skips in the System Log modal (one line per event)."""
    cost_aware = extra.get("cost_aware")
    system_log.set_last_trade_fire(
        {
            "success": False,
            "skipped": True,
            "action": action,
            "symbol": symbol,
            "pattern": pattern,
            "error": reason,
            **extra,
        },
        emit_log=False,
    )
    if cost_aware:
        msg = f"SKIPPED (cost-aware): {action} {symbol} | {pattern or 'n/a'} — {reason}"
    else:
        msg = f"SKIPPED: {action} {symbol} — {reason}"
    system_log.push("trade", msg, {"pattern": pattern, **extra})
    print(f"[AUTO BUY LOOP] Trade skipped: {reason}")

def signal_action_to_trade_side(action: str) -> str:
    """BUY -> LONG, SELL -> SHORT."""
    if action == "BUY":
        return "LONG"
    if action == "SELL":
        return "SHORT"
    return "LONG"


def invert_signal_action(action: str) -> str:
    """Flip execution only — long/BUY pattern fires SELL, short/SELL pattern fires BUY."""
    if action == "BUY":
        return "SELL"
    if action == "SELL":
        return "BUY"
    return action


def execution_action_for_fire(signal_action: str) -> str:
    """Map UVSS signal action to the order the agent actually places."""
    if INVERT_AUTO_TRADE_FIRE:
        return invert_signal_action(signal_action)
    return signal_action


def signal_action_to_bybit_order_action(action: str) -> str:
    """Bybit market open: BUY -> Buy (long), SELL -> Sell (short)."""
    if action in ("BUY", "SELL"):
        return action
    return "BUY"


# Back-compat aliases (older call sites / logs).
taapi_action_to_trade_side = signal_action_to_trade_side
taapi_action_to_bybit_order_action = signal_action_to_bybit_order_action


def load_system_role_text() -> str:
    """AI agent training corpus — loaded as system prompt (not exposed in UI)."""
    parts: list[str] = []
    for name in (
        "SYSTEM_ROLE_AND_IDENTITY.md",
        "AGENT_STRATEGY.md",
        "CANDLESTICK_PATTERNS_INTRO.md",
        "CANDLESTICK_BIBLE_INDEX.md",
        "ML_TRADING_PAPER_INDEX.md",
    ):
        path = _DATA_DIR / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8").strip()
            if text:
                parts.append(text)
        except OSError:
            continue
    try:
        parts.append(strategy_system_blurb())
    except Exception:
        pass
    # Compact in-RAM bible TOC (full sections fetched via fetch_bible, not pasted every turn).
    try:
        blurb = bible_system_prompt_blurb()
        if blurb:
            parts.append(blurb)
    except Exception:
        pass
    try:
        ml_blurb = ml_system_prompt_blurb()
        if ml_blurb:
            parts.append(ml_blurb)
    except Exception:
        pass
    if INVERT_AUTO_TRADE_FIRE:
        parts.append(
            "EXECUTION OVERRIDE (pattern engine unchanged): "
            "When the scanner flags a long/BUY-class pattern (BB-L, L1–L5, MBZ-L, MOM-L), "
            "place SELL (SHORT). When it flags a short/SELL-class pattern (BB-S, S1–S4, MBZ-S, MOM-S), "
            "place BUY (LONG). Do not change how patterns are detected — only invert the order side at fire."
        )
    return "\n\n---\n\n".join(parts)


def fetch_bible_context_for_signal(pattern_or_query: str | None, *, max_chars: int = 1800) -> str:
    """Pull matching bible section from RAM for AI confirmation (microsecond lookup)."""
    if not pattern_or_query:
        return ""
    # Prefer enrich_signal path when full decision dict fields are available later.
    hit = fetch_bible(str(pattern_or_query), max_chars=max_chars)
    if hit.get("ok"):
        return (
            f"[Candlestick Bible · {hit.get('title')} · "
            f"fetch_ns={hit.get('fetch_ns')}]\n{hit.get('text') or ''}"
        )
    results = search_bible(str(pattern_or_query), limit=1)
    if not results:
        return ""
    hit = fetch_bible(results[0]["id"], max_chars=max_chars)
    if not hit.get("ok"):
        return ""
    return (
        f"[Candlestick Bible · {hit.get('title')} · "
        f"fetch_ns={hit.get('fetch_ns')}]\n{hit.get('text') or ''}"
    )


def agent_policy_summary() -> str:
    """Policy text shown in System Log."""
    if UVSS_POLICIES_ENABLED:
        exec_mode = (
            "paper ledger (simulated fills)"
            if bybit_api.mode == "PAPER_TRADING"
            else "Bybit TESTNET linear (real open/close)"
        )
        exit_note = (
            f"Strict Exit (+{agent.STRICT_EXIT_HARD_TARGET_PCT:.1f}% hard target, "
            f"+{agent.STRICT_EXIT_MIN_LOCK_PCT:.2f}% min lock, "
            f"{agent.STRICT_EXIT_TRAIL_MULTIPLIER}×{agent.STRICT_EXIT_FLUCTUATION_X_PCT:.2f}% trail)"
            if AUTO_TRADE_AUTO_EXIT_ENABLED
            else "no auto-exit (manual close / STOP only)"
        )
        whale_note = (
            f" + WhaleBotAlerts ≥{MIN_BTC_AMOUNT:.0f} BTC (Unknown→Exch=SHORT, Exch→Unknown=LONG)"
            if is_btc_pair(agent.active_pair)
            else ""
        )
        return (
            f"Candle patterns + Bible + ML cost-aware{whale_note} | BUY→LONG SELL→SHORT | {exit_note}, no SL exit | "
            f"risk {AUTO_TRADE_CAPITAL_PCT * 100:.0f}% of available capital per auto fire | {exec_mode}"
        )
    return "Auto trade policies not active."


def _auto_entry_skip_reason(trade, fired: bool, order_error: str | None) -> str | None:
    if fired and trade:
        return None
    if order_error:
        return order_error
    if not agent.is_active:
        return "AI automation is not running — click START AI AUTOMATION."
    if agent.emergency_triggered:
        return "Emergency halt is active."
    if len(agent.trades) >= agent.max_concurrent_trades:
        return f"Max concurrent trades ({agent.max_concurrent_trades}) reached."
    return "Auto entry blocked (invalid price, zero balance, or register failed)."


def _log_auto_trade_fire(
    *,
    success: bool,
    mode: str,
    action: str,
    bybit_symbol: str,
    pattern: str | None,
    qty,
    position_usd: float,
    plan: dict,
    result: dict,
    trade_id: int | None,
    error: str | None,
):
    system_log.set_last_trade_fire({
        "success": success,
        "mode": mode,
        "action": action,
        "symbol": bybit_symbol,
        "pattern": pattern,
        "qty": qty,
        "position_usd": position_usd,
        "capital_total": plan.get("total_capital"),
        "capital_pct": plan.get("capital_pct"),
        "sl": result.get("sl"),
        "tp": result.get("tp"),
        "entry": result.get("entry"),
        "reason": result.get("reason"),
        "error": error,
        "trade_id": trade_id,
        "signal_candle_time": result.get("signal_candle_time"),
    })


def _signal_fire_key(pair: str, timeframe_key: str, action: str, pattern: str | None, candle_close_time: int) -> str:
    # One fire slot per candle (pattern/action ignored) — stops multi-pattern spam.
    return f"{pair}|{timeframe_key}|candle|{candle_close_time}"


def _register_signal_fire(key: str) -> None:
    _recent_signal_fire_keys.add(key)
    if len(_recent_signal_fire_keys) > 200:
        # Bound memory — keys are per unique candle; old entries drop off naturally.
        _recent_signal_fire_keys.clear()


async def fire_taapi_auto_trade(
    result: dict,
    bybit_symbol: str,
    plan: dict,
    position_size_usd: float,
    qty,
    *,
    candle_close_time: int | None = None,
    timeframe_key: str | None = None,
    signal_action: str | None = None,
) -> tuple[dict | None, bool, str | None]:
    """Unified auto-entry — execution action may be inverted vs UVSS signal (see INVERT_AUTO_TRADE_FIRE)."""
    exec_action = result["action"]
    orig_signal = signal_action or exec_action
    async with _trade_fire_lock:
        side = taapi_action_to_trade_side(exec_action)
        entry_px = result.get("entry") or agent.current_price
        pattern = result.get("pattern")
        invert_note = (
            f" (pattern signal {orig_signal} → fire {exec_action})"
            if INVERT_AUTO_TRADE_FIRE and orig_signal != exec_action
            else ""
        )
        reason = f"Pattern {pattern or 'signal'} ({orig_signal}→{exec_action}) 1:{RR_RATIO:.0f} R:R{invert_note}"

        if candle_close_time is not None and timeframe_key:
            fire_key = _signal_fire_key(
                agent.active_pair, timeframe_key, exec_action, pattern, candle_close_time
            )
            if fire_key in _recent_signal_fire_keys:
                err = (
                    f"Duplicate candle blocked — already fired on {agent.active_pair} "
                    f"candle {candle_close_time}."
                )
                _log_trade_skip(exec_action, bybit_symbol, pattern, err)
                return None, False, err

        strength = float(result.get("strength") or 0)
        if strength < MIN_PATTERN_STRENGTH:
            err = (
                f"Pattern strength {strength:.2f} < min {MIN_PATTERN_STRENGTH:.2f} — skipped."
            )
            _log_trade_skip(exec_action, bybit_symbol, pattern, err)
            return None, False, err

        if BLOCK_OPPOSITE_AUTO_SIDE and agent.has_opposite_position(side, agent.active_pair):
            err = (
                f"Opposite open position on {agent.active_pair} — "
                f"won't flip to {side} until flat or same-side only."
            )
            _log_trade_skip(exec_action, bybit_symbol, pattern, err)
            return None, False, err

        if candle_close_time is not None and MIN_BARS_BETWEEN_AUTO_ENTRIES > 0:
            last_ct = agent.last_auto_entry_candle_time(agent.active_pair)
            if last_ct is not None:
                # signal_candle_time is often ms from Bybit; normalize to seconds for bar math.
                cur = int(candle_close_time)
                prev = int(last_ct)
                if cur > 1_000_000_000_000:
                    cur //= 1000
                if prev > 1_000_000_000_000:
                    prev //= 1000
                tf_secs = {"30s": 30, "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1D": 86400}.get(
                    timeframe_key or "1m", 60
                )
                bars_apart = abs(cur - prev) / max(tf_secs, 1)
                if bars_apart < MIN_BARS_BETWEEN_AUTO_ENTRIES:
                    err = (
                        f"Cooldownoldown: need {MIN_BARS_BETWEEN_AUTO_ENTRIES} bars between auto entries "
                        f"(only {bars_apart:.1f} since last)."
                    )
                    _log_trade_skip(exec_action, bybit_symbol, pattern, err)
                    return None, False, err

        if agent.has_duplicate_auto_entry(side, agent.active_pair, pattern, candle_close_time, float(entry_px)):
            err = (
                f"Duplicate open position blocked — {side} {pattern or 'signal'} "
                f"already active on {agent.active_pair}."
            )
            _log_trade_skip(exec_action, bybit_symbol, pattern, err)
            return None, False, err

        # Opposite LONG/SHORT blocked above when BLOCK_OPPOSITE_AUTO_SIDE is on.

        if bybit_api.mode == "PAPER_TRADING":
            trade = agent.open_trade(
                side,
                reason=reason,
                source="auto",
                position_size_usd=position_size_usd,
                qty=qty,
                entry_price=entry_px,
                exchange="paper",
                bybit_symbol=bybit_symbol,
                pattern=pattern,
                signal_candle_time=candle_close_time,
                taapi_action=result["action"],
                sl_price=result.get("sl"),
                tp_price=result.get("tp"),
                target_mult=result.get("target_mult"),
            )
            fired = trade is not None
            order_error = None
            log_mode = "PAPER_TRADING"
        else:
            if not is_bybit_testnet_configured():
                return None, False, (
                    "BYBIT_TESTNET_API_KEY / BYBIT_TESTNET_API_SECRET not set — "
                    "add keys from https://testnet.bybit.com to backend/.env"
                )

            executor = get_bybit_executor_agent()
            bybit_order = {**result, "action": taapi_action_to_bybit_order_action(result["action"])}
            fired, order_error = await asyncio.to_thread(executor.execute_trade, bybit_order, qty)
            if not fired and order_error:
                system_log.push(
                    "bybit",
                    f"Order rejected: {order_error}",
                    {"symbol": bybit_symbol, "qty": qty, "pattern": pattern, "action": result["action"]},
                )
            trade = None
            if fired:
                trade = agent.open_trade(
                    side,
                    reason=reason,
                    source="auto",
                    position_size_usd=position_size_usd,
                    qty=qty,
                    skip_exchange_open=True,
                    entry_price=entry_px,
                    exchange="bybit_linear_testnet",
                    bybit_symbol=bybit_symbol,
                    pattern=pattern,
                    signal_candle_time=candle_close_time,
                    taapi_action=result["action"],
                    sl_price=result.get("sl"),
                    tp_price=result.get("tp"),
                    target_mult=result.get("target_mult"),
                )
                if not trade:
                    fired = False
                    order_error = (
                        order_error
                        or "Bybit order filled but local trade register failed — check TESTNET positions."
                    )
                    notifications.push(
                        f"CRITICAL: Bybit order filled for {bybit_symbol} but dashboard register failed. "
                        "Position may be open on TESTNET untracked.",
                        "error",
                    )
            log_mode = "BYBIT_TESTNET"

        if fired and trade and candle_close_time is not None and timeframe_key:
            _register_signal_fire(
                _signal_fire_key(agent.active_pair, timeframe_key, result["action"], pattern, candle_close_time)
            )

        skip_reason = _auto_entry_skip_reason(trade, fired, order_error)
        _log_auto_trade_fire(
            success=bool(fired and trade),
            mode=log_mode,
            action=result["action"],
            bybit_symbol=bybit_symbol,
            pattern=pattern,
            qty=qty,
            position_usd=position_size_usd,
            plan=plan,
            result=result,
            trade_id=trade["id"] if trade else None,
            error=skip_reason,
        )

        if fired and trade:
            venue = "PAPER" if log_mode == "PAPER_TRADING" else "TESTNET"
            signal_note = (
                f"{orig_signal}→{exec_action}"
                if INVERT_AUTO_TRADE_FIRE and orig_signal != exec_action
                else exec_action
            )
            notifications.push(
                f"{venue} {side} opened ({pattern or 'signal'} {signal_note}): {agent.active_pair} | {qty} @ ~${plan['price']:,.0f} | "
                f"${position_size_usd} ({AUTO_TRADE_CAPITAL_PCT * 100:.0f}% of ${plan['total_capital']:,.0f} available) | "
                f"TP={result.get('tp')} SL={result.get('sl')} | pattern={pattern} #{trade['id']}",
                "success",
            )
        elif skip_reason:
            notifications.push(f"Trade skipped: {skip_reason}", "warning")

        return trade, bool(fired and trade), skip_reason


_bybit_executor_agent = None
_bybit_testnet_keys_warned = False

def trade_uses_bybit_executor(trade: dict) -> bool:
    """True when this trade was opened on Bybit TESTNET linear and needs a real close."""
    return (
        trade.get("exchange") == "bybit_linear_testnet"
        and is_bybit_testnet_configured()
    )


def bybit_close_trade(trade: dict) -> tuple[bool, str | None]:
    executor = get_bybit_executor_agent()
    return executor.close_position(trade)


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

async def _fetch_bybit_linear_ticker_price(client: httpx.AsyncClient, bybit_symbol: str) -> float | None:
    """Bybit USDT perpetual (linear) lastPrice — public REST, no API key."""
    return await fetch_ticker_last_price(client, bybit_symbol)


async def fetch_bybit_linear_price(pair_label):
    """Latest linear perpetual last price for pair switching / seeding current_price."""
    symbol = get_bybit_symbol(pair_label)
    if not symbol:
        return None
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            return await _fetch_bybit_linear_ticker_price(client, symbol)
    except Exception as exc:
        print(f"[MARKET FEED] Could not fetch linear price for {pair_label}: {exc}")
        return None

# Tracks the last time bybit_price_feed successfully processed a REAL tick,
# so market_simulator can tell "actively receiving real data" apart from
# "silently stuck" (network hiccup, DNS issue, host blocking outbound, etc.)
# and self-heal by taking over with synthetic movement instead of leaving
# current_price frozen forever.
_last_real_feed_update = 0.0
REAL_FEED_STALE_AFTER_SECONDS = 10

# ==========================================
# ENTRY POLICY: Candle patterns → Bible → ML cost-aware → fire
# ==========================================

async def auto_buy_loop():
    print(
        "[AUTO BUY LOOP] Candle pattern → Bible → ML cost-aware — closed-candle scan "
        f"(burst poll {_AUTO_BUY_BURST_POLL}s after each bar close)."
    )
    async with httpx.AsyncClient(timeout=5.0) as client:
        while True:
            timeframe_key = SECONDS_TO_TIMEFRAME_KEY.get(agent.timeframe_seconds, "1m")
            poll = _auto_buy_poll_seconds(timeframe_key, agent.timeframe_seconds)

            if not agent.is_active or agent.emergency_triggered:
                await asyncio.sleep(poll)
                continue

            if not UVSS_POLICIES_ENABLED:
                await asyncio.sleep(poll)
                continue

            bybit_symbol = get_bybit_symbol(agent.active_pair)
            if bybit_symbol is None:
                await asyncio.sleep(poll)
                continue

            try:
                latest_closed = await probe_latest_closed_kline(client, bybit_symbol, timeframe_key)
            except Exception as exc:
                print(f"[AUTO BUY LOOP] Kline probe failed for {bybit_symbol}: {exc}")
                if agent.active_pair not in _CANDLE_FETCH_WARNED_PAIRS:
                    _CANDLE_FETCH_WARNED_PAIRS.add(agent.active_pair)
                    notifications.push(
                        f"Pattern engine can't read {bybit_symbol} candles — auto-entries paused for {agent.active_pair}.",
                        "warning",
                    )
                await asyncio.sleep(max(poll, 1.0))
                continue
            _CANDLE_FETCH_WARNED_PAIRS.discard(agent.active_pair)

            if latest_closed is None:
                await asyncio.sleep(poll)
                continue

            close_time = latest_closed["close_time"]
            if close_time <= LAST_CANDLE_TIMESTAMPS.get(agent.active_pair, 0):
                await asyncio.sleep(poll)
                continue

            t_detect = time.time()
            LAST_CANDLE_TIMESTAMPS[agent.active_pair] = close_time
            print(f"🔄 New {timeframe_key} candle for {agent.active_pair} @ {close_time} — scanning NOW")

            try:
                history = await resolve_uvss_history(
                    client, agent.active_pair, bybit_symbol, timeframe_key, latest_closed
                )
            except Exception as exc:
                print(f"[AUTO BUY LOOP] History resolve failed for {bybit_symbol}: {exc}")
                await asyncio.sleep(poll)
                continue

            if len(history) < MIN_CANDLES:
                await asyncio.sleep(poll)
                continue

            signal_candle = history[-1]
            result = evaluate_uvss(history, timeframe_key, pair=agent.active_pair)
            # Brain enrich only on actionable signals (keep fire path lean).
            if result.get("action") in ("BUY", "SELL"):
                result = enrich_signal(result)
            candle = {
                "high": signal_candle["high"],
                "low": signal_candle["low"],
                "close": signal_candle["close"],
                "close_time": close_time,
            }
            cost_aware = None
            if UVSS_COST_AWARE_ENTRY:
                fee_pct = bybit_api.get_taker_fee_pct()
                cost_aware = evaluate_cost_aware_entry(
                    result,
                    candle,
                    agent.current_price,
                    timeframe_key,
                    fee_pct,
                )

            # Fire first on BUY/SELL — logging/chat after order path to cut latency.
            fired_trade = False
            if result["action"] in ("BUY", "SELL"):
                if not (cost_aware and cost_aware.get("would_block") and not cost_aware.get("dry_run")):
                    balance = agent.get_available_capital()
                    if balance is not None and balance > 0:
                        entry_px = result.get("entry") or agent.current_price
                        plan = compute_auto_trade_plan(agent, float(entry_px))
                        if plan is not None and result.get("sl") is not None:
                            position_size_usd = plan["position_usd"]
                            qty = plan["qty"]
                            sl_px = result.get("sl")
                            tp_px = result.get("tp")
                            signal_action = result["action"]
                            fire_payload = {**result, "action": execution_action_for_fire(signal_action)}
                            fire_payload["symbol"] = bybit_symbol
                            fire_payload["signal_candle_time"] = close_time

                            if is_bybit_testnet_configured() or bybit_api.mode == "PAPER_TRADING":
                                log_trade_execution(
                                    taapi_action_to_trade_side(execution_action_for_fire(result["action"])),
                                    float(entry_px),
                                    float(sl_px),
                                    float(tp_px) if tp_px else float(entry_px),
                                    float(qty),
                                    float(balance),
                                    result.get("pattern", "signal"),
                                )
                                await fire_taapi_auto_trade(
                                    fire_payload,
                                    bybit_symbol,
                                    plan,
                                    position_size_usd,
                                    qty,
                                    candle_close_time=close_time,
                                    timeframe_key=timeframe_key,
                                    signal_action=signal_action,
                                )
                                fired_trade = True
                                ms = (time.time() - t_detect) * 1000
                                print(f"[AUTO BUY LOOP] Trade fire completed in {ms:.0f}ms after bar close")
                            else:
                                global _bybit_testnet_keys_warned
                                err = (
                                    "BYBIT_TESTNET_API_KEY / BYBIT_TESTNET_API_SECRET not set — "
                                    "add keys from https://testnet.bybit.com to backend/.env"
                                )
                                if not _bybit_testnet_keys_warned:
                                    _bybit_testnet_keys_warned = True
                                    system_log.push("bybit", err, {"symbol": bybit_symbol})
                                    notifications.push(
                                        "Bybit TESTNET keys missing — signals fire but orders cannot execute.",
                                        "error",
                                    )
                                _log_trade_skip(result["action"], bybit_symbol, result.get("pattern"), err)
                        elif plan is None:
                            _log_trade_skip(
                                result["action"], bybit_symbol, result.get("pattern"),
                                f"Could not size 10% trade (available=${balance}, entry=${entry_px}).",
                            )
                        else:
                            _log_trade_skip(
                                result["action"], bybit_symbol, result.get("pattern"),
                                "Signal missing stop-loss level.",
                            )
                    else:
                        _log_trade_skip(
                            result["action"], bybit_symbol, result.get("pattern"),
                            f"Insufficient available capital (balance=${balance}).",
                        )
                else:
                    reason = cost_aware.get("block_reason") or "Cost-aware entry gate blocked weak signal."
                    _log_trade_skip(result["action"], bybit_symbol, result.get("pattern"), reason, cost_aware=cost_aware)

            # Deferred UI/logging (never blocks the fire path above).
            system_log.set_last_uvss_scan(
                agent.active_pair, timeframe_key, result, candle, cost_aware=cost_aware
            )
            system_log.push_agent_chat(
                f"AI brain scanned closed {timeframe_key} candle on {agent.active_pair} "
                f"— detect → Bible → ML gate…",
                status="scanning",
                details={
                    "pair": agent.active_pair,
                    "timeframe": timeframe_key,
                    "close_time": close_time,
                    "fire_ms": round((time.time() - t_detect) * 1000, 1),
                },
            )

            if result["action"] in ("BUY", "SELL"):
                exec_action = execution_action_for_fire(result["action"])
                fire_hint = (
                    f"signal {result['action']} → fire {exec_action} (inverted)"
                    if INVERT_AUTO_TRADE_FIRE and exec_action != result["action"]
                    else str(result["action"])
                )
                system_log.push_agent_chat(
                    brain_chat_summary(result)
                    + f" → {fire_hint} (1:{RR_RATIO:.0f} R:R)"
                    + (" — ORDER FIRED" if fired_trade else " — evaluating entry…"),
                    status="match",
                    details={"decision": result, "pair": agent.active_pair, "timeframe": timeframe_key},
                )
            elif result["action"] not in ("BUY", "SELL"):
                system_log.push_agent_chat(
                    f"No entry signal on {agent.active_pair} this bar. {result.get('reason', '')}",
                    status="no_match",
                    details={"decision": result, "pair": agent.active_pair, "timeframe": timeframe_key},
                )
                print(f"[AUTO BUY LOOP] {result['action']}: {result['reason']}")

            if cost_aware and cost_aware.get("would_block") and cost_aware.get("dry_run"):
                print(
                    f"[AUTO BUY LOOP] Cost-aware DRY-RUN would block {result['action']} "
                    f"{result.get('pattern')}: {cost_aware.get('block_reason')}"
                )

            await asyncio.sleep(_AUTO_BUY_BURST_POLL if fired_trade else poll)


async def whale_alert_loop():
    """BTC/USDT automation: fetch Telegram WhaleBotAlerts → fire LONG/SHORT alongside candle patterns."""
    print(
        f"[WHALE LOOP] BTC/USDT merge — poll {WHALE_SOURCE_URL} every {WHALE_POLL_SECONDS}s "
        f"(≥{MIN_BTC_AMOUNT:.0f} BTC Unknown↔Exchange)."
    )
    async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
        while True:
            if not agent.is_active or agent.emergency_triggered or not is_btc_pair(agent.active_pair):
                await asyncio.sleep(min(WHALE_POLL_SECONDS, 5.0))
                continue

            bybit_symbol = get_bybit_symbol(agent.active_pair) or "BTCUSDT"
            try:
                snap = await fetch_whale_alerts(client)
            except Exception as exc:
                print(f"[WHALE LOOP] fetch failed: {exc}")
                await asyncio.sleep(WHALE_POLL_SECONDS)
                continue

            if not snap.get("ok"):
                system_log.push(
                    "whale",
                    f"WhaleBotAlerts fetch failed: {snap.get('error')}",
                    {"source": WHALE_SOURCE_URL},
                )
                await asyncio.sleep(WHALE_POLL_SECONDS)
                continue

            signals = snap.get("signals") or []
            if not is_seeded():
                n = seed_seen_from_snapshot(signals)
                system_log.push(
                    "whale",
                    f"WhaleBotAlerts seeded — {n} existing alert(s) marked seen "
                    f"(will only fire on NEW ≥{MIN_BTC_AMOUNT:.0f} BTC flows).",
                    {"source": WHALE_SOURCE_URL},
                )
                await asyncio.sleep(WHALE_POLL_SECONDS)
                continue

            system_log.push(
                "whale",
                f"WhaleBotAlerts scanned — {len(signals)} qualifying signal(s) "
                f"(≥{MIN_BTC_AMOUNT:.0f} BTC) from {snap.get('raw_count', 0)} BTC posts.",
                {"min_btc": MIN_BTC_AMOUNT, "source": WHALE_SOURCE_URL},
            )

            for sig in signals:
                sid = sig.get("id")
                if not sid or is_signal_seen(sid):
                    continue

                entry_px = agent.current_price
                result = build_trade_plan_from_signal(sig, float(entry_px) if entry_px else 0.0)
                if not result:
                    mark_signal_seen(sid)
                    continue

                system_log.push_agent_chat(
                    f"Whale signal: {sig['reason']}",
                    status="match",
                    details={"decision": result, "pair": agent.active_pair},
                )

                balance = agent.get_available_capital()
                if balance is None or balance <= 0:
                    _log_trade_skip(
                        result["action"], bybit_symbol, result.get("pattern"),
                        f"Insufficient available capital (balance=${balance}).",
                    )
                    mark_signal_seen(sid)
                    continue

                plan = compute_auto_trade_plan(agent, float(result["entry"]))
                if plan is None:
                    _log_trade_skip(
                        result["action"], bybit_symbol, result.get("pattern"),
                        "Could not size whale trade.",
                    )
                    mark_signal_seen(sid)
                    continue

                candle_key = int(time.time())
                if is_bybit_testnet_configured() or bybit_api.mode == "PAPER_TRADING":
                    log_trade_execution(
                        signal_action_to_trade_side(result["action"]),
                        float(result["entry"]),
                        float(result["sl"]),
                        float(result["tp"]),
                        float(plan["qty"]),
                        float(balance),
                        result.get("pattern", "WHALE"),
                    )
                    await fire_taapi_auto_trade(
                        {**result, "action": execution_action_for_fire(result["action"]), "symbol": bybit_symbol},
                        bybit_symbol,
                        plan,
                        plan["position_usd"],
                        plan["qty"],
                        candle_close_time=candle_key,
                        timeframe_key="whale",
                        signal_action=result["action"],
                    )
                    mark_signal_seen(sid)
                    # One fire per poll cycle — avoid stacking every historical alert at once
                    break
                else:
                    _log_trade_skip(
                        result["action"], bybit_symbol, result.get("pattern"),
                        "BYBIT_TESTNET keys missing — whale signal logged only.",
                    )
                    mark_signal_seen(sid)
                    break

            await asyncio.sleep(WHALE_POLL_SECONDS)


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

async def bybit_price_feed():
    """ Keeps agent.current_price tracking REAL Bybit linear lastPrice every ~0.35s.
    ALWAYS calls process_tick on each successful poll so trailing profit
    booking never freezes when recent-trade has no new prints between polls.
    (Volume is unused for entries; UVSS closed-candle scans drive entries.) """
    global _last_real_feed_update
    print(f"[MARKET FEED] Background task starting (Bybit linear ticker poll, ~{_AUTO_BUY_TICKER_POLL}s).")
    current_symbol = None

    async with httpx.AsyncClient(timeout=6.0) as client:
        while True:
            target_symbol = get_bybit_symbol(agent.active_pair)
            if target_symbol is None:
                await asyncio.sleep(2)
                continue

            if target_symbol != current_symbol:
                current_symbol = target_symbol
                print(f"[MARKET FEED] Polling linear ticker for {target_symbol} ({agent.active_pair}).")

            try:
                price = await _fetch_bybit_linear_ticker_price(client, target_symbol)
                if price is not None:
                    await agent.process_tick(price, 0.0)
                    _last_real_feed_update = time.time()
                else:
                    print(f"[MARKET FEED] Invalid or missing ticker price for {target_symbol}")
            except Exception as exc:
                print(f"[MARKET FEED] Ticker poll error for {target_symbol}: {exc}")

            await asyncio.sleep(_AUTO_BUY_TICKER_POLL)

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
    try:
        stats = bible_memory_stats()
        print(
            f"[BIBLE MEMORY] RAM loaded: {stats.get('section_count')} sections · "
            f"{stats.get('total_chars')} chars · load_ns={stats.get('load_ns')} · "
            f"aliases={stats.get('aliases')}"
        )
        system_log.push(
            "ai",
            "Candlestick Trading Bible memory loaded into RAM (microsecond fetch ready).",
            stats,
        )
    except Exception as exc:
        print(f"[BIBLE MEMORY] load note: {exc}")
    try:
        ml_stats = ml_memory_stats()
        print(
            f"[ML MEMORY] RAM loaded: {ml_stats.get('section_count')} sections · "
            f"{ml_stats.get('total_chars')} chars · load_ns={ml_stats.get('load_ns')} · "
            f"arxiv={ml_stats.get('arxiv_id')}"
        )
        system_log.push(
            "ai",
            "ML Bitcoin trading paper memory loaded into RAM (microsecond fetch ready).",
            {k: v for k, v in ml_stats.items() if k != "takeaways"},
        )
    except Exception as exc:
        print(f"[ML MEMORY] load note: {exc}")
    asyncio.create_task(market_simulator())
    asyncio.create_task(bybit_price_feed())
    asyncio.create_task(bybit_balance_refresher())
    asyncio.create_task(self_ping_keepalive())
    asyncio.create_task(auto_buy_loop())
    asyncio.create_task(whale_alert_loop())
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
    tf_key = SECONDS_TO_TIMEFRAME_KEY.get(agent.timeframe_seconds, "1m")
    system_log.push_agent_chat(
        f"AI brain active on {agent.active_pair} ({tf_key}) — detect → Bible → ML cost-aware → fire.",
        status="active",
        details={"pair": agent.active_pair, "timeframe": tf_key},
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
        "message": (
            f"Bot active — Strict Exit Logic "
            f"(+{agent.STRICT_EXIT_HARD_TARGET_PCT:.1f}% target, "
            f"+{agent.STRICT_EXIT_MIN_LOCK_PCT:.2f}% min lock, "
            f"{agent.STRICT_EXIT_TRAIL_MULTIPLIER}× trail)."
        ),
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
    """ Trading mode for order execution. Chart + signals always use public Bybit linear data. """
    return {"mode": bybit_api.mode, "market_data": "bybit_public_linear"}

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
    """Manual BUY/SELL buttons: open LONG or SHORT (1% margin / 100x) on the active
    pair while AI automation is OFF. Each click adds one manual position."""
    side = payload.side.upper() if payload.side.upper() in ("LONG", "SHORT") else "LONG"
    if agent.emergency_triggered:
        return {"status": "error", "message": "Cannot open a position - emergency halt is active."}
    if agent.is_active:
        return {"status": "error", "message": "Stop AI automation before manual BUY/SELL."}
    if len(agent.trades) >= agent.max_concurrent_trades:
        return {"status": "error", "message": f"Max concurrent trades ({agent.max_concurrent_trades}) reached."}

    label = "BUY (LONG)" if side == "LONG" else "SELL (SHORT)"
    trade = agent.open_trade(side, reason=f"Manual {label} button", source="manual")
    if trade is None:
        if agent._live_insufficient_balance():
            return {"status": "error", "message": "Insufficient balance on your Bybit account."}
        return {"status": "error", "message": "Could not open a manual position."}
    return {
        "status": "success",
        "message": f"Manual {side} filled on {agent.active_pair}.",
        "trade": trade,
        "pair": agent.active_pair,
    }

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
    if not agent._close_single_trade(trade, m, "Manual force-close"):
        return {
            "status": "error",
            "message": "Could not close position on Bybit TESTNET — see notifications.",
        }
    agent.trades = [t for t in agent.trades if t["id"] != payload.id]
    return {"status": "success", "message": f"Position #{payload.id} closed at market price."}

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
    live_price = await fetch_bybit_linear_price(payload.pair)
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
    max_concurrent_trades: int | None = None


def _half_up_round(value: float) -> int:
    """ Round half UP (0.5 -> next integer), matching the modal's strict-integer
    rule. Python's built-in round() uses banker's rounding (round(2.5) == 2),
    which would break the UI's "0.5 or more rounds up" contract. """
    return math.floor(value + 0.5)


@app.post("/agent/config")
async def set_agent_config(payload: AgentConfigPayload):
    """ Applied from the "AI Agent Instructions" pre-start modal.
    - stop_loss_pct (risk level from the popup) -> max_concurrent_trades via
      round(stop_loss_pct * 1.5) (half-up) unless max_concurrent_trades is sent
      explicitly from the frontend (must match the modal display).
    - daily_profit_pct -> optional "Capital profit of the day" target; 0 disables.
    Both are validated and stored before /start-bot is called by the frontend. """
    if payload.stop_loss_pct < 0.5 or payload.stop_loss_pct > 100:
        return {"status": "error", "message": "Risk level must be between 0.5% and 100%."}
    if payload.daily_profit_pct < 0 or payload.daily_profit_pct > 1000:
        return {"status": "error", "message": "Daily profit target must be between 0% and 1000%."}

    if payload.max_concurrent_trades is not None:
        if payload.max_concurrent_trades < 1 or payload.max_concurrent_trades > 500:
            return {"status": "error", "message": "Concurrent trades must be between 1 and 500."}
        max_trades = payload.max_concurrent_trades
    else:
        max_trades = max(1, _half_up_round(payload.stop_loss_pct * 1.5))

    agent.risk_level_pct = payload.stop_loss_pct
    agent.max_concurrent_trades = max_trades
    agent.daily_profit_target_pct = payload.daily_profit_pct
    agent.daily_target_reached = False
    print(f"[AGENT CONFIG] risk_level={payload.stop_loss_pct}% | max_concurrent_trades="
          f"{agent.max_concurrent_trades} | daily_profit_target={agent.daily_profit_target_pct}%")
    system_log.push(
        "ai",
        f"Agent config applied: risk={payload.stop_loss_pct}% | max_concurrent_trades={agent.max_concurrent_trades} | daily_profit={payload.daily_profit_pct}%",
        {"risk_level_pct": payload.stop_loss_pct, "max_concurrent_trades": agent.max_concurrent_trades},
    )
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

@app.get("/agent/bible/stats")
async def agent_bible_stats():
    """Candlestick Trading Bible in-RAM memory stats (startup load once)."""
    return bible_memory_stats()


@app.get("/agent/bible/toc")
async def agent_bible_toc():
    """Full TOC of bible sections available for microsecond fetch."""
    return {"toc": list_bible_toc(), "stats": bible_memory_stats()}


@app.get("/agent/bible/fetch")
async def agent_bible_fetch(
    q: str = Query(..., min_length=1, description="Section id or alias, e.g. hammer, pin bar"),
    max_chars: int = Query(8000, ge=200, le=50000),
):
    """O(1) in-RAM fetch of one bible section by id/alias."""
    return fetch_bible(q, max_chars=max_chars)


@app.get("/agent/bible/search")
async def agent_bible_search(
    q: str = Query(..., min_length=1),
    limit: int = Query(8, ge=1, le=30),
):
    """In-RAM keyword search across bible sections."""
    return {"results": search_bible(q, limit=limit)}


@app.get("/agent/whale/status")
async def agent_whale_status():
    """Latest WhaleBotAlerts fetch snapshot + rules."""
    snap = last_fetch_snapshot()
    return {
        "pair": "BTC/USDT",
        "merged_into_btc": True,
        "active_pair_is_btc": is_btc_pair(agent.active_pair),
        "source": WHALE_SOURCE_URL,
        "min_btc": MIN_BTC_AMOUNT,
        "rules": {
            "short": f"Unknown → Exchange (≥{MIN_BTC_AMOUNT:.0f} BTC) → SELL/SHORT",
            "long": f"Exchange → Unknown (≥{MIN_BTC_AMOUNT:.0f} BTC) → BUY/LONG",
        },
        "seeded": is_seeded(),
        "last_fetch": snap,
    }


@app.get("/agent/ml/stats")
async def agent_ml_stats():
    """ML Bitcoin trading paper in-RAM memory stats."""
    return ml_memory_stats()


@app.get("/agent/ml/toc")
async def agent_ml_toc():
    return {"toc": list_ml_toc(), "stats": ml_memory_stats()}


@app.get("/agent/ml/fetch")
async def agent_ml_fetch(
    q: str = Query(..., min_length=1, description="Section id or alias, e.g. cost aware, xgboost, h2"),
    max_chars: int = Query(8000, ge=200, le=50000),
):
    return fetch_ml(q, max_chars=max_chars)


@app.get("/agent/ml/search")
async def agent_ml_search(
    q: str = Query(..., min_length=1),
    limit: int = Query(8, ge=1, le=30),
):
    return {"results": search_ml(q, limit=limit)}


@app.get("/system/logs")
async def get_system_logs():
    """ Transparency snapshot for the System Log modal — connections, pattern scan,
    trade pipeline, and rolling backend event log. No secrets are returned. """
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
            "taapi_configured": is_taapi_configured(),
            "taapi_paused": TAAPI_PAUSED,
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
            "max_concurrent_trades": agent.max_concurrent_trades,
            "risk_level_pct": agent.risk_level_pct,
            "emergency_triggered": agent.emergency_triggered,
            "policy": agent_policy_summary(),
        },
        "chart": {
            "history_hint": "5M: backend /chart/24h snapshot, else Bybit public linear klines, then mock",
            "live_hint": f"Bybit public linear WebSocket (chart) + backend ticker poll (~{_AUTO_BUY_TICKER_POLL}s) for bot PnL",
            "market_data": "bybit_public_linear",
            "api_key_required": False,
        },
        "last_taapi_scan": system_log.last_taapi_scan,
        "last_trade_fire": system_log.last_trade_fire,
        "entries": system_log.entries[-60:],
        "notifications": notifications.notifications[-20:],
        "agent_chat": system_log.agent_chat[-20:],
    }

@app.get("/chart/24h")
async def get_chart_24h(pair: str | None = Query(None, description="e.g. BTC/USDT")):
    """ Latest 24h high/low + 5m candles. Uses persisted snapshot when available;
    fetches live from Bybit on demand when a mapped pair is missing from cache. """
    if pair:
        bybit_symbol = get_bybit_symbol(pair)
        cache_pair = pair
        try:
            if bybit_symbol:
                entry = await chart_24h_store.ensure_pair(cache_pair, bybit_symbol)
            else:
                entry = chart_24h_store.get_pair(cache_pair)
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
        return {"updated_at": chart_24h_store.updated_at, "pair": pair, **{k: v for k, v in entry.items() if k != "pair"}}
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
            unrealized_net = agent.get_unrealized_net_usd()
            margin_in_use = sum(float(t.get("margin") or 0) for t in agent.trades)
            trade_notional = sum(float(t.get("position_size") or 0) for t in agent.trades)
            # Daily profit = vs paper-account starting capital (lifetime account P&L).
            daily_profit = total_value - agent.starting_capital
            daily_profit_pct = (daily_profit / agent.starting_capital) * 100 if agent.starting_capital else 0

            if (
                agent.is_active
                and agent.daily_profit_target_pct > 0
                and not agent.daily_target_reached
                and not agent.emergency_triggered
                and daily_profit_pct >= agent.daily_profit_target_pct
            ):
                agent.daily_target_reached = True
                notifications.push(
                    f"Daily profit target {agent.daily_profit_target_pct}% reached "
                    f"({daily_profit_pct:.2f}%) — new auto entries halted.",
                    "success",
                )

            # AI Season profit = vs capital at the moment START AI AUTOMATION was clicked.
            if agent.ai_season_start_capital is not None and agent.is_active:
                ai_season_profit = total_value - agent.ai_season_start_capital
                ai_season_profit_pct = (ai_season_profit / agent.ai_season_start_capital) * 100
            else:
                ai_season_profit = 0.0
                ai_season_profit_pct = 0.0

            baseline = agent.get_session_baseline()
            portfolio_drop = ((baseline - total_value) / baseline) * 100 if baseline else 0

            tf_key = SECONDS_TO_TIMEFRAME_KEY.get(agent.timeframe_seconds, "1m")
            scan = system_log.last_taapi_scan
            last_scan = scan if scan and scan.get("pair") == agent.active_pair else None
            blue_box_overlay = build_blue_box_chart_overlay(
                agent.active_pair,
                tf_key,
                is_active=agent.is_active,
                last_scan=last_scan,
            )

            payload = {
                "capital": round(agent.current_capital, 2),
                "available_capital": round(agent.get_available_capital(), 2),
                "total_portfolio_value": round(total_value, 2),
                "unrealized_net_usd": round(unrealized_net, 2),
                "margin_in_use": round(margin_in_use, 2),
                "trade_notional": round(trade_notional, 2),
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
                "profit_floor_mode": "fixed_tp_gross",
                "trading_execution": (
                    "paper_simulation" if bybit_api.mode == "PAPER_TRADING" else "bybit_testnet"
                ),
                "trades": len(agent.trades),
                "agent_chat": system_log.agent_chat[-8:],
                "blue_box_overlay": blue_box_overlay,
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
                "entry_candles": agent.get_entry_candle_highlights(),
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