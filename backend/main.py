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
from engine_runtime import (
    save_runtime,
    restore_runtime,
    FEED_STALE_SECONDS,
)
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
from settings_persist import (
    load_credentials as _load_persisted_creds,
    save_credentials as _save_persisted_creds,
    clear_credentials as _clear_persisted_creds,
    set_live_trading as _persist_live_trading,
)
from chart_24h import chart_24h_refresh_loop, chart_24h_store
from chart_tf_move import fetch_tf_move
from momentum_watchlist import (
    MOMENTUM_REFRESH_EVERY_SECONDS,
    build_momentum_watchlist,
)
import bybit_instruments
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

from timeframe_profiles import capital_pct_fraction, get_timeframe_profile, is_scalp_tf

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
    """Credential store for Bybit & AI.

    Secrets persist on disk under backend/data/api_credentials.json (Docker volume)
    until the user explicitly Resets. Never logged or echoed to the frontend.
    Z.ai defaults still load from ZAI_API_KEY env on every start.
    """
    def __init__(self):
        self.bybit_api_key = ""
        self.bybit_api_secret = ""
        self.bybit_environment = "mainnet"
        self.live_trading_preferred = False
        self.ai_provider = "z-ai"
        self.ai_api_key = ""
        self.ai_model = "glm-4.5-flash"
        self.ai_base_url = "https://api.z.ai/api/paas/v4"
        self._load_from_env()
        self._load_from_disk()

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

    def _load_from_disk(self):
        """Restore UI-saved keys (survive restart). Disk wins over empty; env can seed first."""
        data = _load_persisted_creds()
        if data.get("bybit_api_key"):
            self.bybit_api_key = data["bybit_api_key"]
        if data.get("bybit_api_secret"):
            self.bybit_api_secret = data["bybit_api_secret"]
        if data.get("bybit_environment") in ("mainnet", "testnet"):
            self.bybit_environment = data["bybit_environment"]
        self.live_trading_preferred = bool(data.get("live_trading"))
        if data.get("ai_api_key"):
            self.ai_api_key = data["ai_api_key"]
        if data.get("ai_provider"):
            self.ai_provider = data["ai_provider"]
        if data.get("ai_model"):
            self.ai_model = data["ai_model"]
        if data.get("ai_base_url"):
            self.ai_base_url = data["ai_base_url"].rstrip("/")
        if self.is_bybit_configured():
            print(
                f"[SETTINGS] Bybit keys restored from disk "
                f"(env={self.bybit_environment}, live_pref={self.live_trading_preferred})."
            )

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

        _save_persisted_creds({
            "bybit_api_key": self.bybit_api_key,
            "bybit_api_secret": self.bybit_api_secret,
            "bybit_environment": self.bybit_environment,
            "live_trading": self.live_trading_preferred,
            "ai_provider": self.ai_provider,
            "ai_api_key": self.ai_api_key,
            "ai_model": self.ai_model,
            "ai_base_url": self.ai_base_url,
        })

    def reset(self):
        """User-initiated wipe — clears disk + memory (then re-applies env defaults)."""
        _clear_persisted_creds()
        self.__init__()

    def is_bybit_configured(self):
        return bool(self.bybit_api_key and self.bybit_api_secret)

    def is_ai_configured(self):
        return self.ai_provider != "none" and bool(self.ai_api_key)

    @staticmethod
    def _mask_key(value: str, *, show_tail: int = 4) -> str:
        """Display-only mask — never return the full secret."""
        raw = (value or "").strip()
        if not raw:
            return ""
        if show_tail <= 0 or len(raw) <= show_tail:
            return "•" * max(8, len(raw))
        return ("•" * max(8, len(raw) - show_tail)) + raw[-show_tail:]

    @staticmethod
    def _mask_secret(value: str) -> str:
        raw = (value or "").strip()
        if not raw:
            return ""
        # No tail for secrets — just a filled-looking static mask.
        return "•" * max(12, min(24, len(raw)))

    def status_dict(self):
        # Raw secrets never leave the server; masked previews keep the form looking filled.
        return {
            "bybit_configured": self.is_bybit_configured(),
            "bybit_environment": self.bybit_environment,
            "bybit_persisted": self.is_bybit_configured(),
            "bybit_api_key_masked": self._mask_key(self.bybit_api_key) if self.bybit_api_key else "",
            "bybit_api_secret_masked": self._mask_secret(self.bybit_api_secret) if self.bybit_api_secret else "",
            "live_trading_preferred": bool(self.live_trading_preferred),
            "ai_provider": self.ai_provider,
            "ai_model": self.ai_model,
            "ai_base_url": self.ai_base_url,
            "ai_configured": self.is_ai_configured(),
            "ai_api_key_masked": self._mask_key(self.ai_api_key) if self.ai_api_key else "",
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
    """ Asks the configured AI provider to confirm an existing BUY/SELL setup.
    Returns:
      True  - AI says YES
      False - AI says NO
      None  - no AI configured / unreachable — fail OPEN (do not block the trade). """
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
    side = "LONG" if action == "BUY" else "SHORT" if action == "SELL" else action
    pattern = str(context.get("pattern") or context.get("reason") or "setup")
    if len(pattern) > 48:
        pattern = pattern[:48].rstrip()
    trap_score = context.get("trap_score")
    if trap_score is None:
        trap_score = context.get("confidence")
    score_txt = "—" if trap_score is None else str(trap_score)
    tf_l = timeframe.strip().lower()
    if is_scalp_tf(tf_l):
        thr = 85
    else:
        thr = 65

    prompt = (
        f"PATTERN DETECTED → confirm {side} {pattern} / trap score {score_txt}. "
        f"Pair {pair} {timeframe}. "
        f"Analyze LONG/SHORT, trap/inverse/fake-breakout per policy. "
        f"Reply YES only if confidence ≥ {thr}% (overall ≥75; 5m traps ≥80; other traps ≥90); else NO. "
        f"One word only: YES or NO."
    )

    messages = [
        {
            "role": "system",
            "content": (
                "You confirm trading setups only after policy analysis "
                "(structure, long/short, trap/inverse). "
                "Never invent BUY/SELL. Reply YES or NO only."
            ),
        },
        {"role": "user", "content": prompt},
    ]

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": 4,
                    "temperature": 0,
                },
            )
        if resp.status_code != 200:
            print(f"[AI AGENT] Provider '{provider}' returned HTTP {resp.status_code} - failing open (proceeding with trade).")
            agent.note_ai_result(False)
            return None
        data = resp.json()
        reply = data["choices"][0]["message"]["content"].strip().upper()
        agent.note_ai_result(True)
        token = reply.replace(".", " ").replace(",", " ").split()[0] if reply else ""
        if token.startswith("NO"):
            print(f"[AI AGENT] Provider '{provider}' confirmation reply: '{reply}' -> REJECTED")
            return False
        if token.startswith("YES"):
            print(f"[AI AGENT] Provider '{provider}' confirmation reply: '{reply}' -> PROCEED")
            return True
        print(f"[AI AGENT] Provider '{provider}' unclear reply '{reply}' - failing open.")
        return None
    except Exception as exc:
        print(f"[AI AGENT] Provider '{provider}' request failed ({exc}) - failing open (proceeding with trade).")
        agent.note_ai_result(False)
        return None

# ==========================================
# PILLAR 4 & 5: API & BYBIT EXECUTION GROUND
# ==========================================
# Bybit linear taker fee (percent points) + India GST on the fee line.
# Trade history: Trading Fee 0.055% + GST 18% of fee → all-in ≈ 0.0649% of fill value.
BYBIT_TAKER_FEE_PCT_DEFAULT = 0.055
BYBIT_FEE_GST_MULT = float(os.environ.get("BYBIT_FEE_GST_MULT", "1.18"))


class BybitAPIWrapper:
    """ API Data Cable & Execution Ground (Pillar 4 & 5) """
    def __init__(self):
        # DEFAULT: PAPER TRADING (As per Automation.txt)
        self.mode = "PAPER_TRADING"
        self.connected = False
        # Base taker from Bybit fee-rate API (0.055 = 0.055%). P&L uses all-in via GST.
        self.taker_fee_base_pct = BYBIT_TAKER_FEE_PCT_DEFAULT
        # Compat alias — always all-in (base × GST). Prefer get_taker_fee_pct().
        self.taker_fee_pct = round(self.taker_fee_base_pct * BYBIT_FEE_GST_MULT, 6)

        # Real Bybit account equity (USD), refreshed in the background while LIVE_TRADING.
        # None until the first successful fetch - callers fall back to paper capital until then.
        self.last_known_balance = None
        self.last_known_available = None  # UNIFIED totalAvailableBalance when present
        self.last_error = None
        self._was_failing = False
        self._last_fee_sync_ts = 0.0

    def connect_real_api(self):
        self.mode = "LIVE_TRADING"
        self.connected = True
        self.last_known_balance = None
        settings_store.live_trading_preferred = True
        _persist_live_trading(True)
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
        settings_store.live_trading_preferred = False
        # Only update disk if keys still exist (reset() already wiped the file).
        if settings_store.is_bybit_configured():
            _persist_live_trading(False)

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
        """ RULE 5 wiring: pull the REAL total equity from Bybit's v5 API.

        Checks UNIFIED (derivatives) + FUNDING (funding wallet) so the user sees
        ALL their funds. SPOT is skipped — Bybit rejects it with
        "accountType only support UNIFIED" when the key is UTA-only.
        Returns the equity as a float, or None on any failure.
        """
        if not settings_store.is_bybit_configured():
            self.last_error = "No Bybit API Key/Secret configured."
            return None

        try:
            total_equity = 0.0
            found_any = False

            # --- UNIFIED: /v5/account/wallet-balance (has totalEquity) ---
            query_string = "accountType=UNIFIED"
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
                self.last_error = data.get("retMsg", "Bybit wallet-balance error")
                self._note_failure()
                return None

            account_list = data.get("result", {}).get("list", [])
            if account_list:
                acct0 = account_list[0]
                equity = float(acct0.get("totalEquity", 0))
                total_equity += equity
                found_any = True
                try:
                    avail = acct0.get("totalAvailableBalance")
                    if avail is not None and str(avail) != "":
                        self.last_known_available = float(avail)
                except (TypeError, ValueError):
                    pass

            # --- FUNDING: /v5/asset/wallet-balance (different endpoint, per-coin) ---
            try:
                fund_query = "accountType=FUND"
                fund_headers = self._auth_headers(fund_query)
                async with httpx.AsyncClient(timeout=8.0) as client:
                    fund_resp = await client.get(
                        f"{self._base_url()}/v5/asset/wallet-balance?{fund_query}",
                        headers=fund_headers,
                    )
                if fund_resp.status_code == 200:
                    fund_data = fund_resp.json()
                    if fund_data.get("retCode") == 0:
                        fund_accounts = fund_data.get("result", {}).get("list", [])
                        for acct in fund_accounts:
                            for coin in acct.get("walletBalance", []):
                                if (coin.get("coin") or "").upper() in ("USDT", "USD"):
                                    total_equity += float(coin.get("walletBalance", 0))
                                    found_any = True
            except Exception as exc:
                print(f"[BYBIT] Funding account fetch note: {exc}")

            if not found_any:
                self.last_error = "Bybit returned no account data for this key."
                self._note_failure()
                return None

            self.last_known_balance = total_equity
            self.last_error = None
            if self.mode == "LIVE_TRADING":
                agent.current_capital = total_equity
            # Sync live taker fee tier ( thrrottle ~5 min ) so broker-fee estimates match account.
            try:
                await self._sync_taker_fee_rate()
            except Exception as fee_exc:
                print(f"[BYBIT] fee-rate sync note: {fee_exc}")
            if self._was_failing:
                notifications.push("Bybit connection restored - live balance is syncing again.", "success")
            self._was_failing = False
            return total_equity

        except Exception as exc:
            self.last_error = f"Bybit request failed: {exc}"
            self._note_failure()
            return None

    async def _sync_taker_fee_rate(self, symbol: str = "BTCUSDT") -> None:
        """Pull account linear taker fee from Bybit; store BASE %, P&L uses base×GST."""
        now = time.time()
        if now - float(self._last_fee_sync_ts or 0) < 300:
            return
        if not settings_store.is_bybit_configured():
            return
        query_string = f"category=linear&symbol={symbol}"
        headers = self._auth_headers(query_string)
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                f"{self._base_url()}/v5/account/fee-rate?{query_string}",
                headers=headers,
            )
        if resp.status_code != 200:
            return
        data = resp.json()
        if data.get("retCode") != 0:
            return
        rows = (data.get("result") or {}).get("list") or []
        if not rows:
            return
        raw = rows[0].get("takerFeeRate")
        if raw is None or str(raw) == "":
            return
        # Bybit returns fraction e.g. "0.00055" → store as 0.055 (% points) BASE only
        pct = float(raw) * 100.0
        if pct <= 0 or pct > 1.0:
            return
        prev_base = float(self.taker_fee_base_pct)
        self.taker_fee_base_pct = round(pct, 6)
        self.taker_fee_pct = self.get_taker_fee_pct()
        self._last_fee_sync_ts = now
        if abs(prev_base - self.taker_fee_base_pct) > 1e-6:
            print(
                f"[BYBIT] Live taker fee synced: base {self.taker_fee_base_pct:g}% "
                f"+ GST×{BYBIT_FEE_GST_MULT:g} → all-in {self.taker_fee_pct:g}% "
                f"(was base {prev_base:g}%) via {symbol}"
            )

    def _note_failure(self):
        if not self._was_failing:
            print(f"[BYBIT] Balance fetch failing: {self.last_error}")
            notifications.push(f"Bybit balance unreachable ({self.last_error}). Showing last known value.", "error")
        self._was_failing = True

    def get_taker_fee_base_pct(self) -> float:
        """Bybit taker fee % before GST (e.g. 0.055)."""
        return float(self.taker_fee_base_pct or BYBIT_TAKER_FEE_PCT_DEFAULT)

    def get_taker_fee_pct(self) -> float:
        """All-in taker fee % for True Net Profit: base × GST (default 0.055 × 1.18 ≈ 0.0649)."""
        base = self.get_taker_fee_base_pct()
        all_in = round(base * float(BYBIT_FEE_GST_MULT), 6)
        self.taker_fee_pct = all_in
        return all_in

    def all_in_fee_usd(self, notional: float) -> float:
        """Fee in USDT for a fill notional at all-in (base+GST) rate."""
        return round(float(notional or 0) * (self.get_taker_fee_pct() / 100.0), 6)

    def normalize_fee_usd(
        self,
        fee_usd: float | None,
        fee_pct: float | None = None,
        notional: float | None = None,
    ) -> float:
        """Upgrade pre-GST stored fees to all-in; never double-apply GST."""
        fee = float(fee_usd or 0)
        all_in = self.get_taker_fee_pct()
        base = self.get_taker_fee_base_pct()
        if fee <= 0 and notional is not None and float(notional) > 0:
            return self.all_in_fee_usd(notional)
        if fee <= 0:
            return 0.0
        if fee_pct is not None:
            sp = float(fee_pct)
            if sp >= all_in * 0.98:
                return fee
            if sp > 0 and sp <= base * 1.05:
                return round(fee * BYBIT_FEE_GST_MULT, 6)
        if notional is not None and float(notional) > 0:
            expected_base = float(notional) * (base / 100.0)
            expected_all = float(notional) * (all_in / 100.0)
            if expected_base > 1e-12 and abs(fee - expected_base) / expected_base < 0.20:
                return round(fee * BYBIT_FEE_GST_MULT, 6)
            if expected_all > 1e-12 and abs(fee - expected_all) / expected_all < 0.20:
                return fee
        return fee

    def fee_structure_dict(self) -> dict:
        base = self.get_taker_fee_base_pct()
        all_in = self.get_taker_fee_pct()
        return {
            "taker_fee_base_pct": base,
            "gst_mult": float(BYBIT_FEE_GST_MULT),
            "taker_fee_all_in_pct": all_in,
            "note": (
                f"Bybit taker {base:g}% + GST {(BYBIT_FEE_GST_MULT - 1) * 100:.0f}% "
                f"on fee → all-in {all_in:g}% of fill value per side"
            ),
        }

    def execute_market_buy(self, pair, reason):
        """ REAL market buy on Bybit linear perpetual (or paper print if not live). """
        if self.mode != "LIVE_TRADING":
            print(f"👉 [PAPER TRADING - VIRTUAL] Bybit API -> Market BUY {pair} -> {reason}")
            return True
        bybit_symbol = get_bybit_symbol(pair)
        if not bybit_symbol:
            print(f"🔥 [REAL LIVE] Cannot map {pair} to Bybit symbol — skipped.")
            return False
        executor = get_bybit_executor_agent()
        if executor is None:
            print("🔥 [REAL LIVE] Bybit executor not available — skipped.")
            return False
        # qty will be set by caller via trade record; here we just fire the order signal
        print(f"🔥 [REAL LIVE TRADING - ACTUAL] Bybit REST API -> MARKET BUY {pair} ({bybit_symbol}) -> {reason}")
        return True

    def execute_market_sell(self, pair, reason):
        """ REAL market sell on Bybit linear perpetual (or paper print if not live). """
        if self.mode != "LIVE_TRADING":
            print(f"👉 [PAPER TRADING - VIRTUAL] Bybit API -> Market SELL {pair} -> {reason}")
            return True
        bybit_symbol = get_bybit_symbol(pair)
        if not bybit_symbol:
            print(f"🔥 [REAL LIVE] Cannot map {pair} to Bybit symbol — skipped.")
            return False
        executor = get_bybit_executor_agent()
        if executor is None:
            print("🔥 [REAL LIVE] Bybit executor not available — skipped.")
            return False
        print(f"🔥 [REAL LIVE TRADING - ACTUAL] Bybit REST API -> MARKET SELL {pair} ({bybit_symbol}) -> {reason}")
        return True

    def execute_live_order(self, pair: str, side: str, qty: float, reason: str = "") -> tuple[bool, str | None]:
        """Fire a REAL market order on Bybit using the user's saved mainnet keys.

        Called by the agent when a trade actually opens/closes in LIVE_TRADING mode.
        Returns (ok, error_msg).
        """
        bybit_symbol = get_bybit_symbol(pair)
        if not bybit_symbol:
            return False, f"Cannot map {pair} to a Bybit linear symbol"
        executor = get_bybit_executor_agent()
        if executor is None:
            return False, "Bybit executor agent not built (no keys / init failed)"
        action = "BUY" if side.upper().startswith("LONG") or side.upper() == "BUY" else "SELL"
        signal_payload = {
            "action": action,
            "symbol": bybit_symbol,
            "entry": 0,
            "sl": 0,
            "tp": 0,
            "pattern": reason or "agent_live",
        }
        return executor.execute_trade(signal_payload, qty=qty)

    def execute_live_close(self, trade: dict, qty: float | None = None) -> tuple[bool, str | None]:
        """Fire a REAL reduce-only market close on Bybit for an open position."""
        executor = get_bybit_executor_agent()
        if executor is None:
            return False, "Bybit executor agent not built"
        # Ensure bybit_symbol is set on the trade record
        if not trade.get("bybit_symbol"):
            trade["bybit_symbol"] = get_bybit_symbol(trade.get("pair", ""))
        return executor.close_position(trade, qty=qty)

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
MAX_SAME_SIDE_AUTO_PER_PAIR = int(os.environ.get("MAX_SAME_SIDE_AUTO_PER_PAIR", "3"))
AUTO_TRADE_AUTO_EXIT_ENABLED = True  # Path lock/trail profit + protective SL (same engine)
INVERT_AUTO_TRADE_FIRE = False
# Profit book (gross %, LONG/SHORT symmetric) — default (5m+):
#   Arm @ +0.50%; lock = peak gross (continuous); trail 0.10% → floor peak−0.10
#   (e.g. peak +0.73% → exit floor +0.63%; peak +0.58% → floor +0.48%).
# 1m: hard TP @ +0.34% (no trail) — see PROFIT_HARD_PCT_1M below.
PROFIT_LOCK_PCT = float(os.environ.get("PROFIT_LOCK_PCT", "0.50"))
PROFIT_LOCK_STEP_PCT = float(os.environ.get("PROFIT_LOCK_STEP_PCT", "0.20"))  # unused — continuous trail
PROFIT_TRAIL_FIRST_GIVEBACK_PCT = float(os.environ.get("PROFIT_TRAIL_FIRST_GIVEBACK_PCT", "0.10"))
PROFIT_TRAIL_GIVEBACK_PCT = float(os.environ.get("PROFIT_TRAIL_GIVEBACK_PCT", "0.10"))
# Opposite-signal flip: only exit old auto trade when unrealized gross > this %;
# at or below → keep old open and skip the new opposite fire (no tiny flip-exits).
FLIP_EXIT_MIN_GROSS_PCT = float(os.environ.get("FLIP_EXIT_MIN_GROSS_PCT", "0.25"))
# Do not wipe a brand-new local open just because Bybit position API lags a few seconds.
RECONCILE_GRACE_SECONDS = float(os.environ.get("RECONCILE_GRACE_SECONDS", "30"))
# Protective stop-loss (gross %, LONG/SHORT symmetric) — default (5m+):
#   −0.50% → soft LOSS LOCK arms;
#   −0.50…−0.70% zone: 0.20% upward trail (sell line = best_recovery + 0.20);
#   −0.70% → hard exit (LOSS_BAND_EXIT);
#   recover to −0.20% or better → UNLOCK (no sell) → profit book @ +0.50%.
# 1m-only: fixed hard exits (no trail / unlock) — SL −0.20% · TP +0.34%.
LOSS_PROTECT_PCT = float(os.environ.get("LOSS_PROTECT_PCT", "0.50"))  # soft lock arm
LOSS_RECOVERY_RETRACE_PCT = float(os.environ.get("LOSS_RECOVERY_RETRACE_PCT", "0.20"))
LOSS_RECOVERY_RETRACE_CHOPPY_PCT = float(os.environ.get("LOSS_RECOVERY_RETRACE_CHOPPY_PCT", "0.20"))
LOSS_LOCK_CLEAR_PCT = float(os.environ.get("LOSS_LOCK_CLEAR_PCT", "0.20"))  # unlock → profit book
LOSS_BAND_PCT = float(os.environ.get("LOSS_BAND_PCT", "0.70"))  # hard floor / instant exit
# 1m-only fixed exits (no soft lock / trail / unlock)
LOSS_PROTECT_PCT_1M = float(os.environ.get("LOSS_PROTECT_PCT_1M", "0.20"))  # hard SL
LOSS_BAND_PCT_1M = float(os.environ.get("LOSS_BAND_PCT_1M", "0.20"))  # same = instant hard
LOSS_LOCK_CLEAR_PCT_1M = float(os.environ.get("LOSS_LOCK_CLEAR_PCT_1M", "0.20"))  # unused on 1m hard
PROFIT_HARD_PCT_1M = float(os.environ.get("PROFIT_HARD_PCT_1M", "0.34"))  # hard TP, no trail
PROFIT_LOCK_PCT_1M = float(os.environ.get("PROFIT_LOCK_PCT_1M", str(PROFIT_HARD_PCT_1M)))  # alias


