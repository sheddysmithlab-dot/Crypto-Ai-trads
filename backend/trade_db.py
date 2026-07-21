"""MySQL persistence for trading history / statement (Hostinger-compatible).

Configure via env:
  MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE
  MYSQL_ENABLED=true  (optional; auto-on when host+user+db set)
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

_SCHEMA_PATH = Path(__file__).resolve().parent / "sql" / "schema.sql"
_lock = threading.Lock()
_pool = None  # lazy pymysql connection (single; Hostinger shared plans are fine)


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def mysql_enabled() -> bool:
    flag = _env("MYSQL_ENABLED").lower()
    if flag in ("0", "false", "no", "off"):
        return False
    if flag in ("1", "true", "yes", "on"):
        return True
    # Auto-enable when connection basics are present
    return bool(_env("MYSQL_HOST") and _env("MYSQL_USER") and _env("MYSQL_DATABASE"))


def _connect_kwargs() -> dict:
    return {
        "host": _env("MYSQL_HOST", "127.0.0.1"),
        "port": int(_env("MYSQL_PORT", "3306") or "3306"),
        "user": _env("MYSQL_USER"),
        "password": _env("MYSQL_PASSWORD"),
        "database": _env("MYSQL_DATABASE", "ai_trads"),
        "charset": "utf8mb4",
        "autocommit": True,
        "connect_timeout": 8,
        "read_timeout": 15,
        "write_timeout": 15,
        "cursorclass": None,  # set after import
    }


def _get_conn():
    """Return a live pymysql connection (reconnect if dropped)."""
    global _pool
    import pymysql
    from pymysql.cursors import DictCursor

    with _lock:
        if _pool is not None:
            try:
                _pool.ping(reconnect=True)
                return _pool
            except Exception:
                try:
                    _pool.close()
                except Exception:
                    pass
                _pool = None

        kw = _connect_kwargs()
        kw["cursorclass"] = DictCursor
        _pool = pymysql.connect(**kw)
        return _pool


def init_db() -> dict:
    """Create tables if missing. Safe to call on every startup."""
    if not mysql_enabled():
        print("[MYSQL] Disabled — set MYSQL_HOST / MYSQL_USER / MYSQL_DATABASE to enable.")
        return {"enabled": False, "ok": False, "message": "MySQL not configured"}

    try:
        conn = _get_conn()
        sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        # Split on semicolons; skip empty
        statements = [s.strip() for s in sql.split(";") if s.strip() and not s.strip().startswith("--")]
        with conn.cursor() as cur:
            for stmt in statements:
                # Keep CREATE TABLE block (ignore leading comment-only chunks already filtered)
                if stmt.upper().startswith("CREATE"):
                    cur.execute(stmt)
        _migrate_schema(conn)
        print(
            f"[MYSQL] Connected { _env('MYSQL_HOST') }/{ _env('MYSQL_DATABASE') } — trades + seasons ready."
        )
        return {"enabled": True, "ok": True, "message": "connected"}
    except Exception as exc:
        print(f"[MYSQL] init failed: {exc}")
        return {"enabled": True, "ok": False, "message": str(exc)}


def _migrate_schema(conn) -> None:
    """Add season support on older DBs that already have `trades` without season_id."""
    alters = [
        "ALTER TABLE trades ADD COLUMN season_id BIGINT UNSIGNED NULL AFTER bot_trade_id",
        "ALTER TABLE trades ADD KEY idx_season (season_id)",
    ]
    with conn.cursor() as cur:
        for stmt in alters:
            try:
                cur.execute(stmt)
            except Exception as exc:
                msg = str(exc).lower()
                if "duplicate" in msg or "exists" in msg:
                    continue
                print(f"[MYSQL] migrate skip: {exc}")


def max_bot_trade_id() -> int:
    if not mysql_enabled():
        return 0
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT COALESCE(MAX(bot_trade_id), 0) AS m FROM trades")
            row = cur.fetchone() or {}
            return int(row.get("m") or 0)
    except Exception as exc:
        print(f"[MYSQL] max_bot_trade_id: {exc}")
        return 0


def _trade_uid(trade: dict) -> str:
    opened_at = float(trade.get("opened_at") or time.time())
    return f"{int(opened_at * 1000)}-{int(trade['id'])}"


def upsert_open_trade(trade: dict, username: str | None = None) -> None:
    """Insert (or refresh) an open trade row."""
    if not mysql_enabled():
        return
    try:
        conn = _get_conn()
        opened_at = float(trade.get("opened_at") or time.time())
        uid = _trade_uid(trade)
        sql = """
        INSERT INTO trades (
          trade_uid, bot_trade_id, season_id, username, pair, side, status, source, protected,
          entry_price, margin, position_size, qty, capital_reserved,
          entry_fee_pct, entry_fee_usd, exchange, bybit_symbol, pattern,
          signal_candle_time, opened_at
        ) VALUES (
          %(trade_uid)s, %(bot_trade_id)s, %(season_id)s, %(username)s, %(pair)s, %(side)s, 'active', %(source)s, %(protected)s,
          %(entry_price)s, %(margin)s, %(position_size)s, %(qty)s, %(capital_reserved)s,
          %(entry_fee_pct)s, %(entry_fee_usd)s, %(exchange)s, %(bybit_symbol)s, %(pattern)s,
          %(signal_candle_time)s, %(opened_at)s
        )
        ON DUPLICATE KEY UPDATE
          status='active',
          season_id=COALESCE(VALUES(season_id), season_id),
          margin=VALUES(margin),
          position_size=VALUES(position_size),
          pattern=VALUES(pattern)
        """
        params = {
            "trade_uid": uid,
            "bot_trade_id": int(trade["id"]),
            "season_id": trade.get("season_id"),
            "username": username,
            "pair": trade.get("pair") or "",
            "side": trade.get("side") or "LONG",
            "source": trade.get("source") or "auto",
            "protected": 1 if trade.get("source") == "manual" else 0,
            "entry_price": float(trade.get("entry") or 0),
            "margin": float(trade.get("margin") or 0),
            "position_size": float(trade.get("position_size") or 0),
            "qty": trade.get("qty"),
            "capital_reserved": trade.get("capital_reserved"),
            "entry_fee_pct": trade.get("entry_fee_pct"),
            "entry_fee_usd": float(trade.get("entry_fee_usd") or 0),
            "exchange": trade.get("exchange"),
            "bybit_symbol": trade.get("bybit_symbol"),
            "pattern": trade.get("pattern"),
            "signal_candle_time": trade.get("signal_candle_time"),
            "opened_at": opened_at,
        }
        with conn.cursor() as cur:
            cur.execute(sql, params)
    except Exception as exc:
        print(f"[MYSQL] upsert_open_trade failed: {exc}")


def create_season(*, start_capital: float, started_at: float | None = None) -> int | None:
    """Insert a new AI season row; returns DB id."""
    if not mysql_enabled():
        return None
    try:
        conn = _get_conn()
        ts = float(started_at or time.time())
        uid = f"season-{int(ts * 1000)}"
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ai_seasons (season_uid, status, start_capital, started_at)
                VALUES (%s, 'active', %s, %s)
                """,
                (uid, float(start_capital), ts),
            )
            season_id = int(cur.lastrowid)
        print(f"[MYSQL] AI season #{season_id} started (baseline ${start_capital:,.2f}).")
        return season_id
    except Exception as exc:
        print(f"[MYSQL] create_season failed: {exc}")
        return None


