"""Z.ai + TAAPI credentials for the trading backend.

Resolution order (same idea as auth.py):
  1. Environment variable (Render dashboard / shell)
  2. backend/.env via python-dotenv
  3. Built-in defaults so the dashboard works without manual Render env setup

Secrets are never logged or returned to the frontend.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(_ENV_PATH)

# Defaults when env / .env are unset (override on Render via env vars if needed).
DEFAULT_ZAI_API_KEY = "50ab627b668d48998f9b3ce7fb189864.6sAWRMtICO6mjSRT"
DEFAULT_TAAPI_SECRET = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJjbHVlIjoiNmE0Y2RkOTQ5MzEwZjViOGU2YzdiNzEwIiwiaWF0IjoxNzgzNDIyMzU2LCJleHAiOjMzMjg3ODg2MzU2fQ.ojS0IaZ6Nt7CKGPsernKFxFbnqpDUDEoxyeNMkt3Cno"
)
DEFAULT_TAAPI_EXCHANGE = "bybit"

# TAAPI pattern scans replaced by SMC+VSA (Bybit klines). Keep credentials dormant.
TAAPI_PAUSED = True


def get_zai_api_key() -> str:
    return (
        os.environ.get("ZAI_API_KEY")
        or os.environ.get("AI_API_KEY")
        or DEFAULT_ZAI_API_KEY
        or ""
    ).strip()


def get_taapi_secret() -> str:
    return (
        os.environ.get("TAAPI_SECRET")
        or os.environ.get("TAAPI_API_KEY")
        or os.environ.get("TAAPI_KEY")
        or DEFAULT_TAAPI_SECRET
        or ""
    ).strip()


def get_taapi_exchange() -> str:
    raw = (
        os.environ.get("TAAPI_EXCHANGE")
        or DEFAULT_TAAPI_EXCHANGE
        or "bybit"
    ).strip()
    return raw or "bybit"


def is_zai_configured() -> bool:
    return bool(get_zai_api_key())


def is_taapi_configured() -> bool:
    if TAAPI_PAUSED:
        return False
    return bool(get_taapi_secret())


def get_bybit_testnet_api_key() -> str:
    return (os.environ.get("BYBIT_TESTNET_API_KEY") or "").strip()


def get_bybit_testnet_api_secret() -> str:
    return (os.environ.get("BYBIT_TESTNET_API_SECRET") or "").strip()


def is_bybit_testnet_configured() -> bool:
    return bool(get_bybit_testnet_api_key() and get_bybit_testnet_api_secret())
