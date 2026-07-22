# Trading Statement DB — Hostinger website MySQL

Live setup connects the VPS backend to **Hostinger shared MySQL** (same DB as the website).
There is **no** MySQL container on the VPS.

## backend/.env
```
MYSQL_ENABLED=true
MYSQL_HOST=srv1668.hstgr.io
MYSQL_PORT=3306
MYSQL_USER=u808821982_aitrads
MYSQL_PASSWORD=<from hPanel>
MYSQL_DATABASE=u808821982_aitrads
```

Hostname can also be IP `193.203.184.165`. Do **not** use `auth-db1688.hstgr.io` (wrong host → access denied).

## Required once in hPanel
Databases → Remote MySQL → allow the VPS public IP (e.g. `200.97.171.119`).
Without that whitelist, the API cannot reach Hostinger MySQL.

Table `trades` is created/used by `backend/trade_db.py` + `backend/sql/schema.sql`.
