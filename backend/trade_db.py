"""Trade database module — MySQL-backed persistence for open/closed trades and seasons.

If MySQL env vars are set (MYSQL_ENABLED=true + MYSQL_HOST/USER/PASSWORD/DATABASE)
the module connects and persists everything; otherwise all operations are no-ops
that return safe default values so the bot runs without a database.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except Exception:
    pass

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


# Hostinger shared MySQL caps max_connections_per_hour (~500). Reuse one
# connection per thread so fire/skip/train logging does not open a new TCP
# session on every query.
_tls = threading.local()
_connect_lock = threading.Lock()


class _ReusableConn:
    """Proxy so existing callers can call conn.close() without dropping the socket."""

    __slots__ = ("_real",)

    def __init__(self, real):
        self._real = real

    def close(self) -> None:
        return None

    def cursor(self, *args, **kwargs):
        return self._real.cursor(*args, **kwargs)

    def ping(self, *args, **kwargs):
        return self._real.ping(*args, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self._real, name)


def _new_raw_connection():
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


def _connect():
    """Return a thread-local reusable connection (close() is a no-op)."""
    raw = getattr(_tls, "conn", None)
    if raw is not None:
        try:
            raw.ping(reconnect=True)
            return _ReusableConn(raw)
        except Exception:
            try:
                raw.close()
            except Exception:
                pass
            _tls.conn = None
    with _connect_lock:
        raw = _new_raw_connection()
        _tls.conn = raw
    return _ReusableConn(raw)


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

CREATE TABLE IF NOT EXISTS family_engine_rules (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    family VARCHAR(64) NOT NULL,
    timeframe_key VARCHAR(16) NOT NULL,
    min_of_score DOUBLE NULL,
    min_brain_score DOUBLE NULL,
    min_rr DOUBLE NULL,
    sl_pct DOUBLE NULL,
    tp_pct DOUBLE NULL,
    candle_soft TINYINT(1) NOT NULL DEFAULT 1,
    skip_when_json JSON NULL,
    fire_when_json JSON NULL,
    lesson_text TEXT NULL,
    sample_count INT UNSIGNED NOT NULL DEFAULT 0,
    win_rate DOUBLE NULL,
    avg_r DOUBLE NULL,
    version INT UNSIGNED NOT NULL DEFAULT 1,
    locked TINYINT(1) NOT NULL DEFAULT 0,
    prev_min_of_score DOUBLE NULL,
    prev_win_rate DOUBLE NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_family_tf (family, timeframe_key),
    KEY idx_family (family)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS family_train_events (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    event_uid VARCHAR(64) NOT NULL,
    family VARCHAR(64) NOT NULL,
    pattern VARCHAR(128) NULL,
    pair VARCHAR(32) NULL,
    tf VARCHAR(16) NULL,
    side VARCHAR(8) NULL,
    decision ENUM('FIRE','SKIP','DELAY') NOT NULL,
    score DOUBLE NULL,
    confidence DOUBLE NULL,
    strategy VARCHAR(64) NULL,
    context_json JSON NULL,
    trade_id INT NULL,
    outcome ENUM('win','loss','breakeven','unknown','skipped') NULL,
    closed_reason VARCHAR(512) NULL,
    mfe_pct DOUBLE NULL,
    mae_pct DOUBLE NULL,
    net_pnl_usd DOUBLE NULL,
    fault_tags JSON NULL,
    lesson TEXT NULL,
    created_at DOUBLE NOT NULL,
    closed_at DOUBLE NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_event_uid (event_uid),
    KEY idx_family_tf (family, tf),
    KEY idx_trade_id (trade_id),
    KEY idx_decision_created (decision, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS engine_formulas (
    formula_key VARCHAR(96) NOT NULL,
    group_name VARCHAR(48) NOT NULL DEFAULT 'general',
    value_type ENUM('number','bool','text','json') NOT NULL DEFAULT 'number',
    value_num DOUBLE NULL,
    value_text TEXT NULL,
    value_json JSON NULL,
    note VARCHAR(512) NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (formula_key),
    KEY idx_group (group_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

_SEED_TFS = ("30s", "1m", "5m", "15m", "1h", "1d")
# family, min_of_score, candle_soft, lesson
_SEED_FAMILIES = (
    (
        "doji",
        float(_env("THR_SCORE_CLASSIC_PATTERN", "75")),
        False,
        "Doji = indecision. Prefer FIRE near support/resistance with HTF align; "
        "SKIP chop / mid-range / weak OF below family floor.",
    ),
    (
        "engulfing",
        float(_env("THR_SCORE_ENGULFING", "75")),
        False,
        "Engulfing needs clear body dominance + trend/structure confluence; "
        "SKIP opposing HTF or weak OF below family floor.",
    ),
    (
        "pin bar",
        float(_env("THR_SCORE_CLASSIC_PATTERN", "75")),
        False,
        "Pin bar / hammer / shooting star: require rejection wick + HTF align; "
        "SKIP mid-range chop and opposing structure.",
    ),
    (
        "inside bar",
        float(_env("THR_SCORE_INSIDE_BAR", "75")),
        False,
        "Inside bar: wait mother-bar break + OF confirm; SKIP weak pressure / low RV.",
    ),
)

_db_status: dict = {"ok": False, "message": "not initialised"}


def _seed_family_rules(cur) -> None:
    """Insert default family rules when missing (doji/engulfing/pin/inside × TFs)."""
    for family, min_of, soft, lesson in _SEED_FAMILIES:
        for tf in _SEED_TFS:
            cur.execute(
                """INSERT IGNORE INTO family_engine_rules
                   (family, timeframe_key, min_of_score, min_brain_score, min_rr,
                    candle_soft, lesson_text, sample_count, version, locked)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, 0, 1, 0)""",
                (family, tf, min_of, 6.0, 2.0, 1 if soft else 0, lesson),
            )


# Default live-engine formulas (code/env fallbacks → MySQL source of truth).
# Tuple: (group, key, vtype, value_num, value_text, note) — value_json via text for json type.
_ENGINE_FORMULA_SEEDS: list[tuple[str, str, str, float | None, str | None, str]] = [
    ("exit", "PROFIT_LOCK_PCT", "number", 0.65, None, "Profit book arm %"),
    ("exit", "PROFIT_TRAIL_GIVEBACK_PCT", "number", 0.10, None, "Trail giveback %"),
    ("exit", "PROFIT_TRAIL_FIRST_GIVEBACK_PCT", "number", 0.10, None, "First trail giveback %"),
    ("exit", "LOSS_PROTECT_PCT", "number", 0.60, None, "Soft loss lock arm %"),
    ("exit", "LOSS_BAND_PCT", "number", 0.80, None, "Hard loss floor %"),
    ("exit", "LOSS_RECOVERY_RETRACE_PCT", "number", 0.20, None, "Loss lock trail %"),
    ("exit", "LOSS_LOCK_CLEAR_PCT", "number", 0.20, None, "Unlock to profit book %"),
    ("exit", "LOSS_PROTECT_PCT_1M", "number", 0.60, None, "1m loss arm"),
    ("exit", "LOSS_BAND_PCT_1M", "number", 0.80, None, "1m hard band"),
    ("exit", "PROFIT_HARD_PCT_1M", "number", 0.65, None, "1m profit arm alias"),
    ("exit", "FLIP_EXIT_MIN_GROSS_PCT", "number", 0.25, None, "Min gross to flip-exit"),
    ("exit", "MICRO_CAP_LOSS_ARM_PCT", "number", 0.25, None, "Micro-cap loss arm"),
    ("exit", "MICRO_CAP_LOSS_BAND_PCT", "number", 0.35, None, "Micro-cap loss band"),
    ("exit", "MICRO_CAP_HARD_STOP_PCT", "number", 0.25, None, "Micro-cap hard stop %"),
    ("exit", "STRUCTURE_SL_BUFFER_PCT", "number", 0.05, None, "Structure SL buffer %"),
    ("exit", "STRUCTURE_SL_MIN_DISTANCE_PCT", "number", 0.15, None, "Min structure SL distance %"),
    ("exit", "STRUCTURE_SL_GRACE_SEC", "number", 2.0, None, "Structure SL grace seconds"),
    ("exit", "STRICT_EXIT_HARD_TARGET_PCT", "number", 1.8, None, "Strict exit hard target %"),
    ("exit", "STRICT_EXIT_MIN_LOCK_PCT", "number", 0.65, None, "Strict exit min lock %"),
    ("exit", "STRICT_EXIT_FLUCTUATION_X_PCT", "number", 0.10, None, "Strict exit fluctuation %"),
    ("exit", "STRICT_EXIT_TRAIL_MULTIPLIER", "number", 1.5, None, "Strict exit trail mult"),
    ("exit", "STRICT_EXIT_MAX_LOSS_PCT", "number", 0.80, None, "Strict exit max loss %"),
    ("exit", "HARD_STOP_30S", "number", 0.80, None, "Hard stop 30s %"),
    ("exit", "HARD_STOP_1M", "number", 0.80, None, "Hard stop 1m %"),
    ("exit", "HARD_STOP_3M", "number", 0.80, None, "Hard stop 3m %"),
    ("exit", "HARD_STOP_5M", "number", 0.80, None, "Hard stop 5m %"),
    ("exit", "HARD_STOP_10M", "number", 0.80, None, "Hard stop 10m %"),
    ("exit", "HARD_STOP_15M", "number", 0.80, None, "Hard stop 15m %"),
    ("exit", "HARD_STOP_30M", "number", 0.80, None, "Hard stop 30m %"),
    ("exit", "HARD_STOP_1H", "number", 0.80, None, "Hard stop 1h %"),
    ("exit", "HARD_STOP_1D", "number", 0.80, None, "Hard stop 1D %"),
    ("risk", "MAX_CONCURRENT_TRADES", "number", 10.0, None, "Global max open trades"),
    ("risk", "MAX_SAME_SIDE_AUTO_PER_PAIR", "number", 3.0, None, "Same-side auto per pair"),
    ("risk", "ONE_M_MAX_CONCURRENT", "number", 3.0, None, "Per-pair scalp max concurrent"),
    ("of", "THR_SCORE", "number", 75.0, None, "Base OF floor"),
    ("of", "THR_SCORE_5M", "number", 75.0, None, "5m OF floor"),
    ("of", "THR_SCORE_1M", "number", 75.0, None, "1m OF floor"),
    ("of", "THR_SCORE_CLASSIC_PATTERN", "number", 75.0, None, "Doji/classic OF floor"),
    ("of", "THR_SCORE_ENGULFING", "number", 75.0, None, "Engulfing OF floor"),
    ("of", "THR_SCORE_INSIDE_BAR", "number", 75.0, None, "Inside-bar OF floor"),
    ("of", "THR_SCORE_IMBALANCE_1M", "number", 75.0, None, "Imbalance scalp floor"),
    ("of", "THR_SCORE_TRAP", "number", 90.0, None, "Trap floor HTF"),
    ("of", "THR_SCORE_TRAP_5M", "number", 80.0, None, "Trap floor 5m"),
    ("of", "THR_SCORE_TRAP_1M", "number", 80.0, None, "Trap floor 1m"),
    ("of", "CANDLE_ONLY_FIRE", "bool", 0.0, None, "Allow candle-only fire"),
    ("of", "THR_PRESSURE", "number", 0.60, None, "OF pressure threshold"),
    ("of", "THR_RV_VOL", "number", 1.20, None, "Relative volume threshold"),
    ("of", "STRUCTURE_OPPOSITE_PENALTY", "number", 12.0, None, "OF vs structure penalty"),
    ("of", "THR_PRICE_EFFORT", "number", 0.25, None, "OF price effort threshold"),
    ("of", "THR_UPPER_WICK", "number", 0.35, None, "OF upper wick threshold"),
    ("of", "THR_LOWER_WICK", "number", 0.35, None, "OF lower wick threshold"),
    ("of", "THR_BODY_RATIO", "number", 0.35, None, "OF body ratio threshold"),
    ("of", "THR_Z_ACT", "number", 1.0, None, "OF z-act threshold"),
    ("of", "THR_Z_EXHAUST", "number", 1.5, None, "OF z-exhaust threshold"),
    ("of", "THR_Z_TVOL", "number", 1.0, None, "OF z-tvol threshold"),
    ("of", "THR_FAKE_WICK", "number", 0.30, None, "OF fake wick threshold"),
    ("of", "THR_BREAK_ATR", "number", 0.10, None, "OF break ATR threshold"),
    ("of", "THR_BALANCED", "number", 0.05, None, "OF balanced threshold"),
    ("of", "THR_RV_PRICE_WEAK", "number", 0.70, None, "OF weak RV/price threshold"),
    ("of", "SCORE_FLOOR_EPS", "number", 0.05, None, "OF score floor epsilon"),
    ("of", "LOOKBACK", "number", 20.0, None, "OF lookback candles"),
    ("fire", "MIN_CONFIRM_BODY_PCT", "number", 0.03, None, "Min body % for color confirm"),
    ("fire", "ONE_M_CONFIRM_SKIP_TICKS", "number", 1.0, None, "1m skip N matching ticks"),
    ("fire", "ONE_M_MIN_BARS_BETWEEN_FIRES", "number", 3.0, None, "Min bars between scalp fires"),
    ("fire", "ONE_M_CONFIRM_MAX_BARS", "number", 3.0, None, "Max bars for color confirm"),
    ("fire", "SKIP_FIRST_DETECT", "bool", 1.0, None, "Skip first HTF detect after arm"),
    ("fire", "SKIP_FIRST_DETECT_SCALP", "bool", 0.0, None, "Skip first scalp detect"),
    ("engine", "ENGINE_BOOT_MAX_SEC", "number", 60.0, None, "Boot overlay max sec"),
    ("engine", "RECONCILE_GRACE_SECONDS", "number", 30.0, None, "Live open reconcile grace"),
    (
        "sizing",
        "TIMEFRAME_PROFILES",
        "json",
        None,
        json.dumps(
            {
                "1m": {"win_rate": 30, "lose_rate": 70, "capital_pct": 1.5},
                "5m": {"win_rate": 30, "lose_rate": 70, "capital_pct": 1.5},
                "15m": {"win_rate": 60, "lose_rate": 40, "capital_pct": 10.0},
                "1h": {"win_rate": 70, "lose_rate": 30, "capital_pct": 15.0},
                "1D": {"win_rate": 80, "lose_rate": 20, "capital_pct": 20.0},
                "30s": {"win_rate": 25, "lose_rate": 75, "capital_pct": 2.0},
                "3m": {"win_rate": 40, "lose_rate": 60, "capital_pct": 5.0},
                "10m": {"win_rate": 55, "lose_rate": 45, "capital_pct": 8.0},
                "30m": {"win_rate": 65, "lose_rate": 35, "capital_pct": 12.0},
            }
        ),
        "Per-TF win/lose display + capital_pct sizing",
    ),
]


def _seed_engine_formulas(cur) -> None:
    for group, key, vtype, num, text, note in _ENGINE_FORMULA_SEEDS:
        if vtype == "json":
            cur.execute(
                """INSERT IGNORE INTO engine_formulas
                   (formula_key, group_name, value_type, value_num, value_text, value_json, note)
                   VALUES (%s, %s, %s, NULL, NULL, %s, %s)""",
                (key, group, vtype, text, note),
            )
        else:
            cur.execute(
                """INSERT IGNORE INTO engine_formulas
                   (formula_key, group_name, value_type, value_num, value_text, note)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (key, group, vtype, num, text, note),
            )


