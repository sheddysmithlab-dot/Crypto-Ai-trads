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
from session_schedule import schedule_store
import trade_db
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
from chart_tf_move import fetch_tf_move
from system_log import system_log
from volume_spread_system import (
    MIN_CANDLES,
    parse_bybit_kline,
    reset_blue_box_state,
    build_blue_box_chart_overlay,
)
from bybit_executor import BybitAgent
from ml_trading_memory import (
    fetch_ml,
    list_ml_toc,
    memory_stats as ml_memory_stats,
    ml_system_prompt_blurb,
    search_ml,
)
from agent_brain import (
    ENTRY_PATTERN_NAME,
    brain_chat_summary,
    enrich_signal,
    entry_pattern_profile,
    evaluate_live_entry,
    is_scalp_timeframe,
    strategy_system_blurb,
)
from brain_adapter import evaluate_live_entry_async as _brain_evaluate_async

from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent / "DATA"

from timeframe_profiles import capital_pct_fraction, get_timeframe_profile

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


class BotStopPayload(BaseModel):
    """AI Engine STOP choice from frontend popup."""
    mode: str = "hold"  # hold | emergency


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
        print("[SETTINGS] Entry engines: 1m/5m SCALP + 15m/1h/1D BIBLE.")

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
            "session_schedule": schedule_store.status_dict(),
            "mysql": trade_db.status_dict(),
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

    pair = str(context.get("pair") or "unknown")
    timeframe = str(context.get("timeframe") or "unknown")
    action = str(context.get("action") or "BUY").upper()
    reason = str(context.get("reason") or "no-reason")
    confidence = context.get("confidence")
    current_price = context.get("current_price")
    candle_volume = context.get("candle_volume")
    prev_candle_volume = context.get("prev_candle_volume")
    candle_height = context.get("candle_height")
    prev_candle_height = context.get("prev_candle_height")

    prompt = (
        "You are a risk-confirmation layer for an algorithmic crypto trading bot. "
        f"A strategy signal fired on {pair} ({timeframe}). Proposed action: {action}. "
        f"Reason: {reason}. Confidence: {confidence}. Current price: {current_price}. "
        f"Candle volume: {candle_volume}, previous candle volume: {prev_candle_volume}, "
        f"candle height: {candle_height}, previous candle height: {prev_candle_height}. "
        f"Reply with ONLY the single word YES to confirm this {action} signal, or NO to reject it."
    )

    messages = []
    system_role = load_system_role_text()
    if system_role:
        messages.append({"role": "system", "content": system_role[:20000]})
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
            parts = ["Bybit rejected the connection (403)."]
            if ret_msg:
                parts.append(str(ret_msg))
            parts.append(
                "Check that your API key allows this account and that Mainnet/Testnet matches where the key was created."
            )
            if outbound_ip:
                parts.append(f"If your key uses IP allowlists, add this IP: {outbound_ip}.")
            if settings_store.bybit_environment == "testnet":
                parts.append("Testnet keys must come from Bybit Testnet.")
            else:
                parts.append("Mainnet keys must come from Bybit Mainnet.")
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
# Manual-mode defaults (auto strategy wiped)
MAX_CONCURRENT_TRADES_DEFAULT = int(os.environ.get("MAX_CONCURRENT_TRADES", "10"))
MAX_SAME_SIDE_AUTO_PER_PAIR = int(os.environ.get("MAX_SAME_SIDE_AUTO_PER_PAIR", "1"))
AUTO_TRADE_AUTO_EXIT_ENABLED = True  # Path-based SL + path-based profit book
INVERT_AUTO_TRADE_FIRE = False
# Path-based profit book (gross % from entry):
#   continuous favorable +++ (3 steps) → hold through +0.50%, exit at +0.70%
#   choppy -+-+ path → book at +0.50%
#   hard ceiling always +0.70%
PATH_TP_TIGHT_PCT = 0.5
PATH_TP_WIDE_PCT = 0.7
FIXED_EXIT_PROFIT_PCT = PATH_TP_TIGHT_PCT  # legacy alias (choppy / min book)
# Path-based stop-loss (per trade, tick path):
#   continuous adverse --- (3 steps) → exit at -0.50%
#   choppy -+-+ path → hold past 0.50%, exit at -0.70%
PATH_SL_TIGHT_PCT = 0.5
PATH_SL_WIDE_PCT = 0.7
PATH_SL_ADVERSE_STEPS = 3
PATH_TP_FAVORABLE_STEPS = 3
# Legacy alias (tight SL) — used for initial UI SL price on open.
FIXED_EXIT_LOSS_PCT = PATH_SL_TIGHT_PCT
BTC_REGIME_GATE = False
_AUTO_BUY_TICKER_POLL = float(os.environ.get("MARKET_TICKER_POLL", "0.35"))
_AUTO_BUY_BURST_POLL = float(os.environ.get("AUTO_BUY_BURST_POLL", "0.12"))
_AUTO_BUY_FAST_POLL = float(os.environ.get("AUTO_BUY_FAST_POLL", "0.35"))
_trade_fire_lock = asyncio.Lock()
_recent_signal_fire_keys: set[str] = set()
_CANDLE_FETCH_WARNED_PAIRS = set()
_bybit_testnet_keys_warned = False

