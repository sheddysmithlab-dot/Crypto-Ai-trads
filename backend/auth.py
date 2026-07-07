"""Session auth for the trading dashboard. Credentials live only in env vars."""
from __future__ import annotations

import os
import secrets
import time
from typing import Optional

from fastapi import WebSocket

SESSION_TTL_SECONDS = 12 * 3600
TOKEN_BYTES = 32

_sessions: dict[str, dict] = {}


def _env_username() -> str:
    return (os.environ.get("AUTH_USERNAME") or "").strip()


def _env_password() -> str:
    return os.environ.get("AUTH_PASSWORD") or ""


def auth_is_configured() -> bool:
    return bool(_env_username() and _env_password())


def verify_credentials(username: str, password: str) -> bool:
    expected_user = _env_username()
    expected_pass = _env_password()
    if not expected_user or not expected_pass:
        return False
    user_ok = secrets.compare_digest((username or "").strip(), expected_user)
    pass_ok = secrets.compare_digest(password or "", expected_pass)
    return user_ok and pass_ok


def _purge_expired_sessions() -> None:
    now = time.time()
    expired = [token for token, meta in _sessions.items() if meta["expires_at"] <= now]
    for token in expired:
        _sessions.pop(token, None)


def create_session(username: str) -> str:
    _purge_expired_sessions()
    token = secrets.token_urlsafe(TOKEN_BYTES)
    _sessions[token] = {
        "username": username,
        "expires_at": time.time() + SESSION_TTL_SECONDS,
    }
    return token


def verify_token(token: Optional[str]) -> bool:
    if not token:
        return False
    _purge_expired_sessions()
    meta = _sessions.get(token)
    if not meta:
        return False
    if meta["expires_at"] <= time.time():
        _sessions.pop(token, None)
        return False
    return True


def get_session_username(token: Optional[str]) -> Optional[str]:
    if not verify_token(token):
        return None
    return _sessions[token]["username"]


def revoke_token(token: Optional[str]) -> None:
    if token:
        _sessions.pop(token, None)


def extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


async def require_ws_token(websocket: WebSocket) -> bool:
    return verify_token(websocket.query_params.get("token"))