def _tighten_trade_policy(cur) -> None:
    """Raise soft classic/engulf floors and disable candle-only (existing rows)."""
    cur.execute(
        """UPDATE engine_formulas
           SET value_num = 75, note = 'Doji/classic OF floor'
           WHERE formula_key = 'THR_SCORE_CLASSIC_PATTERN'
             AND (value_num IS NULL OR value_num < 75)"""
    )
    cur.execute(
        """UPDATE engine_formulas
           SET value_num = 75, note = 'Engulfing OF floor'
           WHERE formula_key = 'THR_SCORE_ENGULFING'
             AND (value_num IS NULL OR value_num < 75)"""
    )
    cur.execute(
        """UPDATE engine_formulas
           SET value_num = 80, note = 'Trap floor 1m'
           WHERE formula_key = 'THR_SCORE_TRAP_1M'
             AND (value_num IS NULL OR value_num < 80)"""
    )
    cur.execute(
        """UPDATE engine_formulas
           SET value_num = 0, note = 'Allow candle-only fire'
           WHERE formula_key = 'CANDLE_ONLY_FIRE'
             AND (value_num IS NULL OR value_num <> 0)"""
    )
    cur.execute(
        """UPDATE family_engine_rules
           SET min_of_score = 75, candle_soft = 0
           WHERE locked = 0
             AND family IN ('doji', 'engulfing', 'pin bar', 'inside bar')
             AND (
               min_of_score IS NULL OR min_of_score < 75 OR candle_soft = 1
             )"""
    )


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
            _seed_family_rules(cur)
            _seed_engine_formulas(cur)
            _tighten_trade_policy(cur)
        conn.close()
        _db_status = {"ok": True, "message": "MySQL connected and schema OK"}
        return _db_status
    except Exception as exc:
        _db_status = {"ok": False, "message": str(exc)}
        return _db_status