class AITradingAgent:
    # Strict Exit — raised profit book + dynamic upside ratchet + tight max loss.
    STRICT_EXIT_HARD_TARGET_PCT = float(os.environ.get("STRICT_EXIT_HARD_TARGET", "1.8"))
    # Fee-aware floor: 0.50% gross (raised so winners clear round-trip fees).
    STRICT_EXIT_MIN_LOCK_PCT = float(os.environ.get("STRICT_EXIT_MIN_LOCK", "0.50"))
    STRICT_EXIT_FLUCTUATION_X_PCT = float(os.environ.get("STRICT_EXIT_FLUCTUATION_X", "0.10"))
    STRICT_EXIT_TRAIL_MULTIPLIER = float(os.environ.get("STRICT_EXIT_TRAIL_MULT", "1.5"))
    # Absolute max loss (gross %) — never let a trade bleed past this, any TF.
    STRICT_EXIT_MAX_LOSS_PCT = float(os.environ.get("STRICT_EXIT_MAX_LOSS", "0.40"))
    # Upside "strong move" velocity: peak jump within this window raises profit book.
    UPSIDE_VELOCITY_WINDOW_SEC = float(os.environ.get("UPSIDE_VELOCITY_WINDOW_SEC", "8"))
    UPSIDE_VELOCITY_JUMP_PCT = float(os.environ.get("UPSIDE_VELOCITY_JUMP_PCT", "0.20"))
    # Structure SL buffer beyond swing high/low (invalidation pad).
    STRUCTURE_SL_BUFFER_PCT = float(os.environ.get("STRUCTURE_SL_BUFFER_PCT", "0.05"))  # 0.05% of price
    # Reject / push structure SL if closer than this to entry (DOGE/PEPE 4dp bug).
    STRUCTURE_SL_MIN_DISTANCE_PCT = float(os.environ.get("STRUCTURE_SL_MIN_DISTANCE_PCT", "0.15"))
    # Ignore structure SL for this many seconds after open (first tick false fire).
    STRUCTURE_SL_GRACE_SEC = float(os.environ.get("STRUCTURE_SL_GRACE_SEC", "2.0"))
    # TF hard stop (gross % loss) — tightened after trail losses to −1%+.
    HARD_STOP_PCT_BY_TF: dict[str, float] = {
        "30s": float(os.environ.get("HARD_STOP_30S", "0.30")),
        "1m": float(os.environ.get("HARD_STOP_1M", "0.35")),
        "3m": float(os.environ.get("HARD_STOP_3M", "0.40")),
        "5m": float(os.environ.get("HARD_STOP_5M", "0.40")),
        "10m": float(os.environ.get("HARD_STOP_10M", "0.45")),
        "15m": float(os.environ.get("HARD_STOP_15M", "0.50")),
        "30m": float(os.environ.get("HARD_STOP_30M", "0.55")),
        "1h": float(os.environ.get("HARD_STOP_1H", "0.60")),
        "1D": float(os.environ.get("HARD_STOP_1D", "0.70")),
    }

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
        self.starting_capital = 100_000.0
        self.current_capital = self.starting_capital
        # Total capital risk % from modal -> max_concurrent_trades via round(risk_pct * 2).
        # Also: when session portfolio drop hits this %, auto Hold-stop (no new entries).
        self.risk_level_pct = 5.0
        self.max_concurrent_trades = MAX_CONCURRENT_TRADES_DEFAULT
        self.last_open_skip_reason: str | None = None
        # AI Agent Instructions modal: optional "Capital profit of the day" target.
        # 0.0 means disabled. Once the day's profit % crosses this, new entries are
        # halted (existing positions keep being managed by strict exit logic).
        self.daily_profit_target_pct = 0.0
        self.daily_target_reached = False
        # AI Season: profit tracked from START until STOP (persisted to MySQL).
        self.ai_season_start_capital = None
        self.ai_season_id: int | None = None
        self.ai_season_started_at: float | None = None
        self.ai_season_end_reason: str | None = None
        # Portfolio modal session counters: live while AI season active; frozen on STOP;
        # reset to 0 on next START (not calendar-day / all-time).
        self.session_stats_frozen = False
        self.session_hold_mode = False  # STOP→Hold: no new entries; open trades still auto-exit
        self.session_stats_snapshot: dict = {
            "trade_notional": 0.0,
            "daily_profit": 0.0,
            "daily_profit_pct": 0.0,
            "daily_broker_fee": 0.0,
            "ai_season_profit": 0.0,
            "ai_season_profit_pct": 0.0,
            "ai_season_profit_net": 0.0,
            "ai_season_profit_net_pct": 0.0,
            "exited_booked_usd": 0.0,
            "open_positions": 0,
        }

        # RULE 1: Position Sizing & Leverage (The "100/1" Rule)
        self.leverage = 100
        self.margin_pct = 0.01  # exactly 1% of total capital per trade, never increased

        # POLICY 1 / RULE 4 & 6 Config: Dynamic Trailing Lock (now driven by TRUE NET PROFIT)
        self.current_price = 68415.70
        self.peak_net_pct = 0.0
        self.is_lock_active = False

        # Active chart/engine focus pair (UI). Auto-scan uses `watchlist` (minimized
        # launcher coins). Each trade is marked with its OWN pair price.
        self.active_pair = "BTC/USDT"
        self.watchlist: list[str] = []  # launcher minimized coins (all mapped pairs allowed)
        self.pair_prices: dict[str, float] = {"BTC/USDT": self.current_price}
        self.trade_seq = 0
        self.trades = []  # list of {id, pair, side, entry, margin, position_size, entry_fee_usd}
        self.trade_history = []  # session list (active + sold), cleared only on START/STOP

        # Chart timeframe — shared by all watchlist pairs in auto_buy_loop().
        self.timeframe_seconds = 60

    # Cap = every mapped Bybit pair (frontend TRADING_PAIRS / BYBIT_SYMBOL_MAP).
    MAX_WATCHLIST = 32

    def get_scan_pairs(self) -> list[str]:
        """Pairs the AI scans for patterns + fires on. Watchlist first; else chart pair."""
        out: list[str] = []
        seen: set[str] = set()
        for p in self.watchlist or []:
            label = (p or "").strip()
            if not label or label in seen:
                continue
            seen.add(label)
            out.append(label)
        if out:
            return out
        if self.active_pair:
            return [self.active_pair]
        return []

    def set_watchlist(self, pairs: list[str] | None) -> list[str]:
        """Replace scan watchlist (launcher minimized coins). All mapped Bybit pairs allowed."""
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in pairs or []:
            p = (raw or "").strip().upper().replace("-", "/")
            if not p:
                continue
            if "/" not in p:
                p = f"{p}/USDT"
            if p in ("WHALE/BTC", "WHALE"):
                p = "BTC/USDT"
            if p in seen:
                continue
            if get_bybit_symbol(p) is None:
                print(f"[WATCHLIST] Skipping unmapped pair {p}")
                continue
            seen.add(p)
            cleaned.append(p)
            if len(cleaned) >= self.MAX_WATCHLIST:
                break
        self.watchlist = cleaned
        print(f"[WATCHLIST] AI scan pairs → {cleaned or ['(none — using active chart pair)']}")
        return list(cleaned)

    def watches_btc(self) -> bool:
        return any((p or "").upper().startswith("BTC") for p in self.get_scan_pairs())

    def get_profit_floor_pct(self):
        """Minimum profit lock floor (Rule 2) — fee-aware gross %."""
        return self.STRICT_EXIT_MIN_LOCK_PCT

    def mark_price_for(self, pair: str | None) -> float | None:
        """Last known mark for a pair — never borrow another coin's price."""
        p = (pair or self.active_pair or "").strip()
        if not p:
            return None
        px = self.pair_prices.get(p)
        if px is not None and px > 0:
            return float(px)
        if p == self.active_pair and self.current_price > 0:
            return float(self.current_price)
        return None

    def set_pair_mark(self, pair: str, price: float) -> float | None:
        clean = _sanitize_market_price(price)
        if clean is None:
            return None
        p = (pair or "").strip()
        if not p:
            return None
        self.pair_prices[p] = clean
        if p == self.active_pair:
            self.current_price = clean
        return clean

    def open_trade_pairs(self) -> set[str]:
        return {t.get("pair") for t in self.trades if t.get("pair")}

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

    def hard_stop_pct_for_trade(self, trade: dict) -> float:
        """Effective hard-stop magnitude (positive %) = min(TF stop, absolute max loss)."""
        tf = (trade.get("timeframe_key") or "").strip()
        if not tf:
            tf = SECONDS_TO_TIMEFRAME_KEY.get(self.timeframe_seconds, "1m")
        tf_stop = float(self.HARD_STOP_PCT_BY_TF.get(tf, self.HARD_STOP_PCT_BY_TF.get("5m", 0.40)))
        return min(tf_stop, float(self.STRICT_EXIT_MAX_LOSS_PCT))

    def _detect_strong_upside(self, trade: dict, prev_peak: float, peak: float) -> bool:
        """True when profit is climbing fast (higher upside momentum)."""
        now = time.time()
        if peak > prev_peak + 1e-12:
            last_t = float(trade.get("peak_updated_at") or 0.0)
            last_peak = float(trade.get("peak_at_velocity_mark") or prev_peak)
            trade["peak_updated_at"] = now
            # Fresh window sample
            if last_t <= 0 or (now - last_t) > self.UPSIDE_VELOCITY_WINDOW_SEC:
                trade["peak_at_velocity_mark"] = peak
                trade["velocity_window_start"] = now
            jump = peak - float(trade.get("peak_at_velocity_mark") or peak)
            window = now - float(trade.get("velocity_window_start") or now)
            if jump >= self.UPSIDE_VELOCITY_JUMP_PCT and window <= self.UPSIDE_VELOCITY_WINDOW_SEC:
                trade["strong_upside"] = True
                trade["strong_upside_until"] = now + 20.0
                return True
        until = float(trade.get("strong_upside_until") or 0.0)
        if until > now:
            trade["strong_upside"] = True
            return True
        trade["strong_upside"] = False
        return False

    def _dynamic_profit_book_levels(self, peak: float, strong_upside: bool) -> tuple[float, float, float]:
        """Raise profit-book levels when upside is higher / momentum is strong.

        Returns (min_lock, hard_target, trail_mult).
        Floor ratchets up with peak so strong winners book more, not less.
        """
        base_lock = self.STRICT_EXIT_MIN_LOCK_PCT
        base_target = self.STRICT_EXIT_HARD_TARGET_PCT
        trail_mult = self.STRICT_EXIT_TRAIL_MULTIPLIER

        # Peak-based ratchet (always on once peak climbs).
        if peak >= 2.50:
            min_lock, hard_target = 1.80, 3.50
        elif peak >= 2.00:
            min_lock, hard_target = 1.40, 3.00
        elif peak >= 1.50:
            min_lock, hard_target = 1.00, 2.60
        elif peak >= 1.00:
            min_lock, hard_target = 0.75, 2.20
        elif peak >= 0.75:
            min_lock, hard_target = 0.60, 2.00
        else:
            min_lock, hard_target = base_lock, base_target

        # Strong upside velocity → push targets further + give more trail room.
        if strong_upside:
            min_lock = max(min_lock, peak * 0.55, base_lock)
            hard_target = max(hard_target, base_target + 0.60, peak + 0.80)
            trail_mult = max(1.2, trail_mult - 0.2)  # tighter trail = book more of the move
            # Cap runaway
            min_lock = min(min_lock, peak - 0.05) if peak > base_lock + 0.05 else min_lock
            hard_target = min(hard_target, 4.0)

        min_lock = max(min_lock, base_lock)
        hard_target = max(hard_target, base_target, min_lock + 0.30)
        return round(min_lock, 4), round(hard_target, 4), round(trail_mult, 4)

    def _evaluate_strict_exit(self, trade: dict, gross: float, net: float) -> str | None:
        """Strategy wiped — never auto-exits."""
        return None

    def _trade_metrics(self, t, *, for_close: bool = False):
        """PnL metrics for a position — marked with THAT trade's pair price only."""
        entry = float(t["entry"])
        mark = self.mark_price_for(t.get("pair"))
        # Guard: missing/wrong-coin mark must never invent 80,000% PnL.
        if mark is None or mark <= 0 or entry <= 0:
            mark = entry
        elif mark / entry > 20.0 or entry / mark > 20.0:
            print(
                f"[PILLAR 3: AI AGENT] Ignoring cross-pair mark for #{t.get('id')} "
                f"{t.get('pair')}: mark={mark} vs entry={entry} — holding flat until correct tick."
            )
            mark = entry

        if t["side"] == "LONG":
            gross_pct = ((mark - entry) / entry) * 100
        else:
            gross_pct = ((entry - mark) / entry) * 100

        entry_fee_pct = float(t["entry_fee_pct"])
        exit_fee_pct = bybit_api.get_taker_fee_pct() * (mark / entry)
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
            "mark_price": mark,
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
            "season_id": trade.get("season_id") or self.ai_season_id,
        })
        try:
            trade_db.upsert_open_trade(trade)
        except Exception as exc:
            print(f"[MYSQL] open persist skipped: {exc}")

    def get_entry_candle_highlights(self) -> list[dict]:
        """Candles where auto trades fired — scoped per pair for chart neon markers."""
        seen: set[tuple[str, int]] = set()
        out: list[dict] = []
        for row in self.trade_history:
            if row.get("source") == "manual":
                continue
            raw = row.get("signal_candle_time")
            if raw is None:
                continue
            pair = row.get("pair") or ""
            chart_time = int(raw // 1000) if raw > 1_000_000_000_000 else int(raw)
            key = (pair, chart_time)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "time": chart_time,
                "pair": pair,
                "side": row.get("side", "LONG"),
                "pattern": row.get("pattern"),
                "opened_at": row.get("opened_at"),
            })
        return out

    def _finalize_trade_history(self, trade, metrics, reason):
        exit_px = metrics.get("mark_price")
        if exit_px is None:
            exit_px = self.mark_price_for(trade.get("pair")) or trade.get("entry")
        for row in self.trade_history:
            if row["id"] == trade["id"]:
                row["current"] = round(float(exit_px), price_decimals_for_mark(float(exit_px)))
                # Trade list shows gross profit only — fees live in header broker-fee total.
                row["pnl"] = round(metrics["gross_pct"], 4)
                row["gross_pnl_pct"] = round(metrics["gross_pct"], 4)
                row["gross_pnl_usd"] = round(float(metrics.get("gross_usd") or 0), 2)
                row["net_pnl_usd"] = round(metrics["net_usd"], 2)
                row["exit_fee_usd"] = round(metrics["exit_fee_usd"], 4)
                row["status"] = "sold"
                row["closed_reason"] = reason
                row["closed_at"] = time.time()
                break
        try:
            trade_db.finalize_trade(
                trade,
                exit_price=float(exit_px),
                gross_pnl_pct=float(metrics["gross_pct"]),
                net_pnl_usd=float(metrics["net_usd"]),
                exit_fee_usd=float(metrics["exit_fee_usd"]),
                exit_fee_pct=float(metrics.get("exit_fee_pct") or 0),
                closed_reason=reason,
                gross_pnl_usd=float(metrics.get("gross_usd") or 0),
            )
        except Exception as exc:
            print(f"[MYSQL] close persist skipped: {exc}")

    def get_unrealized_net_usd(self):
        return sum(self._trade_metrics(t)["net_usd"] for t in self.trades)

    def get_session_gross_and_fees_usd(self) -> dict:
        """Gross trade P&L + Bybit broker fees for the CURRENT AI season only.

        Fee rules (buy/sell):
          - Open trade  → only entry (buy/open) fee has been paid → count entry only
          - Closed trade → entry + exit (sell/close) both paid → count both
        Never include a projected exit fee on still-open positions.
        """
        open_gross = 0.0
        open_fees = 0.0
        fee_rate = float(bybit_api.get_taker_fee_pct() or 0.055) / 100.0
        sid = self.ai_season_id
        for t in self.trades:
            if sid is not None and t.get("season_id") != sid:
                continue
            m_open = self._trade_metrics(t, for_close=False)
            open_gross += float(m_open["gross_usd"])
            entry_fee = float(t.get("entry_fee_usd") or 0)
            if entry_fee <= 0:
                # Recover missing fee fields on older in-memory trades
                entry_fee = round(float(t.get("position_size") or 0) * fee_rate, 4)
            # Open = buy/open only — do not invent a sell fee yet.
            open_fees += entry_fee

        closed_gross = 0.0
        closed_fees = 0.0
        for row in self.trade_history:
            if row.get("status") != "sold":
                continue
            if sid is not None and row.get("season_id") != sid:
                continue
            entry_f = float(row.get("entry_fee_usd") or 0)
            exit_f = float(row.get("exit_fee_usd") or 0)
            if entry_f <= 0 and float(row.get("position_size") or 0) > 0:
                entry_f = round(float(row.get("position_size") or 0) * fee_rate, 4)
            # Closed = buy + sell both happened → add both fees.
            closed_fees += entry_f + exit_f
            stored_gross = row.get("gross_pnl_usd")
            if stored_gross is not None:
                closed_gross += float(stored_gross)
            else:
                # Legacy rows: net = gross - fees → gross = net + fees
                net_u = float(row.get("net_pnl_usd") or 0)
                closed_gross += net_u + entry_f + exit_f

        gross = open_gross + closed_gross
        fees = open_fees + closed_fees
        return {
            "gross_usd": gross,
            "broker_fee_usd": fees,
            "net_usd": gross - fees,
            "open_gross_usd": open_gross,
            "closed_gross_usd": closed_gross,
            "open_fee_usd": open_fees,
            "closed_fee_usd": closed_fees,
        }

    def _session_open_trades(self) -> list:
        """Open positions that belong to the current AI season (for portfolio counters)."""
        sid = self.ai_season_id
        if sid is None:
            return []
        return [t for t in self.trades if t.get("season_id") == sid]

    def _reset_session_stats(self) -> None:
        """New AI session — portfolio counters start at 0.0 (live updating resumes)."""
        self.session_stats_frozen = False
        self.session_hold_mode = False
        self.session_stats_snapshot = {
            "trade_notional": 0.0,
            "daily_profit": 0.0,
            "daily_profit_pct": 0.0,
            "daily_broker_fee": 0.0,
            "ai_season_profit": 0.0,
            "ai_season_profit_pct": 0.0,
            "ai_season_profit_net": 0.0,
            "ai_season_profit_net_pct": 0.0,
            "exited_booked_usd": 0.0,
            "open_positions": 0,
        }

    def _freeze_session_stats(self, fee_book: dict | None = None) -> None:
        """STOP — lock Trade Value / Gross Profit / Fees / Open Positions (separate, not netted)."""
        book = fee_book if fee_book is not None else self.get_session_gross_and_fees_usd()
        gross = float(book.get("gross_usd") or 0)
        fees = float(book.get("broker_fee_usd") or 0)
        exited = float(book.get("closed_gross_usd") or 0)
        baseline = float(
            self.ai_season_start_capital
            if self.ai_season_start_capital is not None
            else (self.starting_capital or 0)
        )
        pct = (gross / baseline) * 100 if baseline else 0.0
        net = gross - fees
        net_pct = (net / baseline) * 100 if baseline else 0.0
        session_open = self._session_open_trades()
        self.session_stats_snapshot = {
            "trade_notional": round(
                sum(float(t.get("position_size") or 0) for t in session_open), 2
            ),
            "daily_profit": round(gross, 2),
            "daily_profit_pct": round(pct, 2),
            "daily_broker_fee": round(fees, 4),
            "ai_season_profit": round(gross, 2),
            "ai_season_profit_pct": round(pct, 2),
            "ai_season_profit_net": round(net, 2),
            "ai_season_profit_net_pct": round(net_pct, 2),
            "exited_booked_usd": round(exited, 2),
            "open_positions": len(session_open),
        }
        self.session_stats_frozen = True
        print(
            f"[AI SEASON] Portfolio stats frozen — "
            f"gross ${gross:,.2f} · fees ${fees:,.2f} · net ${net:,.2f} · open {len(session_open)}"
        )

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
        self.ai_season_id = None
        self.ai_season_started_at = None
        self._reset_session_stats()
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
        """ Called when START AI AUTOMATION fires — new season baseline + DB row. """
        # Close any leftover open season row, then start fresh live table.
        if self.ai_season_id is not None:
            self.end_ai_season(clear_live_table=False, reason="restarted")
        self.trade_history = []
        self._reset_session_stats()
        self.ai_season_start_capital = self.get_total_portfolio_value()
        self.ai_season_started_at = time.time()
        self.ai_season_end_reason = None
        try:
            self.ai_season_id = trade_db.create_season(
                start_capital=float(self.ai_season_start_capital),
                started_at=self.ai_season_started_at,
            )
        except Exception as exc:
            print(f"[MYSQL] begin_ai_season persist skipped: {exc}")
            self.ai_season_id = None
        print(
            f"[AI SEASON] Started #{self.ai_season_id or 'mem'} "
            f"baseline ${self.ai_season_start_capital:,.2f}."
        )

    def end_ai_season(self, *, clear_live_table: bool = False, reason: str | None = None):
        """ Called on STOP / Emergency Exit — persist season totals, freeze portfolio stats. """
        end_reason = reason or self.ai_season_end_reason or "stopped"
        if self.ai_season_start_capital is not None or self.ai_season_id is not None:
            fee_book = self.get_session_gross_and_fees_usd()
            # Freeze BEFORE clearing season id / live table so UI stays on final numbers.
            self._freeze_session_stats(fee_book)
            end_cap = self.get_total_portfolio_value()
            sold = [
                r
                for r in self.trade_history
                if r.get("status") == "sold"
                and (self.ai_season_id is None or r.get("season_id") == self.ai_season_id)
            ]
            wins = sum(1 for r in sold if float(r.get("net_pnl_usd") or 0) > 0)
            losses = sum(1 for r in sold if float(r.get("net_pnl_usd") or 0) < 0)
            try:
                trade_db.close_season(
                    self.ai_season_id,
                    end_capital=float(end_cap),
                    gross_pnl_usd=float(fee_book.get("gross_usd") or 0),
                    net_pnl_usd=float(fee_book.get("net_usd") or 0),
                    broker_fee_usd=float(fee_book.get("broker_fee_usd") or 0),
                    trade_count=len(sold),
                    win_count=wins,
                    loss_count=losses,
                    end_reason=end_reason,
                )
            except Exception as exc:
                print(f"[MYSQL] end_ai_season persist skipped: {exc}")
            print(
                f"[AI SEASON] Ended #{self.ai_season_id or 'mem'} "
                f"net ${float(fee_book.get('net_usd') or 0):,.2f} — {end_reason}."
            )
        self.ai_season_start_capital = None
        self.ai_season_id = None
        self.ai_season_started_at = None
        self.ai_season_end_reason = None
        if clear_live_table:
            self.trades = []
            self.trade_history = []
            # After hard stop all positions are closed — keep PnL/fees frozen, zero open book.
            snap = self.session_stats_snapshot
            snap["trade_notional"] = 0.0
            snap["open_positions"] = 0
            print("[AI SEASON] Live trades table cleared.")

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
        _clear_entry_pipeline()
        reset_blue_box_state()
        _invalidate_kline_cache()
        print(f"[TIMEFRAME SYNC] Backend trading timeframe set to {seconds}s.")

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
        pair=None,
        timeframe_key=None,
    ):
        """ RULE 1: Opens a position as a Market Order (RULE 7) with simulated minor slippage.
        Manual entries default to 1% margin x 100x leverage. Auto entries pass
        `position_size_usd` + `qty` from compute_auto_trade_plan() (TF capital %).
        `skip_exchange_open=True` when Bybit TESTNET already filled the order (FIX 4).
        `pair` stamps the trade onto that coin (watchlist multi-pair); defaults to active chart pair. """
        self.last_open_skip_reason = None
        if self.emergency_triggered:
            self.last_open_skip_reason = "Emergency stop active"
            return None

        trade_pair = (pair or self.active_pair or "").strip() or self.active_pair
        mark_px = self.mark_price_for(trade_pair) or self.current_price

        if source == "manual":
            # Manual trading is the opposite of automation - only allowed while the bot
            # is NOT running (START AI AUTOMATION has not been clicked / was stopped).
            if self.is_active:
                self.last_open_skip_reason = "Manual entry blocked while AI engine is running"
                return None
        else:
            if not self.is_active:
                self.last_open_skip_reason = "AI engine is not active"
                return None
            if self.daily_target_reached:
                self.last_open_skip_reason = "Daily profit target already reached"
                return None

            if self.has_duplicate_auto_entry(
                side, trade_pair, pattern, signal_candle_time, float(entry_price or mark_px or 0)
            ):
                self.last_open_skip_reason = (
                    f"Duplicate auto-entry blocked on {trade_pair} "
                    f"({side}, pattern={pattern}, candle={signal_candle_time})"
                )
                print(f"[PILLAR 3: AI AGENT] {self.last_open_skip_reason}.")
                return None

            if not self.has_same_side_auto_capacity(side, trade_pair):
                self.last_open_skip_reason = (
                    f"Same-side stack blocked on {trade_pair} "
                    f"({side} already open — max {MAX_SAME_SIDE_AUTO_PER_PAIR})"
                )
                print(f"[PILLAR 3: AI AGENT] {self.last_open_skip_reason}.")
                return None

        # AI Agent Instructions modal: cap stacked positions at max_concurrent_trades.
        if len(self.trades) >= self.max_concurrent_trades:
            self.last_open_skip_reason = (
                f"Max concurrent trades ({self.max_concurrent_trades}) reached — "
                f"new entry skipped on {trade_pair}"
            )
            notifications.push(self.last_open_skip_reason + ".", "info")
            return None

        if (entry_price is None and (not mark_px or mark_px <= 0)) or (
            entry_price is not None and float(entry_price) <= 0
        ):
            self.last_open_skip_reason = f"Invalid price on {trade_pair}"
            print(f"[PILLAR 3: AI AGENT] Skipping entry — {self.last_open_skip_reason}.")
            return None

        capital_base = self.get_trading_capital_base()
        if bybit_api.mode == "LIVE_TRADING":
            if capital_base is None:
                self.last_open_skip_reason = "Waiting for Bybit balance sync"
                notifications.push("Waiting for Bybit balance sync — please try again shortly.", "warning")
                return None
            if capital_base <= 0:
                self.last_open_skip_reason = "Insufficient Bybit equity ($0)"
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
            self.last_open_skip_reason = "Insufficient balance to open a position"
            notifications.push(self.last_open_skip_reason + ".", "error")
            return None

        # Paper ledger: reserve capital on open (auto = 10% notional slot; manual = margin).
        capital_reserved = round(position_size, 2) if source == "auto" and position_size_usd is not None else round(margin, 2)
        if bybit_api.mode != "LIVE_TRADING":
            if self.current_capital < capital_reserved:
                self.last_open_skip_reason = (
                    f"Insufficient balance — need ${capital_reserved:,.2f}, have ${self.current_capital:,.2f}"
                )
                notifications.push(self.last_open_skip_reason + ".", "error")
                return None

        # RULE 7: Market orders fill with minor slippage vs the requested price
        if entry_price is not None:
            filled_price = float(entry_price)
        else:
            slippage = random.uniform(-0.0002, 0.0002)
            filled_price = float(mark_px) * (1 + slippage)
        filled_price = round(filled_price, price_decimals_for_mark(filled_price))

        if exchange is None and bybit_api.mode == "PAPER_TRADING":
            exchange = "paper"
        if bybit_symbol is None:
            bybit_symbol = get_bybit_symbol(trade_pair)

        if qty is None and position_size_usd is not None:
            qty = compute_order_qty(position_size_usd, filled_price, bybit_symbol=bybit_symbol)

        # RULE 6: Live Entry Fee, based on Bybit's current Taker fee tier
        entry_fee_pct = bybit_api.get_taker_fee_pct()
        entry_fee_usd = round(position_size * (entry_fee_pct / 100), 4)

        # Manual: keep SL+TP from signal when provided.
        # Auto: path SL (--- → 0.5%, -+-+ → 0.7%) + path TP (+++ → 0.7%, -+-+ → 0.5%).
        clean_sl = None
        clean_tp = None
        if source == "auto":
            clean_sl, clean_tp = self._fixed_exit_prices(
                filled_price,
                side,
                loss_pct=PATH_SL_TIGHT_PCT,
                profit_pct=PATH_TP_WIDE_PCT,
            )
        else:
            if sl_price is not None:
                try:
                    clean_sl = round(float(sl_price), price_decimals_for_mark(filled_price))
                except (TypeError, ValueError):
                    clean_sl = None
            if tp_price is not None:
                try:
                    clean_tp = round(float(tp_price), price_decimals_for_mark(filled_price))
                except (TypeError, ValueError):
                    clean_tp = None

        self.trade_seq += 1
        trade = {
            "id": self.trade_seq,
            "pair": trade_pair,
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
            "trough_gross_pct": 0.0,
            "lock_level_pct": None,
            "stop_level_pct": None,
            "is_lock_active": False,
            "is_stop_active": False,
            "exchange": exchange,
            "bybit_symbol": bybit_symbol,
            "pattern": pattern,
            "signal_candle_time": signal_candle_time,
            "taapi_action": taapi_action,
            "sl_price": clean_sl,
            "tp_price": clean_tp,
            "target_mult": target_mult,
            "capital_reserved": capital_reserved,
            "season_id": self.ai_season_id,
            "timeframe_key": timeframe_key or SECONDS_TO_TIMEFRAME_KEY.get(self.timeframe_seconds, "1m"),
            "exit_mode": "path_sl" if source == "auto" else "manual",
            "entry_pattern": ENTRY_PATTERN_NAME,
            # Path-based SL/TP state (auto):
            #   SL --- → −0.5%; SL -+-+ → −0.7%
            #   TP +++ → +0.7%; TP -+-+ → +0.5%
            "path_last_gross_pct": 0.0,
            "path_adverse_streak": 0,
            "path_favorable_streak": 0,
            "path_choppy": False,          # bounce while in loss → wide SL
            "path_profit_choppy": False,   # dip while in profit → tight TP
            "path_continuous_dump": False,  # 3 consecutive adverse steps
            "path_continuous_run": False,   # 3 consecutive favorable steps
            "path_sl_pct": PATH_SL_TIGHT_PCT,
            "path_tp_pct": PATH_TP_WIDE_PCT,  # default: let clean +++ run to 0.7%
        }
        self.trades.append(trade)
        self.set_pair_mark(trade_pair, filled_price)
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
                trade_pair,
                side,
                f"{reason} | ${position_size} notional ({margin} margin x{self.leverage}){qty_label}",
            )
        print(f"[PILLAR 3: AI AGENT] Opened new {side} position #{trade['id']} on {trade_pair} @ {filled_price} "
              f"(margin=${margin}, position=${position_size}, qty={qty}, entry_fee=${entry_fee_usd}, source={source})")
        qty_note = f" | {qty} coins" if qty is not None else ""
        if source == "auto" and position_size_usd is not None:
            cap_pct = auto_trade_capital_pct_for_agent(self) * 100
            risk_note = f"{cap_pct:.0f}% of available capital{qty_note}"
            fill_msg = (
                f"Order Filled: {trade_pair} {side} @ {filled_price:,.4f} "
                f"(${position_size:,.2f} notional, {risk_note})"
            )
        else:
            fill_msg = (
                f"Order Filled: {trade_pair} {side} @ {filled_price:,.4f} "
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
        clean = self.set_pair_mark(pair, price)
        if clean is None and price is not None:
            # fallback seed without wiping other pair marks
            try:
                self.current_price = float(price)
                self.pair_prices[pair] = float(price)
            except (TypeError, ValueError):
                pass
        if pair_changed:
            self.peak_net_pct = 0.0
            self.is_lock_active = False
            # Keep UVSS / kline state for other watchlist pairs — chart focus only.
            print(f"[PILLAR 3: AI AGENT] Active pair switched to {pair}. Open positions preserved (per-pair marks).")
        else:
            print(f"[PILLAR 3: AI AGENT] Active pair refreshed to {pair} @ {self.current_price}.")

    def get_trades_snapshot(self):
        """Active trades on top (newest first), then session exits (newest first)."""
        open_ids = {t["id"] for t in self.trades}
        snapshot = []
        # Latest open trade first, oldest at the bottom of the Open section.
        open_trades = sorted(
            self.trades,
            key=lambda t: (float(t.get("opened_at") or 0), int(t.get("id") or 0)),
            reverse=True,
        )
        for trade in open_trades:
            m = self._trade_metrics(trade)
            mark = m.get("mark_price")
            if mark is None:
                mark = self.mark_price_for(trade.get("pair")) or trade.get("entry") or 0
            # PEPE/DOGE-scale: never force 4dp — that flips 0.00345↔0.0035 and ±1% PnL every tick.
            px_decimals = price_decimals_for_mark(float(mark) if mark else float(trade.get("entry") or 0))
            out = dict(trade)
            out.update({
                "current": round(float(mark), px_decimals),
                "gross_pnl_pct": round(m["gross_pct"], 4),
                # UI trade list: price profit only (no fee). Fees roll up in header.
                "pnl": round(m["gross_pct"], 4),
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
                "dynamic_min_lock_pct": (
                    round(float(trade["dynamic_min_lock_pct"]), 4)
                    if trade.get("dynamic_min_lock_pct") is not None
                    else None
                ),
                "dynamic_hard_target_pct": (
                    round(float(trade["dynamic_hard_target_pct"]), 4)
                    if trade.get("dynamic_hard_target_pct") is not None
                    else None
                ),
                "strong_upside": bool(trade.get("strong_upside")),
            })
            snapshot.append(out)

        # Newest exits first under the open list (by close time, then id).
        sold_rows = [
            row for row in self.trade_history
            if row.get("status") == "sold" and row.get("id") not in open_ids
        ]
        sold_rows.sort(
            key=lambda r: (float(r.get("closed_at") or r.get("opened_at") or 0), int(r.get("id") or 0)),
            reverse=True,
        )
        for row in sold_rows:
            out = dict(row)
            out.setdefault("protected", out.get("source") == "manual")
            # Prefer stored gross for list display.
            if out.get("gross_pnl_pct") is not None:
                out["pnl"] = out["gross_pnl_pct"]
            snapshot.append(out)
        return snapshot

    async def process_tick(self, new_price, volume_increment, pair: str | None = None):
        """Update mark for one pair; always re-check auto exits on every tick."""
        focus = (pair or self.active_pair or "").strip()
        clean_price = None
        if focus:
            clean_price = self.set_pair_mark(focus, new_price)
        else:
            clean_price = _sanitize_market_price(new_price)
            if clean_price is not None:
                self.current_price = clean_price
        if clean_price is None:
            # Bad tick for this pair — still evaluate exits using last known marks.
            print(f"[PILLAR 3: AI AGENT] Ignoring invalid market tick: {new_price!r} pair={focus}")

        corrupt = [t for t in self.trades if t.get("entry", 0) <= 0]
        if corrupt:
            self.trades = [t for t in self.trades if t.get("entry", 0) > 0]
            notifications.push(
                f"Removed {len(corrupt)} corrupt position(s) with invalid entry prices.",
                "warning",
            )

        if not self.trades:
            self.is_lock_active = False
            self.peak_net_pct = 0.0
            return

        if AUTO_TRADE_AUTO_EXIT_ENABLED:
            self._run_auto_exits()

        self._sync_agent_trailing_lock_state()

    def _run_auto_exits(self) -> int:
        """Close auto trades that hit path-SL / path-TP. Returns number closed."""
        still_open = []
        closed_n = 0
        for trade in list(self.trades):
            if trade.get("source") == "manual":
                still_open.append(trade)
                continue
            trade_pair = trade.get("pair") or ""
            entry = float(trade.get("entry") or 0)
            if entry <= 0:
                still_open.append(trade)
                continue
            # Same mark the UI uses (includes cross-pair 20x guard).
            metrics = self._trade_metrics(trade)
            mark = float(metrics.get("mark_price") or 0)
            if mark <= 0:
                still_open.append(trade)
                continue
            try:
                reason = self._evaluate_fixed_pct_exit(trade, mark)
            except Exception as exc:
                print(f"[AUTO-EXIT] evaluate error #{trade.get('id')} {trade_pair}: {exc}")
                still_open.append(trade)
                continue
            if reason:
                if self._close_single_trade(trade, metrics, reason):
                    closed_n += 1
                    print(f"[AUTO-EXIT] {reason}")
                    continue
                print(
                    f"[AUTO-EXIT] Close FAILED #{trade.get('id')} {trade_pair} "
                    f"(will retry) | {reason}"
                )
            still_open.append(trade)
        self.trades = still_open
        return closed_n

    def _fixed_exit_prices(
        self,
        entry: float,
        side: str,
        *,
        loss_pct: float | None = None,
        profit_pct: float | None = None,
    ) -> tuple[float | None, float | None]:
        """SL/TP prices for given gross % levels (defaults: path tight SL + fixed TP)."""
        if entry <= 0:
            return None, None
        loss = (PATH_SL_TIGHT_PCT if loss_pct is None else float(loss_pct)) / 100.0
        profit = (FIXED_EXIT_PROFIT_PCT if profit_pct is None else float(profit_pct)) / 100.0
        if side == "LONG":
            sl = entry * (1.0 - loss)
            tp = entry * (1.0 + profit)
        elif side == "SHORT":
            sl = entry * (1.0 + loss)
            tp = entry * (1.0 - profit)
        else:
            return None, None
        return (
            round(sl, price_decimals_for_mark(entry)),
            round(tp, price_decimals_for_mark(entry)),
        )

    def _update_path_sl_state(self, trade: dict, gross_pct: float) -> None:
        """Track adverse/favorable steps for path-based SL + TP.

        Loss path:
          Adverse (-) → dump streak; 3× → continuous --- → tight −0.50% SL.
          Bounce (+) while in loss → choppy → wide −0.70% SL.
        Profit path:
          Favorable (+) → run streak; 3× → continuous +++ → hold to +0.70% TP.
          Dip (-) while in profit → profit-choppy → book at +0.50% TP.
        """
        last = float(trade.get("path_last_gross_pct") if trade.get("path_last_gross_pct") is not None else 0.0)
        # First sample after open: seed only.
        if trade.get("path_seeded") is not True:
            trade["path_last_gross_pct"] = gross_pct
            trade["path_seeded"] = True
            trade["path_adverse_streak"] = 0
            trade["path_favorable_streak"] = 0
            return

        eps = 1e-6
        if gross_pct < last - eps:
            # Adverse step (PnL worsened)
            streak = int(trade.get("path_adverse_streak") or 0) + 1
            trade["path_adverse_streak"] = streak
            trade["path_favorable_streak"] = 0
            if streak >= PATH_SL_ADVERSE_STEPS:
                trade["path_continuous_dump"] = True
            # Dip while already in profit → choppy profit path (book early at +0.5%)
            if last > eps or gross_pct > eps:
                trade["path_profit_choppy"] = True
        elif gross_pct > last + eps:
            # Favorable step (PnL improved)
            fav = int(trade.get("path_favorable_streak") or 0) + 1
            trade["path_favorable_streak"] = fav
            trade["path_adverse_streak"] = 0
            if fav >= PATH_TP_FAVORABLE_STEPS:
                trade["path_continuous_run"] = True
            # Bounce while in loss → choppy loss path (wide SL)
            if last < -eps or gross_pct < -eps:
                trade["path_choppy"] = True
        else:
            # Flat — break both continuity streaks
            trade["path_adverse_streak"] = 0
            trade["path_favorable_streak"] = 0

        trade["path_last_gross_pct"] = gross_pct

        # Active SL distance for UI
        if trade.get("path_choppy") and not trade.get("path_continuous_dump"):
            trade["path_sl_pct"] = PATH_SL_WIDE_PCT
        elif trade.get("path_continuous_dump") and not trade.get("path_choppy"):
            trade["path_sl_pct"] = PATH_SL_TIGHT_PCT
        elif trade.get("path_choppy"):
            trade["path_sl_pct"] = PATH_SL_WIDE_PCT
        else:
            trade["path_sl_pct"] = PATH_SL_TIGHT_PCT

        # Active TP distance for UI: choppy profit → tight 0.5%; clean +++ → wide 0.7%
        if trade.get("path_profit_choppy"):
            trade["path_tp_pct"] = PATH_TP_TIGHT_PCT
        else:
            trade["path_tp_pct"] = PATH_TP_WIDE_PCT

        # Keep sl_price / tp_price in sync for UI.
        entry = float(trade.get("entry") or 0)
        side = trade.get("side")
        if entry > 0 and side in ("LONG", "SHORT"):
            sl, tp = self._fixed_exit_prices(
                entry,
                side,
                loss_pct=float(trade["path_sl_pct"]),
                profit_pct=float(trade["path_tp_pct"]),
            )
            if sl is not None:
                trade["sl_price"] = sl
            if tp is not None:
                trade["tp_price"] = tp

    def _mark_from_gross_pct(self, entry: float, side: str, gross_pct: float) -> float:
        """Price that realizes exactly gross_pct for side (paper SL/TP fill clamp)."""
        if side == "LONG":
            return entry * (1.0 + float(gross_pct) / 100.0)
        return entry * (1.0 - float(gross_pct) / 100.0)

    def _evaluate_fixed_pct_exit(self, trade: dict, mark: float) -> str | None:
        """Path-based stop-loss + path-based profit book for auto trades.

        Loss: continuous --- → −0.50%; choppy -+-+ → −0.70%; hard floor −0.70%.
        Profit: continuous +++ → hold to +0.70%; choppy -+-+ → book +0.50%; hard ceiling +0.70%.
        Paper: if mark gapped past the rule, settle at the rule price.
        """
        trade.pop("_exit_fill_mark", None)
        entry = float(trade.get("entry") or 0)
        if entry <= 0:
            return None
        side = trade.get("side")
        if side not in ("LONG", "SHORT"):
            return None

        if side == "LONG":
            gross_pct = ((mark - entry) / entry) * 100.0
        else:
            gross_pct = ((entry - mark) / entry) * 100.0

        trade["peak_gross_pct"] = max(float(trade.get("peak_gross_pct") or 0), gross_pct)
        trade["trough_gross_pct"] = min(float(trade.get("trough_gross_pct") or 0), gross_pct)

        self._update_path_sl_state(trade, gross_pct)
        paper = trade.get("exchange") == "paper" or not trade_uses_bybit_executor(trade)

        def _arm_paper_fill(target_gross: float) -> None:
            """Clamp paper settlement to the policy level when mark has gapped past it."""
            if not paper:
                return
            if target_gross >= 0 and gross_pct > target_gross + 1e-9:
                trade["_exit_fill_mark"] = self._mark_from_gross_pct(entry, side, target_gross)
            elif target_gross < 0 and gross_pct < target_gross - 1e-9:
                trade["_exit_fill_mark"] = self._mark_from_gross_pct(entry, side, target_gross)

        # Hard profit ceiling — never hold past +0.70%
        if gross_pct >= PATH_TP_WIDE_PCT:
            _arm_paper_fill(PATH_TP_WIDE_PCT)
            gap = ""
            if trade.get("_exit_fill_mark") is not None:
                gap = f" (gapped {gross_pct:.3f}%→fill +{PATH_TP_WIDE_PCT:g}%)"
            return (
                f"Path TP +{PATH_TP_WIDE_PCT:g}%: {side} gross {gross_pct:.3f}% "
                f"(mark {mark:.6f} vs entry {entry:.6f}){gap}"
            )

        # Hard loss floor — never hold past −0.70%
        if gross_pct <= -PATH_SL_WIDE_PCT:
            _arm_paper_fill(-PATH_SL_WIDE_PCT)
            gap = ""
            if trade.get("_exit_fill_mark") is not None:
                gap = f" (gapped {gross_pct:.3f}%→fill −{PATH_SL_WIDE_PCT:g}%)"
            return (
                f"Path SL −{PATH_SL_WIDE_PCT:g}%: {side} gross {gross_pct:.3f}% "
                f"(mark {mark:.6f} vs entry {entry:.6f}){gap}"
            )

        # ---- Profit book zone (+0.50% … +0.70%) ----
        if gross_pct >= PATH_TP_TIGHT_PCT:
            profit_choppy = bool(trade.get("path_profit_choppy"))
            continuous_run = bool(trade.get("path_continuous_run"))
            if profit_choppy:
                # -+-+ path → book at +0.50%
                _arm_paper_fill(PATH_TP_TIGHT_PCT)
                gap = ""
                if trade.get("_exit_fill_mark") is not None:
                    gap = f" (gapped {gross_pct:.3f}%→fill +{PATH_TP_TIGHT_PCT:g}%)"
                return (
                    f"Path TP +{PATH_TP_TIGHT_PCT:g}% (choppy -+-+): {side} gross {gross_pct:.3f}% "
                    f"(mark {mark:.6f}){gap}"
                )
            # continuous +++ / clean run → hold to +0.70%
            why = "continuous +++" if continuous_run else "clean run"
            trade["path_tp_pct"] = PATH_TP_WIDE_PCT
            return None  # hold to hard ceiling

        # ---- Loss zone (−0.50% … −0.70%) ----
        if gross_pct > -PATH_SL_TIGHT_PCT:
            return None

        choppy = bool(trade.get("path_choppy"))
        continuous = bool(trade.get("path_continuous_dump"))

        if choppy:
            return None  # hold to −0.70%

        why = "continuous ---" if continuous else "no-bounce dump"
        _arm_paper_fill(-PATH_SL_TIGHT_PCT)
        gap = ""
        if trade.get("_exit_fill_mark") is not None:
            gap = f" (gapped {gross_pct:.3f}%→fill −{PATH_SL_TIGHT_PCT:g}%)"
        return (
            f"Path SL −{PATH_SL_TIGHT_PCT:g}% ({why}): {side} gross {gross_pct:.3f}% "
            f"streak={trade.get('path_adverse_streak')} (mark {mark:.6f}){gap}"
        )

    def _close_single_trade(self, trade, metrics, reason) -> bool:
        """Close one position. Returns True if closed locally (and on Bybit when applicable)."""
        # Paper path-SL/TP: if mark gapped past the rule, settle at the rule price.
        fill_mark = trade.pop("_exit_fill_mark", None)
        pair = (trade.get("pair") or "").strip()
        prev_mark = None
        if fill_mark is not None and pair and (
            trade.get("exchange") == "paper" or not trade_uses_bybit_executor(trade)
        ):
            prev_mark = self.pair_prices.get(pair)
            self.pair_prices[pair] = float(fill_mark)
            if pair == self.active_pair:
                self.current_price = float(fill_mark)
        try:
            # Always settle with full round-trip fees at close.
            metrics = self._trade_metrics(trade, for_close=True)
        finally:
            # Restore live mark after settlement; metrics already captured at fill.
            if fill_mark is not None and pair and prev_mark is not None:
                self.pair_prices[pair] = prev_mark
                if pair == self.active_pair:
                    self.current_price = float(prev_mark)
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
        # Hold-stop: when last held position exits, freeze that session book.
        if self.session_hold_mode:
            remaining = [
                t for t in self._session_open_trades()
                if t.get("id") != trade.get("id")
            ]
            if not remaining:
                self.session_hold_mode = False
                if self.ai_season_start_capital is not None or self.ai_season_id is not None:
                    self.end_ai_season(clear_live_table=False, reason="hold_drained")
        return True

    def has_opposite_position(self, side: str, pair: str) -> bool:
        opposite = "SHORT" if side == "LONG" else "LONG"
        return any(t["pair"] == pair and t["side"] == opposite for t in self.trades)

    def close_opposite_positions_for_flip(
        self, side: str, pair: str, *, pattern: str | None = None
    ) -> int:
        """AI flip-exit training: opposite signal on the same chart/pair closes the old side.

        Long + short on different candles of one pair is allowed, but when the new side
        fires, open opposite positions on that pair exit immediately — do not ride the
        reverse move into deeper loss (e.g. LONG still open when SHORT fires).
        """
        opposite = "SHORT" if side == "LONG" else "LONG"
        targets = [
            t
            for t in list(self.trades)
            if (t.get("pair") or "") == pair and t.get("side") == opposite
        ]
        if not targets:
            return 0

        why = (
            f"Flip-exit on {pair}: {side} signal"
            + (f" ({pattern})" if pattern else "")
            + f" closed opposite {opposite}"
        )
        target_ids = {t["id"] for t in targets}
        still_open: list = []
        closed_n = 0
        for trade in list(self.trades):
            if trade.get("id") not in target_ids:
                still_open.append(trade)
                continue
            m = self._trade_metrics(trade, for_close=True)
            if self._close_single_trade(trade, m, why):
                closed_n += 1
                print(
                    f"[FLIP-EXIT] {why} | #{trade.get('id')} "
                    f"gross={m['gross_pct']:.3f}% net=${m['net_usd']:.2f}"
                )
                system_log.push_agent_chat(
                    f"FLIP-EXIT {opposite} #{trade.get('id')} on {pair} — new {side} signal"
                    + (f" ({pattern})" if pattern else ""),
                    status="match",
                    details={
                        "pair": pair,
                        "closed_side": opposite,
                        "new_side": side,
                        "pattern": pattern,
                        "trade_id": trade.get("id"),
                        "gross_pct": m.get("gross_pct"),
                        "net_usd": m.get("net_usd"),
                    },
                )
            else:
                still_open.append(trade)
        self.trades = still_open
        if closed_n:
            notifications.push(
                f"Flip-exit: closed {closed_n}× {opposite} on {pair} before new {side}.",
                "info",
            )
        return closed_n

    def same_side_auto_count(self, side: str, pair: str) -> int:
        return sum(
            1
            for t in self.trades
            if t.get("source") == "auto" and t.get("pair") == pair and t.get("side") == side
        )

    def has_same_side_auto_capacity(self, side: str, pair: str) -> bool:
        """False when this pair already has enough same-side auto positions."""
        limit = max(1, int(MAX_SAME_SIDE_AUTO_PER_PAIR))
        return self.same_side_auto_count(side, pair) < limit

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
        """Strategy wiped — trailing profit-book batch sell disabled."""
        print(f"[AI AGENT] execute_sell ignored (manual mode): {reason}")
        return

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
        self.session_hold_mode = False
        _clear_entry_pipeline()
        self._close_all_positions("EMERGENCY SELL ALL TRIGGERED | RULE 8 confirmed by user")
        self.is_active = False
        self.clear_emergency_state()
        self.end_ai_season(clear_live_table=True, reason="emergency_exit")
        self.peak_net_pct = 0.0
        self.is_lock_active = False

    def manual_stop(self, reason="Manual Kill Switch Activated from Frontend"):
        """Emergency STOP — sell all open positions and halt AI."""
        print(f"[PILLAR 2: BACKEND] {reason}")
        self.session_hold_mode = False
        self._close_all_positions(reason)
        self.is_active = False
        _clear_entry_pipeline()
        self.end_ai_season(clear_live_table=True, reason="manual_stop")
        self.peak_net_pct = 0.0
        self.is_lock_active = False
        system_log.push("ai", "AI automation STOPPED — emergency exit, all positions closed.", {"reason": reason})
        notifications.push("EMERGENCY EXIT: All positions closed and AI automation stopped.", "error")

    def hold_stop(self, reason: str = "AI Engine STOP — hold open trades"):
        """STOP with Hold: no new fires; open trades keep path-SL / TP auto-exit; portfolio keeps updating."""
        print(f"[AI ENGINE] HOLD STOP — {reason}")
        self.is_active = False
        self.session_hold_mode = True
        _clear_entry_pipeline()
        # Keep season_id + start capital so held closes still roll into portfolio counters.
        self.session_stats_frozen = False
        open_n = len(self.trades)
        system_log.push(
            "ai",
            f"AI Engine STOPPED (Hold) — {open_n} position(s) still managed until TP/SL.",
            {"reason": reason, "open_positions": open_n},
        )
        notifications.push(
            f"AI Engine stopped (Hold). {open_n} open trade(s) will auto-exit on TP/SL — no new entries. ({reason})",
            "warning",
        )

    def schedule_soft_stop(self, reason: str):
        """Session schedule off-window: stop new entries, keep open trades + season PnL."""
        if not self.is_active:
            return
        print(f"[SESSION SCHEDULE] Soft stop — {reason}")
        self.is_active = False
        self.session_hold_mode = True
        _clear_entry_pipeline()
        self.session_stats_frozen = False
        system_log.push(
            "ai",
            f"Session schedule paused AI automation — {reason}",
            {"open_positions": len(self.trades)},
        )
        notifications.push(
            f"Session schedule OFF-window — automation paused ({len(self.trades)} open position(s) still managed).",
            "warning",
        )

    def schedule_auto_start(self, reason: str):
        """Session schedule in-window: same wake-up as /start-bot (no browser needed)."""
        if self.is_active:
            return
        if self.emergency_awaiting_decision:
            print(f"[SESSION SCHEDULE] Skip auto-start (emergency awaiting decision): {reason}")
            return
        open_count = len(self.trades)
        self.clear_emergency_state()
        self.daily_target_reached = False
        # Resume existing season if soft-paused with open book; else start fresh.
        if self.ai_season_start_capital is None:
            self.begin_ai_season()
        self.is_active = True
        print(f"[SESSION SCHEDULE] Auto-start — {reason}")
        system_log.push(
            "ai",
            f"Session schedule STARTED AI automation — {reason}",
            {"open_positions": open_count},
        )
        notifications.push(f"Session schedule ON — AI automation started ({reason}).", "success")

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
# POL (not MATIC). PEPE/BONK use Bybit 1000× contract symbols.
BYBIT_SYMBOL_MAP = {
    "BTC": "BTCUSDT",
    "SOL": "SOLUSDT",
    "DOGE": "DOGEUSDT",
    "BNB": "BNBUSDT",
    "ADA": "ADAUSDT",
    "AVAX": "AVAXUSDT",
    "LINK": "LINKUSDT",
    "DOT": "DOTUSDT",
    "POL": "POLUSDT",
    "NEAR": "NEARUSDT",
    "ATOM": "ATOMUSDT",
    "UNI": "UNIUSDT",
    "APT": "APTUSDT",
    "ARB": "ARBUSDT",
    "OP": "OPUSDT",
    "SUI": "SUIUSDT",
    "PEPE": "1000PEPEUSDT",
    "WIF": "WIFUSDT",
    "BONK": "1000BONKUSDT",
    "XAUT": "XAUTUSDT",
}

# qtyStep from Bybit instruments-info (linear). Used to snap order size.
BYBIT_QTY_STEP = {
    "BTCUSDT": 0.001,
    "SOLUSDT": 0.1,
    "DOGEUSDT": 1,
    "BNBUSDT": 0.01,
    "ADAUSDT": 1,
    "AVAXUSDT": 0.1,
    "LINKUSDT": 0.1,
    "DOTUSDT": 0.1,
    "POLUSDT": 1,
    "NEARUSDT": 0.1,
    "ATOMUSDT": 0.1,
    "UNIUSDT": 0.1,
    "APTUSDT": 0.1,
    "ARBUSDT": 1,
    "OPUSDT": 0.1,
    "SUIUSDT": 1,
    "1000PEPEUSDT": 100,
    "WIFUSDT": 1,
    "1000BONKUSDT": 100,
    "XAUTUSDT": 0.001,
}

def get_bybit_symbol(pair_label):
    symbol = (pair_label or "").split("/")[0]
    return BYBIT_SYMBOL_MAP.get(symbol)


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


_last_real_feed_update = 0.0
REAL_FEED_STALE_AFTER_SECONDS = 10


def snap_qty_to_step(qty: float, bybit_symbol: str | None) -> float | None:
    """Floor qty to Bybit lot step so market orders are not rejected."""
    if qty is None or qty <= 0:
        return None
    step = BYBIT_QTY_STEP.get(bybit_symbol) if bybit_symbol else None
    if not step or step <= 0:
        return qty
    snapped = math.floor(qty / step + 1e-12) * step
    if snapped <= 0:
        return None
    # Avoid float junk (e.g. 0.30000000004) in order qty strings.
    decimals = max(0, min(8, -int(math.floor(math.log10(step))) if step < 1 else 0))
    return round(snapped, decimals)


def min_lot_qty(bybit_symbol: str | None) -> float | None:
    step = BYBIT_QTY_STEP.get(bybit_symbol) if bybit_symbol else None
    return float(step) if step and step > 0 else None


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

# Detect on last closed candle → queue → fire at the NEXT candle's open (not on detect bar).
PENDING_ENTRY_SIGNALS: dict[str, dict] = {}
PATTERN_NEON_STAGES: list[dict] = []
THREE_CANDLE_ENTRY = False

_bybit_executor_agent = None


def _timeframe_interval_ms(timeframe_key: str) -> int:
    seconds = {
        "30s": 30,
        "1m": 60,
        "3m": 180,
        "5m": 300,
        "10m": 600,
        "15m": 900,
        "30m": 1800,
        "1h": 3600,
        "1D": 86400,
    }.get(str(timeframe_key or "1m"), 60)
    return int(seconds * 1000)


def _chart_time_sec(raw_ms_or_sec: int | float | None) -> int | None:
    if raw_ms_or_sec is None:
        return None
    raw = int(raw_ms_or_sec)
    return int(raw // 1000) if raw > 1_000_000_000_000 else raw


def _push_pattern_neon(
    *,
    pair: str,
    candle_time_ms: int,
    stage: str,
    side: str | None = None,
    action: str | None = None,
    pattern: str | None = None,
    reason: str | None = None,
) -> None:
    """Append a chart neon stage (detected / confirming / fired / skipped)."""
    tsec = _chart_time_sec(candle_time_ms)
    if tsec is None:
        return
    resolved_side = side
    if not resolved_side and action:
        resolved_side = "LONG" if action == "BUY" else "SHORT" if action == "SELL" else None
    entry = {
        "time": tsec,
        "signal_candle_time": tsec,
        "pair": pair,
        "stage": stage,
        "side": resolved_side,
        "action": action,
        "pattern": pattern or "Pattern",
        "reason": reason,
        "opened_at": time.time() if stage == "fired" else None,
    }
    # Replace same pair+time+stage; keep higher-rank stage on same bar.
    rank = {"detected": 1, "confirming": 2, "skipped": 3, "fired": 4}.get(stage, 0)
    kept: list[dict] = []
    for prev in PATTERN_NEON_STAGES:
        if prev.get("pair") == pair and int(prev.get("time") or 0) == tsec:
            prev_rank = {"detected": 1, "confirming": 2, "skipped": 3, "fired": 4}.get(
                str(prev.get("stage") or ""), 0
            )
            if prev_rank > rank:
                kept.append(prev)
            continue
        kept.append(prev)
    kept.append(entry)
    PATTERN_NEON_STAGES[:] = kept[-120:]


def _clear_entry_pipeline() -> None:
    PENDING_ENTRY_SIGNALS.clear()


def get_pattern_neon_snapshot(pair: str | None = None) -> list[dict]:
    if not pair:
        return list(PATTERN_NEON_STAGES[-80:])
    return [e for e in PATTERN_NEON_STAGES[-80:] if e.get("pair") == pair]


def trade_uses_bybit_executor(trade: dict) -> bool:
    """True when this trade was opened on Bybit TESTNET linear and needs a real close."""
    return (
        trade.get("exchange") == "bybit_linear_testnet"
        and is_bybit_testnet_configured()
    )


def bybit_close_trade(trade: dict, qty: float | None = None) -> tuple[bool, str | None]:
    executor = get_bybit_executor_agent()
    return executor.close_position(trade, qty=qty)


def get_bybit_executor_agent():
    """Lazily builds BybitAgent from TESTNET credentials for real closes."""
    global _bybit_executor_agent
    if _bybit_executor_agent is None:
        key = get_bybit_testnet_api_key()
        secret = get_bybit_testnet_api_secret()
        _bybit_executor_agent = BybitAgent(key, secret, testnet=True)
    return _bybit_executor_agent


def agent_policy_summary() -> str:
    """Policy text shown in System Log."""
    return (
        "CANDLESTICK BRAIN v2 + path SL | "
        f"auto TP +{PATH_TP_TIGHT_PCT:g}% (choppy) / +{PATH_TP_WIDE_PCT:g}% (continuous +++) | "
        f"SL −{PATH_SL_TIGHT_PCT:g}% (continuous ---) / −{PATH_SL_WIDE_PCT:g}% (choppy -+-+) | "
        "manual BUY/SELL + emergency sell-all"
    )


def is_btc_pair(pair: str | None) -> bool:
    return (pair or "").strip().upper().startswith("BTC")


def qty_decimals_for_price(price: float) -> int:
    if price >= 10000:
        return 6
    if price >= 1000:
        return 5
    if price >= 1:
        return 4
    if price >= 0.01:
        return 2
    return 0


def price_decimals_for_mark(price: float) -> int:
    if price >= 1000:
        return 2
    if price >= 1:
        return 4
    if price >= 0.01:
        return 6
    return 8


def auto_trade_capital_pct_for_agent(agent) -> float:
    tf_key = SECONDS_TO_TIMEFRAME_KEY.get(getattr(agent, "timeframe_seconds", 60), "1m")
    return capital_pct_fraction(tf_key)


def compute_order_qty(position_size_usd, current_price, qty_decimals=None, bybit_symbol=None):
    if not current_price or current_price <= 0:
        return None
    if qty_decimals is None:
        qty_decimals = qty_decimals_for_price(current_price)
    qty = round(float(position_size_usd) / float(current_price), qty_decimals)
    return snap_qty_to_step(qty, bybit_symbol)


def compute_auto_trade_plan(
    agent,
    price: float | None = None,
    size_mult: float = 1.0,
    pair: str | None = None,
) -> dict | None:
    trade_pair = (pair or getattr(agent, "active_pair", None) or "").strip()
    mark = agent.mark_price_for(trade_pair) if trade_pair else None
    entry_price = _sanitize_market_price(
        price if price is not None else (mark if mark is not None else agent.current_price)
    )
    if entry_price is None:
        return None
    available = agent.get_available_capital()
    if available is None or available <= 0:
        return None
    mult = max(1.0, float(size_mult))
    cap_frac = auto_trade_capital_pct_for_agent(agent)
    position_usd = round(available * cap_frac * mult, 2)
    if position_usd <= 0:
        return None
    bybit_symbol = get_bybit_symbol(trade_pair)
    decimals = qty_decimals_for_price(entry_price)
    raw_qty = position_usd / entry_price
    qty = snap_qty_to_step(raw_qty, bybit_symbol)
    bumped_to_min_lot = False
    if qty is None or qty <= 0:
        lot = min_lot_qty(bybit_symbol)
        if lot is None:
            return None
        min_notional = lot * entry_price
        leverage = max(float(getattr(agent, "leverage", 1) or 1), 1.0)
        margin_needed = min_notional / leverage
        if margin_needed > available * 0.95:
            return None
        qty = snap_qty_to_step(lot, bybit_symbol) or lot
        position_usd = round(qty * entry_price, 2)
        bumped_to_min_lot = True
    if qty is None or qty <= 0:
        return None
    leverage = max(float(getattr(agent, "leverage", 1) or 1), 1.0)
    margin = round(position_usd / leverage, 4)
    tf_key = SECONDS_TO_TIMEFRAME_KEY.get(agent.timeframe_seconds, "1m")
    profile = get_timeframe_profile(tf_key)
    return {
        "total_capital": round(available, 2),
        "available_capital": round(available, 2),
        "position_usd": position_usd,
        "capital_pct": (position_usd / available * 100) if available else cap_frac * 100 * mult,
        "size_mult": mult,
        "qty": qty,
        "qty_decimals": decimals,
        "bumped_to_min_lot": bumped_to_min_lot,
        "timeframe": tf_key,
        "win_rate": profile["win_rate"],
        "lose_rate": profile["lose_rate"],
        "margin": margin,
        "price": entry_price,
        "entry_price": entry_price,
        "pair": trade_pair,
    }


def evaluate_entry(
    candles,
    timeframe_key: str,
    *,
    pair: str = "default",
    htf_candles=None,
    candles_1m=None,
    candles_5m=None,
):
    """brain.py unified engine — covers all timeframes (1m through 1d)."""
    balance = agent.get_available_capital() or agent.get_trading_capital_base() or 10000.0
    risk_pct = max(float(getattr(agent, "risk_level_pct", 1.0) or 1.0), 0.5) / 100.0
    risk_pct = min(risk_pct, 0.02)
    result = evaluate_live_entry(
        candles,
        timeframe_key,
        pair=pair,
        htf_candles=htf_candles or candles_5m,
        candles_1m=candles_1m,
        candles_5m=candles_5m,
        account_balance=float(balance),
        risk_pct=risk_pct,
    )
    if result.get("action") in ("BUY", "SELL"):
        if INVERT_AUTO_TRADE_FIRE:
            result = dict(result)
            result["action"] = "SELL" if result["action"] == "BUY" else "BUY"
            result["reason"] = f"INVERTED | {result.get('reason')}"
        return enrich_signal(result)
    return result


async def fetch_closed_candle_history(
    client: httpx.AsyncClient,
    bybit_symbol: str,
    timeframe_key: str,
    limit: int = 80,
) -> list[dict]:
    interval = TIMEFRAME_KEY_TO_BYBIT_KLINE.get(timeframe_key, "5")
    rows = await fetch_kline_rows(client, bybit_symbol, interval, limit)
    # Bybit returns newest-first; drop forming (last) bar so we only scan closed candles.
    candles = [parse_bybit_kline(r) for r in reversed(rows)]
    if len(candles) >= 2:
        candles = candles[:-1]
    return candles


async def scan_and_maybe_fire_pair(client: httpx.AsyncClient, pair: str, timeframe_key: str) -> bool:
    """Scan last CLOSED candle for pattern; fire only at the NEXT candle open.

    Flow:
      1) Try to fire any queued signal once the next candle has started.
      2) On a newly closed candle: detect pattern → queue (DETECTED), do NOT fire yet.
    """
    bybit_symbol = get_bybit_symbol(pair)
    if not bybit_symbol:
        return False

    interval_ms = _timeframe_interval_ms(timeframe_key)
    # Multi-pair scan + AI consult can take longer than one candle. Keep the
    # queued signal alive for the fire candle plus at least 45s of grace.
    fire_grace_ms = max(interval_ms, 45_000)

    async def _skip_pending(pending: dict, reason: str, *, fire_candle_ms: int | None = None) -> bool:
        detect = pending.get("detect") or {}
        side = pending.get("side") or ("LONG" if detect.get("action") == "BUY" else "SHORT")
        candle_ms = int(fire_candle_ms or pending.get("fire_candle_time") or pending.get("signal_candle_time") or 0)
        _push_pattern_neon(
            pair=pair,
            candle_time_ms=candle_ms,
            stage="skipped",
            side=side,
            action=detect.get("action"),
            pattern=detect.get("pattern"),
            reason=reason,
        )
        system_log.push_agent_chat(
            f"SKIPPED {side} on {pair}: {reason}",
            status="no_match",
            details={"pair": pair, "side": side, "reason": reason, "detect": detect},
        )
        system_log.set_last_trade_fire(
            {
                "success": False,
                "skipped": True,
                "action": detect.get("action"),
                "symbol": bybit_symbol,
                "pattern": detect.get("pattern"),
                "pair": pair,
                "error": reason,
            }
        )
        PENDING_ENTRY_SIGNALS.pop(pair, None)
        print(f"[BRAIN] SKIPPED {side} {pair}: {reason}")
        return False

    async def _execute_queued_fire(pending: dict) -> bool:
        detect = dict(pending.get("detect") or {})
        side = pending["side"]
        detect_candle_ms = int(pending["signal_candle_time"])
        fire_candle_ms = int(pending["fire_candle_time"])
        candle_close = float(pending.get("detect_close") or detect.get("entry") or 0)

        # Flip-exit training: SHORT fire → close open LONGs on this pair (and vice versa)
        # before capacity checks / new entry, so we do not ride the reverse into loss.
        agent.close_opposite_positions_for_flip(
            side, pair, pattern=detect.get("pattern")
        )

        if len(agent.trades) >= agent.max_concurrent_trades:
            return await _skip_pending(
                pending,
                f"Max concurrent trades ({agent.max_concurrent_trades}) reached "
                f"({len(agent.trades)} open)",
                fire_candle_ms=fire_candle_ms,
            )
        if agent.daily_target_reached:
            return await _skip_pending(pending, "Daily profit target already reached", fire_candle_ms=fire_candle_ms)
        if not agent.has_same_side_auto_capacity(side, pair):
            return await _skip_pending(
                pending,
                f"{side} already open on {pair} (max {MAX_SAME_SIDE_AUTO_PER_PAIR} same-side)",
                fire_candle_ms=fire_candle_ms,
            )
        if agent.has_duplicate_auto_entry(
            side, pair, detect.get("pattern"), detect_candle_ms, candle_close or float(detect.get("entry") or 0)
        ):
            return await _skip_pending(
                pending,
                f"Duplicate entry on same detect candle/pattern for {pair}",
                fire_candle_ms=fire_candle_ms,
            )

        mark_px = agent.mark_price_for(pair) or float(detect.get("entry") or candle_close or 0)
        if not mark_px or mark_px <= 0:
            return await _skip_pending(pending, f"No usable mark price for {pair}", fire_candle_ms=fire_candle_ms)

        plan = compute_auto_trade_plan(agent, price=mark_px, pair=pair)
        if plan is None:
            return await _skip_pending(pending, "Size plan failed (capital/lot)", fire_candle_ms=fire_candle_ms)

        brain_sl = detect.get("sl")
        brain_tp = detect.get("tp")
        if brain_sl and brain_tp and float(brain_sl) > 0 and float(brain_tp) > 0:
            sl_price = round(float(brain_sl), price_decimals_for_mark(mark_px))
            tp_price = round(float(brain_tp), price_decimals_for_mark(mark_px))
            exit_label = f"brain SL={sl_price} TP={tp_price}"
        else:
            sl_price, tp_price = agent._fixed_exit_prices(
                float(mark_px), side, loss_pct=PATH_SL_TIGHT_PCT, profit_pct=PATH_TP_WIDE_PCT
            )
            exit_label = (
                f"path SL {PATH_SL_TIGHT_PCT:g}/{PATH_SL_WIDE_PCT:g}% "
                f"TP {PATH_TP_TIGHT_PCT:g}/{PATH_TP_WIDE_PCT:g}% SL={sl_price} TP={tp_price}"
            )

        rr = detect.get("risk_reward") or (FIXED_EXIT_PROFIT_PCT / FIXED_EXIT_LOSS_PCT)
        trade = agent.open_trade(
            side=side,
            reason=detect.get("reason") or f"Brain {detect.get('pattern')}",
            source="auto",
            position_size_usd=plan["position_usd"],
            qty=plan["qty"],
            entry_price=mark_px,
            bybit_symbol=bybit_symbol,
            pattern=detect.get("pattern"),
            # Chart FIRED neon on the NEXT candle (not the detect bar).
            signal_candle_time=fire_candle_ms,
            taapi_action=detect["action"],
            sl_price=sl_price,
            tp_price=tp_price,
            target_mult=float(rr),
            pair=pair,
            timeframe_key=timeframe_key,
        )
        if not trade:
            return await _skip_pending(
                pending,
                agent.last_open_skip_reason or "open_trade returned None",
                fire_candle_ms=fire_candle_ms,
            )

        PENDING_ENTRY_SIGNALS.pop(pair, None)
        _push_pattern_neon(
            pair=pair,
            candle_time_ms=fire_candle_ms,
            stage="fired",
            side=side,
            action=detect.get("action"),
            pattern=detect.get("pattern"),
            reason=detect.get("reason"),
        )
        system_log.push_agent_chat(
            brain_chat_summary(detect) + f" → FIRED {side} on {pair} (next-candle open)",
            status="match",
            details={
                "pair": pair,
                "trade_id": trade.get("id"),
                "sl": trade.get("sl_price"),
                "tp": trade.get("tp_price"),
                "exit": exit_label,
                "detect_candle": detect_candle_ms,
                "fire_candle": fire_candle_ms,
            },
        )
        system_log.set_last_trade_fire(
            {
                "success": True,
                "action": detect["action"],
                "symbol": bybit_symbol,
                "pattern": detect.get("pattern"),
                "pair": pair,
                "entry": mark_px,
                "sl": trade.get("sl_price"),
                "tp": trade.get("tp_price"),
                "detect_candle": detect_candle_ms,
                "fire_candle": fire_candle_ms,
            }
        )
        print(
            f"[BRAIN] {side} {pair} @ {mark_px} | pattern={detect.get('pattern')} "
            f"detect@{detect_candle_ms} fire@{fire_candle_ms} {exit_label}"
        )
        return True

    # --- 1) Fire queued signal once the next candle has started ---
    pending = PENDING_ENTRY_SIGNALS.get(pair)
    if pending and pending.get("timeframe_key") == timeframe_key:
        fire_candle_ms = int(pending["fire_candle_time"])
        now_ms = int(time.time() * 1000)
        deadline_ms = fire_candle_ms + interval_ms + fire_grace_ms
        # Expire only after fire candle + grace (scan/AI lag must not kill the entry).
        if now_ms > deadline_ms:
            await _skip_pending(pending, "Next-candle fire window expired", fire_candle_ms=fire_candle_ms)
        elif now_ms >= fire_candle_ms:
            _push_pattern_neon(
                pair=pair,
                candle_time_ms=fire_candle_ms,
                stage="confirming",
                side=pending.get("side"),
                action=(pending.get("detect") or {}).get("action"),
                pattern=(pending.get("detect") or {}).get("pattern"),
                reason="Waiting next-candle open → firing",
            )
            if await _execute_queued_fire(pending):
                return True
    elif pending and pending.get("timeframe_key") != timeframe_key:
        PENDING_ENTRY_SIGNALS.pop(pair, None)

    # --- 2) Scan only the latest CLOSED candle (forming bar already dropped) ---
    lookback = max(MIN_CANDLES, 100)
    history = await fetch_closed_candle_history(client, bybit_symbol, timeframe_key, limit=lookback)
    if len(history) < MIN_CANDLES:
        return False

    close_time = int(history[-1]["close_time"])  # Bybit startTime of last closed bar
    if close_time <= LAST_CANDLE_TIMESTAMPS.get(pair, 0):
        return False
    LAST_CANDLE_TIMESTAMPS[pair] = close_time

    _HTF_MAP = {"1m": "5m", "5m": "15m", "15m": "1h", "1h": "1D", "30s": "5m"}
    tf_l = timeframe_key.lower()
    htf_key = _HTF_MAP.get(tf_l)
    htf_candles = None
    if htf_key:
        try:
            htf_candles = await fetch_closed_candle_history(client, bybit_symbol, htf_key, limit=60)
        except Exception:
            htf_candles = None

    candles_1m = history if tf_l in ("1m", "30s") else None
    candles_5m = history if tf_l == "5m" else (htf_candles if tf_l in ("1m", "30s") else None)
    try:
        if candles_1m is None:
            candles_1m = await fetch_closed_candle_history(client, bybit_symbol, "1m", limit=lookback)
        if candles_5m is None:
            candles_5m = await fetch_closed_candle_history(client, bybit_symbol, "5m", limit=60)
    except Exception as exc:
        print(f"[TRAP-OF] 1m/5m fetch failed on {pair}: {exc}")

    balance = agent.get_available_capital() or agent.get_trading_capital_base() or 10000.0
    risk_pct = max(float(getattr(agent, "risk_level_pct", 1.0) or 1.0), 0.5) / 100.0
    risk_pct = min(risk_pct, 0.02)

    try:
        detect = await _brain_evaluate_async(
            history,
            timeframe_key,
            pair=pair,
            htf_candles=htf_candles,
            candles_1m=candles_1m,
            candles_5m=candles_5m,
            account_balance=float(balance),
            risk_pct=risk_pct,
            settings=settings_store,
        )
    except Exception as exc:
        print(f"[AI-BRAIN] evaluate error on {pair}: {exc}")
        return False

    if detect.get("action") in ("BUY", "SELL"):
        detect = enrich_signal(detect)

    system_log.set_last_uvss_scan(
        pair,
        timeframe_key,
        detect,
        {
            "high": history[-1]["high"],
            "low": history[-1]["low"],
            "close": history[-1]["close"],
            "close_time": close_time,
        },
    )

    if detect.get("action") not in ("BUY", "SELL"):
        system_log.push_agent_chat(
            f"AI-Brain scan {pair} @ {timeframe_key}: {detect.get('reason', 'HOLD')}",
            status="scanning",
            details={
                "pair": pair,
                "timeframe": timeframe_key,
                "engine": detect.get("engine"),
                "ai_driven": detect.get("ai_driven"),
                "brain_verdict": detect.get("brain_verdict"),
                "orderflow_trap": (detect.get("orderflow_trap") or {}).get("line"),
            },
        )
        return False

    candle_close = float(history[-1].get("close") or 0)
    if candle_close > 0:
        agent.set_pair_mark(pair, candle_close)

    side = "LONG" if detect["action"] == "BUY" else "SHORT"
    fire_candle_ms = close_time + interval_ms

    # Soft capacity checks at queue time (re-checked at fire).
    if len(agent.trades) >= agent.max_concurrent_trades:
        _push_pattern_neon(
            pair=pair,
            candle_time_ms=close_time,
            stage="skipped",
            side=side,
            action=detect.get("action"),
            pattern=detect.get("pattern"),
            reason="Max concurrent trades reached",
        )
        return False
    if agent.daily_target_reached:
        return False
    if not agent.has_same_side_auto_capacity(side, pair):
        return False
    if agent.has_duplicate_auto_entry(
        side, pair, detect.get("pattern"), close_time, candle_close or float(detect.get("entry") or 0)
    ):
        return False

    # If already waiting on this detect candle, keep it; newer candle replaces.
    existing = PENDING_ENTRY_SIGNALS.get(pair)
    if existing and int(existing.get("signal_candle_time") or 0) == close_time:
        return False

    # Queue FIRST so multi-pair / AI lag does not burn the fire window.
    detect = dict(detect)
    PENDING_ENTRY_SIGNALS[pair] = {
        "detect": detect,
        "side": side,
        "signal_candle_time": close_time,
        "fire_candle_time": fire_candle_ms,
        "timeframe_key": timeframe_key,
        "detect_close": candle_close,
        "queued_at": time.time(),
    }
    _push_pattern_neon(
        pair=pair,
        candle_time_ms=close_time,
        stage="detected",
        side=side,
        action=detect.get("action"),
        pattern=detect.get("pattern"),
        reason=detect.get("reason"),
    )
    _push_pattern_neon(
        pair=pair,
        candle_time_ms=fire_candle_ms,
        stage="confirming",
        side=side,
        action=detect.get("action"),
        pattern=detect.get("pattern"),
        reason="Fire at next candle open",
    )
    system_log.push_agent_chat(
        f"DETECTED {side} on {pair}: {detect.get('pattern')} — fire at next candle open",
        status="match",
        details={
            "pair": pair,
            "detect_candle": close_time,
            "fire_candle": fire_candle_ms,
            "pattern": detect.get("pattern"),
        },
    )
    print(
        f"[BRAIN] DETECTED {side} {pair} pattern={detect.get('pattern')} "
        f"on closed@{close_time} → queue fire@{fire_candle_ms}"
    )

    # AI consult for observability (does not block queue / fire).
    ai_decision = None
    try:
        ai_decision = await consult_ai_provider(
            {
                "pair": pair,
                "timeframe": timeframe_key,
                "action": detect.get("action"),
                "reason": detect.get("reason"),
                "confidence": detect.get("confidence"),
                "current_price": history[-1].get("close"),
                "candle_volume": history[-1].get("volume"),
                "prev_candle_volume": history[-2].get("volume") if len(history) >= 2 else None,
                "candle_height": history[-1].get("high", 0) - history[-1].get("low", 0),
                "prev_candle_height": (
                    history[-2].get("high", 0) - history[-2].get("low", 0)
                    if len(history) >= 2
                    else None
                ),
            }
        )
    except Exception as exc:
        print(f"[AI AGENT] consult_ai_provider error on {pair}: {exc}")

    ai_note = "YES" if ai_decision is True else "NO" if ai_decision is False else "SKIP"
    detect["ai_confirmation"] = ai_note
    if ai_note != "SKIP":
        detect["reason"] = f"{detect.get('reason', '')} | AI={ai_note}"
    if pair in PENDING_ENTRY_SIGNALS:
        PENDING_ENTRY_SIGNALS[pair]["detect"] = detect

    # If next candle already opened while we scanned/AI'd, fire now.
    now_ms = int(time.time() * 1000)
    pending = PENDING_ENTRY_SIGNALS.get(pair)
    if pending and now_ms >= fire_candle_ms:
        deadline_ms = fire_candle_ms + interval_ms + fire_grace_ms
        if now_ms > deadline_ms:
            await _skip_pending(pending, "Next-candle fire window expired", fire_candle_ms=fire_candle_ms)
            return False
        _push_pattern_neon(
            pair=pair,
            candle_time_ms=fire_candle_ms,
            stage="confirming",
            side=pending.get("side"),
            action=(pending.get("detect") or {}).get("action"),
            pattern=(pending.get("detect") or {}).get("pattern"),
            reason="Next candle already open → firing",
        )
        return await _execute_queued_fire(pending)
    return False



async def auto_buy_loop():
    """Multi-pair scanner powered by brain.py (all timeframes unified)."""
    print("[AUTO BUY LOOP] brain.py unified engine online.")
    async with httpx.AsyncClient(timeout=8.0) as client:
        while True:
            try:
                timeframe_key = SECONDS_TO_TIMEFRAME_KEY.get(agent.timeframe_seconds, "1m")
                poll = 0.5 if timeframe_key in ("1m", "30s") else 1.0
                if agent.is_active and not agent.emergency_triggered:
                    pairs = list(agent.get_scan_pairs())
                    # Pending fires first so multi-pair/AI lag cannot expire the window.
                    pending_first = [p for p in pairs if p in PENDING_ENTRY_SIGNALS]
                    rest = [p for p in pairs if p not in PENDING_ENTRY_SIGNALS]
                    for pair in pending_first + rest:
                        try:
                            await scan_and_maybe_fire_pair(client, pair, timeframe_key)
                        except Exception as exc:
                            print(f"[SCAN] error {pair}: {exc}")
                await asyncio.sleep(poll)
            except Exception as exc:
                print(f"[AUTO BUY LOOP] error: {exc}")
                await asyncio.sleep(2.0)


async def market_simulator():
    """Synthetic fallback price walk when Bybit feed is missing/stale."""
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
            await agent.process_tick(new_price, volume_increment, pair=agent.active_pair)
        await asyncio.sleep(0.5)


async def bybit_price_feed():
    """Poll Bybit lastPrice for chart pair + watchlist + every open-trade pair.

    Open-trade pairs are fetched first (in parallel) so path-SL cannot lag behind
    a long sequential scan of the watchlist. Ticks are applied sequentially to
    avoid concurrent mutation of open trades.
    """
    global _last_real_feed_update
    print(f"[MARKET FEED] Background task starting (Bybit linear multi-pair poll, ~{_AUTO_BUY_TICKER_POLL}s).")

    async with httpx.AsyncClient(timeout=6.0) as client:
        while True:
            open_pairs = {p for p in agent.open_trade_pairs() if p}
            priority: list[str] = sorted(open_pairs)
            if agent.active_pair and agent.active_pair not in open_pairs:
                priority.insert(0, agent.active_pair)

            scan_rest = sorted(set(agent.get_scan_pairs()) - set(priority))
            any_ok = False

            async def _fetch_one(pair_label: str) -> tuple[str, float | None]:
                bybit_symbol = get_bybit_symbol(pair_label)
                if bybit_symbol is None:
                    return pair_label, None
                try:
                    price = await _fetch_bybit_linear_ticker_price(client, bybit_symbol)
                    if price is None:
                        print(f"[MARKET FEED] Invalid ticker for {bybit_symbol} ({pair_label})")
                    return pair_label, price
                except Exception as exc:
                    print(f"[MARKET FEED] Ticker poll error for {bybit_symbol}: {exc}")
                    return pair_label, None

            # 1) Open trades + chart pair — fetch parallel, apply ticks one-by-one
            if priority:
                fetched = await asyncio.gather(*[_fetch_one(p) for p in priority])
                for pair_label, price in fetched:
                    if price is not None:
                        await agent.process_tick(price, 0.0, pair=pair_label)
                        any_ok = True

            # 2) Remaining scan pairs — sequential (slower is OK; no open risk)
            for pair_label in scan_rest:
                _pair, price = await _fetch_one(pair_label)
                if price is not None:
                    await agent.process_tick(price, 0.0, pair=pair_label)
                    any_ok = True

            if not priority and not scan_rest:
                await asyncio.sleep(2)
                continue

            if any_ok:
                _last_real_feed_update = time.time()

            await asyncio.sleep(_AUTO_BUY_TICKER_POLL)


async def bybit_balance_refresher():
    """Keep bybit_api.last_known_balance fresh while LIVE_TRADING."""
    while True:
        if bybit_api.mode == "LIVE_TRADING" and bybit_api.connected:
            await bybit_api.fetch_real_balance()
        await asyncio.sleep(3)


KEEPALIVE_INTERVAL_SECONDS = 13 * 60


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
        print("[KEEPALIVE] RENDER_EXTERNAL_URL not set (local/VPS) — keepalive disabled.")
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


async def auto_exit_watchdog():
    """Re-check path-SL / path-TP even if a ticker tick was skipped or failed."""
    print("[AUTO-EXIT] Watchdog online (path SL + path TP 0.5%/0.7%).")
    while True:
        try:
            if AUTO_TRADE_AUTO_EXIT_ENABLED and agent.trades:
                n = agent._run_auto_exits()
                if n:
                    agent._sync_agent_trailing_lock_state()
        except Exception as exc:
            print(f"[AUTO-EXIT] Watchdog error: {exc}")
        await asyncio.sleep(0.35)


@app.on_event("startup")
async def start_background_tasks():
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
    try:
        db_status = trade_db.init_db()
        if db_status.get("ok"):
            synced = trade_db.max_bot_trade_id()
            if synced > agent.trade_seq:
                agent.trade_seq = synced
                print(f"[MYSQL] trade_seq synced to {agent.trade_seq}")
        system_log.push("ai", f"MySQL: {db_status.get('message')}", db_status)
    except Exception as exc:
        print(f"[MYSQL] startup note: {exc}")
    asyncio.create_task(market_simulator())
    asyncio.create_task(bybit_price_feed())
    asyncio.create_task(bybit_balance_refresher())
    asyncio.create_task(self_ping_keepalive())
    asyncio.create_task(auto_buy_loop())
    asyncio.create_task(auto_exit_watchdog())
    asyncio.create_task(chart_24h_refresh_loop(BYBIT_SYMBOL_MAP))
    asyncio.create_task(session_schedule_loop())


async def session_schedule_loop():
    """Mon–Fri IST windows: auto start/stop AI when Session Momentum Engine is ON."""
    print(
        f"[SESSION SCHEDULE] Loop online (enabled={schedule_store.enabled}, "
        f"want_active={schedule_store.status_dict().get('want_active')})."
    )
    while True:
        try:
            st = schedule_store.status_dict()
            if schedule_store.enabled:
                if st["want_active"] and not agent.is_active:
                    labels = ", ".join(st["active_windows"]) or "session window"
                    agent.schedule_auto_start(labels)
                elif not st["want_active"] and agent.is_active:
                    agent.schedule_soft_stop("outside Mon–Fri IST session windows")
        except Exception as exc:
            print(f"[SESSION SCHEDULE] loop error: {exc}")
        await asyncio.sleep(20)


# ==========================================
# 2. REST API COMMAND "WIRES"
# ==========================================
# ── Fresh AI Engine control (frontend uses ONLY these) ──────────────────────
@app.post("/bot/start")
async def bot_start():
    """Fresh AI Engine START — arms brain.py scanner. No modal config chain."""
    # Main engine and Session Momentum Engine are mutually exclusive.
    if schedule_store.enabled:
        schedule_store.set_enabled(False)
        notifications.push(
            "Session Momentum Engine turned OFF — Main AI Engine is taking control.",
            "warning",
        )
    if agent.is_active:
        return {
            "status": "success",
            "is_active": True,
            "message": "AI Engine already running.",
            "session_schedule_enabled": False,
        }
    open_count = len(agent.trades)
    agent.clear_emergency_state()
    agent.daily_target_reached = False
    agent.begin_ai_season()
    agent.is_active = True
    tf_key = SECONDS_TO_TIMEFRAME_KEY.get(agent.timeframe_seconds, "1m")
    profile = entry_pattern_profile(tf_key)
    print(f"[AI ENGINE] START — {agent.active_pair} ({tf_key}) brain.py")
    system_log.push(
        "ai",
        f"AI Engine STARTED on {agent.active_pair} ({open_count} open preserved).",
        {"open_positions": open_count, "timeframe_seconds": agent.timeframe_seconds},
    )
    system_log.push_agent_chat(
        f"AI Engine ON — {profile['name']} · {agent.active_pair} · {tf_key}",
        status="active",
        details={"pair": agent.active_pair, "timeframe": tf_key, "entry_pattern": profile},
    )
    notifications.push(f"AI Engine STARTED on {agent.active_pair}.", "success")
    return {
        "status": "success",
        "is_active": True,
        "message": f"AI Engine started — {profile['name']} ({tf_key}).",
        "open_positions": open_count,
        "entry_pattern": profile["name"],
        "session_schedule_enabled": False,
    }


@app.post("/bot/stop")
async def bot_stop(payload: BotStopPayload | None = None):
    """AI Engine STOP.

    Body: ``{"mode": "hold"|"emergency"}``
      - hold: stop new entries; keep open trades (path SL / TP still auto-exit); portfolio keeps updating
      - emergency: close all positions and freeze/clear session book
    No open trades → clean halt (end season).
    """
    mode = str((payload.mode if payload else "hold") or "hold").strip().lower()
    if mode not in ("hold", "emergency"):
        mode = "hold"

    if not agent.is_active and not agent.trades and not agent.session_hold_mode:
        agent.is_active = False
        return {
            "status": "success",
            "is_active": False,
            "mode": mode,
            "message": "AI Engine already stopped.",
        }

    if mode == "emergency":
        print("[AI ENGINE] STOP — emergency exit (close all).")
        agent.manual_stop("AI Engine STOP — Emergency Exit from Frontend")
        return {
            "status": "success",
            "is_active": False,
            "mode": "emergency",
            "message": "AI Engine stopped. All positions closed.",
            "open_positions": 0,
        }

    if not agent.trades:
        print("[AI ENGINE] STOP — no open trades; halting scanner.")
        agent.is_active = False
        agent.session_hold_mode = False
        if agent.ai_season_start_capital is not None or agent.ai_season_id is not None:
            agent.end_ai_season(clear_live_table=True, reason="stop_empty")
        return {
            "status": "success",
            "is_active": False,
            "mode": "hold",
            "message": "AI Engine stopped.",
            "open_positions": 0,
        }

    print(f"[AI ENGINE] STOP — hold {len(agent.trades)} open trade(s).")
    agent.hold_stop("AI Engine STOP — Hold from Frontend")
    return {
        "status": "success",
        "is_active": False,
        "mode": "hold",
        "message": f"AI Engine stopped (Hold). {len(agent.trades)} trade(s) still managed.",
        "open_positions": len(agent.trades),
    }


@app.get("/bot/status")
async def bot_status():
    """Lightweight poll for AI Engine active flag."""
    return {
        "status": "success",
        "is_active": bool(agent.is_active),
        "open_positions": len(agent.trades),
        "pair": agent.active_pair,
    }


# Legacy aliases (old frontend / cached builds) — keep working but log as legacy
@app.post("/start-bot")
async def start_bot():
    return await bot_start()


@app.post("/emergency-exit")
async def emergency_exit():
    if agent.emergency_awaiting_decision:
        agent.confirm_emergency_exit()
        return {"status": "success", "is_active": False, "message": "Emergency exit confirmed."}
    return await bot_stop()

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
    """Switch chart focus to a Bybit linear pair (does not replace AI watchlist).

    Open positions on other pairs are preserved — only the chart focus moves.
    Auto-scan continues on all minimized watchlist coins.
    """
    global _last_real_feed_update
    live_price = await fetch_bybit_linear_price(payload.pair)
    seed_price = live_price if live_price is not None else _sanitize_market_price(payload.price)
    if seed_price is None:
        return {"status": "error", "message": f"Could not resolve a valid market price for {payload.pair}."}
    open_n = len(agent.trades)
    agent.set_active_pair(payload.pair, seed_price)
    _last_real_feed_update = time.time()
    source = "Bybit live" if live_price is not None else "fallback"
    note = f" ({open_n} open position(s) kept)" if open_n else ""
    return {
        "status": "success",
        "message": f"Chart focus set to {payload.pair} @ ${seed_price:,.4f} ({source}){note}.",
        "pair": agent.active_pair,
        "price": seed_price,
        "open_positions": open_n,
        "watchlist": agent.watchlist,
        "scan_pairs": agent.get_scan_pairs(),
    }

class SetWatchlistPayload(BaseModel):
    pairs: list[str] = []

@app.post("/set-watchlist")
async def set_watchlist(payload: SetWatchlistPayload):
    """Set AI multi-pair scan list (launcher minimized coins — all mapped pairs).

    When empty, the engine falls back to the active chart pair only.
    """
    cleaned = agent.set_watchlist(payload.pairs or [])
    # Seed marks for new watchlist pairs so sizing/exits have a price immediately.
    for pair in cleaned:
        if agent.mark_price_for(pair) is None:
            live = await fetch_bybit_linear_price(pair)
            if live is not None:
                agent.set_pair_mark(pair, live)
    return {
        "status": "success",
        "message": (
            f"AI watchlist set to {len(cleaned)} pair(s): {', '.join(cleaned) or '(none — chart pair only)'}."
        ),
        "watchlist": cleaned,
        "scan_pairs": agent.get_scan_pairs(),
    }

@app.get("/watchlist")
async def get_watchlist():
    return {
        "watchlist": list(agent.watchlist),
        "scan_pairs": agent.get_scan_pairs(),
        "active_pair": agent.active_pair,
    }

class SetTimeframePayload(BaseModel):
    seconds: int

@app.post("/set-timeframe")
async def set_timeframe(payload: SetTimeframePayload):
    """ RULE 2: Dynamic Timeframe Syncing - the frontend tells the backend exactly
    which candle interval to read volume/price data on. """
    agent.set_timeframe(payload.seconds)
    tf_key = SECONDS_TO_TIMEFRAME_KEY.get(agent.timeframe_seconds, "1m")
    profile = get_timeframe_profile(tf_key)
    return {
        "status": "success",
        "message": (
            f"Backend synced to {payload.seconds}s ({tf_key}) — "
            f"trade size {profile['capital_pct']:.0f}% capital, "
            f"win/lose {profile['win_rate']}/{profile['lose_rate']}."
        ),
        "seconds": agent.timeframe_seconds,
        "timeframe": tf_key,
        "profile": profile,
    }


@app.get("/timeframe-profiles")
async def timeframe_profiles():
    """UI reference: win/lose rates + capital % per chart TF."""
    return {
        "profiles": {k: get_timeframe_profile(k) for k in ("1m", "5m", "15m", "1h", "1D")},
        "active": get_timeframe_profile(SECONDS_TO_TIMEFRAME_KEY.get(agent.timeframe_seconds, "1m")),
    }

# ==========================================
# PAPER TRADING CAPITAL — fresh /paper/* API
# ==========================================
class PaperCapitalPayload(BaseModel):
    amount: float


@app.get("/paper/status")
async def paper_status():
    """Current paper capital + mode for the Paper Trading modal."""
    return {
        "status": "success",
        "mode": bybit_api.mode,
        "is_paper": bybit_api.mode == "PAPER_TRADING",
        "capital": round(float(agent.current_capital or 0), 2),
        "starting_capital": round(float(agent.starting_capital or 0), 2),
        "total_portfolio_value": round(float(agent.get_total_portfolio_value() or 0), 2),
    }


@app.post("/paper/set-capital")
async def paper_set_capital(payload: PaperCapitalPayload):
    """Set simulated paper capital. Forces PAPER mode if still on paper path."""
    if bybit_api.mode != "PAPER_TRADING":
        return {
            "status": "error",
            "message": "Cannot change simulated capital while LIVE trading is active.",
            "mode": bybit_api.mode,
        }
    amount = float(payload.amount or 0)
    if amount < 100:
        return {"status": "error", "message": "Minimum paper trading capital is $100."}

    agent.set_paper_capital(amount)
    notifications.push(f"Paper capital set to ${amount:,.2f}.", "success")
    system_log.push("ai", f"Paper capital reset to ${amount:,.2f}.", {"capital": amount})
    return {
        "status": "success",
        "message": f"Paper trading capital set to ${amount:,.2f}.",
        "capital": round(float(agent.current_capital), 2),
        "mode": bybit_api.mode,
        "is_paper": True,
    }


# Legacy alias (old frontend builds)
@app.post("/paper-trading/set-capital")
async def set_paper_capital_legacy(payload: PaperCapitalPayload):
    return await paper_set_capital(payload)

# ==========================================
# AI AGENT INSTRUCTIONS MODAL WIRING
# ==========================================
class AgentConfigPayload(BaseModel):
    stop_loss_pct: float = 5.0
    daily_profit_pct: float = 0.0
    max_concurrent_trades: int | None = None


def _half_up_round(value: float) -> int:
    """ Round half UP (0.5 -> next integer), matching the modal's strict-integer
    rule. Python's built-in round() uses banker's rounding (round(2.5) == 2),
    which would break the UI's "0.5 or more rounds up" contract. """
    return math.floor(value + 0.5)


@app.post("/agent/config")
async def set_agent_config(payload: AgentConfigPayload):
    """ Applied from the "AI Engine Instructions" pre-start modal.
    - stop_loss_pct = total capital risk % → max_concurrent_trades via
      round(stop_loss_pct * 2) (half-up) unless max_concurrent_trades is sent
      explicitly from the frontend (must match the modal display).
    - Same % is the auto Hold-stop threshold when session portfolio drop hits it.
    - daily_profit_pct -> optional daily profit target; 0 disables.
    """
    if payload.stop_loss_pct < 0.5 or payload.stop_loss_pct > 100:
        return {"status": "error", "message": "Total capital risk must be between 0.5% and 100%."}
    if payload.daily_profit_pct < 0 or payload.daily_profit_pct > 1000:
        return {"status": "error", "message": "Daily profit target must be between 0% and 1000%."}

    if payload.max_concurrent_trades is not None:
        if payload.max_concurrent_trades < 1 or payload.max_concurrent_trades > 500:
            return {"status": "error", "message": "Concurrent trades must be between 1 and 500."}
        max_trades = payload.max_concurrent_trades
    else:
        max_trades = max(1, _half_up_round(payload.stop_loss_pct * 2.0))

    agent.risk_level_pct = payload.stop_loss_pct
    agent.max_concurrent_trades = max_trades
    agent.daily_profit_target_pct = payload.daily_profit_pct
    agent.daily_target_reached = False
    print(f"[AGENT CONFIG] capital_risk={payload.stop_loss_pct}% | max_concurrent_trades="
          f"{agent.max_concurrent_trades} | daily_profit_target={agent.daily_profit_target_pct}%")
    system_log.push(
        "ai",
        f"Agent config applied: capital_risk={payload.stop_loss_pct}% | max_concurrent_trades={agent.max_concurrent_trades} | daily_profit={payload.daily_profit_pct}%",
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
    # Inverse of * 2 trade capacity (default capital risk 5% → 10 trades).
    risk_pct = agent.risk_level_pct or (
        round(agent.max_concurrent_trades / 2.0, 1) if agent.max_concurrent_trades else 5.0
    )
    return {
        "stop_loss_pct": risk_pct,
        "risk_pct": risk_pct,
        "daily_profit_pct": agent.daily_profit_target_pct,
        "max_concurrent_trades": agent.max_concurrent_trades,
        "entry_pattern": ENTRY_PATTERN_NAME,
        "entry_pattern_profile": entry_pattern_profile(
            SECONDS_TO_TIMEFRAME_KEY.get(agent.timeframe_seconds, "1m")
        ),
    }


@app.get("/entry-pattern")
async def get_entry_pattern():
    """Active entry profile for the current chart timeframe."""
    tf_key = SECONDS_TO_TIMEFRAME_KEY.get(agent.timeframe_seconds, "1m")
    return {"status": "success", "timeframe": tf_key, **entry_pattern_profile(tf_key)}


# ==========================================
# INTEGRATION SETTINGS: Bybit & AI API Wiring
# ==========================================
@app.get("/settings/status")
async def get_settings_status():
    """ Returns only non-secret configuration state. Keys/secrets are never returned. """
    return settings_store.status_dict()


class SessionSchedulePayload(BaseModel):
    enabled: bool


@app.get("/session-engine/status")
@app.get("/settings/session-schedule")
async def get_session_schedule():
    st = schedule_store.status_dict()
    return {
        "status": "success",
        **st,
        "main_engine_active": bool(agent.is_active),
        "message": (
            "Session Momentum Engine is ON — main AI Engine stays off outside schedule control."
            if st.get("enabled")
            else "Session Momentum Engine is OFF."
        ),
    }


@app.post("/session-engine/start")
async def session_engine_start():
    """Start Session Momentum Engine. Turns Main AI Engine OFF (mutually exclusive)."""
    if agent.is_active:
        agent.manual_stop("Session Momentum Engine START — Main AI Engine released")
    schedule_store.set_enabled(True)
    st = schedule_store.status_dict()
    # If already inside a window, wake immediately
    if st.get("want_active") and not agent.is_active:
        labels = ", ".join(st.get("active_windows") or []) or "session window"
        agent.schedule_auto_start(labels)
    notifications.push(
        "Session Momentum Engine STARTED — trades only during high-momentum market windows (IST).",
        "success",
    )
    system_log.push(
        "ai",
        "Session Momentum Engine STARTED (Main AI Engine OFF).",
        st,
    )
    return {
        "status": "success",
        "message": "Session Momentum Engine started. Main AI Engine is OFF.",
        "schedule": schedule_store.status_dict(),
        "main_engine_active": bool(agent.is_active),
    }


@app.post("/session-engine/stop")
async def session_engine_stop():
    """Stop Session Momentum Engine. Soft-pauses automation if it was schedule-driven."""
    was_enabled = schedule_store.enabled
    schedule_store.set_enabled(False)
    if was_enabled and agent.is_active:
        agent.schedule_soft_stop("Session Momentum Engine STOP from Frontend")
    notifications.push("Session Momentum Engine STOPPED.", "warning")
    system_log.push("ai", "Session Momentum Engine STOPPED.", schedule_store.status_dict())
    return {
        "status": "success",
        "message": "Session Momentum Engine stopped.",
        "schedule": schedule_store.status_dict(),
        "main_engine_active": bool(agent.is_active),
    }


@app.post("/settings/session-schedule")
async def set_session_schedule(payload: SessionSchedulePayload):
    """Legacy toggle — prefer /session-engine/start|stop."""
    if payload.enabled:
        return await session_engine_start()
    return await session_engine_stop()

@app.get("/trades/statement")
async def trades_statement(
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None),
    pair: str | None = Query(None),
    season_id: int | None = Query(None),
):
    """Full trading statement from MySQL. Optional season_id filters one AI season."""
    data = await asyncio.to_thread(
        trade_db.fetch_statement,
        limit=limit,
        offset=offset,
        status=status,
        pair=pair,
        season_id=season_id,
    )
    return data


@app.get("/trades/seasons")
async def trades_seasons(limit: int = Query(50, ge=1, le=200)):
    """Season-wise AI automation summaries (MySQL)."""
    return await asyncio.to_thread(trade_db.fetch_seasons, limit=limit)


@app.get("/settings/mysql-status")
async def mysql_status():
    return trade_db.status_dict()


@app.get("/agent/whale/status")
async def agent_whale_status():
    """Whale auto-entry disabled (strategy wiped)."""
    return {
        "pair": "BTC/USDT",
        "enabled": False,
        "active_pair_is_btc": is_btc_pair(agent.active_pair),
        "note": "Strategy wiped — whale queue disabled",
        "last_fetch": None,
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


@app.get("/chart/tf-move")
async def get_chart_tf_move(
    pair: str = Query(..., description="e.g. BTC/USDT"),
    timeframe: str = Query("1M", description="Chart TF: 1M, 5M, 15M, 1H, 1D"),
):
    """Avg signed % move per candle over the TF-specific lookback window."""
    bybit_symbol = get_bybit_symbol(pair)
    if not bybit_symbol:
        return {
            "pair": pair,
            "timeframe": timeframe,
            "window_label": None,
            "avg_pct": None,
            "total_pct": None,
            "candle_count": 0,
            "last_price": None,
        }
    try:
        return await fetch_tf_move(pair, bybit_symbol, timeframe)
    except Exception as exc:
        print(f"[CHART TF-MOVE] Failed for {pair} {timeframe}: {exc}")
        return {
            "pair": pair,
            "timeframe": timeframe,
            "window_label": None,
            "avg_pct": None,
            "total_pct": None,
            "candle_count": 0,
            "last_price": None,
            "error": str(exc),
        }

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

            # Portfolio session counters: live during AI season / Hold-stop; frozen after Emergency.
            season_live = agent.ai_season_start_capital is not None and not agent.session_stats_frozen
            if season_live:
                session_open = agent._session_open_trades()
                trade_notional = sum(float(t.get("position_size") or 0) for t in session_open)
                open_positions = len(session_open)
                fee_book = agent.get_session_gross_and_fees_usd()
                broker_fee = float(fee_book["broker_fee_usd"])
                # Portfolio shows GROSS profit and fees separately (never net = profit − fees).
                daily_gross = float(fee_book["gross_usd"])
                daily_profit = daily_gross
                baseline = float(agent.ai_season_start_capital or agent.starting_capital or 0)
                daily_profit_pct = (daily_profit / baseline) * 100 if baseline else 0
                ai_season_profit = daily_gross
                ai_season_profit_pct = daily_profit_pct
                ai_season_profit_net = daily_gross - broker_fee
                ai_season_profit_net_pct = (ai_season_profit_net / baseline) * 100 if baseline else 0
                exited_booked_usd = float(fee_book.get("closed_gross_usd") or 0)
            else:
                snap = agent.session_stats_snapshot
                trade_notional = float(snap.get("trade_notional") or 0)
                open_positions = int(snap.get("open_positions") or 0)
                broker_fee = float(snap.get("daily_broker_fee") or 0)
                daily_profit = float(snap.get("daily_profit") or 0)
                daily_profit_pct = float(snap.get("daily_profit_pct") or 0)
                ai_season_profit = float(snap.get("ai_season_profit") or 0)
                ai_season_profit_pct = float(snap.get("ai_season_profit_pct") or 0)
                ai_season_profit_net = float(
                    snap.get("ai_season_profit_net")
                    if snap.get("ai_season_profit_net") is not None
                    else (ai_season_profit - broker_fee)
                )
                ai_season_profit_net_pct = float(
                    snap.get("ai_season_profit_net_pct")
                    if snap.get("ai_season_profit_net_pct") is not None
                    else (
                        (ai_season_profit_net / float(agent.starting_capital or 0)) * 100
                        if agent.starting_capital
                        else 0
                    )
                )
                daily_gross = daily_profit
                exited_booked_usd = float(snap.get("exited_booked_usd") or 0)

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

            season_active = bool(season_live and agent.is_active)

            baseline = agent.get_session_baseline()
            portfolio_drop = ((baseline - total_value) / baseline) * 100 if baseline else 0

            # Total capital risk % (from AI Engine Instructions): when session portfolio
            # mark-to-market drop hits the limit → Hold-stop (no new fires; open trades
            # keep path TP/SL until they exit on their own).
            if (
                agent.is_active
                and float(agent.risk_level_pct or 0) > 0
                and not agent.emergency_triggered
                and not agent.session_hold_mode
                and portfolio_drop >= float(agent.risk_level_pct)
            ):
                agent.hold_stop(
                    f"Total capital risk {agent.risk_level_pct:g}% hit "
                    f"(portfolio −{portfolio_drop:.2f}%) — Hold stop"
                )

            tf_key = SECONDS_TO_TIMEFRAME_KEY.get(agent.timeframe_seconds, "1m")
            scan = system_log.last_taapi_scan
            last_scan = scan if scan and scan.get("pair") == agent.active_pair else None
            # Stub overlay only — wrong kwargs used to crash /ws/portfolio every tick.
            try:
                blue_box_overlay = build_blue_box_chart_overlay([])
            except Exception:
                blue_box_overlay = []

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
                "daily_broker_fee": round(broker_fee, 4),
                "daily_gross_profit": round(daily_gross, 2),
                "exited_booked_usd": round(exited_booked_usd, 2),
                "ai_season_profit": round(ai_season_profit, 2),
                "ai_season_profit_pct": round(ai_season_profit_pct, 2),
                "ai_season_profit_net": round(ai_season_profit_net, 2),
                "ai_season_profit_net_pct": round(ai_season_profit_net_pct, 2),
                "ai_season_active": season_active,
                "session_stats_frozen": bool(agent.session_stats_frozen),
                "session_hold_mode": bool(agent.session_hold_mode),
                "portfolio_drop_pct": round(portfolio_drop, 2),
                "is_active": agent.is_active,
                "timeframe_seconds": agent.timeframe_seconds,
                "timeframe_profile": get_timeframe_profile(
                    SECONDS_TO_TIMEFRAME_KEY.get(agent.timeframe_seconds, "1m")
                ),
                "session_schedule": schedule_store.status_dict(),
                                "emergency": False,
                "risk_level_pct": agent.risk_level_pct,
                "max_concurrent_trades": agent.max_concurrent_trades,
                "profit_floor_pct": agent.get_profit_floor_pct(),
                "profit_floor_mode": "fixed_tp_gross",
                "trading_execution": (
                    "paper_simulation" if bybit_api.mode == "PAPER_TRADING" else "bybit_testnet"
                ),
                "trades": open_positions,
                "agent_chat": system_log.agent_chat[-8:],
                "blue_box_overlay": blue_box_overlay,
                "watchlist": list(agent.watchlist),
                "scan_pairs": agent.get_scan_pairs(),
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
                "pattern_neon": get_pattern_neon_snapshot(agent.active_pair),
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