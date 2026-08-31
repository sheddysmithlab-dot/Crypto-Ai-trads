#!/usr/bin/env bash
# Mount /var/www/aitrads-frontend into shared-edge and serve aitrads.in from VPS.
set -euo pipefail

FE_SRC="${1:-/docker/crypto-ai-trads/crypto-ai-trads/upload/frontend/public_html}"
FE_DST="/var/www/aitrads-frontend"
EDGE="/docker/shared-edge"

mkdir -p "$FE_DST/assets"
rsync -a --delete \
  --exclude '.htaccess' \
  "$FE_SRC/" "$FE_DST/"

# Ensure compose mounts the frontend dir into the container
COMPOSE="$EDGE/docker-compose.yml"
if ! grep -q '/srv/aitrads-frontend' "$COMPOSE"; then
  cp -a "$COMPOSE" "$COMPOSE.bak-fe-$(date +%Y%m%d%H%M%S)"
  python3 - <<'PY'
from pathlib import Path
p = Path("/docker/shared-edge/docker-compose.yml")
t = p.read_text()
needle = "      - ./Caddyfile:/etc/caddy/Caddyfile:ro\n"
insert = needle + "      - /var/www/aitrads-frontend:/srv/aitrads-frontend:ro\n"
if needle not in t:
    raise SystemExit("Caddyfile volume line not found")
if "/srv/aitrads-frontend" not in t:
    p.write_text(t.replace(needle, insert, 1))
    print("compose: mounted /srv/aitrads-frontend")
else:
    print("compose: mount already present")
PY
fi

# Patch Caddyfile aitrads block to use /srv path + SPA try_files
python3 - <<'PY'
from pathlib import Path
p = Path("/docker/shared-edge/Caddyfile")
t = p.read_text()
block = """
# --- Aitrads frontend (static) ---
aitrads.in, www.aitrads.in {
	encode gzip
	root * /srv/aitrads-frontend
	try_files {path} /index.html
	file_server
	header /index.html Cache-Control "no-cache"
	header /assets/* Cache-Control "public, max-age=31536000, immutable"
}
"""
import re
pat = re.compile(r"\n# --- Aitrads frontend.*?\naitrads\.in, www\.aitrads\.in \{.*?\n\}\n", re.S)
if pat.search(t):
    t = pat.sub("\n" + block.strip() + "\n", t)
    print("caddy: replaced aitrads block")
elif "aitrads.in, www.aitrads.in" in t:
    t = t.replace("root * /var/www/aitrads-frontend", "root * /srv/aitrads-frontend")
    print("caddy: fixed root path only")
else:
    t = t.rstrip() + "\n" + block + "\n"
    print("caddy: appended aitrads block")
p.write_text(t)
PY

cd "$EDGE"
docker compose up -d
sleep 2
docker compose exec -T edge caddy validate --config /etc/caddy/Caddyfile
docker compose exec -T edge caddy reload --config /etc/caddy/Caddyfile
docker compose exec -T edge ls -la /srv/aitrads-frontend/assets | head -20
echo "---- local host test ----"
docker compose exec -T edge wget -qO- --header='Host: aitrads.in' http://127.0.0.1/ | head -15
echo "DONE fe=$(grep -o 'index-[^\"/]*\\.js' "$FE_DST/index.html" | head -1)"