def _brain_exit_prices_valid(entry: float, side: str, sl: float, tp: float) -> bool:
    """True when brain stop/target are on the correct side of entry for the side."""
    if entry <= 0 or sl <= 0 or tp <= 0:
        return False
    if side == "LONG":
        return sl < entry and tp > entry
    if side == "SHORT":
        return sl > entry and tp < entry
    return False

LOSS_EMERGENCY_PCT = LOSS_BAND_PCT
# Micro-cap alts — tighter loss band (default 1m 0.35% hard exit is too slow for these).
MICRO_CAP_PAIRS = frozenset({
    "BLESS/USDT",
    "SKR/USDT",
    "AKE/USDT",
    "USELESS/USDT",
})
MICRO_CAP_LOSS_ARM_PCT = float(os.environ.get("MICRO_CAP_LOSS_ARM_PCT", "0.25"))
MICRO_CAP_LOSS_BAND_PCT = float(os.environ.get("MICRO_CAP_LOSS_BAND_PCT", "0.35"))
MICRO_CAP_HARD_STOP_PCT = float(os.environ.get("MICRO_CAP_HARD_STOP_PCT", "0.25"))
# Small-coin loss multipliers (disabled).
# Kept for optional env re-enable without code change.
SMALL_COIN_MID_USD = float(os.environ.get("SMALL_COIN_MID_USD", "1.0"))
SMALL_COIN_MICRO_USD = float(os.environ.get("SMALL_COIN_MICRO_USD", "0.10"))
SMALL_COIN_MID_MULT = float(os.environ.get("SMALL_COIN_MID_MULT", "1.0"))
SMALL_COIN_MICRO_MULT = float(os.environ.get("SMALL_COIN_MICRO_MULT", "1.0"))
SMALL_COIN_ENTRY_GRACE_SEC = float(os.environ.get("SMALL_COIN_ENTRY_GRACE_SEC", "0"))
SMALL_COIN_WICK_TICKS = int(os.environ.get("SMALL_COIN_WICK_TICKS", "0"))
SMALL_COIN_WICK_HOLD_SEC = float(os.environ.get("SMALL_COIN_WICK_HOLD_SEC", "0"))
# Legacy aliases
PATH_TP_TIGHT_PCT = PROFIT_LOCK_PCT
PATH_TP_WIDE_PCT = PROFIT_LOCK_PCT + PROFIT_TRAIL_GIVEBACK_PCT
FIXED_EXIT_PROFIT_PCT = PROFIT_LOCK_PCT
PATH_SL_TIGHT_PCT = LOSS_PROTECT_PCT
PATH_SL_WIDE_PCT = LOSS_EMERGENCY_PCT
PATH_SL_ADVERSE_STEPS = 3
PATH_TP_FAVORABLE_STEPS = 3
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
        "1m": float(os.environ.get("HARD_STOP_1M", "0.20")),  # align with 1m hard SL
        "3m": float(os.environ.get("HARD_STOP_3M", "0.40")),
        "5m": float(os.environ.get("HARD_STOP_5M", "0.35")),  # same as 1m scalp
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
        self.starting_capital = 0.0
        self.current_capital = 0.0
        # Total capital risk % from modal -> max_concurrent_trades via round(risk_pct * 2).
        # Also: when session portfolio drop hits this %, auto Hold-stop (no new entries).
        self.risk_level_pct = 5.0
        self.max_concurrent_trades = MAX_CONCURRENT_TRADES_DEFAULT
        self.last_open_skip_reason: str | None = None
        self.last_close_error: str | None = None
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
        self.one_m_fee_hold = False  # 1m only: pause new entries when fees dominate
        self.one_m_fee_hold_at = 0.0  # when fee_hold was armed (for max-hold ceiling)
        # Momentum watchlist gate (MARKET avg% filter)
        self.momentum_gate_ready = False
        self.momentum_fire_pairs: list[str] = []
        self.momentum_scores: list[dict] = []
        self.momentum_threshold_pct = 0.0
        self.last_momentum_candle_ms = 0
        self.momentum_last_refresh_ms = 0
        self.momentum_scan_done = 0
        self.momentum_scan_total = 0
        self.momentum_scan_stage = ""
        # Connectivity freeze: engine stays ON, but new fires pause until feed/AI recover.
        self.connectivity_frozen = False
        self.freeze_reason: str | None = None
        self._ai_fail_streak = 0
        self._ai_skip_until = 0.0
        self._last_feed_ts = time.time()
        self._last_runtime_save = 0.0
        # trading_ready_at: 0 = ready now. boot_ui_* drives overlay only (scan-aware).
        self.trading_ready_at = 0.0
        self.boot_ui_until = 0.0
        self.boot_started_at = 0.0
        self.engine_armed_at = 0.0  # for 1m/5m hourly soft restart timer
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
        """Pairs the AI scans for patterns + fires on.

        While engine active: empty until momentum gate ready, then only MARKET-avg% passers.
        When inactive: watchlist / chart pair (UI sync only).
        """
        if bool(self.is_active):
            if not bool(getattr(self, "momentum_gate_ready", False)):
                return []
            return list(getattr(self, "momentum_fire_pairs", None) or [])
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
        """Replace scan watchlist (launcher minimized coins). All mapped Bybit pairs allowed.

        HARD INSTRUCTION: changing the watchlist never closes/exits open trades.
        Open-trade pairs are always re-pinned onto the list after rewrite.
        """
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
        # Pin open-trade pairs (may exceed MAX_WATCHLIST — never orphan opens).
        for p in sorted({t.get("pair") for t in self.trades if t.get("pair")}):
            label = (p or "").strip()
            if not label or label in seen:
                continue
            if get_bybit_symbol(label) is None:
                continue
            cleaned.append(label)
            seen.add(label)
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
        pair = (trade.get("pair") or "").strip().upper()
        if pair in MICRO_CAP_PAIRS:
            tf_stop = min(tf_stop, float(MICRO_CAP_HARD_STOP_PCT))
        return min(tf_stop, float(self.STRICT_EXIT_MAX_LOSS_PCT))

    def _is_micro_cap_pair(self, trade: dict) -> bool:
        return (trade.get("pair") or "").strip().upper() in MICRO_CAP_PAIRS

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

        entry_fee_pct = float(t.get("entry_fee_pct") or bybit_api.get_taker_fee_pct())
        all_in_pct = bybit_api.get_taker_fee_pct()
        # Upgrade legacy opens booked at base 0.055% (no GST) to all-in ~0.0649%.
        if entry_fee_pct < all_in_pct * 0.98:
            entry_fee_pct = all_in_pct
        entry_fee_usd = bybit_api.normalize_fee_usd(
            t.get("entry_fee_usd"),
            t.get("entry_fee_pct"),
            t.get("position_size"),
        )
        exit_fee_pct = all_in_pct * (mark / entry)
        if for_close:
            net_pct = gross_pct - entry_fee_pct - exit_fee_pct
        else:
            # Unrealized: do not mark exit fee — that was painting winners red.
            net_pct = gross_pct - entry_fee_pct

        gross_usd = t["position_size"] * (gross_pct / 100)
        exit_fee_usd = t["position_size"] * (exit_fee_pct / 100) if for_close else 0.0
        if for_close:
            net_usd = gross_usd - entry_fee_usd - exit_fee_usd
        else:
            net_usd = gross_usd - entry_fee_usd

        return {
            "gross_pct": gross_pct,
            "net_pct": net_pct,
            "gross_usd": gross_usd,
            "exit_fee_usd": exit_fee_usd,
            "net_usd": net_usd,
            "entry_fee_pct": entry_fee_pct,
            "exit_fee_pct": exit_fee_pct if for_close else 0.0,
            "entry_fee_usd": entry_fee_usd,
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
            "timeframe_key": trade.get("timeframe_key"),
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
        found = False
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
                found = True
                break
        if not found:
            # Restored / orphaned open with no history row — still book the exit for UI.
            self.trade_history.append({
                "id": trade["id"],
                "pair": trade.get("pair"),
                "side": trade.get("side"),
                "entry": trade.get("entry"),
                "current": round(float(exit_px), price_decimals_for_mark(float(exit_px))),
                "margin": trade.get("margin"),
                "position_size": trade.get("position_size"),
                "pnl": round(metrics["gross_pct"], 4),
                "gross_pnl_pct": round(metrics["gross_pct"], 4),
                "gross_pnl_usd": round(float(metrics.get("gross_usd") or 0), 2),
                "net_pnl_usd": round(metrics["net_usd"], 2),
                "entry_fee_usd": trade.get("entry_fee_usd") or 0,
                "exit_fee_usd": round(metrics["exit_fee_usd"], 4),
                "status": "sold",
                "closed_reason": reason,
                "closed_at": time.time(),
                "source": trade.get("source", "auto"),
                "protected": trade.get("source") == "manual",
                "opened_at": trade.get("opened_at"),
                "season_id": trade.get("season_id") or self.ai_season_id,
                "exchange": trade.get("exchange"),
            })
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
        sid = self.ai_season_id
        for t in self.trades:
            if sid is not None and t.get("season_id") != sid:
                continue
            m_open = self._trade_metrics(t, for_close=False)
            open_gross += float(m_open["gross_usd"])
            # Always all-in (base+GST) so Session Broker Fee matches Bybit Trade History.
            open_fees += float(
                m_open.get("entry_fee_usd")
                or bybit_api.all_in_fee_usd(t.get("position_size"))
            )

        closed_gross = 0.0
        closed_fees = 0.0
        closed_count = 0
        for row in self.trade_history:
            if row.get("status") != "sold":
                continue
            if sid is not None and row.get("season_id") != sid:
                continue
            closed_count += 1
            notional = float(row.get("position_size") or 0)
            entry_f = bybit_api.normalize_fee_usd(
                row.get("entry_fee_usd"),
                row.get("entry_fee_pct"),
                notional,
            )
            exit_f = bybit_api.normalize_fee_usd(
                row.get("exit_fee_usd"),
                row.get("exit_fee_pct"),
                notional,
            )
            if entry_f <= 0 and notional > 0:
                entry_f = bybit_api.all_in_fee_usd(notional)
            if exit_f <= 0 and notional > 0:
                # Closed without exit fee recorded — estimate one all-in fill.
                exit_f = bybit_api.all_in_fee_usd(notional)
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
            "closed_count": closed_count,
            "fee_structure": bybit_api.fee_structure_dict(),
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
        self.one_m_fee_hold = False
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
        """Free cash for the next auto slot. LIVE prefers Bybit availableBalance."""
        if bybit_api.mode == "LIVE_TRADING":
            avail = bybit_api.last_known_available
            if avail is not None and avail >= 0:
                return max(0.0, float(avail))
            base = self.get_trading_capital_base()
            return max(0.0, float(base)) if base is not None else 0.0
        return max(0.0, float(self.current_capital))

    def _rollback_failed_live_open(self, trade: dict, err: str | None = None) -> None:
        """Undo a local open that never filled on Bybit — no fake EXITED (BOOKED) row."""
        tid = int(trade.get("id") or 0)
        self.trades = [t for t in self.trades if int(t.get("id") or 0) != tid]
        self.trade_history = [r for r in self.trade_history if int(r.get("id") or 0) != tid]
        try:
            trade_db.delete_trade(tid)
        except Exception as exc:
            print(f"[MYSQL] delete after failed open skipped: {exc}")
        self.persist_runtime(force=True)
        print(
            f"[LIVE OPEN] Rolled back local #{tid} {trade.get('pair')} {trade.get('side')} "
            f"— Bybit order failed ({err or 'unknown'}); not booked as exit."
        )

    def reconcile_live_positions(self) -> int:
        """Drop local opens that no longer exist on Bybit (phantom / restart desync).

        Guards against fake exits:
          - grace window after open (Bybit position list can lag)
          - only book reconcile-exit for trades confirmed as ``bybit_linear``
          - unconfirmed locals (open never filled) are discarded silently after grace

        Returns number of local trades removed (booked or silent).
        """
        if bybit_api.mode != "LIVE_TRADING" or not settings_store.is_bybit_configured():
            return 0
        if not self.trades:
            return 0
        executor = get_bybit_executor_agent()
        if executor is None:
            return 0
        live_rows = executor.fetch_linear_open_positions()
        if live_rows is None:
            # API failure — do not wipe local book
            return 0

        live_keys = set()
        for row in live_rows:
            sym = (row.get("symbol") or "").upper()
            side = (row.get("side") or "").strip()
            if not sym or side not in ("Buy", "Sell"):
                continue
            live_keys.add((sym, "LONG" if side == "Buy" else "SHORT"))

        settled = 0
        still_open = []
        now = time.time()
        grace = max(0.0, float(RECONCILE_GRACE_SECONDS))
        for trade in list(self.trades):
            if trade.get("exchange") == "paper":
                still_open.append(trade)
                continue
            symbol = (trade.get("bybit_symbol") or get_bybit_symbol(trade.get("pair")) or "").upper()
            side = (trade.get("side") or "").upper()
            if not symbol or side not in ("LONG", "SHORT"):
                still_open.append(trade)
                continue
            if (symbol, side) in live_keys:
                still_open.append(trade)
                continue

            opened_at = float(trade.get("opened_at") or 0)
            age = (now - opened_at) if opened_at > 0 else grace + 1.0
            if age < grace:
                # Fresh open — Bybit may not list the position yet; keep showing as ACTIVE.
                still_open.append(trade)
                continue

            confirmed = trade.get("exchange") == "bybit_linear"
            if not confirmed:
                # Never got a Bybit fill ack — drop without fake booked PnL exit.
                tid = int(trade.get("id") or 0)
                self.trade_history = [
                    r for r in self.trade_history if int(r.get("id") or 0) != tid
                ]
                try:
                    trade_db.delete_trade(tid)
                except Exception:
                    pass
                settled += 1
                print(
                    f"[RECONCILE] Dropped unconfirmed local #{tid} {trade.get('pair')} "
                    f"{side} (no Bybit fill ack, not on exchange)"
                )
                continue

            # Confirmed fill but flat on exchange — drop silently (no fake EXITED / bad PnL).
            tid = int(trade.get("id") or 0)
            self.trade_history = [
                r for r in self.trade_history if int(r.get("id") or 0) != tid
            ]
            try:
                trade_db.delete_trade(tid)
            except Exception:
                pass
            settled += 1
            print(
                f"[RECONCILE] Dropped #{tid} {trade.get('pair')} {side} "
                f"(already flat on Bybit — no booked exit)"
            )
        if settled:
            self.trades = still_open
            self._sync_agent_trailing_lock_state()
            self.persist_runtime(force=True)
        else:
            self.trades = still_open
        return settled

    def get_trading_capital_base(self):
        """ Capital used for position sizing. LIVE -> Bybit equity; paper -> simulated ledger. """
        if bybit_api.mode == "LIVE_TRADING":
            # ALWAYS use Bybit balance in LIVE mode — never fall back to paper capital.
            bal = bybit_api.last_known_balance
            if bal is not None and bal > 0:
                return float(bal)
            return 0.0  # LIVE but balance not fetched yet → 0, not paper
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
        if bybit_api.mode == "LIVE_TRADING":
            # LIVE: always Bybit balance — never paper simulation
            return float(bybit_api.last_known_balance or 0)
        reserved = sum(
            float(t.get("capital_reserved") or t.get("margin") or 0) for t in self.trades
        )
        unrealized = self.get_unrealized_net_usd()
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
        self.one_m_fee_hold = False
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
        """Trading-engine candle interval — synced from the chart TF the user picks.

        Persisted to engine_runtime.json so logout / VPS restart keeps the same TF
        (does not fall back to 1m). Open trades are not closed on TF change.
        """
        try:
            seconds = int(seconds)
        except (TypeError, ValueError):
            return
        if seconds <= 0:
            return
        if self.timeframe_seconds == seconds:
            # Still checkpoint so preferred TF is on disk even if unchanged.
            self.persist_runtime(force=True)
            return
        self.timeframe_seconds = seconds
        _reset_scan_candle_baseline()
        reset_blue_box_state()
        _invalidate_kline_cache()
        self.persist_runtime(force=True)
        print(f"[TIMEFRAME SYNC] Backend trading timeframe set to {seconds}s (persisted).")

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
            # Manual BUY/SELL always allowed on the chart pair (even while AI/session runs).
            # Manual positions stay protected from AI auto-close.
            pass
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

        # AI Agent Instructions: global max_concurrent; 1m also caps per pair/chart.
        blocked = concurrent_entry_blocked(self, trade_pair)
        if blocked:
            self.last_open_skip_reason = f"{blocked} — new entry skipped on {trade_pair}"
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
            lev = max(float(self.leverage or 1), 1.0)
            # Keep finer margin precision — at 100x, $0.27 notional → $0.0027 margin,
            # which rounds to $0.00 at 2dp and falsely tripped "Insufficient balance".
            margin = round(position_size / lev, 6)
            if position_size <= 0:
                self.last_open_skip_reason = (
                    "Position size is $0 after sizing — balance too low for this timeframe %"
                )
                notifications.push(self.last_open_skip_reason + ".", "error")
                return None
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
        # Auto: path SL (protect floor, small-coin aware) + path TP.
        clean_sl = None
        clean_tp = None
        # Probe loss tier from filled entry (trade dict not built yet).
        _probe = {"entry": filled_price}
        trail_pct, arm_pct, band_pct, _is_small, _tier = self._loss_policy_for_trade(_probe)
        if source == "auto":
            clean_sl, clean_tp = self._fixed_exit_prices(
                filled_price,
                side,
                loss_pct=arm_pct,
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
            # Path SL + profit lock/trail state (auto):
            #   Default SL: −0.50% soft / −0.70% hard; 1m hard: SL −0.20% / TP +0.34%
            "path_last_gross_pct": 0.0,
            "path_adverse_streak": 0,
            "path_favorable_streak": 0,
            "path_choppy": False,
            "path_profit_choppy": False,
            "path_continuous_dump": False,
            "path_continuous_run": False,
            "profit_lock": False,
            "profit_lock_level": None,  # ratchet: 0.50 → (1m: 0.65) → +0.20 …
            "loss_protect": False,
            "loss_deep_hold": False,  # True once gross ≤ hard floor band
            "loss_adverse_extreme_gross": None,  # worst trough after protect (most negative)
            "loss_recovery_peak_gross": None,    # best recovery after trough
            "loss_adverse_extreme_price": None,
            "loss_recovery_peak_price": None,
            "path_sl_pct": band_pct,
            "loss_protect_pct": arm_pct,
            "path_tp_pct": PROFIT_LOCK_PCT,
        }
        self.trades.append(trade)
        self.set_pair_mark(trade_pair, filled_price)
        self.persist_runtime()
        if bybit_api.mode != "LIVE_TRADING":
            self.current_capital = round(self.current_capital - capital_reserved, 2)
            print(
                f"[CAPITAL] Reserved ${capital_reserved:,.2f} for #{trade['id']} "
                f"(available ${self.current_capital:,.2f}, notional ${position_size:,.2f})."
            )
        self._append_trade_history(trade)
        qty_label = f" | qty={qty}" if qty is not None else ""
        if not skip_exchange_open:
            if bybit_api.mode == "LIVE_TRADING" and qty is not None and qty > 0:
                # Fire REAL order on Bybit
                ok, err = bybit_api.execute_live_order(
                    trade_pair, side, qty,
                    reason=f"{reason} | ${position_size} notional ({margin} margin x{self.leverage}){qty_label}",
                )
                if not ok:
                    notifications.push(f"Bybit order FAILED: {err}", "error")
                    print(f"❌ LIVE ORDER FAILED: {err}")
                    self._rollback_failed_live_open(trade, err)
                    self.last_open_skip_reason = f"Bybit order failed: {err}"
                    return None
                trade["bybit_symbol"] = get_bybit_symbol(trade_pair)
                trade["exchange"] = "bybit_linear"
                # Refresh history exchange tag so UI/reconcile treat this as confirmed live.
                for row in self.trade_history:
                    if row.get("id") == trade["id"]:
                        row["exchange"] = "bybit_linear"
                        break
                self.persist_runtime(force=True)
            else:
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
        if closed_n:
            self.persist_runtime(force=True)
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

    def _loss_policy_for_trade(self, trade: dict) -> tuple[float, float, float, bool, str]:
        """Return (trail_pct, arm_pct, band_pct, is_small_coin, tier_label).

        Micro-cap pairs: soft lock @ −0.25%, hard exit @ −0.35%.
        1m: hard SL @ −0.20% (no trail).
        Others: soft lock @ −0.50%, trail, hard exit @ −0.70%.
        """
        if self._is_1m_trade(trade):
            return (
                0.0,  # no loss trail on 1m
                LOSS_PROTECT_PCT_1M,
                LOSS_BAND_PCT_1M,
                False,
                "1m",
            )
        if self._is_micro_cap_pair(trade):
            return (
                LOSS_RECOVERY_RETRACE_PCT,
                MICRO_CAP_LOSS_ARM_PCT,
                MICRO_CAP_LOSS_BAND_PCT,
                True,
                "micro_cap",
            )
        return LOSS_RECOVERY_RETRACE_PCT, LOSS_PROTECT_PCT, LOSS_BAND_PCT, False, "normal"

    def _loss_lock_clear_pct_for_trade(self, trade: dict) -> float:
        """Unlock threshold (recover to −X% or better). Unused on 1m hard exits."""
        if self._is_1m_trade(trade):
            return float(LOSS_LOCK_CLEAR_PCT_1M)
        return float(LOSS_LOCK_CLEAR_PCT)

    def _loss_retrace_for_trade(self, trade: dict) -> float:
        """Upward recovery trail in loss zone (sell line = best_recovery + this)."""
        if self._is_1m_trade(trade):
            return 0.0
        return float(LOSS_RECOVERY_RETRACE_PCT)

    def _loss_trail_sell_line(self, trade: dict, *, arm_pct: float | None = None) -> float:
        """Sell line while loss lock armed: best_recovery + 0.20% (e.g. −0.55 → −0.35)."""
        trail = self._loss_retrace_for_trade(trade)
        arm = float(arm_pct if arm_pct is not None else LOSS_PROTECT_PCT)
        best = trade.get("loss_recovery_peak_gross")
        anchor = float(best) if best is not None else -arm
        return anchor + trail

    def _loss_band_pct(self) -> float:
        """Hard loss floor (−0.70%)."""
        return float(LOSS_BAND_PCT)

    def _update_path_sl_state(self, trade: dict, gross_pct: float, mark: float | None = None) -> None:
        """Update profit/loss protect UI levels for open trades."""
        trail_pct, arm_pct, band_pct, _is_small, _tier = self._loss_policy_for_trade(trade)
        if trade.get("path_seeded") is not True:
            trade["path_last_gross_pct"] = gross_pct
            trade["path_seeded"] = True
            return

        trade["path_last_gross_pct"] = gross_pct
        lock_start = self._profit_lock_start_pct(trade)
        is_1m_hard = self._is_1m_trade(trade)

        # 1m: fixed hard SL/TP only — no soft lock, unlock, or trail UI.
        if is_1m_hard:
            trade["path_sl_pct"] = band_pct
            trade["loss_protect_pct"] = arm_pct
            trade["path_tp_pct"] = lock_start
            trade["profit_lock"] = False
            trade["profit_lock_level"] = None
            trade["is_lock_active"] = False
            trade["loss_protect"] = False
            trade["loss_deep_hold"] = False
            if gross_pct >= lock_start - 1e-9:
                trade["is_lock_active"] = True
                trade["lock_level_pct"] = lock_start
                trade["sell_trigger_pct"] = lock_start
            elif gross_pct <= -arm_pct + 1e-9:
                trade["is_stop_active"] = True
                trade["stop_level_pct"] = -arm_pct
                trade["sell_trigger_pct"] = -arm_pct
            else:
                trade["is_stop_active"] = False
                trade["stop_level_pct"] = None
                trade["sell_trigger_pct"] = None
            entry = float(trade.get("entry") or 0)
            side = trade.get("side")
            if entry > 0 and side in ("LONG", "SHORT"):
                sl, tp = self._fixed_exit_prices(
                    entry, side, loss_pct=arm_pct, profit_pct=lock_start
                )
                if sl is not None:
                    trade["sl_price"] = sl
                if tp is not None:
                    trade["tp_price"] = tp
            return

        # Soft lock @ arm%; trail in arm…band; hard floor @ band%.
        if gross_pct <= -arm_pct:
            trade["loss_protect"] = True
        if gross_pct <= -band_pct:
            trade["loss_deep_hold"] = True
        if trade.get("loss_protect"):
            self._update_loss_protect_extremes(trade, gross_pct, mark)
            clear_pct = self._loss_lock_clear_pct_for_trade(trade)
            if gross_pct >= -clear_pct - 1e-9:
                self._clear_loss_protect_lock(trade)

        trade["path_sl_pct"] = band_pct
        trade["loss_protect_pct"] = arm_pct

        # Profit step-lock UI
        if gross_pct >= lock_start:
            self._ratchet_profit_lock_level(trade, gross_pct)
        if trade.get("profit_lock"):
            lock_lvl = float(trade.get("profit_lock_level") or lock_start)
            giveback = self._profit_giveback_for_lock(lock_lvl, trade)
            trail_stop = lock_lvl - giveback
            trade["path_tp_pct"] = lock_lvl
            trade["is_lock_active"] = True
            trade["lock_level_pct"] = lock_lvl
            trade["sell_trigger_pct"] = trail_stop
        elif trade.get("loss_protect"):
            active_stop = band_pct if trade.get("loss_deep_hold") else arm_pct
            trade["is_stop_active"] = True
            trade["stop_level_pct"] = -active_stop
            trade["sell_trigger_pct"] = self._loss_trail_sell_line(trade, arm_pct=arm_pct)
            trade["path_tp_pct"] = lock_start
            trade["is_lock_active"] = False
        else:
            trade["path_tp_pct"] = lock_start
            trade["is_lock_active"] = False
            trade["is_stop_active"] = False
            trade["sell_trigger_pct"] = None

        entry = float(trade.get("entry") or 0)
        side = trade.get("side")
        if entry <= 0 or side not in ("LONG", "SHORT"):
            return

        if trade.get("profit_lock"):
            lock_lvl = float(trade.get("profit_lock_level") or lock_start)
            giveback = self._profit_giveback_for_lock(lock_lvl, trade)
            trail_stop = lock_lvl - giveback
            if side == "LONG":
                sl = entry * (1.0 + trail_stop / 100.0)
                tp = entry * (1.0 + lock_lvl / 100.0)
            else:
                sl = entry * (1.0 - trail_stop / 100.0)
                tp = entry * (1.0 - lock_lvl / 100.0)
            trade["sl_price"] = round(sl, price_decimals_for_mark(entry))
            trade["tp_price"] = round(tp, price_decimals_for_mark(entry))
        elif trade.get("loss_protect"):
            active_stop = band_pct if trade.get("loss_deep_hold") else arm_pct
            sl, tp = self._fixed_exit_prices(
                entry, side, loss_pct=active_stop, profit_pct=lock_start
            )
            if sl is not None:
                trade["sl_price"] = sl
            if tp is not None:
                trade["tp_price"] = tp
        else:
            sl, tp = self._fixed_exit_prices(
                entry, side, loss_pct=arm_pct, profit_pct=lock_start
            )
            if sl is not None:
                trade["sl_price"] = sl
            if tp is not None:
                trade["tp_price"] = tp

    def _profit_lock_start_pct(self, trade: dict | None = None) -> float:
        """Profit arm: 1m hard +0.34%; other TFs +0.50% trail book."""
        if self._is_1m_trade(trade):
            return float(PROFIT_HARD_PCT_1M)
        return float(PROFIT_LOCK_PCT)

    def _is_scalp_trade(self, trade: dict | None = None) -> bool:
        """1m and 5m share the same scalp entry/confirm pipeline (exit may differ on 1m)."""
        return is_scalp_tf((trade or {}).get("timeframe_key"))

    def _is_1m_trade(self, trade: dict | None = None) -> bool:
        """True only for 1m trades (not 5m scalp)."""
        tf = str((trade or {}).get("timeframe_key") or "").strip().lower()
        if not tf:
            tf = str(SECONDS_TO_TIMEFRAME_KEY.get(self.timeframe_seconds, "1m")).strip().lower()
        return tf == "1m"

    def _profit_giveback_for_lock(self, lock_lvl: float, trade: dict | None = None) -> float:
        """Trail giveback from peak lock. 1m: 0 (hard exit at arm). Else 0.10%."""
        if self._is_1m_trade(trade):
            return 0.0
        return float(PROFIT_TRAIL_GIVEBACK_PCT)

    def _ratchet_profit_lock_level(self, trade: dict, gross_pct: float) -> float:
        """Continuous profit trail: lock ratchets with peak gross (never backward).

        Examples: peak +0.73% → floor +0.63%; peak +0.58% → floor +0.48%.
        """
        trade["profit_lock"] = True
        start = self._profit_lock_start_pct(trade)
        g = float(gross_pct)
        prev = trade.get("profit_lock_level")
        base = float(prev) if prev is not None else start
        level = max(base, g)
        trade["profit_lock_level"] = float(level)
        return float(trade["profit_lock_level"])

    def _clear_loss_protect_lock(self, trade: dict) -> None:
        """Clear soft loss lock after unlock, trail exit, or hard band exit."""
        trade["loss_protect"] = False
        trade["loss_deep_hold"] = False
        trade["loss_recovery_peak_gross"] = None
        trade["loss_recovery_peak_price"] = None
        trade["loss_adverse_extreme_gross"] = None
        trade["loss_adverse_extreme_price"] = None
        trade["is_stop_active"] = False
        trade["stop_level_pct"] = None
        if not trade.get("profit_lock"):
            trade["sell_trigger_pct"] = None

    def _update_loss_protect_extremes(
        self, trade: dict, gross_pct: float, mark: float | None
    ) -> None:
        """While soft loss lock is armed: track best recovery PnL (never move backward)."""
        side = trade.get("side")
        # Optional: still record worst adverse for logs (does not reset recovery)
        adv = trade.get("loss_adverse_extreme_gross")
        if adv is None or gross_pct < float(adv) - 1e-9:
            trade["loss_adverse_extreme_gross"] = gross_pct
            if mark is not None and mark > 0:
                if side == "LONG":
                    ext = trade.get("loss_adverse_extreme_price")
                    if ext is None or mark < float(ext):
                        trade["loss_adverse_extreme_price"] = float(mark)
                elif side == "SHORT":
                    ext = trade.get("loss_adverse_extreme_price")
                    if ext is None or mark > float(ext):
                        trade["loss_adverse_extreme_price"] = float(mark)

        # Best recovery anchor — ratchet forward only, never reset
        rec = trade.get("loss_recovery_peak_gross")
        if rec is None:
            trade["loss_recovery_peak_gross"] = gross_pct
            if mark is not None and mark > 0:
                trade["loss_recovery_peak_price"] = float(mark)
            return

        if gross_pct > float(rec) + 1e-9:
            trade["loss_recovery_peak_gross"] = gross_pct
            if mark is not None and mark > 0:
                # Best recovery price: LONG = higher mark, SHORT = lower mark
                rec_px = trade.get("loss_recovery_peak_price")
                if side == "LONG":
                    if rec_px is None or mark > float(rec_px):
                        trade["loss_recovery_peak_price"] = float(mark)
                elif side == "SHORT":
                    if rec_px is None or mark < float(rec_px):
                        trade["loss_recovery_peak_price"] = float(mark)

    def _mark_from_gross_pct(self, entry: float, side: str, gross_pct: float) -> float:
        """Price that realizes exactly gross_pct for side (paper SL/TP fill clamp)."""
        if side == "LONG":
            return entry * (1.0 + float(gross_pct) / 100.0)
        return entry * (1.0 - float(gross_pct) / 100.0)

    def _evaluate_fixed_pct_exit(self, trade: dict, mark: float) -> str | None:
        """Single path-exit engine: profit locks + two-tier loss stop.

        1m: hard TP @ +0.34% and hard SL @ −0.20% (no trail / unlock).
        Other TFs: profit trail book + soft lock / recovery trail / hard band.
        LONG/SHORT symmetric on gross %. Fees stay out of the trigger.
        """
        trade.pop("_exit_fill_mark", None)
        entry = float(trade.get("entry") or 0)
        if entry <= 0:
            return None
        side = trade.get("side")
        if side not in ("LONG", "SHORT"):
            return None

        trail_pct, arm_pct, band_pct, is_small, tier = self._loss_policy_for_trade(trade)
        clear_pct = self._loss_lock_clear_pct_for_trade(trade)
        lock_start = self._profit_lock_start_pct(trade)

        if side == "LONG":
            gross_pct = ((mark - entry) / entry) * 100.0
        else:
            gross_pct = ((entry - mark) / entry) * 100.0

        # peak_profit / trough: ratchet only; never reset while position open
        trade["peak_gross_pct"] = max(float(trade.get("peak_gross_pct") or 0), gross_pct)
        trade["trough_gross_pct"] = min(float(trade.get("trough_gross_pct") or 0), gross_pct)

        self._update_path_sl_state(trade, gross_pct, mark=mark)
        paper = trade.get("exchange") == "paper" or not trade_uses_bybit_executor(trade)

        def _arm_paper_fill(target_gross: float) -> None:
            if not paper:
                return
            if target_gross >= 0 and gross_pct > target_gross + 1e-9:
                trade["_exit_fill_mark"] = self._mark_from_gross_pct(entry, side, target_gross)
            elif target_gross < 0 and gross_pct < target_gross - 1e-9:
                trade["_exit_fill_mark"] = self._mark_from_gross_pct(entry, side, target_gross)

        # 1m hard fixed exits — no profit trail, no loss trail/unlock.
        if self._is_1m_trade(trade):
            if gross_pct >= lock_start - 1e-9:
                _arm_paper_fill(lock_start)
                return (
                    f"PROFIT_HARD_EXIT | {side} hard_tp=+{lock_start:g}% now={gross_pct:.3f}% "
                    f"(no trail) tier={tier} mark={mark:.6f} entry={entry:.6f}"
                )
            if gross_pct <= -arm_pct - 1e-9:
                _arm_paper_fill(-arm_pct)
                return (
                    f"LOSS_HARD_EXIT | {side} hard_sl=−{arm_pct:g}% now={gross_pct:.3f}% "
                    f"(no trail) tier={tier} mark={mark:.6f} entry={entry:.6f}"
                )
            return None

        # 1) Profit step-locks (5m+)
        if gross_pct >= lock_start:
            self._ratchet_profit_lock_level(trade, gross_pct)

        if trade.get("profit_lock"):
            lock_lvl = float(trade.get("profit_lock_level") or lock_start)
            giveback_need = self._profit_giveback_for_lock(lock_lvl, trade)
            lock_giveback = lock_lvl - gross_pct
            if lock_giveback >= giveback_need - 1e-9:
                fill_at = lock_lvl - giveback_need
                _arm_paper_fill(fill_at)
                floor_pct = lock_lvl - giveback_need
                return (
                    f"PROFIT_LOCK_EXIT | {side} peak_lock={lock_lvl:.3f}% now={gross_pct:.3f}% "
                    f"giveback={lock_giveback:.3f}%≥{giveback_need:g}% "
                    f"(trail {giveback_need:g}% → floor +{floor_pct:.3f}%) "
                    f"mark={mark:.6f} entry={entry:.6f}"
                )
            return None

        # 2) Soft lock @ arm%; trail in arm…band; hard exit @ band%.
        if gross_pct <= -arm_pct:
            trade["loss_protect"] = True
        if gross_pct <= -band_pct:
            trade["loss_deep_hold"] = True

        if trade.get("loss_protect"):
            self._update_loss_protect_extremes(trade, gross_pct, mark)

            # Recover to −clear% or better → unlock, no sell; profit book takes over.
            if gross_pct >= -clear_pct - 1e-9:
                self._clear_loss_protect_lock(trade)
                return None

            # Hard floor @ band% — instant exit (prevents deep bleed).
            if gross_pct <= -band_pct - 1e-9:
                _arm_paper_fill(-band_pct)
                self._clear_loss_protect_lock(trade)
                return (
                    f"LOSS_BAND_EXIT | {side} hard_stop=−{band_pct:g}% now={gross_pct:.3f}% "
                    f"(trail zone −{arm_pct:g}…−{band_pct:g}%) "
                    f"tier={tier} mark={mark:.6f} entry={entry:.6f}"
                )

            # Recovery trail while still above hard floor.
            best = float(
                trade.get("loss_recovery_peak_gross")
                if trade.get("loss_recovery_peak_gross") is not None
                else gross_pct
            )
            sell_line = best + trail_pct
            if gross_pct >= sell_line - 1e-9:
                _arm_paper_fill(sell_line)
                self._clear_loss_protect_lock(trade)
                return (
                    f"LOSS_RECOVERY_TRAIL | {side} lock={best:.3f}% "
                    f"sell_line={sell_line:.3f}% now={gross_pct:.3f}% "
                    f"(+{trail_pct:g}% trail in −{arm_pct:g}…−{band_pct:g}% zone) "
                    f"tier={tier} mark={mark:.6f} entry={entry:.6f}"
                )
            return None  # HOLD in trail zone until bounce, unlock, or hard floor

        return None

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
            if not trade.get("bybit_symbol"):
                trade["bybit_symbol"] = get_bybit_symbol(trade.get("pair", ""))
            if not trade.get("bybit_symbol"):
                # Live mode but this record was never wired to Bybit — settle locally.
                notifications.push(
                    f"Force-close #{trade['id']} settled locally (no Bybit symbol on trade).",
                    "warning",
                )
                self.last_close_error = None
            else:
                ok, err = bybit_close_trade(trade)
                if not ok:
                    msg = err or "Unknown Bybit close error"
                    self.last_close_error = msg
                    notifications.push(
                        f"Bybit close FAILED #{trade['id']} {trade['pair']}: {msg}",
                        "error",
                    )
                    system_log.push(
                        "bybit",
                        f"Close failed #{trade['id']} {trade.get('bybit_symbol')}: {msg}",
                        {"trade_id": trade["id"], "reason": reason},
                    )
                    return False
                self.last_close_error = None
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(bybit_api.fetch_real_balance())
                except RuntimeError:
                    pass
        else:
            self.last_close_error = None
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
    ) -> str:
        """Handle opposite auto positions when a new signal is about to fire.

        Returns one of:
          ``none``         — no opposite auto trade on this pair → fire new
          ``flipped``      — all opposites had gross > FLIP_EXIT_MIN_GROSS_PCT,
                             closed successfully → fire new
          ``blocked``      — opposite exists with gross ≤ threshold → keep old,
                             skip new fire
          ``close_failed`` — tried to flip-close but exchange/local close failed
                             → keep old, skip new fire

        Path lock/trail/emergency exits are unchanged; this only runs on opposite
        auto-entry fire. Manual positions are never touched.
        """
        opposite = "SHORT" if side == "LONG" else "LONG"
        opposites = [
            t
            for t in list(self.trades)
            if t.get("source") == "auto"
            and t.get("pair") == pair
            and t.get("side") == opposite
        ]
        if not opposites:
            return "none"

        min_pct = float(FLIP_EXIT_MIN_GROSS_PCT)
        scored: list[tuple[dict, dict, float]] = []
        for trade in opposites:
            metrics = self._trade_metrics(trade)
            gross = float(metrics.get("gross_pct") or 0.0)
            scored.append((trade, metrics, gross))

        # Any opposite still ≤ threshold → do not exit, do not open opposite.
        weak = [(t, g) for t, _, g in scored if g <= min_pct]
        if weak:
            detail = ", ".join(f"#{t.get('id')}@{g:.3f}%" for t, g in weak)
            print(
                f"[FLIP] BLOCKED new {side} on {pair}: opposite ≤{min_pct:g}% "
                f"({detail}) — keeping old, skipping new fire"
                + (f" | signal={pattern}" if pattern else "")
            )
            return "blocked"

        closed_ids: list[int] = []
        for trade, metrics, gross in scored:
            reason = (
                f"OPPOSITE_FLIP_EXIT | new {side} signal"
                + (f" {pattern}" if pattern else "")
                + f" | gross={gross:.3f}%>{min_pct:g}%"
            )
            if not self._close_single_trade(trade, metrics, reason):
                print(
                    f"[FLIP] Close FAILED #{trade.get('id')} {pair} {opposite} "
                    f"(gross={gross:.3f}%) — skip new {side} fire"
                )
                return "close_failed"
            closed_ids.append(int(trade.get("id") or 0))
            print(f"[FLIP] Closed #{trade.get('id')} {opposite} {pair} @ {gross:.3f}% → {reason}")

        if closed_ids:
            closed_set = set(closed_ids)
            self.trades = [t for t in self.trades if int(t.get("id") or 0) not in closed_set]
            self.persist_runtime()
        return "flipped"

    def same_side_auto_count(self, side: str, pair: str) -> int:
        return sum(
            1
            for t in self.trades
            if t.get("source") == "auto" and t.get("pair") == pair and t.get("side") == side
        )

    def has_same_side_auto_capacity(self, side: str, pair: str) -> bool:
        """False when this pair already has enough same-side auto positions.

        1m/5m/30s fee pack: allow up to ONE_M_MAX_CONCURRENT same-side stacks per chart
        (3 per pair → e.g. 3 charts × 3 = 9 open).
        """
        limit = max(1, int(MAX_SAME_SIDE_AUTO_PER_PAIR))
        tf = str(
            SECONDS_TO_TIMEFRAME_KEY.get(getattr(self, "timeframe_seconds", 60), "1m")
        ).strip().lower()
        if is_scalp_tf(tf):
            limit = max(limit, int(ONE_M_MAX_CONCURRENT))
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
        for row in self.trade_history:
            if (
                row.get("source") == "auto"
                and row.get("pair") == pair
                and row.get("signal_candle_time") is not None
            ):
                times.append(int(row["signal_candle_time"]))
        return max(times) if times else None

    def one_m_earliest_next_fire_ms(self, pair: str, interval_ms: int) -> int | None:
        """1m-only: after a fire on candle N, next fire earliest at candle N+3.

        Example: trade on candle 1 → no fire on 2–3; detect may land on 3 → fire on 4.
        """
        last = LAST_AUTO_FIRE_CANDLE_MS.get(pair)
        hist = self.last_auto_entry_candle_time(pair)
        candidates = [int(x) for x in (last, hist) if x is not None]
        if not candidates:
            return None
        return max(candidates) + (ONE_M_MIN_BARS_BETWEEN_FIRES * int(interval_ms))

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
        self.connectivity_frozen = False
        self.freeze_reason = None
        self.persist_runtime(force=True)

    def manual_stop(self, reason="Manual Kill Switch Activated from Frontend"):
        """Emergency STOP — sell all open positions and halt AI."""
        print(f"[PILLAR 2: BACKEND] {reason}")
        self.session_hold_mode = False
        self._close_all_positions(reason)
        self.is_active = False
        self.trading_ready_at = 0.0
        self.boot_ui_until = 0.0
        _clear_entry_pipeline()
        self.end_ai_season(clear_live_table=True, reason="manual_stop")
        self.peak_net_pct = 0.0
        self.is_lock_active = False
        system_log.push("ai", "AI automation STOPPED — emergency exit, all positions closed.", {"reason": reason})
        notifications.push("EMERGENCY EXIT: All positions closed and AI automation stopped.", "error")
        self.connectivity_frozen = False
        self.freeze_reason = None
        self.persist_runtime(force=True)

    def hold_stop(self, reason: str = "AI Engine STOP — hold open trades"):
        """STOP with Hold: no new fires; open trades keep path-SL / TP auto-exit; portfolio keeps updating."""
        print(f"[AI ENGINE] HOLD STOP — {reason}")
        self.is_active = False
        self.session_hold_mode = True
        self.trading_ready_at = 0.0
        self.boot_ui_until = 0.0
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
        self.persist_runtime(force=True)

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
        self.persist_runtime(force=True)

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
        self.begin_trading_warmup()
        print(f"[SESSION SCHEDULE] Auto-start — {reason}")
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(apply_momentum_watchlist_refresh(reason="schedule_start"))
        except RuntimeError:
            pass
        system_log.push(
            "ai",
            f"Session schedule STARTED AI automation — {reason}",
            {"open_positions": open_count, "warmup_sec": ENGINE_WARMUP_SEC},
        )
        notifications.push(
            f"Session schedule ON — trading READY now; boot UI {ENGINE_WARMUP_SEC}s ({reason}).",
            "success",
        )
        self.persist_runtime()

    def resume_trading_after_emergency(self):
        """ Legacy continue endpoint — portfolio stop-loss removed; clears halt flags only. """
        self.clear_emergency_state()
        self.is_active = True
        self.begin_trading_warmup()
        print("[AI AGENT] Trading resumed (portfolio stop-loss disabled).")
        notifications.push(
            f"Trading resumed — live now (boot UI {ENGINE_WARMUP_SEC}s cosmetic).",
            "warning",
        )
        self.persist_runtime()

    def begin_trading_warmup(self) -> None:
        """Arm on Continue: scan + trade immediately; boot overlay follows momentum scan."""
        self.trading_ready_at = 0.0
        now = time.time()
        self.boot_started_at = now
        self.engine_armed_at = now
        # Safety max — overlay closes earlier when momentum_gate_ready + min intro elapsed.
        self.boot_ui_until = now + float(ENGINE_BOOT_MAX_SEC)
        self.momentum_gate_ready = False
        self.momentum_fire_pairs = []
        self.momentum_scores = []
        self.watchlist = []  # reset — fresh momentum scan rebuilds fire/watchlist
        self.momentum_scan_done = 0
        self.momentum_scan_total = 0
        self.momentum_scan_stage = "starting"
        self.last_momentum_candle_ms = 0
        # Seed cursor on next scan so already-closed history is not traded as fresh detects.
        _reset_scan_candle_baseline()
        print(
            f"[AI ENGINE] Armed — trading READY now "
            f"(boot UI scan-driven, max {ENGINE_BOOT_MAX_SEC:g}s). "
            f"Momentum watchlist gate pending. "
            f"Detect on closed candle → 1m: lock, skip 1st green/red tick, fire on 2nd "
            f"(max {ONE_M_CONFIRM_MAX_BARS} bars); 5m: fire on 1st green/red tick; "
            f"other TFs: fire at next candle open. "
            f"First detect per pair is skipped."
        )

    def trading_ready(self) -> bool:
        if not self.is_active:
            return False
        ready_at = float(getattr(self, "trading_ready_at", 0) or 0)
        if ready_at <= 0:
            return True
        return time.time() >= ready_at

    def warmup_remaining_sec(self) -> float:
        """Boot overlay remaining — closes when boot_ui_until elapses (scan sets short deadline)."""
        until = float(getattr(self, "boot_ui_until", 0) or 0)
        if until <= 0:
            ready_at = float(getattr(self, "trading_ready_at", 0) or 0)
            if ready_at <= 0:
                return 0.0
            return max(0.0, ready_at - time.time())
        return max(0.0, until - time.time())

    def _on_momentum_scan_progress(self, done: int, total: int, stage: str) -> None:
        self.momentum_scan_done = int(done)
        self.momentum_scan_total = int(total)
        self.momentum_scan_stage = str(stage or "")

    def persist_runtime(self, force: bool = False) -> None:
        """Checkpoint engine state so restart/outage does not wipe open book."""
        now = time.time()
        if not force and (now - float(self._last_runtime_save or 0)) < 2.0:
            return
        self._last_runtime_save = now
        save_runtime(self)

    def note_market_feed(self) -> None:
        """Call on every live price tick — clears feed-stale freeze when healthy."""
        self._last_feed_ts = time.time()
        if self.connectivity_frozen and self.freeze_reason == "market_feed_stale":
            self.unfreeze_connectivity("Market feed restored")

    def note_ai_result(self, ok: bool) -> None:
        """AI health only — never freeze entries. Brain/OF keep firing when AI is down.

        Legacy `ai_provider_down` freezes (from older builds) are cleared on any result.
        """
        if self.connectivity_frozen and self.freeze_reason == "ai_provider_down":
            self.unfreeze_connectivity("AI outage no longer blocks entries (brain/OF continue)")
        if ok:
            self._ai_fail_streak = 0
            self._ai_skip_until = 0.0
            return
        self._ai_fail_streak = int(self._ai_fail_streak or 0) + 1
        # Brief cool-down so flaky AI does not stall the multi-pair scan loop.
        if self._ai_fail_streak >= 2:
            self._ai_skip_until = time.time() + 90.0

    def ai_consult_allowed(self) -> bool:
        """False during AI cool-down after consecutive provider failures."""
        until = float(getattr(self, "_ai_skip_until", 0) or 0)
        return time.time() >= until

    def refresh_feed_health(self) -> None:
        """Freeze new entries only if market ticks go stale (true feed outage)."""
        if not self.is_active:
            return
        # Clear obsolete AI freezes left from older runtime files / builds.
        if self.connectivity_frozen and self.freeze_reason == "ai_provider_down":
            self.unfreeze_connectivity("Cleared legacy AI freeze — entries resume")
            return
        age = time.time() - float(self._last_feed_ts or 0)
        if age >= FEED_STALE_SECONDS:
            self.freeze_connectivity("market_feed_stale")

    def freeze_connectivity(self, reason: str) -> None:
        if self.connectivity_frozen and self.freeze_reason == reason:
            return
        was = self.connectivity_frozen
        self.connectivity_frozen = True
        self.freeze_reason = reason
        self.persist_runtime(force=True)
        label = {
            "ai_provider_down": "AI provider unreachable",
            "market_feed_stale": "Market feed / backend connectivity stale",
        }.get(reason, reason)
        if not was:
            print(f"[ENGINE FREEZE] {label} — new fires paused; open trades still managed.")
            system_log.push(
                "ai",
                f"Engine FROZEN — {label}. Open trades keep updating; new entries paused until reconnect.",
                {"reason": reason, "open_positions": len(self.trades)},
            )
            notifications.push(
                f"Engine frozen ({label}). Open positions stay live; new trades pause until connectivity returns.",
                "warning",
            )

    def unfreeze_connectivity(self, detail: str = "Connectivity restored") -> None:
        if not self.connectivity_frozen:
            return
        prev = self.freeze_reason
        self.connectivity_frozen = False
        self.freeze_reason = None
        self._ai_fail_streak = 0
        self.persist_runtime(force=True)
        print(f"[ENGINE UNFREEZE] {detail} (was {prev})")
        system_log.push(
            "ai",
            f"Engine UNFROZEN — {detail}. Resuming from same open book.",
            {"was_reason": prev, "open_positions": len(self.trades)},
        )
        notifications.push(
            f"Engine unfrozen — {detail}. Continuing from existing open trades.",
            "success",
        )

    def entries_allowed(self) -> bool:
        """False while frozen / hold / emergency / 1m fee-budget — open trades still update."""
        if self.emergency_triggered:
            return False
        if self.connectivity_frozen:
            return False
        if self.session_hold_mode and not self.is_active:
            return False
        if not self.trading_ready():
            return False
        if ONE_M_FEE_HOLD_ENABLED and self.one_m_fee_hold and is_scalp_tf(self._chart_tf_key()):
            return False
        return bool(self.is_active)

    def _chart_tf_key(self) -> str:
        return str(SECONDS_TO_TIMEFRAME_KEY.get(self.timeframe_seconds, "1m")).strip().lower()

    def refresh_one_m_fee_budget(self) -> None:
        """Scalp (1m/5m): optional pause when REALIZED fees dominate closed book.

        Disabled by default (ONE_M_FEE_HOLD_ENABLED=false). When off, clears any
        leftover hold and never arms new fee pauses.
        """
        if not ONE_M_FEE_HOLD_ENABLED:
            if self.one_m_fee_hold:
                self.one_m_fee_hold = False
                self.one_m_fee_hold_at = 0.0
            return
        if not is_scalp_tf(self._chart_tf_key()):
            # Higher TFs: never keep a leftover scalp fee hold armed.
            if self.one_m_fee_hold:
                self.one_m_fee_hold = False
                self.one_m_fee_hold_at = 0.0
            return
        book = self.get_session_gross_and_fees_usd()
        closed = int(book.get("closed_count") or 0)
        # REALIZED only — matches "fees ate the wins" intent without open noise.
        fees = float(book.get("closed_fee_usd") or 0)
        gross = float(book.get("closed_gross_usd") or 0)
        net = gross - fees

        def _release(why: str) -> None:
            self.one_m_fee_hold = False
            self.one_m_fee_hold_at = 0.0
            print(f"[FEE BUDGET] scalp fee hold RELEASED — {why}. New entries resume.")
            system_log.push_agent_chat(
                f"Scalp FEE HOLD released — {why}.",
                status="match",
                details={"fees": fees, "gross": gross, "net": net, "closed": closed},
            )
            notifications.push("Fee budget recovered — new entries resume.", "success")

        # Already holding — check whether to release.
        if self.one_m_fee_hold:
            armed_at = float(getattr(self, "one_m_fee_hold_at", 0) or 0)
            # Missing timestamp (old runtime) → treat as timed out so sticky holds clear.
            age = (time.time() - armed_at) if armed_at > 0 else ONE_M_FEE_HOLD_MAX_SECONDS
            recovered = bool(net > 0 and fees < max(gross, 1e-9) * ONE_M_FEE_BUDGET_RATIO)
            timed_out = age >= ONE_M_FEE_HOLD_MAX_SECONDS
            if closed < ONE_M_FEE_HOLD_MIN_CLOSED or recovered or timed_out:
                if closed < ONE_M_FEE_HOLD_MIN_CLOSED:
                    why = "closed sample below minimum"
                elif recovered:
                    why = "closed book recovered (net>0, fees under ratio)"
                else:
                    why = f"max hold {ONE_M_FEE_HOLD_MAX_SECONDS:.0f}s elapsed"
                _release(why)
            return

        if closed < ONE_M_FEE_HOLD_MIN_CLOSED:
            return
        # Ignore dust — tiny accounts otherwise pause on cents of fee noise.
        if fees < ONE_M_FEE_HOLD_MIN_FEE_USD:
            return
        reason = None
        if gross > 0 and fees >= gross * ONE_M_FEE_BUDGET_RATIO:
            reason = (
                f"scalp fee budget: closed fees ${fees:.2f} ≥ {ONE_M_FEE_BUDGET_RATIO * 100:.0f}% "
                f"of closed gross ${gross:.2f} after {closed} closes"
            )
        elif net <= 0:
            reason = (
                f"scalp fee budget: closed net ${net:.2f} ≤ 0 "
                f"(fees ${fees:.2f}) after {closed} closes"
            )
        if not reason:
            return
        self.one_m_fee_hold = True
        self.one_m_fee_hold_at = time.time()
        print(f"[FEE BUDGET] {reason} — new scalp entries paused")
        system_log.push_agent_chat(
            f"Scalp FEE HOLD — {reason}. Open trades still manage; no new fires.",
            status="no_match",
            details={"fees": fees, "gross": gross, "net": net, "closed": closed},
        )
        notifications.push(
            "Fee budget hit — new entries paused (open trades still exit).",
            "warning",
        )


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
    """Map BTC/USDT → BTCUSDT. Hardcoded map first, then live instruments cache."""
    symbol = (pair_label or "").split("/")[0]
    mapped = BYBIT_SYMBOL_MAP.get(symbol)
    if mapped:
        return mapped
    return bybit_instruments.resolve_symbol(pair_label)


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


