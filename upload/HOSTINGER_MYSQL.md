# Hostinger MySQL — Trading Statement setup

## 1) Create database (Hostinger hPanel)
1. hPanel → **Databases** → **MySQL Databases**
2. Create database e.g. `uXXXX_aitrads`
3. Create user + strong password, assign **All privileges** to that DB
4. Note: **Host**, **Database**, **Username**, **Password**

## 2) Import table
phpMyAdmin → select DB → **Import** / **SQL** → paste contents of:
`backend/sql/schema.sql`

Or run only:
```sql
CREATE DATABASE IF NOT EXISTS your_db_name CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
-- then run schema.sql inside that DB
```

## 3) Allow VPS to connect (Remote MySQL)
hPanel → **Remote MySQL** → add your **VPS public IP** (`200.97.171.119` or current).
Without this, Docker backend cannot reach Hostinger MySQL.

## 4) VPS `backend/.env`
```
MYSQL_ENABLED=true
MYSQL_HOST=srvXXXX.hstgr.io
MYSQL_PORT=3306
MYSQL_USER=uXXXX_aitrads
MYSQL_PASSWORD=********
MYSQL_DATABASE=uXXXX_aitrads
```

Then:
```
cd /docker/crypto-ai-trads/crypto-ai-trads
docker compose -f docker-compose.backend-only.yml up -d --build
docker logs crypto-ai-trads-backend-1 --tail 40 | grep MYSQL
```
You should see: `[MYSQL] Connected … — trades table ready.`

## 5) UI
Profile icon (top-right) → **Trading Statement**
API: `GET /trades/statement` (auth required)

Every open/close trade is saved automatically once MySQL is connected.
