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