def snap_qty_to_step(
    qty: float,
    bybit_symbol: str | None,
    *,
    round_up: bool = False,
) -> float | None:
    """Snap qty to Bybit lot step. Floor by default; ceil when meeting min notional."""
    if qty is None or qty <= 0:
        return None
    step = bybit_instruments.qty_step(bybit_symbol)
    if step is None:
        step = BYBIT_QTY_STEP.get(bybit_symbol) if bybit_symbol else None
    if not step or step <= 0:
        return qty
    if round_up:
        snapped = math.ceil(qty / step - 1e-12) * step
    else:
        snapped = math.floor(qty / step + 1e-12) * step
    if snapped <= 0:
        return None
    # Avoid float junk (e.g. 0.30000000004) in order qty strings.
    decimals = max(0, min(8, -int(math.floor(math.log10(step))) if step < 1 else 0))
    return round(snapped, decimals)


def qty_for_notional(
    notional_usd: float,
    entry_price: float,
    bybit_symbol: str | None,
    *,
    min_notional: float = 0.0,
) -> tuple[float | None, float]:
    """Return (qty, effective_notional) that meets Bybit min order value.

    Floored qty can land just under minNotional (ErrCode 110094). We target a
    small buffer above the exchange minimum and round qty UP to the lot step.
    """
    if entry_price is None or entry_price <= 0:
        return None, 0.0
    # 3% buffer so mark drift / tick doesn't drop value under 5 USDT.
    target = max(float(notional_usd or 0), float(min_notional or 0) * 1.03, float(min_notional or 0) + 0.05)
    if target <= 0:
        return None, 0.0
    raw = target / float(entry_price)
    qty = snap_qty_to_step(raw, bybit_symbol, round_up=True)
    if qty is None or qty <= 0:
        lot = min_lot_qty(bybit_symbol)
        qty = snap_qty_to_step(lot, bybit_symbol, round_up=True) if lot else None
    if qty is None or qty <= 0:
        return None, 0.0

    step = bybit_instruments.qty_step(bybit_symbol) or BYBIT_QTY_STEP.get(bybit_symbol) or 0
    need = float(min_notional or 0)
    # Bump one step at a time until notional clears the exchange floor.
    for _ in range(25):
        notion = float(qty) * float(entry_price)
        if need <= 0 or notion + 1e-9 >= need:
            return qty, round(notion, 4)
        if not step or step <= 0:
            break
        qty = snap_qty_to_step(float(qty) + float(step), bybit_symbol, round_up=True)
        if qty is None:
            break
    notion = float(qty) * float(entry_price) if qty else 0.0
    if need > 0 and notion + 1e-9 < need:
        return None, notion
    return qty, round(notion, 4)