def close_season(
    season_id: int | None,
    *,
    end_capital: float,
    gross_pnl_usd: float,
    net_pnl_usd: float,
    broker_fee_usd: float,
    trade_count: int,
    win_count: int,
    loss_count: int,
    end_reason: str | None = None,
    ended_at: float | None = None,
) -> None:
    """Finalize season totals when AI automation stops."""
    if not mysql_enabled() or not season_id:
        return
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE ai_seasons SET
                  status='closed',
                  end_capital=%s,
                  gross_pnl_usd=%s,
                  net_pnl_usd=%s,
                  broker_fee_usd=%s,
                  trade_count=%s,
                  win_count=%s,
                  loss_count=%s,
                  ended_at=%s,
                  end_reason=%s
                WHERE id=%s
                """,
                (
                    float(end_capital),
                    float(gross_pnl_usd),
                    float(net_pnl_usd),
                    float(broker_fee_usd),
                    int(trade_count),
                    int(win_count),
                    int(loss_count),
                    float(ended_at or time.time()),
                    (end_reason or "")[:256],
                    int(season_id),
                ),
            )
        print(
            f"[MYSQL] AI season #{season_id} closed — "
            f"net ${net_pnl_usd:,.2f} ({trade_count} trades)."
        )
    except Exception as exc:
        print(f"[MYSQL] close_season failed: {exc}")


def fetch_seasons(*, limit: int = 50) -> dict[str, Any]:
    empty = {"enabled": mysql_enabled(), "ok": False, "seasons": [], "message": "MySQL not configured"}
    if not mysql_enabled():
        return empty
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  id, season_uid, status, start_capital, end_capital,
                  gross_pnl_usd, net_pnl_usd, broker_fee_usd,
                  trade_count, win_count, loss_count,
                  started_at, ended_at, end_reason
                FROM ai_seasons
                ORDER BY started_at DESC
                LIMIT %s
                """,
                (int(limit),),
            )
            rows = cur.fetchall() or []

        def _f(v):
            if v is None:
                return None
            if hasattr(v, "as_tuple"):
                return float(v)
            return v

        seasons = []
        for r in rows:
            seasons.append({k: _f(v) if k.endswith(("_usd", "_capital", "_at")) else v for k, v in r.items()})
        return {"enabled": True, "ok": True, "seasons": seasons, "message": "ok"}
    except Exception as exc:
        print(f"[MYSQL] fetch_seasons failed: {exc}")
        empty["enabled"] = True
        empty["message"] = str(exc)
        return empty


