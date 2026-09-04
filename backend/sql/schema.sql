# Hostinger phpMyAdmin OR VPS Docker MySQL init (docker-entrypoint-initdb.d).
# DB name is created by MySQL container / Hostinger — this file creates tables.

CREATE TABLE IF NOT EXISTS ai_seasons (
  id                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  season_uid        VARCHAR(64) NOT NULL,
  status            ENUM('active','closed') NOT NULL DEFAULT 'active',
  start_capital     DECIMAL(20,8) NOT NULL DEFAULT 0,
  end_capital       DECIMAL(20,8) NULL,
  gross_pnl_usd     DECIMAL(20,8) NULL,
  net_pnl_usd       DECIMAL(20,8) NULL,
  broker_fee_usd    DECIMAL(20,8) NULL,
  trade_count       INT UNSIGNED NOT NULL DEFAULT 0,
  win_count         INT UNSIGNED NOT NULL DEFAULT 0,
  loss_count        INT UNSIGNED NOT NULL DEFAULT 0,
  started_at        DOUBLE NOT NULL,
  ended_at          DOUBLE NULL,
  end_reason        VARCHAR(256) NULL,
  created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

  PRIMARY KEY (id),
  UNIQUE KEY uq_season_uid (season_uid),
  KEY idx_status_started (status, started_at),
  KEY idx_ended (ended_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS trades (
  id                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  trade_uid         VARCHAR(64) NOT NULL,
  bot_trade_id      INT UNSIGNED NOT NULL,
  season_id         BIGINT UNSIGNED NULL,
  username          VARCHAR(64) NULL,
  pair              VARCHAR(32) NOT NULL,
  side              ENUM('LONG','SHORT') NOT NULL,
  status            ENUM('active','locked','sold') NOT NULL DEFAULT 'active',
  source            ENUM('auto','manual') NOT NULL DEFAULT 'auto',
  protected         TINYINT(1) NOT NULL DEFAULT 0,

  entry_price       DECIMAL(20,8) NOT NULL,
  exit_price        DECIMAL(20,8) NULL,
  margin            DECIMAL(20,8) NOT NULL DEFAULT 0,
  position_size     DECIMAL(20,8) NOT NULL DEFAULT 0,
  qty               DECIMAL(28,12) NULL,
  capital_reserved  DECIMAL(20,8) NULL,

  entry_fee_pct     DECIMAL(12,8) NULL,
  entry_fee_usd     DECIMAL(20,8) NOT NULL DEFAULT 0,
  exit_fee_pct      DECIMAL(12,8) NULL,
  exit_fee_usd      DECIMAL(20,8) NOT NULL DEFAULT 0,

  gross_pnl_pct     DECIMAL(12,6) NULL,
  gross_pnl_usd     DECIMAL(20,8) NULL,
  net_pnl_usd       DECIMAL(20,8) NULL,

  peak_gross_pct    DECIMAL(12,6) NULL,
  exchange          VARCHAR(48) NULL,
  bybit_symbol      VARCHAR(32) NULL,
  pattern           VARCHAR(128) NULL,
  signal_candle_time BIGINT NULL,
  closed_reason     VARCHAR(512) NULL,

  opened_at         DOUBLE NOT NULL,
  closed_at         DOUBLE NULL,
  created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

  PRIMARY KEY (id),
  UNIQUE KEY uq_trade_uid (trade_uid),
  KEY idx_bot_trade_id (bot_trade_id),
  KEY idx_season (season_id),
  KEY idx_status_opened (status, opened_at),
  KEY idx_pair_opened (pair, opened_at),
  KEY idx_closed (closed_at),
  KEY idx_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Self-improving engine (also created by trade_db.init_db inline _SCHEMA)
CREATE TABLE IF NOT EXISTS family_engine_rules (
  id                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  family            VARCHAR(64) NOT NULL,
  timeframe_key     VARCHAR(16) NOT NULL,
  min_of_score      DOUBLE NULL,
  min_brain_score   DOUBLE NULL,
  min_rr            DOUBLE NULL,
  sl_pct            DOUBLE NULL,
  tp_pct            DOUBLE NULL,
  candle_soft       TINYINT(1) NOT NULL DEFAULT 1,
  skip_when_json    JSON NULL,
  fire_when_json    JSON NULL,
  lesson_text       TEXT NULL,
  sample_count      INT UNSIGNED NOT NULL DEFAULT 0,
  win_rate          DOUBLE NULL,
  avg_r             DOUBLE NULL,
  version           INT UNSIGNED NOT NULL DEFAULT 1,
  locked            TINYINT(1) NOT NULL DEFAULT 0,
  prev_min_of_score DOUBLE NULL,
  prev_win_rate     DOUBLE NULL,
  updated_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_family_tf (family, timeframe_key),
  KEY idx_family (family)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS family_train_events (
  id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  event_uid      VARCHAR(64) NOT NULL,
  family         VARCHAR(64) NOT NULL,
  pattern        VARCHAR(128) NULL,
  pair           VARCHAR(32) NULL,
  tf             VARCHAR(16) NULL,
  side           VARCHAR(8) NULL,
  decision       ENUM('FIRE','SKIP','DELAY') NOT NULL,
  score          DOUBLE NULL,
  confidence     DOUBLE NULL,
  strategy       VARCHAR(64) NULL,
  context_json   JSON NULL,
  trade_id       INT NULL,
  outcome        ENUM('win','loss','breakeven','unknown','skipped') NULL,
  closed_reason  VARCHAR(512) NULL,
  mfe_pct        DOUBLE NULL,
  mae_pct        DOUBLE NULL,
  net_pnl_usd    DOUBLE NULL,
  fault_tags     JSON NULL,
  lesson         TEXT NULL,
  created_at     DOUBLE NOT NULL,
  closed_at      DOUBLE NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_event_uid (event_uid),
  KEY idx_family_tf (family, tf),
  KEY idx_trade_id (trade_id),
  KEY idx_decision_created (decision, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS engine_formulas (
  formula_key   VARCHAR(96) NOT NULL,
  group_name    VARCHAR(48) NOT NULL DEFAULT 'general',
  value_type    ENUM('number','bool','text','json') NOT NULL DEFAULT 'number',
  value_num     DOUBLE NULL,
  value_text    TEXT NULL,
  value_json    JSON NULL,
  note          VARCHAR(512) NULL,
  updated_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (formula_key),
  KEY idx_group (group_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