def min_lot_qty(bybit_symbol: str | None) -> float | None:
    lot = bybit_instruments.min_order_qty(bybit_symbol)
    if lot is not None and lot > 0:
        return float(lot)
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

# Detect on last closed candle → queue → fire
# (1m: skip 1st green/red tick, fire on 2nd; 5m: first green/red tick; else next open).
PENDING_ENTRY_SIGNALS: dict[str, dict] = {}
# 1m only: last auto fire candle open-time per pair (blocks fires after a gap).
LAST_AUTO_FIRE_CANDLE_MS: dict[str, int] = {}
ONE_M_MIN_BARS_BETWEEN_FIRES = 3  # fire on N → next fire earliest N+3 (was 5)
# 1m/5m: after pattern+AI lock, wait up to N bars for live green/red START (not candle close).
ONE_M_CONFIRM_MAX_BARS = 3
# 1m only: skip this many matching color ticks before fire (1 = pehla skip, dusra pe fire).
ONE_M_CONFIRM_SKIP_TICKS = int(os.environ.get("ONE_M_CONFIRM_SKIP_TICKS", "1"))
ONE_M_MAX_CONCURRENT = 3  # hard cap PER PAIR while chart TF is 1m (fee control)
# Hold new scalp entries when broker fees eat the session book (disabled by default).
ONE_M_FEE_HOLD_ENABLED = os.environ.get("ONE_M_FEE_HOLD_ENABLED", "0").strip().lower() in (
    "1", "true", "yes",
)
ONE_M_FEE_BUDGET_RATIO = 0.45  # closed fees ≥ 45% of closed gross → hold (when enabled)
ONE_M_FEE_HOLD_MIN_CLOSED = 3  # need at least this many closed round-trips
ONE_M_FEE_HOLD_MIN_FEE_USD = float(os.environ.get("ONE_M_FEE_HOLD_MIN_FEE_USD", "0.03"))
ONE_M_FEE_HOLD_MAX_SECONDS = float(os.environ.get("ONE_M_FEE_HOLD_MAX_SECONDS", "600"))
# Engine boot UI: intro + analysis overlay (cosmetic; trading starts on Continue).
ENGINE_BOOT_INTRO_SEC = 10
ENGINE_BOOT_ANALYSIS_SEC = 10  # legacy UI fallback; boot now ends on scan+min intro
ENGINE_BOOT_MAX_SEC = float(os.environ.get("ENGINE_BOOT_MAX_SEC", "60"))
ENGINE_WARMUP_SEC = ENGINE_BOOT_INTRO_SEC + ENGINE_BOOT_ANALYSIS_SEC  # legacy total label
# Soft AI Engine re-arm every N seconds on 1m/5m only (open trades held).
ENGINE_HOURLY_RESTART_SEC = float(os.environ.get("ENGINE_HOURLY_RESTART_SEC", "3600"))
PATTERN_NEON_STAGES: list[dict] = []
THREE_CANDLE_ENTRY = False
# After arm: skip the first BUY/SELL detect once per pair (all charts).
FIRST_DETECT_SKIPPED: set[str] = set()
# Patterns that must never open a trade (detect may log, entry is skipped).
SKIP_TRADE_PATTERNS = frozenset(
    {
        "MA_COMPRESSION_CONSOLIDATION_ZONE",
        "IMBALANCE",
        "QUALIFIED_IMBALANCE",
        "RAW_IMBALANCE",
    }
)

