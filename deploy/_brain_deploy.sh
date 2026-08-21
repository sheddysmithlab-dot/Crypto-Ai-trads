#!/usr/bin/env bash
set -euo pipefail
cd /docker/crypto-ai-trads/crypto-ai-trads
git fetch origin main
git reset --hard origin/main
echo "HEAD=$(git rev-parse --short HEAD)"
docker compose -f docker-compose.backend-only.yml up -d --build
sleep 10
docker compose -f docker-compose.backend-only.yml exec -T backend python - <<'PY'
import main
from brain_adapter import ENGINE_NAME, evaluate_live_entry
import random, time

print("import ok — engine:", ENGINE_NAME)

# quick BUY path smoke
random.seed(1)
def mk(n,t): return [{'open':100+i*t,'high':100+i*t+0.1,'low':100+i*t-0.05,'close':100+i*t+t*0.8,'volume':500,'close_time':int(time.time()*1000)+i*60000} for i in range(n)]
r=evaluate_live_entry(mk(80,0.5),'1h',pair='BTC/USDT',account_balance=10000)
print("smoke verdict:", r['action'], "| sl:", r.get('sl'), "tp:", r.get('tp'))
PY
echo "health=$(curl -sS -m 8 https://api.aitrads.in/health)"
docker compose -f docker-compose.backend-only.yml ps
