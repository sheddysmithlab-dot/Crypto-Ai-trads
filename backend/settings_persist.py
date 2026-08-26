"""Persist Bybit (and optional AI) credentials across restarts.

Stored under backend/data/ (Docker volume). Cleared only when the user
explicitly calls settings reset / remove — never on browser close or redeploy.
Secrets are never logged or returned to the frontend.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_DEFAULT_DATA = Path(__file__).resolve().parent / "data"
DATA_DIR = Path(os.environ.get("ENGINE_RUNTIME_DATA_DIR", str(_DEFAULT_DATA)))
CREDS_PATH = DATA_DIR / "api_credentials.json"


def _safe_read() -> dict:
    try:
        if not CREDS_PATH.is_file():
            return {}
        data = json.loads(CREDS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        print(f"[SETTINGS PERSIST] load note: {exc}")
        return {}


def load_credentials() -> dict[str, Any]:
    """Return persisted credential fields (may be empty)."""
    raw = _safe_read()
    return {
        "bybit_api_key": str(raw.get("bybit_api_key") or "").strip(),
        "bybit_api_secret": str(raw.get("bybit_api_secret") or "").strip(),
        "bybit_environment": (
            raw.get("bybit_environment")
            if raw.get("bybit_environment") in ("mainnet", "testnet")
            else "mainnet"
        ),
        "live_trading": bool(raw.get("live_trading")),
        "ai_provider": str(raw.get("ai_provider") or "").strip(),
        "ai_api_key": str(raw.get("ai_api_key") or "").strip(),
        "ai_model": str(raw.get("ai_model") or "").strip(),
        "ai_base_url": str(raw.get("ai_base_url") or "").strip(),
    }


def save_credentials(payload: dict[str, Any]) -> None:
    """Merge + write credential file. Empty string values do not wipe existing secrets."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        current = _safe_read()
        out = dict(current)

        for key in (
            "bybit_api_key",
            "bybit_api_secret",
            "ai_api_key",
        ):
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                out[key] = val.strip()

        if payload.get("bybit_environment") in ("mainnet", "testnet"):
            out["bybit_environment"] = payload["bybit_environment"]
        if "live_trading" in payload:
            out["live_trading"] = bool(payload["live_trading"])

        for key in ("ai_provider", "ai_model", "ai_base_url"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                out[key] = val.strip()

        tmp = CREDS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
        tmp.replace(CREDS_PATH)
        try:
            os.chmod(CREDS_PATH, 0o600)
        except OSError:
            pass
        print(
            "[SETTINGS PERSIST] Saved credentials to disk "
            f"(bybit={'yes' if out.get('bybit_api_key') and out.get('bybit_api_secret') else 'no'}, "
            f"live={bool(out.get('live_trading'))})."
        )
    except Exception as exc:
        print(f"[SETTINGS PERSIST] save note: {exc}")


def clear_credentials() -> None:
    """User-initiated remove — delete persisted file."""
    try:
        if CREDS_PATH.is_file():
            CREDS_PATH.unlink()
            print("[SETTINGS PERSIST] Cleared saved credentials (user reset).")
    except Exception as exc:
        print(f"[SETTINGS PERSIST] clear note: {exc}")


def set_live_trading(enabled: bool) -> None:
    save_credentials({"live_trading": bool(enabled)})
