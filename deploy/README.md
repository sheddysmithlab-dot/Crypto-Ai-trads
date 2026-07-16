# Deploy layout

| Part | Where | How |
|------|--------|-----|
| **Frontend** | `aitrads.in` | File Manager → `public_html` (static files from `upload/frontend/public_html/`) |
| **Backend API** | `api.aitrads.in` | VPS: `docker compose up -d --build` (root `docker-compose.yml`) |

Do **not** expose the app on the raw VPS hostname (`srv1831231.hstgr.cloud`).  
Use `api.aitrads.in` only.

See `upload/BUILD.txt` for step-by-step VPS cleanup + deploy.
