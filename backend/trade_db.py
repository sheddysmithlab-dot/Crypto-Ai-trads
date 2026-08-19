"""Trade database module — MySQL-backed persistence for open/closed trades and seasons.

If MySQL env vars are set (MYSQL_ENABLED=true + MYSQL_HOST/USER/PASSWORD/DATABASE)
the module connects and persists everything; otherwise all operations are no-ops
that return safe default values so the bot runs without a database.
"""
from __future__ import annotations

import os
import time
from typing import Any

try:
    import pymysql
    import pymysql.cursors
    _PYMYSQL_OK = True
except ImportError:
    _PYMYSQL_OK = False


def _env(name: str, default: str = "") -> str:
    raw = (os.environ.get(name) or default).strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ('"', "'"):
        return raw[1:-1]
    return raw


def _mysql_enabled() -> bool:
    return _env("MYSQL_ENABLED", "false").lower() in ("1", "true", "yes") and _PYMYSQL_OK


def _connect():
    return pymysql.connect(
        host=_env("MYSQL_HOST", "localhost"),
        port=int(_env("MYSQL_PORT", "3306")),
        user=_env("MYSQL_USER", "root"),
        password=_env("MYSQL_PASSWORD", ""),
        database=_env("MYSQL_DATABASE", "aitrads"),
        connect_timeout=8,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


_SCHEMA = """
CREATE TABLE IF NOT EXISTS bot_trades (
    id INT PRIMARY KEY,
    pair VARCHAR(32),
    side VARCHAR(8),
    entry DOUBLE,
    sl DOUBLE,
    tp DOUBLE,
    status VARCHAR(16),
    source VARCHAR(16),
    pattern TEXT,
    reason TEXT,
    pnl DOUBLE,
    gross_pnl_pct DOUBLE,
    opened_at DOUBLE,
    closed_at DOUBLE,
    season_id INT,
    data JSON
);

CREATE TABLE IF NOT EXISTS bot_seasons (
    id INT AUTO_INCREMENT PRIMARY KEY,
    start_capital DOUBLE,
    end_capital DOUBLE,
    started_at DOUBLE,
    ended_at DOUBLE,
    reason TEXT,
    data JSON
);
"""

_db_status: dict = {"ok": False, "message": "not initialised"}


def init_db() -> dict:
    global _db_status
    if not _mysql_enabled():
        _db_status = {"ok": False, "message": "MySQL disabled or pymysql not installed"}
        return _db_status
    try:
        conn = _connect()
        with conn.cursor() as cur:
            for stmt in _SCHEMA.strip().split(";"):
                s = stmt.strip()
                if s:
                    cur.execute(s)
        conn.close()
        _db_status = {"ok": True, "message": "MySQL connected and schema OK"}
        return _db_status
    except Exception as exc:
        _db_status = {"ok": False, "message": str(exc)}
        return _db_status


def status_dict() -> dict:
    return dict(_db_status)


def max_bot_trade_id() -> int:
    if not _mysql_enabled():
        return 0
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(id) AS m FROM bot_trades")
            row = cur.fetchone()
        conn.close()
        return int(row["m"] or 0) if row else 0
    except Exception:
        return 0


def upsert_open_trade(trade: dict) -> None:
    if not _mysql_enabled():
        return
    try:
        import json as _json
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO bot_trades
                   (id, pair, side, entry, sl, tp, status, source, pattern, reason,
                    pnl, gross_pnl_pct, opened_at, season_id, data)
                   VALUES (%(id)s, %(pair)s, %(side)s, %(entry)s, %(sl)s, %(tp)s,
                           %(status)s, %(source)s, %(pattern)s, %(reason)s,
                           %(pnl)s, %(gross_pnl_pct)s, %(opened_at)s, %(season_id)s, %(data)s)
                   ON DUPLICATE KEY UPDATE
                   sl=VALUES(sl), tp=VALUES(tp), status=VALUES(status),
                   pnl=VALUES(pnl), gross_pnl_pct=VALUES(gross_pnl_pct), data=VALUES(data)
                """,
                {
                    "id": trade.get("id", 0),
                    "pair": trade.get("pair", ""),
                    "side": trade.get("side", ""),
                    "entry": trade.get("entry"),
                    "sl": trade.get("sl_price"),
                    "tp": trade.get("tp_price"),
                    "status": "open",
                    "source": trade.get("source", "auto"),
                    "pattern": trade.get("pattern", ""),
                    "reason": (trade.get("reason") or "")[:500],
                    "pnl": trade.get("pnl", 0),
                    "gross_pnl_pct": trade.get("gross_pnl_pct"),
                    "opened_at": trade.get("opened_at") or time.time(),
                    "season_id": trade.get("season_id"),
                    "data": _json.dumps({k: v for k, v in trade.items()
                                         if k not in ("reason",)}, default=str),
                },
            )
        conn.close()
    except Exception as exc:
        print(f"[TRADE_DB] upsert_open_trade error: {exc}")


def finalize_trade(trade: dict) -> None:
    if not _mysql_enabled():
        return
    try:
        import json as _json
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE bot_trades
                   SET status='sold', pnl=%(pnl)s, gross_pnl_pct=%(gross_pnl_pct)s,
                       closed_at=%(closed_at)s, data=%(data)s
                   WHERE id=%(id)s
                """,
                {
                    "id": trade.get("id", 0),
                    "pnl": trade.get("pnl", 0),
                    "gross_pnl_pct": trade.get("gross_pnl_pct"),
                    "closed_at": trade.get("closed_at") or time.time(),
                    "data": _json.dumps({k: v for k, v in trade.items()
                                         if k not in ("reason",)}, default=str),
                },
            )
        conn.close()
    except Exception as exc:
        print(f"[TRADE_DB] finalize_trade error: {exc}")


def create_season(*, start_capital: float, started_at: float) -> int | None:
    if not _mysql_enabled():
        return None
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO bot_seasons (start_capital, started_at) VALUES (%s, %s)",
                (start_capital, started_at),
            )
            sid = cur.lastrowid
        conn.close()
        return sid
    except Exception as exc:
        print(f"[TRADE_DB] create_season error: {exc}")
        return None


def close_season(season_id: int, *, end_capital: float, ended_at: float, reason: str = "") -> None:
    if not _mysql_enabled() or season_id is None:
        return
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE bot_seasons SET end_capital=%s, ended_at=%s, reason=%s WHERE id=%s",
                (end_capital, ended_at, reason[:200], season_id),
            )
        conn.close()
    except Exception as exc:
        print(f"[TRADE_DB] close_season error: {exc}")


def fetch_statement(*, limit: int = 200, offset: int = 0) -> list[dict]:
    if not _mysql_enabled():
        return []
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM bot_trades WHERE status='sold' ORDER BY id DESC LIMIT %s OFFSET %s",
                (limit, offset),
            )
            rows = cur.fetchall()
        conn.close()
        return list(rows)
    except Exception:
        return []


def fetch_seasons(*, limit: int = 50) -> list[dict]:
    if not _mysql_enabled():
        return []
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM bot_seasons ORDER BY id DESC LIMIT %s", (limit,)
            )
            rows = cur.fetchall()
        conn.close()
        return list(rows)
    except Exception:
        return []