def status_dict() -> dict:
    return dict(_db_status)


def fetch_engine_formulas(*, group: str | None = None) -> list[dict]:
    if not _mysql_enabled():
        return []
    try:
        conn = _connect()
        with conn.cursor() as cur:
            if group:
                cur.execute(
                    "SELECT * FROM engine_formulas WHERE group_name=%s ORDER BY formula_key",
                    (group,),
                )
            else:
                cur.execute("SELECT * FROM engine_formulas ORDER BY group_name, formula_key")
            rows = cur.fetchall()
        conn.close()
        return list(rows or [])
    except Exception as exc:
        print(f"[TRADE_DB] fetch_engine_formulas error: {exc}")
        return []


def upsert_engine_formula(
    formula_key: str,
    *,
    group_name: str = "general",
    value_type: str = "number",
    value_num: float | None = None,
    value_text: str | None = None,
    value_json: Any = None,
    note: str | None = None,
) -> bool:
    if not _mysql_enabled() or not formula_key:
        return False
    vjson = None
    if value_json is not None:
        vjson = value_json if isinstance(value_json, str) else json.dumps(value_json, default=str)
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO engine_formulas
                   (formula_key, group_name, value_type, value_num, value_text, value_json, note)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)
                   ON DUPLICATE KEY UPDATE
                   group_name=VALUES(group_name),
                   value_type=VALUES(value_type),
                   value_num=VALUES(value_num),
                   value_text=VALUES(value_text),
                   value_json=VALUES(value_json),
                   note=COALESCE(VALUES(note), note)""",
                (
                    formula_key,
                    group_name,
                    value_type,
                    value_num,
                    value_text,
                    vjson,
                    note,
                ),
            )
        conn.close()
        return True
    except Exception as exc:
        print(f"[TRADE_DB] upsert_engine_formula error: {exc}")
        return False


def fetch_family_rules(*, family: str | None = None) -> list[dict]:
    if not _mysql_enabled():
        return []
    try:
        conn = _connect()
        with conn.cursor() as cur:
            if family:
                cur.execute(
                    "SELECT * FROM family_engine_rules WHERE family=%s ORDER BY timeframe_key",
                    (family,),
                )
            else:
                cur.execute("SELECT * FROM family_engine_rules ORDER BY family, timeframe_key")
            rows = cur.fetchall()
        conn.close()
        return list(rows or [])
    except Exception as exc:
        print(f"[TRADE_DB] fetch_family_rules error: {exc}")
        return []


def get_family_rule(family: str, timeframe_key: str) -> dict | None:
    if not _mysql_enabled() or not family:
        return None
    tf = (timeframe_key or "1m").strip().lower() or "1m"
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM family_engine_rules WHERE family=%s AND timeframe_key=%s LIMIT 1",
                (family, tf),
            )
            row = cur.fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as exc:
        print(f"[TRADE_DB] get_family_rule error: {exc}")
        return None


def update_family_rule(family: str, timeframe_key: str, **fields: Any) -> bool:
    """Update selected columns on a family×TF rule. Returns True on success."""
    if not _mysql_enabled() or not family:
        return False
    allowed = {
        "min_of_score", "min_brain_score", "min_rr", "sl_pct", "tp_pct",
        "candle_soft", "skip_when_json", "fire_when_json", "lesson_text",
        "sample_count", "win_rate", "avg_r", "version", "locked",
        "prev_min_of_score", "prev_win_rate",
    }
    sets = []
    vals: list[Any] = []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k in ("skip_when_json", "fire_when_json") and v is not None and not isinstance(v, str):
            v = json.dumps(v, default=str)
        sets.append(f"{k}=%s")
        vals.append(v)
    if not sets:
        return False
    tf = (timeframe_key or "1m").strip().lower() or "1m"
    vals.extend([family, tf])
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE family_engine_rules SET {', '.join(sets)} "
                f"WHERE family=%s AND timeframe_key=%s",
                tuple(vals),
            )
        conn.close()
        return True
    except Exception as exc:
        print(f"[TRADE_DB] update_family_rule error: {exc}")
        return False


def insert_train_event(
    *,
    family: str,
    decision: str,
    pattern: str | None = None,
    pair: str | None = None,
    tf: str | None = None,
    side: str | None = None,
    score: float | None = None,
    confidence: float | None = None,
    strategy: str | None = None,
    context: dict | None = None,
    trade_id: int | None = None,
    outcome: str | None = None,
    event_uid: str | None = None,
) -> str | None:
    """Append a FIRE/SKIP/DELAY training event. Returns event_uid or None."""
    if not _mysql_enabled() or not family:
        return None
    uid = event_uid or uuid.uuid4().hex
    dec = (decision or "SKIP").strip().upper()
    if dec not in ("FIRE", "SKIP", "DELAY"):
        dec = "SKIP"
    out = (outcome or "").strip().lower() or None
    if out and out not in ("win", "loss", "breakeven", "unknown", "skipped"):
        out = "unknown"
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO family_train_events
                   (event_uid, family, pattern, pair, tf, side, decision,
                    score, confidence, strategy, context_json, trade_id,
                    outcome, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    uid,
                    family,
                    (pattern or "")[:128] or None,
                    (pair or "")[:32] or None,
                    (tf or "")[:16] or None,
                    (side or "")[:8] or None,
                    dec,
                    score,
                    confidence,
                    (strategy or "")[:64] or None,
                    json.dumps(context or {}, default=str) if context else None,
                    trade_id,
                    out if dec != "FIRE" else None,
                    time.time(),
                ),
            )
        conn.close()
        return uid
    except Exception as exc:
        print(f"[TRADE_DB] insert_train_event error: {exc}")
        return None


def finalize_train_event_for_trade(
    trade_id: int,
    *,
    outcome: str,
    closed_reason: str | None = None,
    mfe_pct: float | None = None,
    mae_pct: float | None = None,
    net_pnl_usd: float | None = None,
    fault_tags: list | None = None,
    lesson: str | None = None,
) -> bool:
    if not _mysql_enabled() or trade_id is None:
        return False
    out = (outcome or "unknown").strip().lower()
    if out not in ("win", "loss", "breakeven", "unknown", "skipped"):
        out = "unknown"
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE family_train_events
                   SET outcome=%s, closed_reason=%s, mfe_pct=%s, mae_pct=%s,
                       net_pnl_usd=%s, fault_tags=%s, lesson=%s, closed_at=%s
                   WHERE trade_id=%s AND decision='FIRE'""",
                (
                    out,
                    (closed_reason or "")[:512] or None,
                    mfe_pct,
                    mae_pct,
                    net_pnl_usd,
                    json.dumps(fault_tags or [], default=str),
                    lesson,
                    time.time(),
                    int(trade_id),
                ),
            )
        conn.close()
        return True
    except Exception as exc:
        print(f"[TRADE_DB] finalize_train_event_for_trade error: {exc}")
        return False