_bybit_executor_agent = None


def _pattern_is_trade_skipped(detect: dict | None) -> str | None:
    """Return skip reason if detect pattern is on the blocklist; else None."""
    d = detect or {}
    raw = (
        str(d.get("pattern") or "")
        or str(d.get("pattern_label") or "")
        or str(d.get("strategy") or "")
        or str(d.get("trap_type") or "")
        or str(d.get("reason") or "")
    )
    up = raw.upper().replace(" ", "_").replace("-", "_")
    for blocked in SKIP_TRADE_PATTERNS:
        key = blocked.upper().replace(" ", "_")
        if key and key in up:
            return blocked
    return None


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
    LAST_AUTO_FIRE_CANDLE_MS.clear()


def _reset_scan_candle_baseline() -> None:
    """Clear candle cursors + queues on engine arm / TF change.

    First scan per pair only seeds LAST_CANDLE_TIMESTAMPS (no trade on already-closed
    history). First BUY/SELL detect per pair is also skipped. After that: detect on
    each NEW close → fire at next candle open.
    """
    LAST_CANDLE_TIMESTAMPS.clear()
    FIRST_DETECT_SKIPPED.clear()
    _clear_entry_pipeline()


async def apply_momentum_watchlist_refresh(*, reason: str = "refresh") -> dict:
    """Score liquid Bybit universe; rewrite watchlist to MARKET-avg% qualifiers.

    HARD INSTRUCTION (do not violate):
      - 7th-candle / chart / watchlist refresh·replace·add·edit MUST NOT close,
        exit, drop, or hide related OPEN trades.
      - Only NEW-entry universe (fire list) changes; open positions keep path
        TP/SL management via open_trade_pairs price feed until their own exit.
    """
    tf_key = SECONDS_TO_TIMEFRAME_KEY.get(agent.timeframe_seconds, "1m")
    prev_fire = set(getattr(agent, "momentum_fire_pairs", None) or [])
    prev_watch = set(agent.watchlist or [])
    open_before = [
        (int(t.get("id") or 0), str(t.get("pair") or ""), str(t.get("side") or ""))
        for t in list(agent.trades or [])
    ]
    open_pairs = sorted({p for p in agent.open_trade_pairs() if p})

    agent.momentum_scan_stage = "instruments"
    async with httpx.AsyncClient(timeout=30.0) as client:
        await bybit_instruments.ensure_instruments(client)
        agent.momentum_scan_stage = "liquid"
        symbol_map = await bybit_instruments.build_liquid_symbol_map(
            client,
            fallback_map=BYBIT_SYMBOL_MAP,
        )

    avail = float(agent.get_available_capital() or 0)

    def _lot_ok(coin: str, bybit_symbol: str) -> bool:
        return bybit_instruments.lot_affordable(
            bybit_symbol,
            available_capital=avail if avail > 0 else 1e9,
            leverage=float(getattr(agent, "leverage", 100) or 100),
        )

    async def _progress(done: int, total: int, stage: str) -> None:
        agent._on_momentum_scan_progress(done, total, stage)

    agent.momentum_scan_total = len(symbol_map)
    agent.momentum_scan_done = 0
    agent.momentum_scan_stage = "scoring"

    built = await build_momentum_watchlist(
        symbol_map=symbol_map,
        engine_tf=tf_key,
        # Fresh start: do not dock previous chart pair into the new fire list.
        active_pair=None if reason in ("bot_start", "schedule_start", "boot", "hourly_restart") else agent.active_pair,
        # Watchlist/fire size = MAX_WATCHLIST only — NOT capped by trade-risk max_concurrent.
        # max_concurrent still limits how many positions can be OPEN at once.
        max_pairs=int(getattr(agent, "MAX_WATCHLIST", 32) or 32),
        progress_cb=_progress,
        lot_ok=_lot_ok if avail > 0 else None,
    )
    thr = float(built["threshold"])
    new_fire = list(built["qualified"])
    scores = list(built["scores"])
    # Fire = NEW-entry universe only. Open pairs are pinned onto watchlist so
    # they stay visible/managed without forcing new entries if they fell off cut.
    new_watch = list(new_fire)

    # Start/boot: chart → #1 fire pair. Does NOT close any open trades.
    if reason in ("bot_start", "schedule_start", "boot", "hourly_restart") and new_fire:
        first = new_fire[0]
        mark = float(agent.pair_prices.get(first) or agent.current_price or 0)
        agent.set_active_pair(first, mark)

    pinned: list[str] = []
    seen_w = set(new_watch)
    for p in open_pairs:
        if p not in seen_w:
            new_watch.append(p)
            pinned.append(p)
            seen_w.add(p)

    agent.set_watchlist(new_watch)
    # set_watchlist may truncate at MAX_WATCHLIST — force-pin opens back.
    if open_pairs:
        wl = list(agent.watchlist or [])
        seen = set(wl)
        for p in open_pairs:
            if p not in seen:
                wl.append(p)
                seen.add(p)
                if p not in pinned:
                    pinned.append(p)
        agent.watchlist = wl

    agent.momentum_fire_pairs = new_fire
    agent.momentum_scores = [
        {
            "pair": s["pair"],
            "avg_pct": s["avg_pct"],
            "passed": s["passed"],
        }
        for s in scores
    ]
    agent.momentum_threshold_pct = thr
    agent.momentum_gate_ready = True
    agent.momentum_last_refresh_ms = int(time.time() * 1000)
    agent.momentum_scan_stage = "ready"
    agent.momentum_scan_done = int(built.get("scored") or len(scores))
    agent.momentum_scan_total = int(built.get("scored") or len(scores))
    # First boot only: keep overlay up through remaining intro MP4, then ~1.5s READY.
    # Do NOT rewrite on later 7-candle refreshes (would re-open overlay).
    until = float(getattr(agent, "boot_ui_until", 0) or 0)
    if until > time.time():
        started = float(getattr(agent, "boot_started_at", 0) or 0)
        intro_left = 0.0
        if started > 0:
            intro_left = max(0.0, float(ENGINE_BOOT_INTRO_SEC) - (time.time() - started))
        agent.boot_ui_until = time.time() + max(1.5, intro_left + 1.5)
        print(
            f"[BOOT UI] Scan ready — overlay holds intro {intro_left:.1f}s + 1.5s READY "
            f"(closes in {max(1.5, intro_left + 1.5):.1f}s)."
        )

    # Drop unfilled pending signals only — NEVER close filled open trades here.
    for pair in list(PENDING_ENTRY_SIGNALS.keys()):
        if pair not in set(new_fire):
            PENDING_ENTRY_SIGNALS.pop(pair, None)

    added = sorted(set(new_fire) - prev_fire)
    removed = sorted(prev_fire - set(new_fire))
    kept = sorted(set(new_fire) & prev_fire)

    open_after = [
        (int(t.get("id") or 0), str(t.get("pair") or ""), str(t.get("side") or ""))
        for t in list(agent.trades or [])
    ]
    if open_after != open_before:
        print(
            "[MOMENTUM][GUARD] Open-trade book changed during watchlist refresh — "
            f"before={open_before} after={open_after}. "
            "Refresh path must never exit opens; investigate caller."
        )

    summary = (
        f"Momentum gate ({reason}) TF={tf_key} thr>{thr:g}% · "
        f"scored={built.get('scored', len(scores))} "
        f"fire={len(new_fire)} watch={len(agent.watchlist or [])} · "
        f"+{len(added)} -{len(removed)} · "
        f"open_pinned={len(pinned)} open_kept={len(open_pairs)}"
    )
    print(f"[MOMENTUM] {summary}")
    if built.get("quiet"):
        system_log.push_agent_chat(
            f"Momentum: no pairs above {thr:g}% on {tf_key} — new entries quiet "
            f"(open trades kept; chart {agent.active_pair}).",
            status="no_match",
            details={"threshold": thr, "tf": tf_key, "reason": reason, "open_pairs": open_pairs},
        )
        notifications.push(
            f"Momentum filter: no coins above {thr:g}% ({tf_key}). "
            "New entries paused — open trades unchanged.",
            "warning",
        )
    else:
        system_log.push_agent_chat(
            f"Momentum watchlist · thr>{thr:g}% · "
            f"{', '.join(new_fire[:8])}{'…' if len(new_fire) > 8 else ''}"
            + (f" · open pinned {len(open_pairs)}" if open_pairs else ""),
            status="match",
            details={
                "threshold": thr,
                "tf": tf_key,
                "added": added,
                "removed": removed,
                "kept": kept,
                "scored": built.get("scored"),
                "reason": reason,
                "open_pairs_pinned": open_pairs,
            },
        )
        if added or removed:
            notifications.push(
                f"Momentum watchlist updated (+{len(added)} / −{len(removed)}) — open trades unchanged.",
                "info",
            )

    return {
        "threshold": thr,
        "watchlist": list(agent.watchlist or []),
        "qualified": new_fire,
        "scores": scores,
        "quiet": bool(built.get("quiet")),
        "scored": built.get("scored"),
        "added": added,
        "removed": removed,
        "kept": kept,
        "fire_pairs": new_fire,
        "prev_watch": sorted(prev_watch),
        "open_pairs_pinned": open_pairs,
    }


_momentum_refresh_running = False
_momentum_refresh_started_at = 0.0
# Hard ceiling so a stuck refresh can never block the gate forever.
MOMENTUM_REFRESH_TIMEOUT_SECONDS = float(
    os.environ.get("MOMENTUM_REFRESH_TIMEOUT_SECONDS", "180")
)


def _run_momentum_refresh_background(reason: str) -> None:
    """Fire apply_momentum_watchlist_refresh as a background task.

    Never blocks auto_buy_loop. A guard skips the trigger if a refresh is already
    running (or if it appears stuck past the timeout ceiling). The scan loop keeps
    trading on the current fire_pairs while the new universe re-scores in parallel.
    """
    global _momentum_refresh_running, _momentum_refresh_started_at
    if _momentum_refresh_running:
        elapsed = time.time() - _momentum_refresh_started_at
        if elapsed < MOMENTUM_REFRESH_TIMEOUT_SECONDS:
            print(
                f"[MOMENTUM] refresh already running ({elapsed:.0f}s) — skip '{reason}' trigger"
            )
            return
        print(
            f"[MOMENTUM] refresh stuck {elapsed:.0f}s > {MOMENTUM_REFRESH_TIMEOUT_SECONDS:.0f}s "
            f"ceiling — forcing new '{reason}' refresh"
        )

    async def _runner():
        global _momentum_refresh_running
        try:
            await apply_momentum_watchlist_refresh(reason=reason)
        except Exception as exc:
            print(f"[MOMENTUM] background refresh error: {exc}")
        finally:
            _momentum_refresh_running = False

    _momentum_refresh_running = True
    _momentum_refresh_started_at = time.time()
    asyncio.create_task(_runner())
    print(f"[MOMENTUM] background refresh dispatched ({reason}) — scan loop unblocked")


async def maybe_refresh_momentum_every_n_candles(
    client: httpx.AsyncClient, timeframe_key: str
) -> None:
    """Boot-only momentum universe filter (inline until gate ready).

    Periodic candle re-scan DISABLED. While engine is ON, a separate
    ``momentum_universe_timer_loop`` re-scores every hour in the
    background and adds new coins — open trades are always held/pinned.
    """
    if not agent.is_active or agent.emergency_triggered:
        return
    if getattr(agent, "momentum_gate_ready", False):
        return  # no every-N candle re-scan
    await apply_momentum_watchlist_refresh(reason="boot")
    bybit_symbol = get_bybit_symbol(agent.active_pair)
    if bybit_symbol:
        try:
            hist = await fetch_closed_candle_history(
                client, bybit_symbol, timeframe_key, limit=3
            )
            if hist:
                agent.last_momentum_candle_ms = int(hist[-1]["close_time"])
        except Exception:
            agent.last_momentum_candle_ms = int(time.time() * 1000)


async def momentum_universe_timer_loop():
    """Every 1h: soft universe restart (new coins) while holding open trades.

    Does NOT restart the Docker process or close positions — only re-runs the
    momentum watchlist/fire list in the background (same path as boot refresh).
    """
    interval = max(60, int(MOMENTUM_REFRESH_EVERY_SECONDS))
    print(
        f"[MOMENTUM] Timer loop online — soft universe restart every {interval // 60} min "
        f"(open trades held; new coins may be added)."
    )
    while True:
        try:
            await asyncio.sleep(interval)
            if not agent.is_active or agent.emergency_triggered:
                continue
            if not getattr(agent, "momentum_gate_ready", False):
                continue
            # 1m/5m: hourly engine soft-restart already re-arms + rescans — skip duplicate.
            tf = str(agent._chart_tf_key() or "").strip().lower()
            if tf in ("1m", "5m"):
                continue
            open_n = len(agent.trades or [])
            print(
                f"[MOMENTUM] {interval // 60}-min soft restart — re-scoring universe "
                f"(holding {open_n} open trade(s))"
            )
            _run_momentum_refresh_background(f"every_{interval // 60}_min")
        except Exception as exc:
            print(f"[MOMENTUM] timer loop note: {exc}")
            await asyncio.sleep(30)


async def engine_hourly_restart_loop():
    """Every 1h on 1m/5m: soft-rearm AI Engine (fresh scan/confirm); keep open trades.

    Same effect as a soft START: warmup + momentum rebuild. Does not STOP season,
    does not close positions, does not restart Docker.
    """
    interval = max(60.0, float(ENGINE_HOURLY_RESTART_SEC))
    print(
        f"[AI ENGINE] Hourly soft-restart loop online — every {interval / 60:.0f} min "
        f"on 1m/5m only (open trades held)."
    )
    while True:
        try:
            await asyncio.sleep(20)
            if not agent.is_active or agent.emergency_triggered:
                continue
            tf = str(agent._chart_tf_key() or "").strip().lower()
            if tf not in ("1m", "5m"):
                continue
            armed = float(getattr(agent, "engine_armed_at", 0) or 0)
            if armed <= 0:
                agent.engine_armed_at = time.time()
                continue
            elapsed = time.time() - armed
            if elapsed < interval:
                continue
            # Skip if still in mid-boot scan (gate not ready / overlay open).
            if not bool(getattr(agent, "momentum_gate_ready", False)):
                continue
            until = float(getattr(agent, "boot_ui_until", 0) or 0)
            if until > time.time():
                continue

            open_n = len(agent.trades or [])
            print(
                f"[AI ENGINE] {interval / 60:.0f}-min soft restart on {tf} "
                f"(holding {open_n} open trade(s)) — re-arm scan + watchlist"
            )
            system_log.push(
                "ai",
                f"AI Engine soft restart ({tf}) after {interval / 60:.0f} min — "
                f"open trades held, fresh momentum scan.",
                {"tf": tf, "open_trades": open_n, "interval_sec": interval},
            )
            system_log.push_agent_chat(
                f"AI Engine RESTART ({tf}) — {interval / 60:.0f}m cycle · "
                f"{open_n} open held · fresh scan",
                status="ok",
            )
            notifications.push(
                f"AI Engine soft restart ({tf}) — open trades kept, scanning again.",
                "info",
            )
            agent.one_m_fee_hold = False
            agent.begin_trading_warmup()  # resets engine_armed_at
            try:
                await apply_momentum_watchlist_refresh(reason="hourly_restart")
            except Exception as exc:
                print(f"[AI ENGINE] hourly restart momentum note: {exc}")
                agent.momentum_gate_ready = True
                agent.boot_ui_until = 0.0
            agent.persist_runtime(force=True)
        except Exception as exc:
            print(f"[AI ENGINE] hourly restart loop note: {exc}")
            await asyncio.sleep(30)


def get_pattern_neon_snapshot(pair: str | None = None) -> list[dict]:
    if not pair:
        return list(PATTERN_NEON_STAGES[-80:])
    return [e for e in PATTERN_NEON_STAGES[-80:] if e.get("pair") == pair]