def finalize_trade(
    trade: dict,
    *,
    exit_price: float,
    gross_pnl_pct: float,
    net_pnl_usd: float,
    exit_fee_usd: float,
    exit_fee_pct: float | None = None,
    closed_reason: str | None = None,
    gross_pnl_usd: float | None = None,
) -> None:
    """Mark trade sold and store exit metrics."""
    if not mysql_enabled():
        return
    try:
        conn = _get_conn()
        uid = _trade_uid(trade)
        closed_at = time.time()
        if gross_pnl_usd is None:
            notional = float(trade.get("position_size") or 0)
            gross_pnl_usd = round(notional * (gross_pnl_pct / 100.0), 4)

        sql = """
        UPDATE trades SET
          status='sold',
          exit_price=%(exit_price)s,
          exit_fee_usd=%(exit_fee_usd)s,
          exit_fee_pct=%(exit_fee_pct)s,
          gross_pnl_pct=%(gross_pnl_pct)s,
          gross_pnl_usd=%(gross_pnl_usd)s,
          net_pnl_usd=%(net_pnl_usd)s,
          closed_reason=%(closed_reason)s,
          closed_at=%(closed_at)s,
          peak_gross_pct=%(peak_gross_pct)s
        WHERE trade_uid=%(trade_uid)s
        """
        params = {
            "exit_price": float(exit_price),
            "exit_fee_usd": float(exit_fee_usd or 0),
            "exit_fee_pct": exit_fee_pct,
            "gross_pnl_pct": float(gross_pnl_pct),
            "gross_pnl_usd": float(gross_pnl_usd),
            "net_pnl_usd": float(net_pnl_usd),
            "closed_reason": (closed_reason or "")[:512],
            "closed_at": closed_at,
            "peak_gross_pct": trade.get("peak_gross_pct"),
            "trade_uid": uid,
        }
        with conn.cursor() as cur:
            cur.execute(sql, params)
            if cur.rowcount == 0:
                upsert_open_trade(trade)
                cur.execute(sql, params)
    except Exception as exc:
        print(f"[MYSQL] finalize_trade failed: {exc}")