def fetch_closed_train_events(
    *,
    family: str,
    timeframe_key: str | None = None,
    limit: int = 100,
) -> list[dict]:
    if not _mysql_enabled() or not family:
        return []
    try:
        conn = _connect()
        with conn.cursor() as cur:
            if timeframe_key:
                cur.execute(
                    """SELECT * FROM family_train_events
                       WHERE family=%s AND tf=%s AND decision='FIRE'
                         AND outcome IN ('win','loss','breakeven')
                       ORDER BY id DESC LIMIT %s""",
                    (family, timeframe_key, int(limit)),
                )
            else:
                cur.execute(
                    """SELECT * FROM family_train_events
                       WHERE family=%s AND decision='FIRE'
                         AND outcome IN ('win','loss','breakeven')
                       ORDER BY id DESC LIMIT %s""",
                    (family, int(limit)),
                )
            rows = cur.fetchall()
        conn.close()
        return list(rows or [])
    except Exception as exc:
        print(f"[TRADE_DB] fetch_closed_train_events error: {exc}")
        return []


def fetch_recent_train_events(
    *,
    family: str | None = None,
    decision: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Dashboard feed: recent FIRE/SKIP/DELAY rows (open + closed)."""
    if not _mysql_enabled():
        return []
    lim = max(1, min(int(limit or 100), 500))
    try:
        conn = _connect()
        with conn.cursor() as cur:
            clauses: list[str] = []
            args: list[Any] = []
            if family:
                clauses.append("family=%s")
                args.append(family.strip().lower())
            if decision:
                dec = decision.strip().upper()
                if dec in ("FIRE", "SKIP", "DELAY"):
                    clauses.append("decision=%s")
                    args.append(dec)
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            args.append(lim)
            cur.execute(
                f"""SELECT * FROM family_train_events{where}
                    ORDER BY id DESC LIMIT %s""",
                tuple(args),
            )
            rows = cur.fetchall()
        conn.close()
        out: list[dict] = []
        for row in rows or []:
            item = dict(row)
            for key in ("context_json", "fault_tags"):
                val = item.get(key)
                if isinstance(val, str) and val.strip():
                    try:
                        item[key] = json.loads(val)
                    except Exception:
                        pass
            out.append(item)
        return out
    except Exception as exc:
        print(f"[TRADE_DB] fetch_recent_train_events error: {exc}")
        return []

def count_closed_since_version(
    *,
    family: str,
    timeframe_key: str,
    since_created_at: float | None = None,
) -> int:
    """Count FIRE outcomes for family×TF (optionally after a timestamp)."""
    if not _mysql_enabled() or not family:
        return 0
    try:
        conn = _connect()
        with conn.cursor() as cur:
            if since_created_at:
                cur.execute(
                    """SELECT COUNT(*) AS c FROM family_train_events
                       WHERE family=%s AND tf=%s AND decision='FIRE'
                         AND outcome IN ('win','loss','breakeven')
                         AND COALESCE(closed_at, created_at) >= %s""",
                    (family, timeframe_key, float(since_created_at)),
                )
            else:
                cur.execute(
                    """SELECT COUNT(*) AS c FROM family_train_events
                       WHERE family=%s AND tf=%s AND decision='FIRE'
                         AND outcome IN ('win','loss','breakeven')""",
                    (family, timeframe_key),
                )
            row = cur.fetchone()
        conn.close()
        return int((row or {}).get("c") or 0)
    except Exception:
        return 0


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
                    "data": json.dumps({k: v for k, v in trade.items()
                                         if k not in ("reason",)}, default=str),
                },
            )
        conn.close()
    except Exception as exc:
        print(f"[TRADE_DB] upsert_open_trade error: {exc}")


def delete_trade(trade_id: int) -> None:
    """Remove a trade row (used when a LIVE open fails and we roll back the local book)."""
    if not _mysql_enabled():
        return
    try:
        tid = int(trade_id)
    except (TypeError, ValueError):
        return
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM bot_trades WHERE id=%s", (tid,))
        conn.close()
    except Exception as exc:
        print(f"[TRADE_DB] delete_trade error: {exc}")


def finalize_trade(
    trade: dict,
    *,
    exit_price: float | None = None,
    gross_pnl_pct: float | None = None,
    net_pnl_usd: float | None = None,
    exit_fee_usd: float | None = None,
    exit_fee_pct: float | None = None,
    closed_reason: str | None = None,
    gross_pnl_usd: float | None = None,
) -> None:
    if not _mysql_enabled():
        return
    try:
        row = dict(trade or {})
        if exit_price is not None:
            row["current"] = exit_price
        if gross_pnl_pct is not None:
            row["gross_pnl_pct"] = gross_pnl_pct
            row["pnl"] = gross_pnl_pct
        if net_pnl_usd is not None:
            row["net_pnl_usd"] = net_pnl_usd
        if exit_fee_usd is not None:
            row["exit_fee_usd"] = exit_fee_usd
        if exit_fee_pct is not None:
            row["exit_fee_pct"] = exit_fee_pct
        if closed_reason is not None:
            row["closed_reason"] = closed_reason
        if gross_pnl_usd is not None:
            row["gross_pnl_usd"] = gross_pnl_usd
        row["status"] = "sold"
        row["closed_at"] = row.get("closed_at") or time.time()
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE bot_trades
                   SET status='sold', pnl=%(pnl)s, gross_pnl_pct=%(gross_pnl_pct)s,
                       closed_at=%(closed_at)s, data=%(data)s
                   WHERE id=%(id)s
                """,
                {
                    "id": row.get("id", 0),
                    "pnl": row.get("pnl", 0),
                    "gross_pnl_pct": row.get("gross_pnl_pct"),
                    "closed_at": row.get("closed_at"),
                    "data": json.dumps(
                        {k: v for k, v in row.items() if k not in ("reason",)},
                        default=str,
                    ),
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