def trade_uses_bybit_executor(trade: dict) -> bool:
    """True when this trade should be closed/executed on Bybit (live or testnet)."""
    if trade.get("exchange") == "paper":
        return False
    return bybit_api.mode == "LIVE_TRADING" and settings_store.is_bybit_configured()


def bybit_close_trade(trade: dict, qty: float | None = None) -> tuple[bool, str | None]:
    executor = get_bybit_executor_agent()
    if executor is None:
        return False, "Bybit executor not available"
    return executor.close_position(trade, qty=qty)


def get_bybit_executor_agent():
    """Lazily builds BybitAgent from the user's saved keys (mainnet or testnet).

    Uses settings_store (which persists user-saved keys on disk) so LIVE trades
    actually fire real orders on Bybit — not just paper prints.
    """
    global _bybit_executor_agent
    if _bybit_executor_agent is None:
        key = settings_store.bybit_api_key
        secret = settings_store.bybit_api_secret
        if not key or not secret:
            return None
        is_testnet = settings_store.bybit_environment == "testnet"
        _bybit_executor_agent = BybitAgent(key, secret, testnet=is_testnet)
        print(
            f"[BYBIT EXECUTOR] Built agent for {'TESTNET' if is_testnet else 'MAINNET'} "
            f"(keys from settings_store)."
        )
    return _bybit_executor_agent


def reset_bybit_executor_agent():
    """Force rebuild on next call (after keys change / environment switch)."""
    global _bybit_executor_agent
    _bybit_executor_agent = None