def fetch_statement(
    *,
    limit: int = 200,
    offset: int = 0,
    status: str | None = None,
    pair: str | None = None,
    season_id: int | None = None,
) -> dict[str, Any]:
    """Return rows + summary totals for the Trading Statement UI."""
    empty = {
        "enabled": mysql_enabled(),
        "ok": False,
        "rows": [],
        "summary": {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "open_count": 0,
            "gross_pnl_usd": 0.0,
            "net_pnl_usd": 0.0,
            "fees_usd": 0.0,
        },
        "message": "MySQL not configured",
    }
    if not mysql_enabled():
        return empty

    try:
        conn = _get_conn()
        where = ["1=1"]
        params: list[Any] = []
        if status in ("active", "sold", "locked"):
            where.append("status=%s")
            params.append(status)
        if pair:
            where.append("pair=%s")
            params.append(pair)
        if season_id:
            where.append("season_id=%s")
            params.append(int(season_id))
        wh = " AND ".join(where)

        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                  COUNT(*) AS total_trades,
                  SUM(CASE WHEN status='sold' AND COALESCE(net_pnl_usd,0) > 0 THEN 1 ELSE 0 END) AS wins,
                  SUM(CASE WHEN status='sold' AND COALESCE(net_pnl_usd,0) < 0 THEN 1 ELSE 0 END) AS losses,
                  SUM(CASE WHEN status IN ('active','locked') THEN 1 ELSE 0 END) AS open_count,
                  COALESCE(SUM(CASE WHEN status='sold' THEN gross_pnl_usd ELSE 0 END),0) AS gross_pnl_usd,
                  COALESCE(SUM(CASE WHEN status='sold' THEN net_pnl_usd ELSE 0 END),0) AS net_pnl_usd,
                  COALESCE(SUM(entry_fee_usd + exit_fee_usd),0) AS fees_usd
                FROM trades
                WHERE {wh}
                """,
                params,
            )
            summary_row = cur.fetchone() or {}

            cur.execute(
                f"""
                SELECT
                  id, bot_trade_id, season_id, username, pair, side, status, source, protected,
                  entry_price, exit_price, margin, position_size, qty,
                  entry_fee_usd, exit_fee_usd, gross_pnl_pct, gross_pnl_usd, net_pnl_usd,
                  exchange, pattern, closed_reason, opened_at, closed_at, created_at
                FROM trades
                WHERE {wh}
                ORDER BY COALESCE(closed_at, opened_at) DESC
                LIMIT %s OFFSET %s
                """,
                [*params, int(limit), int(offset)],
            )
            rows = cur.fetchall() or []

        def _f(v):
            if v is None:
                return None
            if hasattr(v, "as_tuple"):  # Decimal
                return float(v)
            return v

        clean_rows = []
        for r in rows:
            clean_rows.append({k: _f(v) if k.endswith(("_usd", "_pct", "_price", "margin", "position_size", "qty", "opened_at", "closed_at")) or k in ("entry_price", "exit_price") else v for k, v in r.items()})

        summary = {
            "total_trades": int(summary_row.get("total_trades") or 0),
            "wins": int(summary_row.get("wins") or 0),
            "losses": int(summary_row.get("losses") or 0),
            "open_count": int(summary_row.get("open_count") or 0),
            "gross_pnl_usd": float(summary_row.get("gross_pnl_usd") or 0),
            "net_pnl_usd": float(summary_row.get("net_pnl_usd") or 0),
            "fees_usd": float(summary_row.get("fees_usd") or 0),
        }
        return {
            "enabled": True,
            "ok": True,
            "rows": clean_rows,
            "summary": summary,
            "limit": limit,
            "offset": offset,
            "season_id": season_id,
            "message": "ok",
        }
    except Exception as exc:
        print(f"[MYSQL] fetch_statement failed: {exc}")
        empty["message"] = str(exc)
        empty["enabled"] = True
        return empty


def status_dict() -> dict:
    if not mysql_enabled():
        return {"enabled": False, "ok": False, "host": None, "database": None}
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM trades")
            n = int((cur.fetchone() or {}).get("c") or 0)
        return {
            "enabled": True,
            "ok": True,
            "host": _env("MYSQL_HOST"),
            "database": _env("MYSQL_DATABASE"),
            "trade_rows": n,
        }
    except Exception as exc:
        return {
            "enabled": True,
            "ok": False,
            "host": _env("MYSQL_HOST"),
            "database": _env("MYSQL_DATABASE"),
            "message": str(exc),
        }
