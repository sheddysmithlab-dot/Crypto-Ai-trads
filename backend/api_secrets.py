"""Z.ai credentials for the trading backend.

Resolution order:
  1. Environment variable
  2. backend/.env via python-dotenv
  3. Built-in Z.ai default (override via env in production)

Secrets are never logged or returned to the frontend.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(_ENV_PATH)

DEFAULT_ZAI_API_KEY = "50ab627b668d48998f9b3ce7fb189864.6sAWRMtICO6mjSRT"

# Legacy flag — TAAPI.io is not used (signals = Bybit klines + UVSS).
TAAPI_PAUSED = True


def get_zai_api_key() -> str:
    return (
        os.environ.get("ZAI_API_KEY")
        or os.environ.get("AI_API_KEY")
        or DEFAULT_ZAI_API_KEY
        or ""
    ).strip()


def get_taapi_secret() -> str:
    """Always empty — TAAPI removed."""
    return ""


def get_taapi_exchange() -> str:
    return "bybit"


def is_zai_configured() -> bool:
    return bool(get_zai_api_key())


def is_taapi_configured() -> bool:
    return False


def get_bybit_testnet_api_key() -> str:
    return (os.environ.get("BYBIT_TESTNET_API_KEY") or "").strip()


def get_bybit_testnet_api_secret() -> str:
    return (os.environ.get("BYBIT_TESTNET_API_SECRET") or "").strip()


def is_bybit_testnet_configured() -> bool:
    return bool(get_bybit_testnet_api_key() and get_bybit_testnet_api_secret())