def agent_policy_summary() -> str:
    """Policy text shown in System Log."""
    return (
        "CANDLESTICK BRAIN + path exit | "
        f"1m hard TP +{PROFIT_HARD_PCT_1M:g}% / SL −{LOSS_PROTECT_PCT_1M:g}% (no trail); "
        f"else profit arm +{PROFIT_LOCK_PCT:g}% peak-trail −{PROFIT_TRAIL_GIVEBACK_PCT:g}% "
        f"(e.g. +0.73→floor +0.63); "
        f"else LOCK −{LOSS_PROTECT_PCT:g}% trail +{LOSS_RECOVERY_RETRACE_PCT:g}% "
        f"in −{LOSS_PROTECT_PCT:g}…−{LOSS_BAND_PCT:g}% (hard @ −{LOSS_BAND_PCT:g}%); "
        f"unlock @−{LOSS_LOCK_CLEAR_PCT:g}% → profit +{PROFIT_LOCK_PCT:g}% | "
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


def count_open_trades_for_pair(agent, pair: str) -> int:
    want = (pair or "").strip().upper()
    if not want:
        return 0
    return sum(
        1
        for t in (getattr(agent, "trades", None) or [])
        if (t.get("pair") or "").strip().upper() == want
    )


def effective_max_concurrent_trades(agent) -> int:
    """Global user max (AI Instructions). 1m per-pair cap is separate — see concurrent_entry_blocked."""
    return max(1, int(getattr(agent, "max_concurrent_trades", 1) or 1))


def concurrent_entry_blocked(agent, pair: str) -> str | None:
    """Skip reason if a new entry would exceed caps; else None.

    Scalp TFs (1m/5m/30s): cap is **per pair/chart** (ONE_M_MAX_CONCURRENT), not a low UI
    global max — otherwise risk%→max_concurrent=3 blocks all other charts.
    Higher TFs: user global max_concurrent_trades only.
    """
    tf = str(
        SECONDS_TO_TIMEFRAME_KEY.get(getattr(agent, "timeframe_seconds", 60), "1m")
    ).strip().lower()
    open_n = len(getattr(agent, "trades", None) or [])
    user_max = effective_max_concurrent_trades(agent)

    if is_scalp_tf(tf):
        n = count_open_trades_for_pair(agent, pair)
        if n >= ONE_M_MAX_CONCURRENT:
            return (
                f"Max concurrent on {pair} ({ONE_M_MAX_CONCURRENT}/chart) reached "
                f"({n} open on this pair)"
            )
        # Absolute safety only — never let a UI max of 3 freeze multi-chart stacking.
        abs_cap = max(user_max, int(ONE_M_MAX_CONCURRENT) * 40)
        if open_n >= abs_cap:
            return f"Max concurrent trades ({abs_cap}) reached"
        return None

    if open_n >= user_max:
        return f"Max concurrent trades ({user_max}) reached"
    return None


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
        print(f"[SIZE] Skip {trade_pair}: available capital ${available}")
        return None
    leverage = max(float(getattr(agent, "leverage", 1) or 1), 1.0)
    mult = max(1.0, float(size_mult))
    cap_frac = auto_trade_capital_pct_for_agent(agent)
    # TF capital% is target notional; on small balances Bybit min (~$5) is higher —
    # bump up to exchange min when the required MARGIN still fits available cash.
    position_usd = round(float(available) * cap_frac * mult, 2)
    bybit_symbol = get_bybit_symbol(trade_pair)
    decimals = qty_decimals_for_price(entry_price)
    inst = bybit_instruments.get_instrument(bybit_symbol) or {}
    min_notional_rule = float(inst.get("minNotionalValue") or 0) or 0.0
    lot = min_lot_qty(bybit_symbol)
    min_lot_notional = float(lot) * float(entry_price) if lot and lot > 0 else 0.0
    exchange_min = max(min_notional_rule, min_lot_notional)
    max_margin = float(available) * 0.95

    bumped_to_min_lot = False
    if exchange_min > 0 and (position_usd <= 0 or position_usd < exchange_min):
        margin_for_min = exchange_min / leverage
        if margin_for_min > max_margin:
            print(
                f"[SIZE] Skip {trade_pair}: exchange min ${exchange_min:.2f} needs "
                f"margin ${margin_for_min:.4f} > 95% available ${available:.2f}"
            )
            return None
        position_usd = round(exchange_min, 2)
        bumped_to_min_lot = True
        print(
            f"[SIZE] {trade_pair}: TF% ${available * cap_frac * mult:.2f} < min "
            f"${exchange_min:.2f} — bump to min (margin ${margin_for_min:.4f})"
        )

    if position_usd <= 0:
        print(
            f"[SIZE] Skip {trade_pair}: size rounds to $0 "
            f"(avail=${available:.2f} × {cap_frac * 100:g}%)"
        )
        return None

    # Reject only when min order margin exceeds the TF-budget margin ceiling
    # (15% of available as margin → notional can be 15%×leverage).
    max_budget_margin = float(available) * 0.15
    if exchange_min > 0 and (exchange_min / leverage) > max_budget_margin:
        print(
            f"[SIZE] Skip {trade_pair}: min margin ${exchange_min / leverage:.4f} "
            f"> 15% budget margin ${max_budget_margin:.4f} (avail=${available:.2f})"
        )
        return None

    qty, effective_notional = qty_for_notional(
        position_usd,
        entry_price,
        bybit_symbol,
        min_notional=exchange_min,
    )
    if qty is None or qty <= 0:
        print(
            f"[SIZE] Skip {trade_pair}: cannot size qty ≥ min ${exchange_min:.2f} "
            f"at px={entry_price}"
        )
        return None
    if effective_notional > position_usd:
        position_usd = round(effective_notional, 2)
        bumped_to_min_lot = True

    # Final margin sanity
    margin = round(position_usd / leverage, 6)
    if margin > max_margin:
        print(
            f"[SIZE] Skip {trade_pair}: margin ${margin:.4f} > 95% available ${available:.2f}"
        )
        return None

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


async def fetch_forming_candle(
    client: httpx.AsyncClient,
    bybit_symbol: str,
    timeframe_key: str,
) -> dict | None:
    """Latest in-progress kline (Bybit newest-first → rows[0])."""
    interval = TIMEFRAME_KEY_TO_BYBIT_KLINE.get(timeframe_key, "5")
    rows = await fetch_kline_rows(client, bybit_symbol, interval, 2)
    if not rows:
        return None
    return parse_bybit_kline(rows[0])


MIN_CONFIRM_BODY_PCT = float(os.environ.get("MIN_CONFIRM_BODY_PCT", "0.03"))


def _candle_body_confirms_side(side: str, open_px: float, close_px: float) -> bool:
    """LONG needs green tick (price > open); SHORT needs red (price < open).

    Requires minimum body size (default 0.03% gross) — blocks one-tick fake bounces.
    """
    try:
        o = float(open_px)
        c = float(close_px)
    except (TypeError, ValueError):
        return False
    if o <= 0:
        return False
    body_pct = abs(c - o) / o * 100.0
    if body_pct < MIN_CONFIRM_BODY_PCT:
        return False
    if side == "LONG":
        return c > o
    if side == "SHORT":
        return c < o
    return False


async def scan_and_maybe_fire_pair(client: httpx.AsyncClient, pair: str, timeframe_key: str) -> bool:
    """Scan last CLOSED candle for pattern; queue entry then fire on confirm/open.

    1m/5m scalp flow:
      detect + AI → lock → on the NEXT forming candle, fire as soon as live
      price turns green (LONG) or red (SHORT) vs that bar's open — do NOT wait
      for candle close (first tick of matching color is enough).
    Other TFs:
      detect → queue → fire at next candle open (unchanged).
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

        blocked = _pattern_is_trade_skipped(detect)
        if blocked:
            return await _skip_pending(
                pending,
                f"Pattern blocked: {blocked}",
                fire_candle_ms=fire_candle_ms,
            )

        # Scalp (1m/5m/30s): no fire on candle 2/3 after a fire on candle 1 (next earliest = candle 4).
        tf_l = (timeframe_key or "").strip().lower()
        if is_scalp_tf(tf_l):
            earliest = agent.one_m_earliest_next_fire_ms(pair, interval_ms)
            if earliest is not None and fire_candle_ms < earliest:
                return await _skip_pending(
                    pending,
                    f"scalp spacing: next fire after candle gap "
                    f"(earliest fire@{earliest}, attempted@{fire_candle_ms})",
                    fire_candle_ms=fire_candle_ms,
                )

        # Opposite flip (additive only): >0.25% gross → exit old + continue fire;
        # ≤0.25% → keep old + skip new. Path lock/trail/emergency exits unchanged.
        flip = agent.close_opposite_positions_for_flip(
            side, pair, pattern=detect.get("pattern")
        )
        if flip == "blocked":
            return await _skip_pending(
                pending,
                f"Opposite flip blocked: open opposite ≤{FLIP_EXIT_MIN_GROSS_PCT:g}% "
                f"gross — kept old, skipped new {side}",
                fire_candle_ms=fire_candle_ms,
            )
        if flip == "close_failed":
            return await _skip_pending(
                pending,
                f"Opposite flip close failed — kept old, skipped new {side}",
                fire_candle_ms=fire_candle_ms,
            )

        blocked = concurrent_entry_blocked(agent, pair)
        if blocked:
            return await _skip_pending(
                pending,
                blocked,
                fire_candle_ms=fire_candle_ms,
            )
        if agent.daily_target_reached:
            return await _skip_pending(pending, "Daily profit target already reached", fire_candle_ms=fire_candle_ms)
        if ONE_M_FEE_HOLD_ENABLED and getattr(agent, "one_m_fee_hold", False) and is_scalp_tf(timeframe_key):
            return await _skip_pending(
                pending,
                "fee budget hold — new entries paused",
                fire_candle_ms=fire_candle_ms,
            )
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
            return await _skip_pending(
                pending,
                "Size plan failed — coin min lot/notional too large for available balance "
                f"(${agent.get_available_capital():.2f})",
                fire_candle_ms=fire_candle_ms,
            )

        brain_sl = detect.get("sl")
        brain_tp = detect.get("tp")
        tf_key = str(pending.get("timeframe_key") or timeframe_key or "").strip().lower()
        if tf_key == "1m":
            arm_pct = LOSS_PROTECT_PCT_1M
            band_pct = LOSS_BAND_PCT_1M
            lock_pct = PROFIT_HARD_PCT_1M
            hard_1m = True
        else:
            arm_pct = LOSS_PROTECT_PCT
            band_pct = LOSS_BAND_PCT
            clear_pct = LOSS_LOCK_CLEAR_PCT
            lock_pct = PROFIT_LOCK_PCT
            hard_1m = False
        if (
            brain_sl
            and brain_tp
            and float(brain_sl) > 0
            and float(brain_tp) > 0
            and _brain_exit_prices_valid(float(mark_px), side, float(brain_sl), float(brain_tp))
        ):
            sl_price = round(float(brain_sl), price_decimals_for_mark(mark_px))
            tp_price = round(float(brain_tp), price_decimals_for_mark(mark_px))
            exit_label = f"brain SL={sl_price} TP={tp_price}"
        else:
            sl_price, tp_price = agent._fixed_exit_prices(
                float(mark_px), side, loss_pct=arm_pct, profit_pct=lock_pct
            )
            if hard_1m:
                exit_label = (
                    f"1m hard SL −{arm_pct:g}% / TP +{lock_pct:g}% (no trail) "
                    f"SL={sl_price} TP={tp_price}"
                )
            else:
                exit_label = (
                    f"loss lock −{arm_pct:g}% trail +{LOSS_RECOVERY_RETRACE_PCT:g}% "
                    f"in −{arm_pct:g}…−{band_pct:g}% (hard @ −{band_pct:g}%); "
                    f"unlock @−{clear_pct:g}% → profit +{lock_pct:g}% | "
                    f"profit peak-trail arm +{lock_pct:g}%/−{PROFIT_TRAIL_GIVEBACK_PCT:g}% "
                    f"(floor = peak − {PROFIT_TRAIL_GIVEBACK_PCT:g}%) "
                    f"SL={sl_price} TP={tp_price}"
                )
            if brain_sl and brain_tp and float(brain_sl) > 0 and float(brain_tp) > 0:
                exit_label = (
                    f"brain SL/TP invalid for {side} (sl={brain_sl}, tp={brain_tp}) — {exit_label}"
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
            # Chart FIRED neon on the entry candle (next bar open after detect).
            signal_candle_time=int(fire_candle_ms),
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
        if is_scalp_tf(timeframe_key):
            LAST_AUTO_FIRE_CANDLE_MS[pair] = int(fire_candle_ms)
        fire_label = (
            "scalp green/red start"
            if pending.get("mode") in ("confirm_1m", "confirm_scalp")
            else "next-candle open"
        )
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
            brain_chat_summary(detect) + f" → FIRED {side} on {pair} ({fire_label})",
            status="match",
            details={
                "pair": pair,
                "trade_id": trade.get("id"),
                "sl": trade.get("sl_price"),
                "tp": trade.get("tp_price"),
                "exit": exit_label,
                "detect_candle": detect_candle_ms,
                "fire_candle": fire_candle_ms,
                "mode": pending.get("mode"),
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
                "mode": bybit_api.mode,
            }
        )
        print(
            f"[BRAIN] {side} {pair} @ {mark_px} | pattern={detect.get('pattern')} "
            f"detect@{detect_candle_ms} fire@{fire_candle_ms} {exit_label}"
        )
        return True

    # --- 1a) Scalp confirm-lock: fire on green/red tick of forming candle ---
    # 5m: first matching tick. 1m: skip 1st matching tick, fire on 2nd.
    # LONG → live > open (green); SHORT → live < open (red). No candle-close wait.
    pending = PENDING_ENTRY_SIGNALS.get(pair)
    if (
        pending
        and pending.get("timeframe_key") == timeframe_key
        and pending.get("mode") in ("confirm_1m", "confirm_scalp")
    ):
        side = pending.get("side") or "LONG"
        detect = pending.get("detect") or {}
        detect_ms = int(pending.get("signal_candle_time") or 0)
        want = "green" if side == "LONG" else "red"
        max_bars = int(ONE_M_CONFIRM_MAX_BARS)
        tf_l = str(timeframe_key or "").strip().lower()
        is_1m_confirm = tf_l == "1m"
        skip_ticks_needed = int(ONE_M_CONFIRM_SKIP_TICKS) if is_1m_confirm else 0

        # Matched earlier during warmup — fire when trading ready.
        if pending.get("confirm_matched") and int(pending.get("fire_candle_time") or 0) > 0:
            if not agent.trading_ready():
                _push_pattern_neon(
                    pair=pair,
                    candle_time_ms=int(pending["fire_candle_time"]),
                    stage="confirming",
                    side=side,
                    action=detect.get("action"),
                    pattern=detect.get("pattern"),
                    reason=f"Confirm matched · warmup hold {agent.warmup_remaining_sec():.0f}s",
                )
                return False
            return await _execute_queued_fire(pending)

        try:
            forming = await fetch_forming_candle(client, bybit_symbol, timeframe_key)
        except Exception as exc:
            print(f"[BRAIN] scalp confirm forming-candle fail {pair}: {exc}")
            return False
        if not forming:
            return False

        bar_start = int(forming.get("close_time") or 0)
        open_px = float(forming.get("open") or 0)
        # Prefer live WS mark; else forming-bar close; else public ticker.
        live_px = float(agent.mark_price_for(pair) or 0)
        forming_close = float(forming.get("close") or 0)
        if live_px <= 0 and forming_close > 0:
            live_px = forming_close
        if live_px <= 0:
            try:
                tick = await fetch_ticker_last_price(client, bybit_symbol)
                if tick and float(tick) > 0:
                    live_px = float(tick)
            except Exception:
                pass
        if live_px > 0:
            agent.set_pair_mark(pair, live_px)
        if open_px <= 0 or live_px <= 0 or bar_start <= 0:
            return False

        # Still on the detect candle — wait until the NEXT bar opens.
        if bar_start <= detect_ms:
            _push_pattern_neon(
                pair=pair,
                candle_time_ms=detect_ms + interval_ms,
                stage="confirming",
                side=side,
                action=detect.get("action"),
                pattern=detect.get("pattern"),
                reason=f"Lock {side} — wait next bar for {want} start",
            )
            return False

        # How many bars into the confirm window (1 = first bar after detect).
        bars_into = max(1, int((bar_start - detect_ms) // max(interval_ms, 1)))
        pending["confirm_bars_seen"] = bars_into
        pending["last_confirm_candle_ms"] = bar_start

        # Timeout: past max bars with no matching color tick → skip.
        if bars_into > max_bars:
            PENDING_ENTRY_SIGNALS[pair] = pending
            return await _skip_pending(
                pending,
                f"scalp confirm timeout after {max_bars} candles (no {want} start)",
                fire_candle_ms=bar_start,
            )

        matched = _candle_body_confirms_side(side, open_px, live_px)
        if matched:
            skipped = int(pending.get("confirm_match_skips") or 0)
            # 1m: pehla green/red tick skip → dusra pe fire
            if skipped < skip_ticks_needed:
                pending["confirm_match_skips"] = skipped + 1
                PENDING_ENTRY_SIGNALS[pair] = pending
                _push_pattern_neon(
                    pair=pair,
                    candle_time_ms=bar_start,
                    stage="confirming",
                    side=side,
                    action=detect.get("action"),
                    pattern=detect.get("pattern"),
                    reason=f"{want} tick #{skipped + 1} skipped — wait next {want} tick",
                )
                system_log.push_agent_chat(
                    f"CONFIRM {side} on {pair}: 1m skip {want} tick #{skipped + 1}/"
                    f"{skip_ticks_needed} @ live={live_px} open={open_px} "
                    f"(bar {bars_into}/{max_bars}) — wait 2nd tick",
                    status="match",
                    details={
                        "pair": pair,
                        "side": side,
                        "confirm_bar": bar_start,
                        "live": live_px,
                        "open": open_px,
                        "seen": bars_into,
                        "skipped": skipped + 1,
                    },
                )
                print(
                    f"[BRAIN] {side} {pair} 1m skip {want} tick #{skipped + 1}: "
                    f"live={live_px} open={open_px} bar@{bar_start} — wait next"
                )
                return False

            pending["fire_candle_time"] = bar_start
            pending["confirm_matched"] = True
            PENDING_ENTRY_SIGNALS[pair] = pending
            if not agent.trading_ready():
                _push_pattern_neon(
                    pair=pair,
                    candle_time_ms=bar_start,
                    stage="confirming",
                    side=side,
                    action=detect.get("action"),
                    pattern=detect.get("pattern"),
                    reason=f"{want} started · warmup hold",
                )
                system_log.push_agent_chat(
                    f"CONFIRM {side} on {pair}: {want} started @ live={live_px} "
                    f"open={open_px} (bar {bars_into}/{max_bars}) — warmup hold",
                    status="match",
                    details={
                        "pair": pair,
                        "side": side,
                        "confirm_bar": bar_start,
                        "live": live_px,
                        "open": open_px,
                        "seen": bars_into,
                    },
                )
                return False
            tick_note = "2nd tick" if is_1m_confirm else "1st tick"
            _push_pattern_neon(
                pair=pair,
                candle_time_ms=bar_start,
                stage="confirming",
                side=side,
                action=detect.get("action"),
                pattern=detect.get("pattern"),
                reason=f"{want} {tick_note} → firing now",
            )
            system_log.push_agent_chat(
                f"CONFIRM {side} on {pair}: {want} {tick_note} @ live={live_px} "
                f"open={open_px} (bar {bars_into}/{max_bars}) → fire NOW",
                status="match",
                details={
                    "pair": pair,
                    "side": side,
                    "confirm_bar": bar_start,
                    "live": live_px,
                    "open": open_px,
                    "seen": bars_into,
                },
            )
            print(
                f"[BRAIN] {side} {pair} {want}-start confirm ({tick_note}): "
                f"live={live_px} open={open_px} bar@{bar_start} → fire"
            )
            return await _execute_queued_fire(pending)

        # Wrong color / flat — keep lock; poll again next tick (~0.5s).
        PENDING_ENTRY_SIGNALS[pair] = pending
        _push_pattern_neon(
            pair=pair,
            candle_time_ms=bar_start,
            stage="confirming",
            side=side,
            action=detect.get("action"),
            pattern=detect.get("pattern"),
            reason=f"Hold {side} — wait {want} start {bars_into}/{max_bars}",
        )
        return False

    # --- 1b) Non-scalp: fire queued signal once the next candle has OPENed ---
    pending = PENDING_ENTRY_SIGNALS.get(pair)
    if pending and pending.get("timeframe_key") == timeframe_key:
        fire_candle_ms = int(pending["fire_candle_time"])
        now_ms = int(time.time() * 1000)
        deadline_ms = fire_candle_ms + interval_ms + fire_grace_ms
        warmup_hold = not agent.trading_ready()
        # During warmup: keep scanning/queue alive, but do not fire or expire yet.
        if warmup_hold:
            if now_ms >= fire_candle_ms:
                _push_pattern_neon(
                    pair=pair,
                    candle_time_ms=fire_candle_ms,
                    stage="confirming",
                    side=pending.get("side"),
                    action=(pending.get("detect") or {}).get("action"),
                    pattern=(pending.get("detect") or {}).get("pattern"),
                    reason=f"Warmup hold · trades unlock in {agent.warmup_remaining_sec():.0f}s",
                )
        # Expire only after fire candle + grace (scan/AI lag must not kill the entry).
        elif now_ms > deadline_ms:
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

    # Locked confirm on this pair → never run a fresh brain/AI detect underneath.
    existing_lock = PENDING_ENTRY_SIGNALS.get(pair)
    if existing_lock and existing_lock.get("mode") in ("confirm_1m", "confirm_scalp"):
        return False

    # --- 2) Scan only the latest CLOSED candle (forming bar already dropped) ---
    lookback = max(MIN_CANDLES, 100)
    history = await fetch_closed_candle_history(client, bybit_symbol, timeframe_key, limit=lookback)
    if len(history) < MIN_CANDLES:
        return False

    close_time = int(history[-1]["close_time"])  # Bybit startTime of last closed bar
    last_seen = LAST_CANDLE_TIMESTAMPS.get(pair)
    if last_seen is None:
        # First observation after engine arm / TF change: seed only.
        # Never detect/trade on candles that were already closed before this session.
        LAST_CANDLE_TIMESTAMPS[pair] = close_time
        print(
            f"[BRAIN] Baseline seed {pair} @ closed={close_time} — "
            f"wait for NEXT closed candle before detect"
        )
        return False
    if close_time <= int(last_seen):
        return False
    LAST_CANDLE_TIMESTAMPS[pair] = close_time

    _HTF_MAP = {
        "1m": "5m",
        "30s": "5m",
        "5m": "15m",
        "15m": "1h",
        "1h": "1D",
    }
    tf_l = (timeframe_key or "1m").strip().lower()
    # 1D has no weekly HTF wired — skip (brain 1d config allows missing HTF).
    htf_key = None if tf_l == "1d" else _HTF_MAP.get(tf_l)
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

    blocked_pat = _pattern_is_trade_skipped(detect)
    if blocked_pat:
        side = "LONG" if detect["action"] == "BUY" else "SHORT"
        _push_pattern_neon(
            pair=pair,
            candle_time_ms=close_time,
            stage="skipped",
            side=side,
            action=detect.get("action"),
            pattern=detect.get("pattern"),
            reason=f"Pattern blocked: {blocked_pat}",
        )
        system_log.push_agent_chat(
            f"SKIPPED {side} on {pair}: {blocked_pat} (no trade on this pattern)",
            status="no_match",
            details={
                "pair": pair,
                "side": side,
                "pattern": detect.get("pattern"),
                "blocked": blocked_pat,
                "reason": "pattern_blocklist",
            },
        )
        print(f"[BRAIN] SKIP pattern={blocked_pat} on {pair} — no trade")
        return False

    # First valid BUY/SELL after arm: optional skip once per pair (HTF default on; 1m/5m off).
    skip_first_on_scalp = os.environ.get("SKIP_FIRST_DETECT_SCALP", "0").strip().lower() in (
        "1", "true", "yes",
    )
    skip_first_on_htf = os.environ.get("SKIP_FIRST_DETECT", "1").strip().lower() not in (
        "0", "false", "no",
    )
    should_skip_first = (
        (is_scalp_tf(tf_l) and skip_first_on_scalp)
        or (not is_scalp_tf(tf_l) and skip_first_on_htf)
    )
    if should_skip_first and pair not in FIRST_DETECT_SKIPPED:
        FIRST_DETECT_SKIPPED.add(pair)
        side = "LONG" if detect["action"] == "BUY" else "SHORT"
        _push_pattern_neon(
            pair=pair,
            candle_time_ms=close_time,
            stage="skipped",
            side=side,
            action=detect.get("action"),
            pattern=detect.get("pattern"),
            reason="First detect skipped (safety on all charts)",
        )
        system_log.push_agent_chat(
            f"SKIPPED first {side} on {pair}: {detect.get('pattern')} (first trade skip)",
            status="no_match",
            details={
                "pair": pair,
                "side": side,
                "pattern": detect.get("pattern"),
                "reason": "first_detect_skip",
            },
        )
        print(f"[BRAIN] SKIP first detect {side} {pair} pattern={detect.get('pattern')}")
        return False

    candle_close = float(history[-1].get("close") or 0)
    if candle_close > 0:
        agent.set_pair_mark(pair, candle_close)

    side = "LONG" if detect["action"] == "BUY" else "SHORT"
    # Pattern on closed bar N → provisional next-open time (1m confirm overrides at match).
    fire_candle_ms = close_time + interval_ms

    # Soft capacity checks at queue time (re-checked at fire).
    blocked = concurrent_entry_blocked(agent, pair)
    if blocked:
        _push_pattern_neon(
            pair=pair,
            candle_time_ms=close_time,
            stage="skipped",
            side=side,
            action=detect.get("action"),
            pattern=detect.get("pattern"),
            reason=blocked if len(blocked) <= 48 else "Max concurrent on this chart",
        )
        return False
    if agent.daily_target_reached:
        return False
    if ONE_M_FEE_HOLD_ENABLED and getattr(agent, "one_m_fee_hold", False) and is_scalp_tf(tf_l):
        _push_pattern_neon(
            pair=pair,
            candle_time_ms=close_time,
            stage="skipped",
            side=side,
            action=detect.get("action"),
            pattern=detect.get("pattern"),
            reason="fee budget hold",
        )
        return False
    if not agent.has_same_side_auto_capacity(side, pair):
        return False
    if agent.has_duplicate_auto_entry(
        side, pair, detect.get("pattern"), close_time, candle_close or float(detect.get("entry") or 0)
    ):
        return False

    # Mid-wait: keep existing lock; do not replace.
    existing = PENDING_ENTRY_SIGNALS.get(pair)
    if existing:
        return False

    detect = dict(detect)
    use_confirm_scalp = is_scalp_tf(tf_l)
    is_1m_lock = tf_l == "1m"
    want = "green" if side == "LONG" else "red"
    PENDING_ENTRY_SIGNALS[pair] = {
        "detect": detect,
        "side": side,
        "signal_candle_time": close_time,
        "fire_candle_time": fire_candle_ms,
        "timeframe_key": timeframe_key,
        "detect_close": candle_close,
        "queued_at": time.time(),
        "mode": "confirm_scalp" if use_confirm_scalp else "next_open",
        "confirm_bars_seen": 0,
        "last_confirm_candle_ms": close_time,
        "confirm_matched": False,
        "confirm_match_skips": 0,
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
    if use_confirm_scalp:
        wait_reason = (
            f"Lock {side} — skip 1st {want} tick, fire on 2nd"
            if is_1m_lock
            else f"Lock {side} — wait {want} start (live tick)"
        )
        _push_pattern_neon(
            pair=pair,
            candle_time_ms=fire_candle_ms,
            stage="confirming",
            side=side,
            action=detect.get("action"),
            pattern=detect.get("pattern"),
            reason=wait_reason,
        )
        if is_1m_lock:
            system_log.push_agent_chat(
                f"LOCKED {side} on {pair}: {detect.get('pattern')} — skip 1st {want} tick, "
                f"fire on 2nd (max {ONE_M_CONFIRM_MAX_BARS} bars) | AI={detect.get('ai_confirmation', 'SKIP')}",
                status="detect",
                details={
                    "pair": pair,
                    "side": side,
                    "mode": "confirm_scalp",
                    "max_bars": ONE_M_CONFIRM_MAX_BARS,
                    "skip_ticks": ONE_M_CONFIRM_SKIP_TICKS,
                },
            )
            print(
                f"[BRAIN] LOCKED {side} on {pair}: {detect.get('pattern')} — "
                f"1m skip 1st {want} tick, fire on 2nd "
                f"(max {ONE_M_CONFIRM_MAX_BARS} bars) | AI={detect.get('ai_confirmation', 'SKIP')}"
            )
        else:
            system_log.push_agent_chat(
                f"LOCKED {side} on {pair}: {detect.get('pattern')} — fire on first {want} tick "
                f"(max {ONE_M_CONFIRM_MAX_BARS} bars, no close wait) | AI={detect.get('ai_confirmation', 'SKIP')}",
                status="detect",
                details={
                    "pair": pair,
                    "side": side,
                    "mode": "confirm_scalp",
                    "max_bars": ONE_M_CONFIRM_MAX_BARS,
                },
            )
            print(
                f"[BRAIN] LOCKED {side} on {pair}: {detect.get('pattern')} — fire on first {want} tick "
                f"(max {ONE_M_CONFIRM_MAX_BARS} bars, no close wait) | AI={detect.get('ai_confirmation', 'SKIP')}"
            )
        return False

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
        f"on closed@{close_time} → queue fire@{fire_candle_ms} "
        f"(AI={detect.get('ai_confirmation', 'SKIP')})"
    )

    # AI confirm already applied in evaluate_live_entry_async (NO → never reaches here).

    # If next candle already opened while we scanned, fire now (non-1m only).
    now_ms = int(time.time() * 1000)
    pending = PENDING_ENTRY_SIGNALS.get(pair)
    if pending and now_ms >= fire_candle_ms:
        if not agent.trading_ready():
            _push_pattern_neon(
                pair=pair,
                candle_time_ms=fire_candle_ms,
                stage="confirming",
                side=pending.get("side"),
                action=(pending.get("detect") or {}).get("action"),
                pattern=(pending.get("detect") or {}).get("pattern"),
                reason=f"Warmup hold · trades unlock in {agent.warmup_remaining_sec():.0f}s",
            )
            return False
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
                poll = 0.5  # same fast scan cadence as 1m on every TF
                if agent.is_active and not agent.emergency_triggered:
                    agent.refresh_feed_health()
                    agent.refresh_one_m_fee_budget()
                    await maybe_refresh_momentum_every_n_candles(client, timeframe_key)
                    # Warmup: scan + detect + queue immediately; fire gated inside scan_and_maybe_fire_pair.
                    # Feed-stale freeze: pause NEW detects only; keep managing open + pending fires.
                    frozen = bool(agent.connectivity_frozen) or (
                        ONE_M_FEE_HOLD_ENABLED
                        and getattr(agent, "one_m_fee_hold", False)
                        and is_scalp_tf(timeframe_key)
                    )
                    fire_pairs = list(agent.get_scan_pairs())
                    pending_keys = [
                        p
                        for p, pend in PENDING_ENTRY_SIGNALS.items()
                        if pend.get("timeframe_key") == timeframe_key
                    ]
                    confirm_locks = [
                        p
                        for p, pend in PENDING_ENTRY_SIGNALS.items()
                        if pend.get("mode") in ("confirm_1m", "confirm_scalp")
                        and pend.get("timeframe_key") == timeframe_key
                    ]
                    # While a 1m pattern is locked for body confirm, pause all other pair scans.
                    if confirm_locks:
                        scan_list = list(dict.fromkeys(confirm_locks))
                    else:
                        pending_first = [p for p in pending_keys if p in PENDING_ENTRY_SIGNALS]
                        rest = [] if frozen else [p for p in fire_pairs if p not in PENDING_ENTRY_SIGNALS]
                        scan_list = pending_first + rest
                    for pair in scan_list:
                        try:
                            await scan_and_maybe_fire_pair(client, pair, timeframe_key)
                        except Exception as exc:
                            print(f"[SCAN] error {pair}: {exc}")
                    agent.persist_runtime()
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
                agent.note_market_feed()

            await asyncio.sleep(_AUTO_BUY_TICKER_POLL)


async def bybit_balance_refresher():
    """Keep LIVE equity + open-position book synced with Bybit every few seconds."""
    while True:
        try:
            if bybit_api.mode == "LIVE_TRADING" and bybit_api.connected:
                await bybit_api.fetch_real_balance()
                n = agent.reconcile_live_positions()
                if n:
                    print(f"[RECONCILE] Settled {n} phantom local open(s) against Bybit.")
        except Exception as exc:
            print(f"[BYBIT] balance/reconcile loop note: {exc}")
        await asyncio.sleep(3)


KEEPALIVE_INTERVAL_SECONDS = 5 * 60


async def _ping_health(client: httpx.AsyncClient, self_url: str) -> bool:
    try:
        resp = await client.get(f"{self_url}/health")
        print(f"[KEEPALIVE] Self-ping OK (HTTP {resp.status_code}) — /health only, no trades touched.")
        return True
    except Exception as exc:
        print(f"[KEEPALIVE] Self-ping failed ({exc}) — will retry next interval.")
        return False


async def self_ping_keepalive():
    """Keep process/proxy warm. Engine trading does NOT depend on any browser tab.

    Prefer RENDER_EXTERNAL_URL (PaaS), else loopback on the container port.
    """
    self_url = (os.environ.get("RENDER_EXTERNAL_URL") or "").rstrip("/")
    if not self_url:
        port = (os.environ.get("PORT") or "8000").strip() or "8000"
        self_url = f"http://127.0.0.1:{port}"

    interval = int(os.environ.get("KEEPALIVE_INTERVAL_SECONDS", str(KEEPALIVE_INTERVAL_SECONDS)))
    print(
        f"[KEEPALIVE] Pinging {self_url}/health every {interval // 60} minutes "
        f"(read-only — browser optional; engine keeps running headless)."
    )
    async with httpx.AsyncClient(timeout=15.0) as client:
        await _ping_health(client, self_url)
        while True:
            await asyncio.sleep(interval)
            await _ping_health(client, self_url)
            if agent.is_active:
                print(
                    f"[HEADLESS] Engine ON · open={len(agent.trades)} · "
                    f"scan={agent.get_scan_pairs()} · "
                    f"fee_hold={bool(getattr(agent, 'one_m_fee_hold', False))} · "
                    f"(no browser required)"
                )


async def auto_exit_watchdog():
    """Re-check path-SL / path-TP even if a ticker tick was skipped or failed."""
    print(
        f"[AUTO-EXIT] Watchdog online "
        f"(1m hard TP +{PROFIT_HARD_PCT_1M:g}% / SL −{LOSS_PROTECT_PCT_1M:g}% no trail | "
        f"else profit arm +{PROFIT_LOCK_PCT:g}% peak-trail −{PROFIT_TRAIL_GIVEBACK_PCT:g}% | "
        f"else loss lock −{LOSS_PROTECT_PCT:g}% trail +{LOSS_RECOVERY_RETRACE_PCT:g}% "
        f"in −{LOSS_PROTECT_PCT:g}…−{LOSS_BAND_PCT:g}% (hard @ −{LOSS_BAND_PCT:g}%); "
        f"unlock @−{LOSS_LOCK_CLEAR_PCT:g}%)."
    )
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
    try:
        restored = restore_runtime(agent)
        if restored.get("restored"):
            # Re-evaluate fee hold against closed book (clears false sticky pauses).
            try:
                agent.refresh_one_m_fee_budget()
            except Exception as fee_exc:
                print(f"[FEE BUDGET] post-restore refresh note: {fee_exc}")
            agent.persist_runtime(force=True)
            system_log.push(
                "ai",
                "Engine runtime restored after restart — continuing until user stops.",
                restored,
            )
            notifications.push(
                f"Engine restored: {'ON' if restored.get('is_active') else 'OFF'} · "
                f"{restored.get('trades', 0)} open trade(s).",
                "info",
            )
    except Exception as exc:
        print(f"[ENGINE RUNTIME] startup restore note: {exc}")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            inst = await bybit_instruments.ensure_instruments(client)
            print(
                f"[INSTRUMENTS] Startup: ok={inst.get('ok')} "
                f"count={inst.get('count')} cached={inst.get('cached')}"
            )
    except Exception as exc:
        print(f"[INSTRUMENTS] startup note: {exc}")
    # Restore mode from header preference (live_trading flag), NOT from keys alone.
    # Keys are required for LIVE; mode is chosen via LIVE/PAPER button → /trading-mode.
    try:
        want_live = bool(settings_store.live_trading_preferred) and settings_store.is_bybit_configured()
        if want_live:
            bybit_api.mode = "LIVE_TRADING"
            bybit_api.connected = True
            # Wipe paper capital — LIVE mode uses Bybit balance exclusively.
            agent.current_capital = 0.0
            agent.starting_capital = 0.0
            print(
                f"[SETTINGS] Restoring LIVE_TRADING "
                f"({settings_store.bybit_environment}) — header preference + Bybit keys. "
                f"Paper capital wiped — using Bybit balance only."
            )
            notifications.push(
                f"LIVE trading resumed ({settings_store.bybit_environment}). "
                f"Switch via header Live/Paper button anytime (engine off).",
                "warning",
            )
            try:
                equity = await bybit_api.fetch_real_balance()
                if equity is not None:
                    agent.current_capital = float(equity)
                    if not agent.starting_capital:
                        agent.starting_capital = float(equity)
                    print(f"[SETTINGS] Live Bybit equity synced: ${equity:,.2f}")
            except Exception as exc:
                print(f"[SETTINGS] Initial Bybit equity sync note: {exc}")

            async def _startup_reconcile():
                await asyncio.sleep(1.0)
                try:
                    await bybit_api.fetch_real_balance()
                    n = agent.reconcile_live_positions()
                    if n:
                        print(f"[RECONCILE] Startup cleared {n} phantom open(s).")
                except Exception as exc:
                    print(f"[RECONCILE] startup note: {exc}")

            asyncio.create_task(_startup_reconcile())
        else:
            bybit_api.mode = "PAPER_TRADING"
            bybit_api.connected = False
            if settings_store.live_trading_preferred and not settings_store.is_bybit_configured():
                settings_store.live_trading_preferred = False
                _persist_live_trading(False)
                print("[SETTINGS] LIVE preferred but no Bybit keys — forced PAPER.")
            if agent.current_capital <= 0:
                agent.starting_capital = 100_000.0
                agent.current_capital = agent.starting_capital
                print("[SETTINGS] PAPER mode (capital seeded $100,000). Use header button for LIVE.")
            else:
                keys_note = "keys on disk" if settings_store.is_bybit_configured() else "no keys"
                print(
                    f"[SETTINGS] PAPER mode ({keys_note}, restored capital). "
                    f"Header button controls LIVE."
                )
    except Exception as exc:
        print(f"[SETTINGS] Bybit restore note: {exc}")
    asyncio.create_task(market_simulator())
    asyncio.create_task(bybit_price_feed())
    asyncio.create_task(bybit_balance_refresher())
    asyncio.create_task(self_ping_keepalive())
    asyncio.create_task(auto_buy_loop())
    asyncio.create_task(auto_exit_watchdog())
    asyncio.create_task(momentum_universe_timer_loop())
    asyncio.create_task(engine_hourly_restart_loop())
    asyncio.create_task(chart_24h_refresh_loop(BYBIT_SYMBOL_MAP))
    asyncio.create_task(session_schedule_loop())
    asyncio.create_task(engine_runtime_checkpoint_loop())


async def engine_runtime_checkpoint_loop():
    """Periodic checkpoint so a crash mid-session still restores open book."""
    while True:
        try:
            if agent.is_active or agent.trades or agent.session_hold_mode:
                agent.persist_runtime()
        except Exception as exc:
            print(f"[ENGINE RUNTIME] checkpoint note: {exc}")
        await asyncio.sleep(15)


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
    agent.one_m_fee_hold = False
    agent.begin_ai_season()
    agent.is_active = True
    agent.connectivity_frozen = False
    agent.freeze_reason = None
    agent._ai_fail_streak = 0
    agent._last_feed_ts = time.time()
    agent.begin_trading_warmup()
    system_log.push_agent_chat(
        "AI Engine ON — runs on VPS headless (browser optional). Close tab safely; trading continues until you press STOP.",
        status="ok",
    )
    # First pass: filter all mapped coins by MARKET avg% before any new fires.
    try:
        await apply_momentum_watchlist_refresh(reason="bot_start")
        bybit_symbol = get_bybit_symbol(agent.active_pair)
        if bybit_symbol:
            async with httpx.AsyncClient(timeout=12.0) as client:
                hist = await fetch_closed_candle_history(
                    client,
                    bybit_symbol,
                    SECONDS_TO_TIMEFRAME_KEY.get(agent.timeframe_seconds, "1m"),
                    limit=3,
                )
                if hist:
                    agent.last_momentum_candle_ms = int(hist[-1]["close_time"])
    except Exception as exc:
        print(f"[MOMENTUM] start refresh failed: {exc}")
        agent.momentum_gate_ready = True
        agent.momentum_fire_pairs = list(agent.watchlist or ([agent.active_pair] if agent.active_pair else []))
    agent.persist_runtime(force=True)
    tf_key = SECONDS_TO_TIMEFRAME_KEY.get(agent.timeframe_seconds, "1m")
    profile = entry_pattern_profile(tf_key)
    fire_n = len(getattr(agent, "momentum_fire_pairs", None) or [])
    thr = float(getattr(agent, "momentum_threshold_pct", 0) or 0)
    print(f"[AI ENGINE] START — {agent.active_pair} ({tf_key}) brain.py · momentum fire={fire_n} thr>{thr:g}%")
    refresh_min = max(1, int(ENGINE_HOURLY_RESTART_SEC if is_scalp_tf(tf_key) else MOMENTUM_REFRESH_EVERY_SECONDS) // 60)
    system_log.push(
        "ai",
        f"AI Engine STARTED on {agent.active_pair} ({open_count} open preserved). "
        f"Momentum watchlist: {fire_n} pair(s) above {thr:g}% on {tf_key}. "
        f"Soft universe restart every {refresh_min} min "
        f"(open trades held).",
        {
            "open_positions": open_count,
            "timeframe_seconds": agent.timeframe_seconds,
            "warmup_sec": ENGINE_WARMUP_SEC,
            "momentum_fire": fire_n,
            "momentum_threshold": thr,
            "soft_restart_min": refresh_min,
        },
    )
    system_log.push_agent_chat(
        f"AI Engine ON · momentum thr>{thr:g}% · fire {fire_n} · {profile['name']} · {agent.active_pair} · {tf_key}",
        status="active",
        details={
            "pair": agent.active_pair,
            "timeframe": tf_key,
            "entry_pattern": profile,
            "warmup_sec": ENGINE_WARMUP_SEC,
            "momentum_fire_pairs": list(getattr(agent, "momentum_fire_pairs", None) or []),
            "momentum_threshold_pct": thr,
        },
    )
    notifications.push(
        f"AI Engine STARTED — momentum filter active (>{thr:g}% · {fire_n} coins).",
        "success",
    )
    return {
        "status": "success",
        "is_active": True,
        "message": (
            f"AI Engine started — momentum thr>{thr:g}% · {fire_n} fire pair(s) "
            f"({profile['name']}, {tf_key})."
        ),
        "open_positions": open_count,
        "warmup_sec": ENGINE_WARMUP_SEC,
        "boot_intro_sec": ENGINE_BOOT_INTRO_SEC,
        "boot_analysis_sec": ENGINE_BOOT_ANALYSIS_SEC,
        "trading_ready_at": agent.trading_ready_at,
        "boot_ui_until": getattr(agent, "boot_ui_until", 0),
        "entry_pattern": profile["name"],
        "session_schedule_enabled": False,
        "momentum_threshold_pct": thr,
        "momentum_fire_pairs": list(getattr(agent, "momentum_fire_pairs", None) or []),
        "watchlist": list(agent.watchlist),
    }


@app.post("/bot/stop")
async def bot_stop(payload: BotStopPayload | None = None):
    """AI Engine STOP — explicit user action only (browser close must NEVER call this).

    Body: ``{"mode": "hold"|"emergency"}``
      - hold: stop new entries; keep open trades (path SL / TP still auto-exit); portfolio keeps updating
      - emergency: close all positions and freeze/clear session book
    No open trades → clean halt (end season).
    """
    mode = str((payload.mode if payload else "hold") or "hold").strip().lower()
    if mode not in ("hold", "emergency"):
        mode = "hold"
    print(f"[AI ENGINE] STOP requested mode={mode} (explicit API — not browser disconnect)")

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
        agent.persist_runtime(force=True)
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
        agent.connectivity_frozen = False
        agent.freeze_reason = None
        agent.persist_runtime(force=True)
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
    """Lightweight poll for AI Engine active flag (use on page load — WS may lag)."""
    tf_key = SECONDS_TO_TIMEFRAME_KEY.get(agent.timeframe_seconds, "1m")
    return {
        "status": "success",
        "is_active": bool(agent.is_active),
        "session_hold_mode": bool(agent.session_hold_mode),
        "one_m_fee_hold": bool(getattr(agent, "one_m_fee_hold", False)),
        "connectivity_frozen": bool(agent.connectivity_frozen),
        "open_positions": len(agent.trades),
        "pair": agent.active_pair,
        "watchlist": list(agent.watchlist or []),
        "scan_pairs": agent.get_scan_pairs(),
        "timeframe_seconds": int(agent.timeframe_seconds or 60),
        "timeframe": tf_key,
        "fee_structure": bybit_api.fee_structure_dict(),
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
    """Legacy endpoint — prefer POST /trading-mode {mode: LIVE_TRADING} from header button."""
    if not settings_store.is_bybit_configured():
        return {
            "status": "error",
            "message": "Add Bybit API keys in Settings before enabling LIVE TRADING.",
            "trading_mode": bybit_api.mode,
        }
    print("[PILLAR 2: BACKEND] Switching from Paper Trading to Live Real Trading...")
    reset_bybit_executor_agent()
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


class TradingModePayload(BaseModel):
    mode: str  # PAPER_TRADING | LIVE_TRADING


@app.post("/trading-mode")
async def set_trading_mode(payload: TradingModePayload):
    """Header Live/Paper swap. Blocked while AI Engine is ON or open positions exist."""
    want = (payload.mode or "").strip().upper()
    if want not in ("PAPER_TRADING", "LIVE_TRADING"):
        return {
            "status": "error",
            "message": "mode must be PAPER_TRADING or LIVE_TRADING",
            "trading_mode": bybit_api.mode,
        }

    if agent.is_active:
        return {
            "status": "error",
            "message": "Stop AI Engine before switching Live / Paper trading.",
            "trading_mode": bybit_api.mode,
            "engine_active": True,
        }
    if agent.trades:
        return {
            "status": "error",
            "message": "Close all open positions before switching Live / Paper trading.",
            "trading_mode": bybit_api.mode,
            "open_trades": len(agent.trades),
        }
    if agent.emergency_triggered:
        return {
            "status": "error",
            "message": "Clear emergency halt before switching trading mode.",
            "trading_mode": bybit_api.mode,
        }

    if want == bybit_api.mode:
        return {
            "status": "success",
            "message": f"Already on {want}.",
            "trading_mode": bybit_api.mode,
            "unchanged": True,
        }

    if want == "PAPER_TRADING":
        reset_bybit_executor_agent()
        bybit_api.disconnect_real_api(reason="User switched to Paper Trading")
        # Keep existing paper capital if set; otherwise seed a safe default.
        if float(agent.current_capital or 0) < 100:
            agent.set_paper_capital(100000.0)
        notifications.push("Switched to PAPER TRADING (simulated capital).", "success")
        system_log.push(
            "ai",
            "Trading mode → PAPER_TRADING",
            {"mode": "PAPER_TRADING", "capital": float(agent.current_capital or 0)},
        )
        return {
            "status": "success",
            "message": "PAPER TRADING active — virtual capital, no real orders.",
            "trading_mode": "PAPER_TRADING",
            "capital": round(float(agent.current_capital or 0), 2),
        }

    # LIVE_TRADING
    if not settings_store.is_bybit_configured():
        return {
            "status": "error",
            "message": "Add Bybit API keys in Settings before enabling LIVE TRADING.",
            "trading_mode": bybit_api.mode,
            "needs_keys": True,
        }
    reset_bybit_executor_agent()
    bybit_api.connect_real_api()
    equity = await bybit_api.fetch_real_balance()
    if equity is not None:
        agent.on_live_connected(equity)
    system_log.push(
        "ai",
        "Trading mode → LIVE_TRADING",
        {"mode": "LIVE_TRADING", "equity": equity},
    )
    if equity is None:
        return {
            "status": "success",
            "message": (
                "LIVE TRADING enabled, but balance sync failed. "
                f"{bybit_api.last_error or 'Check keys / network.'}"
            ),
            "trading_mode": "LIVE_TRADING",
            "equity": None,
        }
    return {
        "status": "success",
        "message": f"LIVE TRADING active — Bybit equity ${equity:,.2f}.",
        "trading_mode": "LIVE_TRADING",
        "equity": equity,
    }

# ==========================================
# SINGLE-COIN, MULTI-TRADE WIRING
# ==========================================
class OpenTradePayload(BaseModel):
    side: str = "LONG"
    pair: str | None = None


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
    """Manual BUY/SELL: open LONG or SHORT on the chart-selected pair (1% margin / 100x).

    Works whether Main AI / Session Momentum is ON or OFF. Manual positions are protected.
    """
    side = payload.side.upper() if payload.side.upper() in ("LONG", "SHORT") else "LONG"
    if agent.emergency_triggered:
        return {"status": "error", "message": "Cannot open a position - emergency halt is active."}
    if len(agent.trades) >= agent.max_concurrent_trades:
        return {"status": "error", "message": f"Max concurrent trades ({agent.max_concurrent_trades}) reached."}

    pair = (payload.pair or agent.active_pair or "").strip()
    if not pair:
        return {"status": "error", "message": "No chart pair selected."}

    # Prefer live mark for the requested chart coin
    live_price = await fetch_bybit_linear_price(pair)
    if live_price is not None and live_price > 0:
        agent.set_pair_mark(pair, float(live_price))
        if pair != agent.active_pair:
            agent.set_active_pair(pair, float(live_price))
        else:
            agent.current_price = float(live_price)
    elif pair != agent.active_pair:
        # Still focus the pair even if ticker briefly fails — open_trade uses mark/fallback
        seed = agent.mark_price_for(pair) or agent.current_price or 0.0
        if seed and seed > 0:
            agent.set_active_pair(pair, float(seed))

    label = "BUY (LONG)" if side == "LONG" else "SELL (SHORT)"
    trade = agent.open_trade(
        side,
        reason=f"Manual {label} · {pair}",
        source="manual",
        pair=pair,
    )
    if trade is None:
        reason = agent.last_open_skip_reason or "Could not open a manual position."
        if agent._live_insufficient_balance():
            return {"status": "error", "message": "Insufficient balance on your Bybit account."}
        return {"status": "error", "message": reason}
    return {
        "status": "success",
        "message": f"Manual {side} filled on {pair}.",
        "trade": trade,
        "pair": pair,
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
    """Force-closes one open position (trash icon). Trailing/profit lock never blocks this."""
    if not payload.confirmed:
        return {"status": "error", "message": "Force close requires explicit confirmation."}
    trade = next((t for t in agent.trades if t["id"] == payload.id), None)
    if not trade:
        return {"status": "error", "message": "Trade not found or already closed."}

    m = agent._trade_metrics(trade)
    # Manual force-close bypasses trail hold — close runs even while status is "locked".
    if not agent._close_single_trade(trade, m, "Manual force-close"):
        detail = (getattr(agent, "last_close_error", None) or "").strip()
        # Keep message short for alerts; full Bybit retMsg is in detail / notifications.
        short = detail
        if "retMsg=" in detail:
            short = detail.split("retMsg=", 1)[1].split(" | ", 1)[0].strip()
        elif len(detail) > 180:
            short = detail[:177] + "..."
        return {
            "status": "error",
            "message": short or "Could not close position on Bybit — see notifications.",
            "detail": detail or None,
        }
    agent.trades = [t for t in agent.trades if t["id"] != payload.id]
    agent._sync_agent_trailing_lock_state()
    agent.persist_runtime(force=True)
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

@app.get("/markets")
async def get_markets():
    """Return dynamic Bybit instruments symbol map for frontend chart resolution.

    Merges hardcoded BYBIT_SYMBOL_MAP with the live instruments cache so new
    watchlist coins (TAC, BLESS, …) resolve to correct Bybit symbols.
    """
    dyn = bybit_instruments.symbol_map_for_momentum()
    merged = dict(BYBIT_SYMBOL_MAP)
    for coin, sym in (dyn or {}).items():
        if coin not in merged:
            merged[coin] = sym
    return {
        "status": "success",
        "symbol_map": merged,
        "count": len(merged),
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
    tf_key = SECONDS_TO_TIMEFRAME_KEY.get(agent.timeframe_seconds, "1m")
    return {
        "profiles": {k: get_timeframe_profile(k) for k in ("1m", "5m", "15m", "1h", "1D")},
        "active": get_timeframe_profile(tf_key),
        "timeframe": tf_key,
        "timeframe_seconds": int(agent.timeframe_seconds or 60),
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
    reset_bybit_executor_agent()
    # Log only that credentials were updated - never the raw values
    print(f"[SETTINGS] Bybit credentials {'updated' if payload.bybit_api_key else 'unchanged'} "
          f"(env={settings_store.bybit_environment}). AI provider set to '{settings_store.ai_provider}'.")

    # Mode is controlled by header Live/Paper button (/trading-mode), not by saving keys.
    # Saving keys only enables LIVE as an option; removing keys forces PAPER.
    if not settings_store.is_bybit_configured():
        bybit_api.disconnect_real_api(reason="No Bybit keys saved")
        return {
            "status": "success",
            "trading_mode": "PAPER_TRADING",
            "message": (
                "Settings saved. No Bybit keys → PAPER mode. "
                "Add keys, then use the header Live/Paper button to go LIVE."
            ),
        }

    if bybit_api.mode == "LIVE_TRADING":
        equity = await bybit_api.fetch_real_balance()
        return {
            "status": "success",
            "trading_mode": "LIVE_TRADING",
            "equity": equity,
            "message": (
                f"Settings saved. LIVE trading ({settings_store.bybit_environment}). "
                f"Balance: ${equity:,.2f}." if equity is not None else
                "Settings saved. LIVE trading active (balance sync pending)."
            ),
        }

    return {
        "status": "success",
        "trading_mode": "PAPER_TRADING",
        "message": (
            f"Settings saved. Bybit keys stored ({settings_store.bybit_environment}). "
            "Still on PAPER — use the header Live/Paper button to switch to LIVE."
        ),
    }

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
    reset_bybit_executor_agent()
    bybit_api.disconnect_real_api(reason="Settings reset — keys removed")
    print("[SETTINGS] All stored Bybit & AI settings have been reset (disk cleared). PAPER trading mode.")
    return {
        "status": "success",
        "trading_mode": "PAPER_TRADING",
        "message": "Bybit keys removed → PAPER trading mode. Add keys again anytime to go LIVE.",
    }

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
            # OPEN POSITIONS / TRADE VALUE always reflect the real local open book (all pairs),
            # then Bybit reconcile keeps that book honest against the exchange.
            season_live = agent.ai_season_start_capital is not None and not agent.session_stats_frozen
            if season_live:
                open_book = list(agent.trades)
                trade_notional = sum(float(t.get("position_size") or 0) for t in open_book)
                open_positions = len(open_book)
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
                # Even when season counters are frozen, never hide a still-open local trade.
                open_book = list(agent.trades)
                trade_notional = (
                    sum(float(t.get("position_size") or 0) for t in open_book)
                    if open_book
                    else float(snap.get("trade_notional") or 0)
                )
                open_positions = len(open_book) if open_book else int(snap.get("open_positions") or 0)
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

            # Heal stuck boot UI: engine already has a universe → never keep SCAN overlay open.
            if agent.is_active and not bool(getattr(agent, "momentum_gate_ready", False)):
                if agent.watchlist or getattr(agent, "momentum_fire_pairs", None):
                    agent.momentum_gate_ready = True
                    agent.boot_ui_until = 0.0
                    agent.momentum_scan_stage = "ready"
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
                "fee_structure": bybit_api.fee_structure_dict(),
                "exited_booked_usd": round(exited_booked_usd, 2),
                "ai_season_profit": round(ai_season_profit, 2),
                "ai_season_profit_pct": round(ai_season_profit_pct, 2),
                "ai_season_profit_net": round(ai_season_profit_net, 2),
                "ai_season_profit_net_pct": round(ai_season_profit_net_pct, 2),
                "ai_season_active": season_active,
                "session_stats_frozen": bool(agent.session_stats_frozen),
                "session_hold_mode": bool(agent.session_hold_mode),
                "connectivity_frozen": bool(agent.connectivity_frozen),
                "freeze_reason": agent.freeze_reason,
                "trading_ready": bool(agent.trading_ready()),
                "warmup_remaining_sec": round(agent.warmup_remaining_sec(), 1),
                "warmup_total_sec": ENGINE_BOOT_MAX_SEC,
                "boot_intro_sec": ENGINE_BOOT_INTRO_SEC,
                "boot_analysis_sec": ENGINE_BOOT_ANALYSIS_SEC,
                "one_m_fee_hold": bool(getattr(agent, "one_m_fee_hold", False)),
                "momentum_gate_ready": bool(getattr(agent, "momentum_gate_ready", False)),
                "momentum_threshold_pct": float(getattr(agent, "momentum_threshold_pct", 0) or 0),
                "momentum_fire_pairs": list(getattr(agent, "momentum_fire_pairs", None) or []),
                "momentum_scores": list(getattr(agent, "momentum_scores", None) or [])[:24],
                "momentum_last_refresh_ms": int(getattr(agent, "momentum_last_refresh_ms", 0) or 0),
                "momentum_scan_done": int(getattr(agent, "momentum_scan_done", 0) or 0),
                "momentum_scan_total": int(getattr(agent, "momentum_scan_total", 0) or 0),
                "momentum_scan_stage": str(getattr(agent, "momentum_scan_stage", "") or ""),
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
                "pattern_neon": get_pattern_neon_snapshot(),
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